"""proj eval —— 一键收口自检（机械项：布局/隐私/体积/垃圾/循环/Wiki/预算/领域包）。"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .graph import build_graph
from .imports import find_unused
from .packs import DEFAULT_PACKS_DIR, list_packs
from .scanner import scan
from .taskcheck import check_tasks

DESC_BUDGET = 1200
BODY_BUDGET = 2200


@dataclass
class Check:
    name: str
    level: str
    detail: str


@dataclass
class EvalReport:
    root: str
    checks: list[dict] = field(default_factory=list)
    failed: int = 0
    warned: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def _skill_budget(preset_dir: Path) -> tuple[int, int, int, list[str]]:
    skills_dir = preset_dir / "skills"
    if not skills_dir.is_dir():
        return 0, 0, 0, [f"未找到 {skills_dir}"]
    count = desc_total = body_total = 0
    problems: list[str] = []
    for d in sorted(p for p in skills_dir.iterdir() if p.is_dir()):
        md = d / "SKILL.md"
        if not md.is_file():
            continue
        count += 1
        text = md.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"(?m)^description:\s*(.+)$", text)
        desc_total += len(m.group(1).strip()) if m else 0
        body_total += len(text.splitlines())
    if desc_total > DESC_BUDGET:
        problems.append(f"description 合计 {desc_total} > {DESC_BUDGET}")
    if body_total > BODY_BUDGET:
        problems.append(f"正文合计 {body_total} > {BODY_BUDGET}")
    return count, desc_total, body_total, problems


def run_eval(root: Path, preset_dir: Path | None = None) -> EvalReport:
    root = root.resolve()
    report = EvalReport(root=str(root))
    s = scan(root)

    def add(name: str, level: str, detail: str) -> None:
        report.checks.append(asdict(Check(name, level, detail)))
        if level == "FAIL":
            report.failed += 1
        elif level == "WARN":
            report.warned += 1

    layout = s.layout
    # 源码根允许 repo/ 或 src/（多数既有项目用 src/）
    has_source = layout.get("hasRepo") or (root / "src").is_dir()
    missing = [k for k in ("hasLocal", "hasDocs") if not layout.get(k)]
    if not has_source:
        missing.insert(0, "hasRepo/src")
    add("布局", "WARN" if missing else "PASS", "缺 " + ", ".join(missing) if missing else "源码根 local/ docs/ 齐备")
    add("隐私", "FAIL" if s.privacy.get("hardFail") else "PASS",
        f"密钥 {len(s.privacy.get('secrets', []))} / .env {len(s.privacy.get('dotenv', []))}")
    over = s.files.get("overFail", [])
    add("体积", "FAIL" if over else "PASS", f"{len(over)} 个文件超 FAIL 阈值" if over else "无超限文件")
    junk = sum(s.junk.get("dirs", {}).values()) + sum(s.junk.get("files", {}).values())
    add("垃圾", "WARN" if junk else "PASS", f"{junk} 项生成物/垃圾" if junk else "无")
    missing_ignore = s.ignore.get("missing", [])
    add(".gitignore", "WARN" if missing_ignore else "PASS",
        "缺 " + ", ".join(missing_ignore) if missing_ignore else "关键条目齐备")

    g = build_graph(root)
    # 循环是债务信号，不是发布阻塞（与 quality-gate 口径一致：WARN）
    add("依赖循环", "WARN" if g.cycles else "PASS",
        f"{len(g.cycles)} 组循环" if g.cycles else f"无循环（{len(g.nodes)} 节点 / {len(g.edges)} 边）")

    wiki_index = root / "docs" / "wiki" / "index.json"
    if not wiki_index.is_file():
        add("Wiki 索引", "WARN", "无 docs/wiki/index.json（跑 proj wiki index）")
    else:
        idx_time = wiki_index.stat().st_mtime
        newer = [p for p in (root / "docs").rglob("*.md") if "wiki" not in p.parts and p.stat().st_mtime > idx_time]
        add("Wiki 索引", "WARN" if newer else "PASS",
            f"{len(newer)} 个 docs/*.md 比索引新" if newer else "索引新鲜")

    tests_dir = root / "tests"
    test_files = list(tests_dir.glob("test_*.py")) if tests_dir.is_dir() else []
    add("测试", "PASS" if test_files else "WARN", f"{len(test_files)} 个测试文件" if test_files else "无 tests/test_*.py")

    imports_report = find_unused(root)
    if imports_report.unused:
        add("未使用 import", "WARN",
            f"{len(imports_report.unused)} 处（{imports_report.files_with_unused} 个文件）")
    else:
        add("未使用 import", "PASS", "未发现")

    task_report = check_tasks(root)
    if task_report.tasks:
        if task_report.problems:
            add("任务文档", "WARN", f"{len(task_report.tasks)} 个任务，{len(task_report.problems)} 个问题")
        else:
            add("任务文档", "PASS", f"{len(task_report.tasks)} 个任务字段齐全")
    else:
        add("任务文档", "PASS", "无 work/tasks（跳过）")

    preset = preset_dir or (root / "pydev-share" / "preset" / "pydev")
    if preset.is_dir():
        count, desc_total, body_total, problems = _skill_budget(preset)
        add("技能预算", "FAIL" if problems else "PASS",
            "; ".join(problems) if problems else f"{count} 技能 / description {desc_total} / 正文 {body_total}")
    else:
        add("技能预算", "WARN", f"未找到预设目录 {preset}")

    packs_dir = Path(DEFAULT_PACKS_DIR)
    packs_list = list_packs(packs_dir)
    if packs_list:
        empty = [p.name for p in packs_list if not p.skills]
        add("领域包", "WARN" if empty else "PASS",
            f"{len(packs_list)} 包 / {sum(len(p.skills) for p in packs_list)} 技能" + (f"（空包：{', '.join(empty)}）" if empty else ""))
    else:
        add("领域包", "WARN", f"未找到领域包目录 {packs_dir}")
    return report


def print_eval(report: EvalReport) -> None:
    print(f"=== proj eval: {report.root}")
    icon = {"PASS": "OK  ", "WARN": "WARN", "FAIL": "FAIL"}
    for c in report.checks:
        print(f"  [{icon.get(c['level'], c['level'])}] {c['name']}：{c['detail']}")
    print(f"  结论：FAIL={report.failed} WARN={report.warned}")


def write_json(path: str, data: dict) -> None:
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
