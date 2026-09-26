#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""学校排课冲突裁决工具（纯 Python 标准库，单文件）。

用法:
    python3 scheduler.py 输入文件        # 从文件读取命令流
    python3 scheduler.py < 输入文件      # 从标准输入读取

输入为命令流文本，每行一条命令（# 开头为注释，空行忽略）:

    teacher <教师名>                            定义教师
    class   <班级名>                            定义班级
    slot    <时段ID> <星期> <HH:MM> <HH:MM>     定义时段（可跨天，按星期+时间段判重叠）
    add     <课程> <教师> <班级> <时段ID> <优先级>   新增课程安排（优先级为整数，大者胜）
    move    <课程> <新时段ID>                    调整安排时段（触发冲突重检与传播）
    remove  <课程>                              移除安排（触发冲突重检与传播）
    report                                      立即打印当前排课表与报告

所有命令处理完毕后自动打印一次最终报告（排课表 + 被挤掉清单 + 错误清单 + 操作历史）。

裁决规则:
    同一教师或同一班级在重叠时段（同星期且时间段相交）只能保留一个安排；
    优先级数值大者胜出，优先级相同则先提交者胜出；被挤掉的安排会被记录报告，
    并在后续调整使其不再冲突时自动恢复（调整传播）。每次变更后全量重仲裁，
    保证结果只取决于当前有效安排集合，与操作顺序无关、可追溯。
