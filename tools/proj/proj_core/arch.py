"""proj arch —— 模块级架构监管（声明模型 + 实际依赖 + 违规）。

设计参考：Codex project-architecture 的 model.toml / 违规检查思路（只借原则，不复制其工具链）。
模型文件：`.dsh/architecture.toml`
  [[modules]] id / paths / public / allowed / parent / status / description
实际依赖：由 proj_core.graph 的文件级 import 图折叠到模块。
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .graph import build_graph

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore[assignment]

MODEL_REL = ".dsh/architecture.toml"
DOC_LIMIT = 160
_EXEMPT_DOC = ("__init__.py",)


def file_lines(path: Path) -> int:
    try:
        return path.read_text(encoding="utf-8", errors="replace").count("\n") + 1
    except OSError:
        return 0


def file_doc(path: Path) -> str:
    """取文件头 docstring 第一段（或 `# 职责:` 注释），截断到 DOC_LIMIT。"""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")[:3000]
    except OSError:
        return ""
    m = re.search(r'("""|\'\'\')(.*?)\1', text, re.DOTALL)
    body = m.group(2) if m else ""
    if not body:
        for line in text.splitlines()[:20]:
            if line.lstrip().startswith("# 职责"):
                body = line.split("：", 1)[-1].split(":", 1)[-1]
                break
    body = body.strip().split("\n\n")[0].strip()
    body = re.sub(r"\s+", " ", body)
    return body[:DOC_LIMIT]


@dataclass
class Module:
    id: str
    paths: list[str]
    allowed: list[str] = field(default_factory=list)
    public: list[str] = field(default_factory=list)
    parent: str | None = None
    entrypoints: list[str] = field(default_factory=list)
    status: str = "implemented"
    description: str = ""


