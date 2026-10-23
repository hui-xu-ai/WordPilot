"""proj context —— 有界上下文包：项目档案 + 交接卡 + Wiki + 任务卡 + 架构。"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .common import read_text, token_estimate
from .wiki import DEFAULT_INDEX

# 优先级从高到低：预算不足时先丢后面的
SOURCES = [
    ("task", 60),
    ("handoff", 80),
    ("project-profile", 60),
    ("wiki-index", 60),
    ("architecture", 40),
]


@dataclass
class ContextPack:
    root: str
    task: str | None
    max_tokens: int
    sections: list[dict] = field(default_factory=list)
    chars: int = 0
    tokens: int = 0
    truncated: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def _task_path(root: Path, task: str | None) -> Path | None:
    if not task:
        return None
    p = Path(task)
    if p.is_absolute():
        return p / "TASK.md" if p.is_dir() else p
    cand = root / task
    return cand / "TASK.md" if cand.is_dir() else cand


def _sources(root: Path, task: str | None) -> dict[str, Path | None]:
    return {
        "task": _task_path(root, task),
        "handoff": root / ".dsh-memory" / "HANDOFF.md",
        "project-profile": root / ".dsh" / "project-profile.json",
        "wiki-index": root / DEFAULT_INDEX / "INDEX.md",
        "architecture": root / "docs" / "ARCHITECTURE.md",
    }


def build_context(root: Path, task: str | None = None, max_tokens: int = 2500) -> ContextPack:
    root = root.resolve()
    pack = ContextPack(root=str(root), task=task, max_tokens=max_tokens)
    paths = _sources(root, task)
    budget = max_tokens * 4

    for label, limit in SOURCES:
        path = paths.get(label)
        if not path or not path.exists():
            continue
        body = "\n".join(read_text(path).splitlines()[:limit]).rstrip()
        if label == "project-profile":
            try:
                body = json.dumps(json.loads(body), ensure_ascii=False, indent=2)[:4000]
            except json.JSONDecodeError:
                pass
        pack.sections.append({"label": label, "path": str(path), "body": body})

    # 预算裁剪：从最低优先级开始整段丢弃，必要时截断最高优先级
    while pack.sections and sum(len(s["body"]) for s in pack.sections) > budget:
        if len(pack.sections) == 1:
            only = pack.sections[0]
            only["body"] = only["body"][:budget]
            pack.truncated = True
            break
        pack.sections.pop()
        pack.truncated = True

    pack.chars = sum(len(s["body"]) for s in pack.sections)
    pack.tokens = token_estimate("".join(s["body"] for s in pack.sections))
    return pack


def print_context(pack: ContextPack) -> None:
    for s in pack.sections:
        print(f"--- {s['label']} ({s['path']}) ---")
        print(s["body"])
    note = "（已按预算裁剪）" if pack.truncated else ""
    print(f"--- context: {len(pack.sections)} 段 / {pack.chars} 字符 ≈ {pack.tokens} tokens / 预算 {pack.max_tokens}{note} ---")
