"""proj adr —— ADR 索引与校验（docs/adr/*.md）。"""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .common import read_text

ADR_RE = re.compile(r"^#\s*ADR[- ]?(\d{1,4})\s*[:：]?\s*(.+)$", re.MULTILINE)
# 容忍常见 Markdown 写法：`状态: x` / `- 状态：x` / `**状态**：x`
STATUS_RE = re.compile(r"(?m)^\s*(?:[-*+]\s*)?(?:\*\*)?(?:状态|Status)(?:\*\*)?\s*[:：]\s*(.+?)\s*$")
DATE_RE = re.compile(r"(?m)^\s*(?:[-*+]\s*)?(?:\*\*)?(?:日期|Date)(?:\*\*)?\s*[:：]\s*(.+?)\s*$")
REQUIRED_SECTIONS = ("背景", "决策", "后果")
ALTERNATIVES_RE = re.compile(r"(?m)^\s*#{0,6}\s*(?:备选|Alternatives)")


@dataclass
class Adr:
    number: int
    title: str
    path: str
    status: str = ""
    date: str = ""
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def collect(root: Path, adr_dir: str = "docs/adr") -> list[Adr]:
    base = root / adr_dir
    if not base.is_dir():
        return []
    out: list[Adr] = []
    for p in sorted(base.glob("*.md")):
        if p.name.upper().startswith("INDEX"):
            continue
        rel = str(p.relative_to(root)).replace("\\", "/")
        text = read_text(p)
        m = ADR_RE.search(text)
        if not m:
            out.append(Adr(0, p.stem, rel, problems=["缺 `# ADR-NNN: 标题` 标题"]))
            continue
        adr = Adr(int(m.group(1)), m.group(2).strip(), rel)
        sm, dm = STATUS_RE.search(text), DATE_RE.search(text)
        adr.status = sm.group(1) if sm else ""
        adr.date = dm.group(1) if dm else ""
        if not adr.status:
            adr.problems.append("缺「状态」")
        if not adr.date:
            adr.problems.append("缺「日期」")
        adr.problems.extend(f"缺「{s}」段" for s in REQUIRED_SECTIONS if s not in text)
        if not ALTERNATIVES_RE.search(text):
            adr.problems.append("缺「备选」段")
        out.append(adr)
    return out


def validate(adrs: list[Adr]) -> list[str]:
    problems: list[str] = []
    nums = [a.number for a in adrs if a.number]
    dup = sorted({n for n in nums if nums.count(n) > 1})
    if dup:
        problems.append("重复编号：" + ", ".join(f"ADR-{n:03d}" for n in dup))
    if nums:
        missing = sorted(set(range(1, max(nums) + 1)) - set(nums))
        if missing:
            problems.append("编号断号：" + ", ".join(f"ADR-{n:03d}" for n in missing))
    for a in adrs:
        problems.extend(f"{a.path}：{p}" for p in a.problems)
    return problems


def render_index(adrs: list[Adr]) -> str:
    lines = [
        "# ADR 索引",
        "",
        "> 由 `proj adr index` 生成；编号唯一且连续，每条含 状态/日期/背景/决策/后果/备选。",
        "",
        "| 编号 | 标题 | 状态 | 日期 | 文件 |",
        "|---|---|---|---|---|",
    ]
    for a in adrs:
        num = f"ADR-{a.number:03d}" if a.number else "—"
        flag = "" if a.ok else " ⚠️"
        lines.append(f"| {num} | {a.title}{flag} | {a.status or '—'} | {a.date or '—'} | [{a.path}]({Path(a.path).name}) |")
    return "\n".join(lines) + "\n"


def build_index(root: Path, adr_dir: str = "docs/adr", out: str | None = None, write: bool = True) -> dict:
    root = root.resolve()
    adr_root = (root / adr_dir).resolve()
    try:
        adr_root.relative_to(root)
        inside = True
    except ValueError:
        inside = False
    if not inside:
        return {"adr_dir": adr_dir, "index": adr_dir, "count": 0, "entries": [],
                "problems": [f"ADR 目录越界（必须位于 root 内）：{adr_dir}"], "written": False}
    adrs = collect(root, adr_dir)
    problems = validate(adrs)
    # `--out` 允许绝对路径（可能在 root 外）；显示时不再盲目 relative_to，避免 ValueError
    raw_out = Path(out) if out else root / f"{adr_dir}/INDEX.md"
    out_path = raw_out if raw_out.is_absolute() else (root / raw_out)
    try:
        display = str(out_path.relative_to(root)).replace("\\", "/")
    except ValueError:
        display = str(out_path)
    if adrs and write:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # 原子替换：目标若是硬链接/别名，写临时文件再 replace，不修改共享 inode
        tmp = out_path.with_name(out_path.name + f".tmp-{os.urandom(4).hex()}")
        tmp.write_text(render_index(adrs), encoding="utf-8")
        os.replace(tmp, out_path)
    return {
        "adr_dir": adr_dir,
        "index": display,
        "count": len(adrs),
        "entries": [asdict(a) for a in adrs],
        "problems": problems,
        "written": bool(adrs and write),
    }


def print_adr(data: dict) -> None:
    print(f"=== proj adr index（{data['count']} 条）→ {data['index']}")
    for a in data["entries"]:
        num = f"ADR-{a['number']:03d}" if a["number"] else "—"
        mark = "" if not a["problems"] else "  ⚠️ " + "；".join(a["problems"])
        print(f"  {num}  {a['title']}  [{a['status'] or '—'}]{mark}")
    if data["problems"]:
        print(f"  [FAIL] {len(data['problems'])} 个问题：")
        for p in data["problems"][:10]:
            print("    - " + p)
    else:
        print("  [OK] 编号连续、字段齐全")


def write_json(path: str, data: dict) -> None:
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
