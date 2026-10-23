"""proj scan —— 布局 / 规模 / 垃圾 / 隐私 / .gitignore 扫描。"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .common import (
    IGNORE_EXPECT,
    SIZE_EXEMPT_PREFIXES,
    SKIP_SCAN_TOP,
    TEXT_EXT,
    is_private_rel,
    iter_files,
    read_text,
    run_git,
    scan_text_secrets,
)


@dataclass
class FileInfo:
    path: str
    bytes: int
    lines: int | None = None


@dataclass
class Scan:
    root: str
    generated_at: str
    git: dict = field(default_factory=dict)
    layout: dict = field(default_factory=dict)
    files: dict = field(default_factory=dict)
    junk: dict = field(default_factory=dict)
    privacy: dict = field(default_factory=dict)
    ignore: dict = field(default_factory=dict)
    issues: list[dict] = field(default_factory=list)
    score: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def scan(root: Path, max_lines_warn: int = 400, max_lines_fail: int = 800) -> Scan:
    root = root.resolve()
    s = Scan(root=str(root), generated_at=datetime.now(UTC).isoformat())

    branch = run_git(root, "rev-parse", "--abbrev-ref", "HEAD")
    status = run_git(root, "status", "--porcelain") or ""
    tracked = run_git(root, "ls-files") or ""
    tracked_list = [x for x in tracked.splitlines() if x.strip()]
    s.git = {
        "isRepo": branch is not None,
        "branch": (branch or "").strip() or None,
        "modified": sum(1 for x in status.splitlines() if x[:2].strip() and not x.startswith("??")),
        "untracked": sum(1 for x in status.splitlines() if x.startswith("??")),
        "tracked": len(tracked_list),
    }

    s.layout = {
        "hasRepo": (root / "repo").is_dir(),
        "hasLocal": (root / "local").is_dir(),
        "hasDocs": (root / "docs").is_dir(),
        "hasDsh": (root / ".dsh").is_dir(),
        "violations": [],
    }
    if not s.layout["hasRepo"]:
        s.layout["violations"].append("缺少 repo/（源码主目录）")
    if not s.layout["hasLocal"]:
        s.layout["violations"].append("缺少 local/（私有数据/运行产物）")
    if not s.layout["hasDocs"]:
        s.layout["violations"].append("缺少 docs/（架构与规范）")

    total_bytes = 0
    total = 0
    over_warn: list[FileInfo] = []
    over_fail: list[FileInfo] = []
    largest: list[FileInfo] = []
    for p in iter_files(root):
        rel = str(p.relative_to(root)).replace("\\", "/")
        try:
            size = p.stat().st_size
        except OSError:
            continue
        total += 1
        total_bytes += size
        lines = None
        if p.suffix.lower() in TEXT_EXT and size <= 2_000_000 and not rel.startswith(SIZE_EXEMPT_PREFIXES):
            text = read_text(p)
            lines = text.count("\n") + 1 if text else None
        fi = FileInfo(rel, size, lines)
        largest.append(fi)
        if lines is not None:
            if lines >= max_lines_fail:
                over_fail.append(fi)
            elif lines >= max_lines_warn:
                over_warn.append(fi)
    largest.sort(key=lambda x: x.bytes, reverse=True)
    s.files = {
        "total": total,
        "bytes": total_bytes,
        "largest": [asdict(x) for x in largest[:10]],
        "overWarn": [asdict(x) for x in sorted(over_warn, key=lambda x: x.lines or 0, reverse=True)[:20]],
        "overFail": [asdict(x) for x in sorted(over_fail, key=lambda x: x.lines or 0, reverse=True)[:20]],
    }

    junk_dirs = {name: 0 for name in ("__pycache__", ".pytest_cache", "coverage", "htmlcov", "node_modules", ".ruff_cache", ".mypy_cache")}
    junk_files = {"log": 0, "screenshot": 0, "tmp": 0}
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root).replace("\\", "/")
        top = "" if rel_dir == "." else rel_dir.split("/", 1)[0]
        if top in SKIP_SCAN_TOP:
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if d not in {".git"}]
        for d in list(dirnames):
            if d in junk_dirs:
                junk_dirs[d] += 1
        for name in filenames:
            low = name.lower()
            if low.endswith((".log", ".log.1")):
                junk_files["log"] += 1
            if low.endswith((".png", ".jpg", ".jpeg")) and any(k in low for k in ("screen", "shot", "capture")):
                junk_files["screenshot"] += 1
            if low.endswith((".tmp", ".bak", ".orig")) or low.startswith("~$"):
                junk_files["tmp"] += 1
    s.junk = {"dirs": junk_dirs, "files": junk_files}

    secrets: list[dict] = []
    dotenv: list[str] = []
    private_tracked = [x for x in tracked_list if is_private_rel(x)]
    for p in iter_files(root):
        rel = str(p.relative_to(root)).replace("\\", "/")
        if rel.startswith("docs/test-fixtures/runs/"):
            continue
        low = p.name.lower()
        if low == ".env" or low.startswith(".env."):
            dotenv.append(rel)
            continue
        if p.suffix.lower() not in TEXT_EXT or p.stat().st_size > 1_000_000:
            continue
        for lineno, kind in scan_text_secrets(read_text(p)):
            secrets.append({"path": rel, "line": lineno, "kind": kind})
    s.privacy = {
        "secrets": secrets[:50],
        "dotenv": dotenv[:50],
        "privateTracked": private_tracked[:50],
        "hardFail": bool(secrets or private_tracked),
    }

    gi = root / ".gitignore"
    gi_text = read_text(gi)
    missing = [x for x in IGNORE_EXPECT if x.rstrip("/") not in gi_text.replace("\\", "/")]
    s.ignore = {"exists": gi.exists(), "missing": missing}

    # 顶层 issues：机器可消费的统一问题清单（C9 实测：原先只有 stdout 有 FAIL/WARN）
    issues: list[dict] = []
    for v in s.layout["violations"]:
        issues.append({"level": "WARN", "code": "layout", "path": "", "detail": v})
    for f in over_fail:
        issues.append({"level": "FAIL", "code": "size", "path": f.path, "detail": f"{f.lines} 行 ≥ {max_lines_fail}"})
    for x in secrets:
        issues.append({"level": "FAIL", "code": "secret", "path": x["path"], "detail": f"第 {x['line']} 行疑似 {x['kind']}"})
    for x in private_tracked:
        issues.append({"level": "FAIL", "code": "private-path", "path": x, "detail": "私有/运行区文件被 git 跟踪"})
    for x in dotenv:
        issues.append({"level": "WARN", "code": "dotenv", "path": x, "detail": ".env 不应入库"})
    for x in missing:
        issues.append({"level": "WARN", "code": "ignore", "path": ".gitignore", "detail": f"缺 {x}"})
    s.issues = issues

    score = 100
    if s.privacy["secrets"]:
        score -= 40
    if s.privacy["privateTracked"]:
        score -= 30
    if s.layout["violations"]:
        score -= 10
    if over_fail:
        score -= 10
    if s.ignore["missing"]:
        score -= min(10, len(s.ignore["missing"]))
    s.score = {
        "total": max(0, score),
        "layout": max(0, 100 - 10 * len(s.layout["violations"])),
        "privacy": 0 if s.privacy["hardFail"] else 100,
        "size": max(0, 100 - 5 * len(over_fail)),
        "ignore": max(0, 100 - 10 * len(missing)),
    }
    return s


def print_scan(s: Scan) -> None:
    print(f"=== proj scan: {s.root}")
    print(f"  git: {s.git.get('branch')} | tracked={s.git.get('tracked')} | modified={s.git.get('modified')} | untracked={s.git.get('untracked')}")
    print(f"  layout: repo={s.layout['hasRepo']} local={s.layout['hasLocal']} docs={s.layout['hasDocs']} .dsh={s.layout['hasDsh']}")
    for v in s.layout["violations"]:
        print(f"    [WARN] {v}")
    print(f"  files: {s.files['total']} 个 | {s.files['bytes']/1024/1024:.1f} MB | >400 行={len(s.files['overWarn'])} | >800 行={len(s.files['overFail'])}")
    for f in s.files["overFail"][:5]:
        print(f"    [FAIL] {f['path']}  {f['lines']} 行")
    for f in s.files["overWarn"][:5]:
        print(f"    [WARN] {f['path']}  {f['lines']} 行")
    if s.privacy["secrets"]:
        for x in s.privacy["secrets"][:10]:
            print(f"    [FAIL] 疑似密钥 {x['path']}:{x['line']} ({x['kind']})")
    if s.privacy["privateTracked"]:
        for x in s.privacy["privateTracked"][:10]:
            print(f"    [FAIL] 私有路径被跟踪 {x}")
    if s.privacy["dotenv"]:
        print(f"    [WARN] .env 文件 {len(s.privacy['dotenv'])} 个（确认不入库）")
    if s.ignore["missing"]:
        print(f"    [WARN] .gitignore 缺: {', '.join(s.ignore['missing'])}")
    hot = {k: v for k, v in s.junk["dirs"].items() if v}
    if hot or any(s.junk["files"].values()):
        print(f"  junk: {hot} files={s.junk['files']}")
    print(f"  score: {s.score['total']}/100 | privacy={s.score['privacy']} size={s.score['size']} ignore={s.score['ignore']}")
