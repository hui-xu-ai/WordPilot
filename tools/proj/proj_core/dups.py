"""proj dup / impact —— 重复代码块与影响面（grep 级，不建 AST）。"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from .common import TEXT_EXT, iter_files, read_text

COMMENT_PREFIXES = ("#", "//", "/*", "*", "<!--", '"""', "'''")
SYMBOL_RE = re.compile(r"^\s*(?:async\s+)?(?:def|class|function|const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE)
# 常见短名不构成"重复实现"信号（main/Ok/Bad 之类跨文件同名很常见）
COMMON_SYMBOLS = {
    "main", "parse", "setup", "run", "test", "init", "load", "save", "render",
    "get", "set", "handler", "process", "start", "stop", "index", "read",
    "write", "open", "close", "update", "create", "delete", "list", "add",
    "remove", "Ok", "Bad", "Error", "Result", "Config", "State", "Args", "Item",
    # 验收实测（round4 C4）：这些通用名仍被报成"重复实现"，一并排除
    "rows", "here", "text", "body", "lines", "names", "root", "repo", "build",
    "results", "head", "spec", "seed", "walk", "warn", "invoke", "workspace",
    "path", "file", "files", "name", "value", "values", "data", "args", "kwargs",
    "output", "input", "check", "validate", "handle", "helper", "utils", "log",
    "logger", "cache", "client", "server", "request", "response", "message",
}
_COMMON_LOWER = {w.lower() for w in COMMON_SYMBOLS}


@dataclass
class Cluster:
    digest: str
    lines: int
    locations: list[dict] = field(default_factory=list)


def _significant(line: str) -> str | None:
    s = line.strip()
    if not s or s.startswith(COMMENT_PREFIXES):
        return None
    return s


def _files(root: Path, limit: int = 200_000):
    for p in iter_files(root):
        if p.suffix.lower() in TEXT_EXT:
            text = read_text(p)
            if 0 < len(text) <= limit:
                yield p, text


def find_duplicates(root: Path, min_lines: int = 6, min_chars: int = 40, max_clusters: int = 50) -> list[Cluster]:
    root = root.resolve()
    windows: dict[str, list[tuple[str, int]]] = {}
    for p, text in _files(root):
        rel = str(p.relative_to(root)).replace("\\", "/")
        sig = [(i + 1, _significant(line)) for i, line in enumerate(text.splitlines())]
        for start in range(max(0, len(sig) - min_lines + 1)):
            chunk = sig[start:start + min_lines]
            values = [v for _, v in chunk]
            if any(v is None for v in values):
                continue
            body = "\n".join(v for v in values if v)
            if len(body) < min_chars:
                continue
            digest = hashlib.sha1(body.encode("utf-8")).hexdigest()
            windows.setdefault(digest, []).append((rel, chunk[0][0]))
    clusters: list[Cluster] = []
    for digest, locs in windows.items():
        uniq: list[tuple[str, int]] = []
        for rel, line in locs:
            if uniq and uniq[-1][0] == rel and line - uniq[-1][1] < min_lines:
                continue
            uniq.append((rel, line))
        if len(uniq) >= 2:
            clusters.append(Cluster(digest[:10], min_lines, [{"path": r, "line": n} for r, n in uniq]))
    clusters.sort(key=lambda c: -len(c.locations))
    return clusters[:max_clusters]


def find_duplicate_symbols(root: Path) -> dict[str, list[str]]:
    root = root.resolve()
    seen: dict[str, list[str]] = {}
    for p, text in _files(root):
        rel = str(p.relative_to(root)).replace("\\", "/")
        for name in SYMBOL_RE.findall(text):
            # 只对"看起来像业务标识"的名字报警：≥5 字符 + 非通用短名（大小写不敏感）
            if len(name) < 5 or name.lower() in _COMMON_LOWER:
                continue
            seen.setdefault(name, [])
            if rel not in seen[name]:
                seen[name].append(rel)
    return {name: files for name, files in seen.items() if len(files) >= 2}


def impact(root: Path, target: str) -> dict:
    root = root.resolve()
    target_path = (root / target).resolve()
    kind = "path" if target_path.exists() else "symbol"
    patterns: list[re.Pattern] = []
    if kind == "path":
        rel = str(target_path.relative_to(root)).replace("\\", "/")
        stem = target_path.stem
        dotted = rel[:-3].replace("/", ".") if rel.endswith(".py") else rel.replace("/", ".")
        for name in {stem, dotted}:
            patterns.append(re.compile(r"\b" + re.escape(name) + r"\b"))
        patterns.append(re.compile(re.escape(rel)))
    else:
        patterns.append(re.compile(r"\b" + re.escape(target) + r"\b"))
    hits: list[dict] = []
    for p, text in _files(root):
        rel = str(p.relative_to(root)).replace("\\", "/")
        if kind == "path" and rel == str(target_path.relative_to(root)).replace("\\", "/"):
            continue
        count = 0
        samples: list[str] = []
        for lineno, line in enumerate(text.splitlines(), 1):
            if any(pat.search(line) for pat in patterns):
                count += 1
                if len(samples) < 3:
                    samples.append(f"{lineno}: {line.strip()[:120]}")
        if count:
            hits.append({"path": rel, "count": count, "samples": samples})
    hits.sort(key=lambda h: -h["count"])
    return {"target": target, "kind": kind, "total": sum(h["count"] for h in hits), "hits": hits[:50]}


def print_duplicates(clusters: list[Cluster]) -> None:
    print(f"=== proj dup（{len(clusters)} 组重复块）")
    for c in clusters[:20]:
        print(f"  [{c.digest}] {c.lines} 行 × {len(c.locations)} 处")
        for loc in c.locations[:4]:
            print(f"      {loc['path']}:{loc['line']}")


def print_symbols(symbols: dict[str, list[str]]) -> None:
    print(f"=== proj dup --symbols（{len(symbols)} 个同名符号）")
    for name, files in list(symbols.items())[:20]:
        print(f"  {name}: {', '.join(files[:4])}")


def print_impact(data: dict) -> None:
    print(f"=== proj impact: {data['target']}（{data['kind']}，{data['total']} 处）")
    for h in data["hits"][:20]:
        print(f"  {h['count']:>3}  {h['path']}")
        for s in h["samples"][:2]:
            print(f"        {s}")