"""

import sys
from dataclasses import dataclass


def parse_time(text):
    """把 HH:MM 解析为分钟数，非法格式抛 ValueError。"""
    parts = text.split(":")
    if len(parts) != 2:
        raise ValueError("时间格式应为 HH:MM")
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        raise ValueError("时间格式应为 HH:MM")
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("时间超出范围: %s" % text)
    return hour * 60 + minute


def fmt_time(minutes):
    return "%02d:%02d" % (minutes // 60, minutes % 60)


@dataclass
class Slot:
    sid: str
    day: str
    start: int  # 分钟
    end: int    # 分钟

    def overlaps(self, other):
        return self.day == other.day and self.start < other.end and other.start < self.end

    def label(self):
        return "%s %s %s-%s" % (self.sid, self.day, fmt_time(self.start), fmt_time(self.end))


@dataclass
class Arrangement:
    course: str
    teacher: str
    klass: str
    slot: str
    priority: int
    seq: int  # 提交顺序号，用于同优先级时先到先得的稳定裁决

    def label(self):
        return "%s(教师:%s 班级:%s 时段:%s 优先级:%d)" % (
            self.course, self.teacher, self.klass, self.slot, self.priority)


class Scheduler:
    def __init__(self):
        self.teachers = set()
        self.classes = set()
        self.slots = {}          # sid -> Slot（保持定义顺序）
        self.arrangements = {}   # course -> Arrangement
        self.errors = []         # 错误清单: [str]
        self.history = []        # 操作历史: [str]
        self.placed = {}         # 当前排上的: course -> Arrangement
        self.bumped = {}         # 当前被挤掉的: course -> 原因
        self._seq = 0

    # ---------- 基础校验 ----------

    def _error(self, lineno, msg):
        self.errors.append("第%d行: %s" % (lineno, msg))

    # ---------- 仲裁核心 ----------

    def _arbitrate(self):
        """全量重仲裁：按 (优先级降序, 提交顺序升序) 依次尝试放入，冲突者被挤掉。"""
        order = sorted(self.arrangements.values(),
                       key=lambda a: (-a.priority, a.seq))
        placed = {}
        bumped = {}
        for arr in order:
            slot = self.slots[arr.slot]
            reason = None
            for other in placed.values():
                oslot = self.slots[other.slot]
                if not slot.overlaps(oslot):
                    continue
                if other.teacher == arr.teacher:
                    reason = ("教师冲突: 教师 %s 在时段 %s 已排课程 %s"
                              % (arr.teacher, arr.slot, other.course))
                    break
                if other.klass == arr.klass:
                    reason = ("班级冲突: 班级 %s 在时段 %s 已排课程 %s"
                              % (arr.klass, arr.slot, other.course))
                    break
            if reason is None:
                placed[arr.course] = arr
            else:
                bumped[arr.course] = reason
        return placed, bumped

    def _recompute_and_log(self, lineno, action):
        """变更后重仲裁，并把变化（新挤掉/恢复/原因变化）写入历史。"""
        new_placed, new_bumped = self._arbitrate()
        effects = []
        for course in sorted(new_bumped):
            if course not in self.bumped:
                effects.append("%s 被挤掉（%s）" % (course, new_bumped[course]))
            elif new_bumped[course] != self.bumped[course]:
                effects.append("%s 仍被挤掉（原因变为: %s）" % (course, new_bumped[course]))
        for course in sorted(self.bumped):
            if course not in new_bumped:
                effects.append("%s 恢复排课（时段 %s）"
                               % (course, new_placed[course].slot))
        self.placed, self.bumped = new_placed, new_bumped
        entry = "第%d行: %s" % (lineno, action)
        if effects:
            entry += " => " + "；".join(effects)
        self.history.append(entry)

    # ---------- 命令实现 ----------

    def cmd_teacher(self, lineno, name):
        if name in self.teachers:
            self._error(lineno, "重复定义教师: %s" % name)
            return
        self.teachers.add(name)
        self.history.append("第%d行: 定义教师 %s" % (lineno, name))

    def cmd_class(self, lineno, name):
        if name in self.classes:
            self._error(lineno, "重复定义班级: %s" % name)
            return
        self.classes.add(name)
        self.history.append("第%d行: 定义班级 %s" % (lineno, name))

    def cmd_slot(self, lineno, sid, day, start_text, end_text):
        if sid in self.slots:
            self._error(lineno, "重复定义时段: %s" % sid)
            return
        try:
            start = parse_time(start_text)
            end = parse_time(end_text)
        except ValueError as exc:
            self._error(lineno, "时段 %s 时间非法: %s" % (sid, exc))
            return
        if start >= end:
            self._error(lineno, "时段 %s 开始时间不早于结束时间" % sid)
            return
        self.slots[sid] = Slot(sid, day, start, end)
        self.history.append("第%d行: 定义时段 %s" % (lineno, self.slots[sid].label()))

    def cmd_add(self, lineno, course, teacher, klass, sid, priority_text):
        if course in self.arrangements:
            self._error(lineno, "课程 %s 已存在安排，不能重复添加（可用 move 调整）" % course)
            return
        ok = True
        if teacher not in self.teachers:
            self._error(lineno, "课程 %s 引用了不存在的教师: %s" % (course, teacher))
            ok = False
        if klass not in self.classes:
            self._error(lineno, "课程 %s 引用了不存在的班级: %s" % (course, klass))
            ok = False
        if sid not in self.slots:
            self._error(lineno, "课程 %s 引用了不存在的时段: %s" % (course, sid))
            ok = False
        try:
            priority = int(priority_text)
        except ValueError:
            self._error(lineno, "课程 %s 优先级不是整数: %s" % (course, priority_text))
            ok = False
        if not ok:
            return
        self._seq += 1
        self.arrangements[course] = Arrangement(course, teacher, klass, sid,
                                                priority, self._seq)
        self._recompute_and_log(lineno, "新增安排 %s"
                                % self.arrangements[course].label())

    def cmd_move(self, lineno, course, new_sid):
        arr = self.arrangements.get(course)
        if arr is None:
            self._error(lineno, "move 失败: 课程 %s 不存在" % course)
            return
        if new_sid not in self.slots:
            self._error(lineno, "move 失败: 时段 %s 不存在" % new_sid)
            return
        old_sid = arr.slot
        arr.slot = new_sid
        self._recompute_and_log(lineno, "调整课程 %s 时段 %s -> %s"
                                % (course, old_sid, new_sid))

    def cmd_remove(self, lineno, course):
        if course not in self.arrangements:
            self._error(lineno, "remove 失败: 课程 %s 不存在" % course)
            return
        del self.arrangements[course]
        self._recompute_and_log(lineno, "移除课程 %s 的安排" % course)

    # ---------- 输入解析 ----------

    def run_lines(self, lines, out):
        for lineno, raw in enumerate(lines, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            cmd, args = parts[0], parts[1:]
            try:
                if cmd == "teacher" and len(args) == 1:
                    self.cmd_teacher(lineno, *args)
                elif cmd == "class" and len(args) == 1:
                    self.cmd_class(lineno, *args)
                elif cmd == "slot" and len(args) == 4:
                    self.cmd_slot(lineno, *args)
                elif cmd == "add" and len(args) == 5:
                    self.cmd_add(lineno, *args)
                elif cmd == "move" and len(args) == 2:
                    self.cmd_move(lineno, *args)
                elif cmd == "remove" and len(args) == 1:
                    self.cmd_remove(lineno, *args)
                elif cmd == "report" and not args:
                    self.print_report(out, title="中间报告（第%d行）" % lineno)
                else:
                    self._error(lineno, "无法解析的命令: %s" % line)
            except Exception as exc:  # 兜底：单行出错不影响后续命令
                self._error(lineno, "命令执行异常: %s (%s)" % (line, exc))

    # ---------- 报告输出 ----------

    def print_report(self, out, title="最终报告"):
        w = lambda s="": print(s, file=out)
        w("=" * 72)
        w(title)
        w("=" * 72)

        w("\n【排课表】（按时段定义顺序）")
        if not self.placed:
            w("  （空）")
        for sid, slot in self.slots.items():
            courses = sorted((a for a in self.placed.values() if a.slot == sid),
                             key=lambda a: a.seq)
            for a in courses:
                w("  %-6s %-4s %s-%s | 课程:%s | 教师:%s | 班级:%s | 优先级:%d"
                  % (sid, slot.day, fmt_time(slot.start), fmt_time(slot.end),
                     a.course, a.teacher, a.klass, a.priority))

        w("\n【被挤掉的安排】")
        if not self.bumped:
            w("  （无）")
        for course in sorted(self.bumped):
            a = self.arrangements[course]
            w("  %s | 原因: %s" % (a.label(), self.bumped[course]))

        w("\n【错误清单】")
        if not self.errors:
            w("  （无）")
        for e in self.errors:
            w("  " + e)

        w("\n【操作历史】")
        if not self.history:
            w("  （无）")
        for h in self.history:
            w("  " + h)
        w("")


def main(argv):
    if len(argv) > 2 or (len(argv) == 2 and argv[1] in ("-h", "--help")):
        print(__doc__)
        return 0 if len(argv) == 2 else 2
    if len(argv) == 2:
        with open(argv[1], encoding="utf-8") as f:
            lines = f.readlines()
    else:
        lines = sys.stdin.readlines()
    sched = Scheduler()
    sched.run_lines(lines, sys.stdout)
    sched.print_report(sys.stdout)
    return 1 if sched.errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
