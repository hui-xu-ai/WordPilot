"""proj push-check —— 提交/推送前的隐私、体积、垃圾与提交信息检查。"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .common import (
    is_junk_path,
    is_private_rel,
    read_text,
    run_git,
    scan_text_secrets,
)

CONVENTIONAL = re.compile(r"^(feat|fix|docs|chore|test|refactor|perf|build|ci|revert)(\([^)]+\))?!?: .+")
LARGE_FAIL = 5 * 1024 * 1024
LARGE_WARN = 1 * 1024 * 1024


@dataclass
class Issue:
    level: str
    code: str
    path: str = ""
    detail: str = ""


@dataclass
class PushCheck:
    root: str
    mode: str
    files: list[dict] = field(default_factory=list)
    issues: list[dict] = field(default_factory=list)
    remote: str | None = None
    unpushed: int | None = None
    message: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def failed(self) -> bool:
        return any(i["level"] == "FAIL" for i in self.issues)


def _changes(root: Path, mode: str, rev_range: str | None) -> list[tuple[str, str]]:
    if mode == "staged":
        out = run_git(root, "diff", "--cached", "--name-status", "--diff-filter=ACMRD")
    elif mode == "last":
        out = run_git(root, "diff-tree", "--no-commit-id", "--name-status", "-r", "HEAD")
    elif mode == "push":
        upstream = (run_git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}") or "").strip()
        if upstream:
            out = run_git(root, "diff", "--name-status", f"{upstream}..HEAD")
        else:
            # 新分支/无上游：列出当前历史里所有变更文件（--root 含首个提交）
            out = run_git(root, "log", "--pretty=format:", "--name-status", "--root", "HEAD")
    else:
        out = run_git(root, "diff", "--name-status", rev_range or "HEAD~1..HEAD")
    rows: list[tuple[str, str]] = []
    for line in (out or "").splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            rows.append((parts[0].strip(), parts[-1].strip()))
    return rows


def _blob(root: Path, path: str, mode: str, rev_range: str | None) -> str:
    if mode == "staged":
        spec = f":{path}"
    elif mode in ("last", "push"):
        spec = f"HEAD:{path}"
    else:
        target = (rev_range or "HEAD").split("..")[-1] or "HEAD"
        spec = f"{target}:{path}"
    return run_git(root, "show", spec) or ""


def _blob_size(root: Path, path: str, mode: str, rev_range: str | None) -> int:
    if mode == "staged":
        spec = f":{path}"
    elif mode in ("last", "push"):
        spec = f"HEAD:{path}"
    else:
        target = (rev_range or "HEAD").split("..")[-1] or "HEAD"
        spec = f"{target}:{path}"
    out = run_git(root, "cat-file", "-s", spec)
    try:
        return int((out or "0").strip() or 0)
    except ValueError:
        return 0


def push_check(root: Path, mode: str = "staged", rev_range: str | None = None, message: str = "") -> PushCheck:
    root = root.resolve()
    pc = PushCheck(root=str(root), mode=mode)
    changes = _changes(root, mode, rev_range)
    pc.files = [{"status": st, "path": p} for st, p in changes]

    upstream = run_git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    pc.remote = (upstream or "").strip() or None
    if pc.remote:
        count = run_git(root, "rev-list", "--count", f"{pc.remote}..HEAD")
        try:
            pc.unpushed = int((count or "0").strip() or 0)
        except ValueError:
            pc.unpushed = None

    for status, path in changes:
        rel = path.replace("\\", "/")
        if is_private_rel(rel):
            pc.issues.append(asdict(Issue("FAIL", "private-path", rel, "私有/运行区文件不应进代码仓")))
        if is_junk_path(rel):
            pc.issues.append(asdict(Issue("FAIL", "junk-path", rel, "生成物/垃圾路径不应提交")))
        low = Path(rel).name.lower()
        if low == ".env" or low.startswith(".env."):
            pc.issues.append(asdict(Issue("FAIL", "dotenv", rel, ".env 不应提交")))
        if status == "D":
            continue
        size = _blob_size(root, rel, mode, rev_range)
        if size >= LARGE_FAIL:
            pc.issues.append(asdict(Issue("FAIL", "large-file", rel, f"{size/1024/1024:.1f} MB ≥ 5 MB")))
        elif size >= LARGE_WARN:
            pc.issues.append(asdict(Issue("WARN", "large-file", rel, f"{size/1024/1024:.1f} MB ≥ 1 MB")))
        if Path(rel).suffix.lower() in (".py", ".js", ".ts", ".md", ".json", ".yml", ".yaml", ".ps1", ".mjs", ".txt", ".sh"):
            text = _blob(root, rel, mode, rev_range)
            if len(text) <= 1_000_000:
                for lineno, kind in scan_text_secrets(text):
                    pc.issues.append(asdict(Issue("FAIL", "secret", rel, f"第 {lineno} 行疑似 {kind}")))

    msg = message or _read_commit_message(root)
    pc.message = msg
    if msg:
        subject = msg.strip().splitlines()[0]
        if not CONVENTIONAL.match(subject):
            pc.issues.append(asdict(Issue("WARN", "commit-message", "", f"提交信息不符合 Conventional Commits：{subject[:80]}")))
        if len(subject) < 10:
            pc.issues.append(asdict(Issue("WARN", "commit-message", "", "提交信息过短（<10 字符）")))
    else:
        pc.issues.append(asdict(Issue("WARN", "commit-message", "", "没有提交信息（staged 模式下读 .git/COMMIT_EDITMSG）")))
    return pc


def _read_commit_message(root: Path) -> str:
    git_dir = run_git(root, "rev-parse", "--git-dir")
    if not git_dir:
        return ""
    path = Path((git_dir or ".git").strip())
    if not path.is_absolute():
        path = root / path
    return read_text(path / "COMMIT_EDITMSG").strip()


def print_push_check(pc: PushCheck) -> None:
    print(f"=== proj push-check ({pc.mode}) : {pc.root}")
    print(f"  文件：{len(pc.files)} 个（" + ", ".join(sorted({f['status'] for f in pc.files})) + "）")
    print(f"  remote：{pc.remote or '(无)'} | 未推送提交：{pc.unpushed if pc.unpushed is not None else '-'}")
    if pc.message:
        print(f"  提交信息：{pc.message.strip().splitlines()[0][:100]}")
    for i in pc.issues:
        where = f" {i['path']}" if i["path"] else ""
        print(f"    [{i['level']}] {i['code']}{where} — {i['detail']}")
    fails = sum(1 for i in pc.issues if i["level"] == "FAIL")
    warns = sum(1 for i in pc.issues if i["level"] == "WARN")
    print(f"  结论：{'FAIL' if fails else 'PASS'}（FAIL={fails} WARN={warns}）")


# 兼容：调用方可用 `python -m` 或直接 import
__all__ = ["Issue", "PushCheck", "print_push_check", "push_check"]