@dataclass
class ArchReport:
    root: str
    model_path: str
    declared: bool
    modules: list[dict] = field(default_factory=list)
    edges: list[dict] = field(default_factory=list)
    violations: list[dict] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    files: list[dict] = field(default_factory=list)
    file_edges: list[list[str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _norm(p: str) -> str:
    return p.replace("\\", "/").strip("/")


def load_model(root: Path) -> tuple[list[Module], list[str]]:
    path = root / MODEL_REL
    if not path.is_file():
        return [], [f"未找到模型 {MODEL_REL}（跑 proj arch init 生成）"]
    if tomllib is None:
        return [], ["当前 Python 无 tomllib（需 3.11+）"]
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [], [f"模型解析失败：{exc}"]
    mods: list[Module] = []
    seen: set[str] = set()
    for item in raw.get("modules", []):
        mid = str(item.get("id", "")).strip()
        if not mid or mid in seen:
            return [], [f"模块 id 无效或重复：{mid}"]
        seen.add(mid)
        mods.append(Module(
            id=mid,
            paths=[_norm(p) for p in item.get("paths", [])],
            allowed=[str(a) for a in item.get("allowed", [])],
            public=[_norm(p) for p in item.get("public", [])],
            parent=item.get("parent"),
            entrypoints=[_norm(p) for p in item.get("entrypoints", [])],
            status=str(item.get("status", "implemented")),
            description=str(item.get("description", "")),
        ))
    if not mods:
        return [], ["模型至少声明一个模块"]
    ids = {m.id for m in mods}
    for m in mods:
        if not m.paths:
            return [], [f"模块 {m.id} 未声明 paths"]
        for a in m.allowed:
            if a not in ids or a == m.id:
                return [], [f"模块 {m.id} 的 allowed 引用无效：{a}"]
    return mods, []


def derive_modules(root: Path) -> list[Module]:
    """无模型时的候选：src/* 一级目录（不含 assets/config/__pycache__）。"""
    src = root / "src"
    if not src.is_dir():
        return []
    skip = {"assets", "config", "__pycache__", "plugins_user"}
    out: list[Module] = []
    for d in sorted(p for p in src.iterdir() if p.is_dir()):
        if d.name in skip or d.name.startswith("."):
            continue
        out.append(Module(id=d.name, paths=[f"src/{d.name}"], description="（自动推导，待人工确认）"))
    return out


def _module_of(rel: str, mods: list[Module]) -> str | None:
    best: tuple[int, str] | None = None
    for m in mods:
        for p in m.paths:
            if rel == p or rel.startswith(p.rstrip("/") + "/"):
                score = len(p)
                if best is None or score > best[0]:
                    best = (score, m.id)
    return best[1] if best else None


def build_arch(root: Path) -> ArchReport:
    root = root.resolve()
    mods, problems = load_model(root)
    declared = bool(mods)
    if not mods:
        mods = derive_modules(root)
    report = ArchReport(root=str(root), model_path=MODEL_REL, declared=declared, problems=problems)
    g = build_graph(root)
    counts: dict[str, int] = {m.id: 0 for m in mods}
    files: dict[str, list[str]] = {m.id: [] for m in mods}
    for node in g.nodes:
        mid = _module_of(_norm(node), mods)
        if mid:
            counts[mid] += 1
            files[mid].append(node)
    indeg: dict[str, int] = {n: 0 for n in g.nodes}
    outdeg: dict[str, int] = {n: 0 for n in g.nodes}
    for a, b in g.edges:
        outdeg[a] = outdeg.get(a, 0) + 1
        indeg[b] = indeg.get(b, 0) + 1
    report.files = [{"path": n, "module": _module_of(_norm(n), mods) or "",
                     "indeg": indeg.get(n, 0), "outdeg": outdeg.get(n, 0),
                     "desc": file_doc(root / n),
                     "lines": file_lines(root / n)} for n in g.nodes]
    report.file_edges = [[a, b] for a, b in g.edges]
    agg: dict[tuple[str, str], int] = {}
    for a, b in g.edges:
        ma, mb = _module_of(_norm(a), mods), _module_of(_norm(b), mods)
        if not ma or not mb or ma == mb:
            continue
        agg[(ma, mb)] = agg.get((ma, mb), 0) + 1
    allowed = {m.id: set(m.allowed) | ({m.parent} if m.parent else set()) for m in mods}
    public = {m.id: m.public for m in mods}
    report.modules = [{"id": m.id, "paths": m.paths, "files": counts.get(m.id, 0),
                       "file_list": sorted(files.get(m.id, []))[:200],
                       "allowed": sorted(allowed[m.id]), "public": public[m.id],
                       "entrypoints": m.entrypoints,
                       "status": m.status, "description": m.description} for m in mods]
    for (a, b), n in sorted(agg.items()):
        report.edges.append({"from": a, "to": b, "count": n})
        if declared and b not in allowed[a]:
            report.violations.append({"kind": "dependency", "from": a, "to": b, "count": n,
                                      "reason": f"{a} 未声明允许依赖 {b}"})
    # 公开入口：模块声明了 public 时，跨模块 import 的目标文件必须落在 public 内
    if declared:
        for a, b in g.edges:
            ma, mb = _module_of(_norm(a), mods), _module_of(_norm(b), mods)
            if not ma or not mb or ma == mb or not public.get(mb):
                continue
            target = _norm(b)
            if not any(target == p or target.startswith(p.rstrip("/") + "/") for p in public[mb]):
                report.violations.append({"kind": "public", "from": ma, "to": mb, "count": 1,
                                          "file": target,
                                          "reason": f"{target} 不在 {mb} 的公开入口内"})
    report.warnings = [f"缺职责头：{f['path']}" for f in report.files
                       if f["module"] and not f["desc"]
                       and not f["path"].endswith(_EXEMPT_DOC)
                       and "/tests/" not in f["path"]]
    return report


def render_model(mods: list[Module], title: str) -> str:
    lines = ["# 由 proj arch init 生成；请人工收紧 allowed（只留真正允许的依赖）",
             "# public 为空 = 不限制跨模块 import；填了则只允许 import 这些路径", "",
             "[project]", f'title = "{title}"', 'python_roots = ["src"]', ""]
    for m in mods:
        lines.append("[[modules]]")
        lines.append(f'id = "{m.id}"')
        lines.append("paths = [" + ", ".join(f'"{p}"' for p in m.paths) + "]")
        lines.append("allowed = [" + ", ".join(f'"{a}"' for a in m.allowed) + "]")
        lines.append("public = [" + ", ".join(f'"{p}"' for p in m.public) + "]")
        lines.append("entrypoints = [" + ", ".join(f'"{p}"' for p in m.entrypoints) + "]")
        lines.append(f'status = "{m.status}"')
        lines.append("")
    return "\n".join(lines)


def init_model(root: Path, force: bool = False) -> dict:
    root = root.resolve()
    path = root / MODEL_REL
    if path.exists() and not force:
        return {"ok": False, "error": f"{MODEL_REL} 已存在（--force 覆盖）"}
    mods = derive_modules(root)
    if not mods:
        return {"ok": False, "error": "未找到 src/* 模块目录"}
    # 初始 allowed = 当前实际依赖（保证先绿）；随后由人收紧
    g = build_graph(root)
    ids = {m.id for m in mods}
    for m in mods:
        deps = set()
        for a, b in g.edges:
            ma, mb = _module_of(_norm(a), mods), _module_of(_norm(b), mods)
            if ma == m.id and mb and mb != m.id:
                deps.add(mb)
        m.allowed = sorted(d for d in deps if d in ids)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_model(mods, root.name), encoding="utf-8")
    return {"ok": True, "path": str(path), "modules": len(mods)}


def print_arch(report: ArchReport) -> None:
    print(f"=== proj arch: {report.root}（模型={'已声明' if report.declared else '未声明/推导'}）")
    for m in report.modules:
        print(f"  [{m['status']}] {m['id']:<12} files={m['files']:<3} allowed={','.join(m['allowed']) or '-'}")
    for e in report.edges:
        print(f"  {e['from']} -> {e['to']}  x{e['count']}")
    if report.violations:
        print(f"  [FAIL] 违规 {len(report.violations)} 条：")
        for v in report.violations:
            if v.get("kind") == "public":
                print(f"    [public] {v['from']} -> {v.get('file', v['to'])}（{v['reason']}）")
            else:
                print(f"    [dep]    {v['from']} -> {v['to']}  x{v['count']}")
    if report.problems:
        for p in report.problems:
            print(f"  [WARN] {p}")


def print_init(data: dict) -> None:
    if data.get("ok"):
        print(f"=== proj arch init：已生成 {data['path']}（{data['modules']} 个模块）")
        print("  下一步：人工收紧 allowed，再跑 proj arch check")
    else:
        print(f"=== proj arch init 失败：{data.get('error')}")


def write_json(path: str, data: dict) -> None:
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def arch_context(root: Path) -> dict:
    """有界摘要（给 AI 读）：模块/职责/入口/允许依赖/实际边/违规/缺职责。"""
    report = build_arch(root)
    lines = ["# 架构摘要（proj arch context）", ""]
    lines.append("## 模块")
    for m in report.modules:
        lines.append(f"- **{m['id']}**（{m['files']} 文件）{m['description'] or '（未写职责）'}")
        entry = m["public"] or m.get("entrypoints") or []
        lines.append(f"  入口：{', '.join(entry) or '未声明'}；允许依赖：{', '.join(m['allowed']) or '无'}")
    lines.append("")
    lines.append("## 实际跨模块依赖")
    for e in report.edges:
        lines.append(f"- {e['from']} -> {e['to']} ×{e['count']}")
    if report.violations:
        lines.append("")
        lines.append(f"## 违规 {len(report.violations)} 条")
        for v in report.violations[:20]:
            lines.append(f"- [{v.get('kind', 'dependency')}] {v['from']} -> {v.get('file', v['to'])}")
    if report.warnings:
        lines.append("")
        lines.append(f"## 待补职责头 {len(report.warnings)} 个")
        for w in report.warnings[:20]:
            lines.append(f"- {w}")
    text = "\n".join(lines)
    return {"text": text, "lines": len(lines), "tokens_est": len(text) // 4}


def render_code_map(report, title: str) -> str:
    out = [f"# {title} · 代码地图（CODE_MAP）", "",
           "> 由 `proj arch map` 从**文件头 docstring**生成；改职责请改文件头，不要改本文件。", ""]
    for m in report.modules:
        out.append(f"## {m['id']}（{m['files']} 文件）")
        out.append(m["description"] or "（未写模块职责）")
        out.append("")
        out.append("| 文件 | 职责 |")
        out.append("|---|---|")
        for f in report.files:
            if f["module"] != m["id"]:
                continue
            out.append(f"| `{f['path']}` | {f['desc'] or '（未写）'} |")
        out.append("")
    return "\n".join(out)
