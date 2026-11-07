"""proj wiki —— 项目文档索引与检索（docs/wiki/index.json + INDEX.md）。"""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .common import SKIP_DIRS, SKIP_SCAN_TOP, read_text

TITLE_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
HEADING_RE = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)
TAGS_RE = re.compile(r"^tags:\s*\[(.*?)\]", re.MULTILINE)
DEFAULT_INDEX = "docs/wiki"


@dataclass
class Entry:
    path: str
    title: str
    headings: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    bytes: int = 0
    mtime: str = ""
    archive: bool = False


def _doc_files(root: Path) -> list[Path]:
    """默认范围：根目录 *.md + docs/ + repo/docs/（存在时）。"""
    found: list[Path] = list(root.glob("*.md"))
    bases = [root / "docs"]
    if (root / "repo" / "docs").is_dir():
        bases.append(root / "repo" / "docs")
    for base in bases:
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            rel_top = os.path.relpath(dirpath, root).replace("\\", "/").split("/", 1)[0]
            if rel_top in SKIP_SCAN_TOP:
                dirnames[:] = []
                continue
            for name in filenames:
                if name.lower().endswith(".md"):
                    found.append(Path(dirpath) / name)
    out: list[Path] = []
    seen: set[str] = set()
    for p in found:
        rel = str(p.relative_to(root)).replace("\\", "/")
        if rel in seen:
            continue
        # 索引自身不进索引（否则 generated_at/bytes 自引用 ⇒ 每次跑都变，破坏幂等）
        if rel.startswith(("docs/wiki/", "repo/docs/wiki/")):
            continue
        seen.add(rel)
        out.append(p)
    return sorted(out, key=lambda p: str(p).lower())


def _entry(root: Path, path: Path) -> Entry:
    rel = str(path.relative_to(root)).replace("\\", "/")
    text = read_text(path, limit=400_000)
    m = TITLE_RE.search(text)
    title = m.group(1).strip() if m else path.stem
    headings = [h[1].strip() for h in HEADING_RE.findall(text)][:20]
    tm = TAGS_RE.search(text)
    tags = [t.strip() for t in tm.group(1).split(",") if t.strip()] if tm else []
    if not tags:
        parts = rel.split("/")
        tags = [parts[1]] if len(parts) > 2 and parts[0] == "docs" else []
    try:
        st = path.stat()
        size, mtime = st.st_size, datetime.fromtimestamp(st.st_mtime, UTC).isoformat()
    except OSError:
        size, mtime = len(text.encode("utf-8")), ""
    return Entry(rel, title, headings, tags, size, mtime, archive="/archive/" in f"/{rel}")


def build_index(root: Path, out_dir: str = DEFAULT_INDEX) -> dict:
    root = root.resolve()
    entries = [_entry(root, p) for p in _doc_files(root)]
    # 输出确定性：不含 generated_at（内容只随被索引文件变化）⇒ 可重复跑、可进 G0 新鲜度比较
    data = {
        "schema": 1,
        "root": str(root),
        "count": len(entries),
        "entries": [asdict(e) for e in entries],
    }
    out = root / out_dir
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "INDEX.md").write_text(_render_markdown(data), encoding="utf-8")
    return data


def _render_markdown(data: dict) -> str:
    lines = ["# Wiki 索引", "", f"> 由 `proj wiki index` 生成（内容确定性，可重复跑）｜条目：{data['count']}", ""]
    by_dir: dict[str, list[dict]] = {}
    for e in data["entries"]:
        top = e["path"].split("/")[0] if "/" in e["path"] else "."
        by_dir.setdefault(top, []).append(e)
    for top in sorted(by_dir):
        lines.append(f"## {top}")
        for e in sorted(by_dir[top], key=lambda x: x["path"]):
            mark = "（archive）" if e["archive"] else ""
            lines.append(f"- [{e['title']}]({e['path']}){mark} — {e['bytes']} B")
        lines.append("")
    return "\n".join(lines)


def load_index(root: Path, out_dir: str = DEFAULT_INDEX) -> dict:
    path = root / out_dir / "index.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def search(root: Path, query: str, limit: int = 10, out_dir: str = DEFAULT_INDEX) -> list[dict]:
    data = load_index(root, out_dir)
    if not data:
        return []
    tokens = [t for t in re.split(r"\s+", query.strip().lower()) if t]
    if not tokens:
        return []
    hits: list[tuple[int, dict, list[str]]] = []
    for e in data.get("entries", []):
        title = e.get("title", "").lower()
        path = e.get("path", "").lower()
        heads = [h.lower() for h in e.get("headings", [])]
        score = 0
        matched: list[str] = []
        for t in tokens:
            if t in title:
                score += 3
            for h in heads:
                if t in h:
                    score += 2
                    if h not in matched:
                        matched.append(h)
            if t in path:
                score += 1
        if score:
            hits.append((score, e, matched[:3]))
    hits.sort(key=lambda x: (-x[0], x[1]["path"]))
    return [{"score": s, "path": e["path"], "title": e["title"], "matched": m, "mtime": e.get("mtime", "")} for s, e, m in hits[:limit]]


def print_search(results: list[dict], query: str) -> None:
    print(f"=== proj wiki search: {query!r}（{len(results)} 命中）")
    for r in results:
        print(f"  [{r['score']:>2}] {r['title']} — {r['path']}")
        if r["matched"]:
            print(f"        命中标题：{' / '.join(r['matched'])}")


def print_index(data: dict) -> None:
    print(f"=== proj wiki index: {data.get('root')}（{data.get('count', 0)} 条）")
    for e in data.get("entries", [])[:20]:
        print(f"  {e['title']} — {e['path']}")
    if data.get("count", 0) > 20:
        print(f"  ... 其余 {data['count'] - 20} 条见 docs/wiki/INDEX.md")
