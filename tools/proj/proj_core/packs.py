"""proj pack —— 领域包的列出 / 查看 / 安装（按项目，不塞进核心预设）。"""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .common import read_text

DEFAULT_PACKS_DIR = "pydev-share/preset/pydev/packs"


@dataclass
class Pack:
    name: str
    path: str
    title: str = ""
    summary: str = ""
    skills: list[str] = field(default_factory=list)
    available: bool = True

    def to_dict(self) -> dict:
        return {
            "name": self.name, "path": self.path, "title": self.title,
            "summary": self.summary, "skills": self.skills, "available": self.available,
        }


def _parse_meta(text: str) -> tuple[str, str]:
    title, summary = "", ""
    for line in text.splitlines():
        if line.startswith("# ") and not title:
            title = line[2:].strip()
        elif line.startswith("> ") and not summary:
            summary = line[2:].strip()
    return title, summary


def list_packs(packs_dir: Path) -> list[Pack]:
    packs_dir = packs_dir.resolve()
    if not packs_dir.is_dir():
        return []
    packs: list[Pack] = []
    for d in sorted(packs_dir.iterdir()):
        if not d.is_dir():
            continue
        skills_dir = d / "skills"
        skills = sorted(p.name for p in skills_dir.iterdir() if (p / "SKILL.md").is_file()) if skills_dir.is_dir() else []
        meta = read_text(d / "PACK.md")
        title, summary = _parse_meta(meta)
        packs.append(Pack(d.name, str(d), title or d.name, summary, skills, bool(skills)))
    return packs


def get_pack(packs_dir: Path, name: str) -> Pack | None:
    return next((p for p in list_packs(packs_dir) if p.name == name), None)


def install_pack(packs_dir: Path, name: str, dest: Path, apply: bool = False) -> dict:
    """把 pack 的技能复制到 dest/<pack>/（默认 dry-run）。"""
    packs_dir = packs_dir.resolve()
    pack = get_pack(packs_dir, name)
    if not pack:
        return {"ok": False, "error": f"pack 不存在：{name}"}
    if not pack.skills:
        return {"ok": False, "error": f"pack 无可安装技能：{name}"}
    target = dest.resolve() / name
    result = {"ok": True, "pack": name, "dest": str(target), "skills": pack.skills, "applied": False}
    if not apply:
        return result
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(Path(pack.path), target)
    result["applied"] = True
    return result


def uninstall_pack(dest: Path, name: str, apply: bool = False) -> dict:
    target = dest.resolve() / name
    exists = target.exists()
    result = {"ok": exists, "pack": name, "dest": str(target), "applied": False}
    if exists and apply:
        shutil.rmtree(target)
        result["applied"] = True
    return result


def print_packs(packs: list[Pack]) -> None:
    print(f"=== proj pack（{len(packs)} 个包）")
    for p in packs:
        state = "可用" if p.available else "无技能"
        print(f"  {p.name:<16} [{state}] {p.title} — {len(p.skills)} 技能：{', '.join(p.skills) or '-'}")


def print_pack(pack: Pack) -> None:
    print(f"=== pack {pack.name}: {pack.title}")
    if pack.summary:
        print(f"  {pack.summary}")
    print(f"  路径：{pack.path}")
    for s in pack.skills:
        skill_md = Path(pack.path) / "skills" / s / "SKILL.md"
        text = read_text(skill_md)
        desc = next((l.split(":", 1)[1].strip() for l in text.splitlines() if l.startswith("description:")), "")
        print(f"  - {s}: {desc}")
