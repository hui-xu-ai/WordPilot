"""proj task check —— 校验 work/tasks/<id>/ 的 intake 五字段与 plan 的 verify 行。"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .common import read_text

INTAKE_FIELDS = ("问题", "用户", "约束", "非目标", "验收")
FIELD_RE = re.compile(r"(?m)^\s*(?:[-*+]\s*)?(?:#{1,6}\s*|\*\*)?(问题|用户|约束|非目标|验收)(?:\*\*)?\s*[:：]")


def _has_field(text: str, name: str) -> bool:
    return re.search(
        rf"(?m)^\s*(?:[-*+]\s*)?(?:#{{1,6}}\s*|\*\*)?{re.escape(name)}(?:\*\*)?\s*[:：]", text
    ) is not None
STEP_RE = re.compile(r"(?m)^\s*(?:[-*+]|\d+\.)\s+\S")
VERIFY_RE = re.compile(r"verify\s*[:：]", re.IGNORECASE)


@dataclass
class TaskReport:
    root: str
    tasks: list[dict] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def check_tasks(root: Path) -> TaskReport:
    root = root.resolve()
    report = TaskReport(root=str(root))
    tasks_dir = root / "work" / "tasks"
    if not tasks_dir.is_dir():
        return report
    for task_dir in sorted(p for p in tasks_dir.iterdir() if p.is_dir()):
        rel = str(task_dir.relative_to(root)).replace("\\", "/")
        info: dict = {"task": rel, "has_intake": False, "has_plan": False, "problems": []}
        intake = task_dir / "intake.md"
        if intake.is_file():
            info["has_intake"] = True
            text = read_text(intake)
            missing = [f for f in INTAKE_FIELDS if not _has_field(text, f)]
            if missing:
                info["problems"].append("intake 缺字段：" + ", ".join(missing))
        plan = task_dir / "plan.md"
        if plan.is_file():
            info["has_plan"] = True
            text = read_text(plan)
            steps = STEP_RE.findall(text)
            verifies = VERIFY_RE.findall(text)
            info["steps"] = len(steps)
            info["verifies"] = len(verifies)
            if not verifies:
                info["problems"].append("plan 没有任何 verify: 行（没有验证命令的步骤不算计划）")
            elif len(verifies) < len(steps):
                info["problems"].append(f"plan 有 {len(steps)} 个步骤但只有 {len(verifies)} 条 verify:")
        if not info["has_intake"] and not info["has_plan"]:
            info["problems"].append("既无 intake.md 也无 plan.md")
        report.tasks.append(info)
        report.problems.extend(f"{rel}：{p}" for p in info["problems"])
    return report


def print_tasks(report: TaskReport) -> None:
    if not report.tasks:
        print("=== proj task check（无 work/tasks，跳过；未做任何校验）")
        return
    print(f"=== proj task check（{len(report.tasks)} 个任务）")
    for t in report.tasks:
        mark = "OK" if not t["problems"] else "FAIL"
        print(f"  [{mark}] {t['task']}  intake={t['has_intake']} plan={t['has_plan']}")
        for p in t["problems"]:
            print(f"        - {p}")
    if report.problems:
        print(f"  结论：FAIL={len(report.problems)}")
    else:
        print("  结论：所有任务文档字段齐全")


def write_json(path: str, data: dict) -> None:
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
