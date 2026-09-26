#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""学校排课冲突裁决工具（纯 Python 标准库，单文件）。

用法:
    python3 scheduler.py [输入文件]      # 省略输入文件时从标准输入读取

输入为逐行指令流（# 开头为注释，空行忽略）:

    TEACHER  <教师>                              声明教师
    CLASS    <班级>                              声明班级
    SLOT     <时段ID> <星期> <开始HH:MM> <结束HH:MM>   声明时段
    SCHEDULE <安排ID> <课程> <教师> <班级> <时段ID> <优先级>   新增安排
    ADJUST   <安排ID> <新时段ID>                  调整安排时段（触发冲突重检与传播）
    REPORT                                       立即打印当前报告（结束时也会自动打印）

裁决规则:
    * 同一教师或同一班级，在重叠时段上出现多门课程即冲突（教师冲突/班级冲突）；
    * 冲突按优先级裁决：优先级高者保留，低者被挤掉并记录；优先级相同则先到者保留；
    * ADJUST 改时段后重新裁决，并自动传播：被挤掉的安排若冲突已解除则恢复入表；
    * 引用不存在的教师/班级/时段的安排被拒绝并记入错误清单；
    * 全部事件记入历史，可追溯。
"""

import sys
from dataclasses import dataclass


# ---------------------------------------------------------------- 数据模型

def parse_time(text):
    """'HH:MM' -> 分钟数；非法格式抛 ValueError。"""
    try:
        hh_s, mm_s = text.split(":")
        hh, mm = int(hh_s), int(mm_s)
        if not (0 <= hh < 24 and 0 <= mm < 60):
            raise ValueError
    except ValueError:
        raise ValueError("非法时间格式 %r（应为 HH:MM）" % text)
    return hh * 60 + mm


def fmt_time(minutes):
    return "%02d:%02d" % (minutes // 60, minutes % 60)


@dataclass
class Slot:
    slot_id: str
    day: str
    start: int
    end: int

    def overlaps(self, other):
        return (self.day == other.day
                and self.start < other.end
                and other.start < self.end)

    def label(self):
        return "%s(%s %s-%s)" % (self.slot_id, self.day,
                                 fmt_time(self.start), fmt_time(self.end))


@dataclass
class Arrangement:
    arr_id: str
    course: str
    teacher: str
    class_name: str
    slot_id: str
    priority: int
    valid: bool = True    # 引用（教师/班级/时段）是否合法
    active: bool = False  # 当前是否在排课表内

    def label(self):
        return ("%s[课程=%s 教师=%s 班级=%s 时段=%s 优先级=%d]"
                % (self.arr_id, self.course, self.teacher,
                   self.class_name, self.slot_id, self.priority))


# ---------------------------------------------------------------- 排课引擎

class Scheduler:
    def __init__(self):
        self.teachers = set()
        self.classes = set()
        self.slots = {}          # slot_id -> Slot（保持插入顺序）
        self.arrangements = {}   # arr_id -> Arrangement
        self.errors = []         # 错误清单（引用缺失、非法指令等）
        self.reports = []        # 冲突裁决报告（冲突、挤掉、恢复）
        self.history = []        # 全量历史
        self._seq = 0

    # ---- 记录 ------------------------------------------------------

    def _log(self, msg):
        self._seq += 1
        self.history.append("[%03d] %s" % (self._seq, msg))

    def _error(self, msg):
        self.errors.append(msg)
        self._log("错误: " + msg)

    def _report(self, msg):
        self.reports.append(msg)
        self._log(msg)

    # ---- 基础数据 --------------------------------------------------

    def add_teacher(self, name):
        if name in self.teachers:
            self._error("教师 %r 重复声明，已忽略" % name)
            return
        self.teachers.add(name)
        self._log("声明教师 %s" % name)

    def add_class(self, name):
        if name in self.classes:
            self._error("班级 %r 重复声明，已忽略" % name)
            return
        self.classes.add(name)
        self._log("声明班级 %s" % name)

    def add_slot(self, slot_id, day, start_s, end_s):
        if slot_id in self.slots:
            self._error("时段 %r 重复声明，已忽略" % slot_id)
            return
        start = parse_time(start_s)
        end = parse_time(end_s)
        if end <= start:
            self._error("时段 %r 结束时间不晚于开始时间，已忽略" % slot_id)
            return
        slot = Slot(slot_id, day, start, end)
        self.slots[slot_id] = slot
        self._log("声明时段 %s" % slot.label())

    # ---- 冲突检测 --------------------------------------------------

    def _conflicts(self, arr):
        """返回与 arr 冲突的当前在表安排列表。"""
        slot = self.slots[arr.slot_id]
        result = []
        for other in self.arrangements.values():
            if other is arr or not other.active or not other.valid:
                continue
            if other.teacher != arr.teacher and other.class_name != arr.class_name:
                continue
            if slot.overlaps(self.slots[other.slot_id]):
                result.append(other)
        return result

    @staticmethod
    def _conflict_kind(arr, other):
        kinds = []
        if other.teacher == arr.teacher:
            kinds.append("教师冲突(%s)" % arr.teacher)
        if other.class_name == arr.class_name:
            kinds.append("班级冲突(%s)" % arr.class_name)
        return "、".join(kinds)

    # ---- 裁决 ------------------------------------------------------

    def _place(self, arr):
        """把 arr 当作挑战者入表：无冲突直接入表；有冲突按优先级裁决。"""
        conf = self._conflicts(arr)
        if not conf:
            arr.active = True
            self._log("安排 %s 入表（时段 %s）" % (arr.arr_id, arr.slot_id))
            return
        top = max(c.priority for c in conf)
        if arr.priority > top:
            for c in conf:
                c.active = False
                self._report(
                    "冲突裁决: %s(优先级%d) 与 %s(优先级%d) 发生%s，"
                    "%s 被挤掉" % (arr.arr_id, arr.priority, c.arr_id,
                                   c.priority, self._conflict_kind(arr, c),
                                   c.arr_id))
            arr.active = True
            self._log("安排 %s 入表（时段 %s）" % (arr.arr_id, arr.slot_id))
        else:
            arr.active = False
            others = "、".join("%s(优先级%d)" % (c.arr_id, c.priority)
                               for c in conf)
            blockers = "、".join(c.arr_id for c in conf
                                 if c.priority >= arr.priority)
            kinds = "、".join(sorted({self._conflict_kind(arr, c) for c in conf}))
            self._report("冲突裁决: %s(优先级%d) 与 %s 发生%s，"
                         "因 %s 优先级不低，%s 被挤掉"
                         % (arr.arr_id, arr.priority, others, kinds,
                            blockers, arr.arr_id))

    def _propagate(self):
        """调整传播：被挤掉的合法安排若冲突已解除，按优先级从高到低恢复。"""
        changed = True
        while changed:
            changed = False
            bumped = [a for a in self.arrangements.values()
                      if a.valid and not a.active]
            bumped.sort(key=lambda a: (-a.priority, a.arr_id))
            for arr in bumped:
                if not self._conflicts(arr):
                    arr.active = True
                    changed = True
                    self._report("调整传播: %s 的冲突已解除，恢复入表（时段 %s）"
                                 % (arr.arr_id, arr.slot_id))

    # ---- 安排指令 --------------------------------------------------

    def schedule(self, arr_id, course, teacher, class_name, slot_id, priority):
        if arr_id in self.arrangements:
            self._error("安排 %r 的 ID 重复，已忽略" % arr_id)
            return
        arr = Arrangement(arr_id, course, teacher, class_name, slot_id, priority)
        self.arrangements[arr_id] = arr

        problems = []
        if teacher not in self.teachers:
            problems.append("教师 %r 不存在" % teacher)
        if class_name not in self.classes:
            problems.append("班级 %r 不存在" % class_name)
        if slot_id not in self.slots:
            problems.append("时段 %r 不存在" % slot_id)
        if problems:
            arr.valid = False
            self._error("安排 %s 被拒绝: %s" % (arr.label(), "；".join(problems)))
            return

        self._log("收到新安排 %s" % arr.label())
        self._place(arr)
        self._propagate()

    def adjust(self, arr_id, new_slot):
        arr = self.arrangements.get(arr_id)
        if arr is None:
            self._error("调整失败: 安排 %r 不存在" % arr_id)
            return
        if not arr.valid:
            self._error("调整失败: 安排 %s 引用缺失（无效安排），不能调整" % arr_id)
            return
        if new_slot not in self.slots:
            self._error("调整失败: 时段 %r 不存在" % new_slot)
            return
        if not arr.active:
            self._error("调整失败: 安排 %s 当前被挤掉、不在表内，不能调整" % arr_id)
            return
        old_slot = arr.slot_id
        arr.slot_id = new_slot
        arr.active = False  # 摘下，作为挑战者重新裁决
        self._log("调整 %s: 时段 %s -> %s，重新检查冲突"
                  % (arr_id, old_slot, new_slot))
        self._place(arr)
        self._propagate()

    # ---- 报告 ------------------------------------------------------

    def render_report(self):
        out = []
        out.append("=" * 64)
        out.append("排课表（生效安排，共 %d 项）"
                   % sum(1 for a in self.arrangements.values() if a.active))
        out.append("=" * 64)
        any_active = False
        for slot in self.slots.values():
            items = [a for a in self.arrangements.values()
                     if a.active and a.slot_id == slot.slot_id]
            if not items:
                continue
            any_active = True
            out.append("  时段 %s:" % slot.label())
            for a in sorted(items, key=lambda x: (-x.priority, x.arr_id)):
                out.append("    %-6s 课程=%-4s 教师=%-4s 班级=%-4s 优先级=%d"
                           % (a.arr_id, a.course, a.teacher,
                              a.class_name, a.priority))
        if not any_active:
            out.append("  （空）")

        inactive = [a for a in self.arrangements.values() if not a.active]
        out.append("")
        out.append("被挤掉 / 被拒绝的安排（共 %d 项）" % len(inactive))
        out.append("-" * 64)
        if inactive:
            for a in inactive:
                state = "无效(引用缺失)" if not a.valid else "被挤掉"
                out.append("  %-6s %-12s %s" % (a.arr_id, state, a.label()))
        else:
            out.append("  （无）")

        out.append("")
        out.append("冲突裁决报告（共 %d 条）" % len(self.reports))
        out.append("-" * 64)
        out.extend(("  " + r) if self.reports else "  （无）"
                   for r in (self.reports or [""]))
        if not self.reports:
            out.pop()
            out.append("  （无）")

        out.append("")
        out.append("错误清单（共 %d 条）" % len(self.errors))
        out.append("-" * 64)
        if self.errors:
            out.extend("  " + e for e in self.errors)
        else:
            out.append("  （无）")

        out.append("")
        out.append("变更历史（共 %d 条，可追溯）" % len(self.history))
        out.append("-" * 64)
        out.extend("  " + h for h in self.history)
        return "\n".join(out)


# ---------------------------------------------------------------- 指令解析

USAGE = __doc__


def process_line(sched, line, lineno):
    line = line.strip()
    if not line or line.startswith("#"):
        return
    parts = line.split()
    cmd = parts[0].upper()
    try:
        if cmd == "TEACHER" and len(parts) == 2:
            sched.add_teacher(parts[1])
        elif cmd == "CLASS" and len(parts) == 2:
            sched.add_class(parts[1])
        elif cmd == "SLOT" and len(parts) == 5:
            sched.add_slot(parts[1], parts[2], parts[3], parts[4])
        elif cmd == "SCHEDULE" and len(parts) == 7:
            try:
                priority = int(parts[6])
            except ValueError:
                raise ValueError("优先级 %r 不是整数" % parts[6])
            sched.schedule(parts[1], parts[2], parts[3], parts[4],
                           parts[5], priority)
        elif cmd == "ADJUST" and len(parts) == 3:
            sched.adjust(parts[1], parts[2])
        elif cmd == "REPORT" and len(parts) == 1:
            print(sched.render_report())
        else:
            sched._error("第 %d 行: 无法解析的指令: %r" % (lineno, line))
    except ValueError as exc:
        sched._error("第 %d 行: %s" % (lineno, exc))


def main(argv):
    if len(argv) > 2 or (len(argv) == 2 and argv[1] in ("-h", "--help")):
        sys.stdout.write(USAGE)
        return 0
    sched = Scheduler()
    if len(argv) == 2:
        with open(argv[1], "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    else:
        lines = sys.stdin.readlines()
    for lineno, line in enumerate(lines, 1):
        process_line(sched, line, lineno)
    print(sched.render_report())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
