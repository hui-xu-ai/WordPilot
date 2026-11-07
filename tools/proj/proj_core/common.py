"""proj 公共常量与只读工具（stdlib only）。"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache",
    ".ruff_cache", ".mypy_cache", ".pnpm-store", "dist", "build", ".idea",
    ".vscode", ".next", ".nuxt", "coverage", "htmlcov", ".tox",
}
# 私有/运行区：内容不参与"源码扫描"，但仍单独检查"是否被 git 跟踪"。
SKIP_SCAN_TOP = {"work", "local", ".dsh-memory", "outputs"}
PRIVATE_TOP = {"local", ".dsh-memory", "work", "outputs"}
TEXT_EXT = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".mjs", ".cjs", ".go", ".rs", ".java",
    ".kt", ".cs", ".cpp", ".c", ".h", ".hpp", ".rb", ".php", ".swift", ".md",
    ".yml", ".yaml", ".toml", ".json", ".ini", ".cfg", ".conf", ".txt", ".sh",
    ".ps1", ".sql", ".html", ".css", ".scss", ".vue", ".svelte",
}
SECRET_PATTERNS = [
    ("private-key", re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----")),
    ("aws-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("openai-key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
]
# generic-assignment 单独处理：先抓赋值，再做"像不像密钥"的判定，
# 否则 `token = "color.state.error"` 这类主题令牌会被误报（SmartProof 实测）。
GENERIC_ASSIGN_RE = re.compile(
    r"(?i)\b(?:api[_-]?key|secret|token|password)\b\s*[:=]\s*['\"]([^'\"]{16,})['\"]"
)


def looks_like_secret(value: str) -> bool:
    """≥16 字符且至少三类字符（大写/小写/数字/符号）才算疑似密钥。"""
    classes = sum((
        any(c.islower() for c in value),
        any(c.isupper() for c in value),
        any(c.isdigit() for c in value),
        any(not c.isalnum() for c in value),
    ))
    return len(value) >= 16 and classes >= 3
IGNORE_EXPECT = [
    "local/", ".dsh-memory/", "work/", "__pycache__/", ".venv/", "venv/",
    ".pytest_cache/", "node_modules/", "*.log", ".env",
]
JUNK_DIR_NAMES = (
    "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache",
    "node_modules", "coverage", "htmlcov", "dist", "build",
)
JUNK_FILE_SUFFIXES = (".log", ".log.1", ".tmp", ".bak", ".orig", ".pyc", ".pyo")
# 生成物/索引不参与"源码行数"门禁（它们是派生物，改源码后重生成即可）。
SIZE_EXEMPT_PREFIXES = ("docs/wiki/index.json", "docs/wiki/INDEX.md", "docs/archive/")
ALLOWLIST_MARKERS = ("allowlist secret", "gate: pattern-source")


def run_git(root: Path, *args: str) -> str | None:
    """运行 git 子命令；失败返回 None（不抛）。"""
    try:
        p = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=20, check=False, stdin=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        return p.stdout if p.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def iter_files(root: Path, skip_private: bool = True):
    """遍历文件；默认跳过私有/运行区的内容。"""
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root).replace("\\", "/")
        top = "" if rel_dir == "." else rel_dir.split("/", 1)[0]
        if skip_private and top in SKIP_SCAN_TOP:
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            yield Path(dirpath) / name


def is_private_rel(rel: str) -> bool:
    """只按顶层目录判断私有路径（`docs/.../runs` 这类不算）。"""
    parts = rel.replace("\\", "/").split("/")
    return bool(parts) and parts[0] in PRIVATE_TOP


def is_junk_path(rel: str) -> bool:
    parts = rel.replace("\\", "/").split("/")
    if any(p in JUNK_DIR_NAMES for p in parts[:-1]):
        return True
    low = parts[-1].lower()
    if low.startswith("~$") or low.endswith(JUNK_FILE_SUFFIXES):
        return True
    return low.endswith((".png", ".jpg", ".jpeg")) and any(k in low for k in ("screen", "shot", "capture"))


def read_text(path: Path, limit: int | None = None) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    return text[:limit] if limit else text


def line_count(path: Path) -> int | None:
    text = read_text(path)
    return text.count("\n") + 1 if text else None


def scan_text_secrets(text: str) -> list[tuple[int, str]]:
    """返回 [(行号, 类型)]；带 allowlist 标记的行跳过。"""
    hits: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if any(m in line for m in ALLOWLIST_MARKERS):
            continue
        for kind, pat in SECRET_PATTERNS:
            if pat.search(line):
                hits.append((lineno, kind))
                break
        else:
            m = GENERIC_ASSIGN_RE.search(line)
            if m and looks_like_secret(m.group(1)):
                hits.append((lineno, "generic-assignment"))
    return hits


def token_estimate(text: str) -> int:
    return len(text) // 4
