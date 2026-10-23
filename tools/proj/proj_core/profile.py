"""proj profile —— 自动探测项目档案（.dsh/project-profile.json）。"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .common import read_text, run_git


@dataclass
class Profile:
    root: str
    stack: list[str] = field(default_factory=list)
    commands: dict = field(default_factory=dict)
    conventions: dict = field(default_factory=dict)
    gates: list[str] = field(default_factory=list)
    verified_at: str = ""
    evidence: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _exists(root: Path, *names: str) -> bool:
    return any((root / n).exists() for n in names)


def detect(root: Path) -> Profile:
    root = root.resolve()
    p = Profile(root=str(root), verified_at=datetime.now(UTC).isoformat())
    if _exists(root, "pyproject.toml", "requirements.txt", "setup.py", "setup.cfg"):
        p.stack.append("python")
    if _exists(root, "package.json"):
        p.stack.append("node")
    if _exists(root, "go.mod"):
        p.stack.append("go")
    if _exists(root, "Cargo.toml"):
        p.stack.append("rust")
    if _exists(root, "Dockerfile", "docker-compose.yml", "compose.yaml"):
        p.stack.append("docker")
    if (root / ".github" / "workflows").is_dir():
        p.stack.append("ci")

    pyproject = read_text(root / "pyproject.toml")
    package = read_text(root / "package.json")
    cmds: dict[str, str] = {}
    if "python" in p.stack:
        cmds["test"] = "python -m pytest -q"
        cmds["lint"] = "python -m ruff check ."
        if "mypy" in pyproject:
            cmds["typecheck"] = "python -m mypy ."
        if "[build-system]" in pyproject:
            cmds["build"] = "python -m build"
    if "node" in p.stack:
        try:
            scripts = json.loads(package).get("scripts", {})
        except json.JSONDecodeError:
            scripts = {}
        if "test" in scripts:
            cmds["test"] = "npm test"
        if "lint" in scripts:
            cmds["lint"] = "npm run lint"
        if "build" in scripts:
            cmds["build"] = "npm run build"
        if (root / "tsconfig.json").exists():
            cmds["typecheck"] = "npx tsc --noEmit"
    p.commands = cmds

    branch = (run_git(root, "rev-parse", "--abbrev-ref", "HEAD") or "").strip() or None
    p.conventions = {
        "commits": "conventional",
        "branch": "trunk" if branch in ("main", "master") else (branch or "unknown"),
        "api": "openapi" if _exists(root, "openapi.yaml", "openapi.json", "swagger.yaml") else "none",
    }
    p.gates = ["G0", "G1", "G2", "G3", "G4", "G5", "G6"]
    commit = (run_git(root, "rev-parse", "--short", "HEAD") or "").strip() or None
    cfg = (pyproject + package).encode("utf-8")
    p.evidence = {"commit": commit, "configHash": hashlib.sha256(cfg).hexdigest()[:16] if cfg else None}
    return p


def write_profile(root: Path, profile: Profile | None = None) -> Path:
    root = root.resolve()
    profile = profile or detect(root)
    out = root / ".dsh" / "project-profile.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(profile.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def print_profile(p: Profile) -> None:
    print(f"=== proj profile: {p.root}")
    print(f"  stack     : {', '.join(p.stack) or '(未识别)'}")
    print(f"  commands  : {json.dumps(p.commands, ensure_ascii=False)}")
    print(f"  conventions: {json.dumps(p.conventions, ensure_ascii=False)}")
    print(f"  gates     : {', '.join(p.gates)}")
    print(f"  evidence  : commit={p.evidence.get('commit')} configHash={p.evidence.get('configHash')}")
