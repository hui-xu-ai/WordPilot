"""proj graph —— 模块依赖图与循环检测（Python ast + JS/TS 正则）。"""
from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .common import iter_files, read_text

PY_EXT = ".py"
JS_EXT = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}
JS_IMPORT_RE = re.compile(r"""(?:import\s[^'"]*from\s*|require\(\s*|import\(\s*)['"]([^'"]+)['"]""")
JS_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")


@dataclass
class Graph:
    nodes: list[str] = field(default_factory=list)
    edges: list[tuple[str, str]] = field(default_factory=list)
    external: dict[str, list[str]] = field(default_factory=dict)
    unresolved: dict[str, list[str]] = field(default_factory=dict)
    cycles: list[list[str]] = field(default_factory=list)

    def to_dict(self) -> dict:
        indeg: dict[str, int] = {n: 0 for n in self.nodes}
        for _, dst in self.edges:
            indeg[dst] = indeg.get(dst, 0) + 1
        top = sorted(indeg.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
        return {
            "nodes": len(self.nodes),
            "edges": len(self.edges),
            "top_imported": [{"path": p, "indegree": n} for p, n in top if n],
            "cycles": self.cycles,
            "external": self.external,
            "unresolved": self.unresolved,
        }


def _py_module_map(root: Path) -> dict[str, str]:
    """dotted module -> 相对路径（含包 __init__）。"""
    out: dict[str, str] = {}
    for p in iter_files(root):
        if p.suffix.lower() != PY_EXT:
            continue
        rel = str(p.relative_to(root)).replace("\\", "/")
        rel = rel.removeprefix("repo/")
        parts = rel[:-3].split("/")
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if parts:
            out[".".join(parts)] = str(p.relative_to(root)).replace("\\", "/")
    return out


def _py_specs(text: str) -> list[str]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    specs: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            specs.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = "." * node.level + (node.module or "")
                specs.append(base)
                specs.extend(base + "." + a.name for a in node.names)
            elif node.module:
                specs.append(node.module)
                specs.extend(node.module + "." + a.name for a in node.names)
    return specs


def _resolve_py(rel: str, spec: str, modules: dict[str, str]) -> str | None:
    if spec.startswith("."):
        level = len(spec) - len(spec.lstrip("."))
        rest = spec[level:]
        pkg = rel[:-3].split("/")[:-1]
        if pkg and pkg[-1] == "__init__":
            pkg = pkg[:-1]
        keep = len(pkg) - (level - 1)
        base = pkg[:keep] if keep >= 0 else []
        target = ".".join([*base, *([rest] if rest else [])])
    else:
        target = spec
    while target:
        if target in modules:
            return modules[target]
        if "." not in target:
            break
        target = target.rsplit(".", 1)[0]
    return None


def _resolve_fallback(root: Path, rel: str, spec: str) -> str | None:
    """脚本目录式 import、点号路径，以及常见 import 根（src/、repo/、lib/）的解析。"""
    spec = spec.lstrip(".")
    if not spec:
        return None
    bases = [(root / rel).parent, root, root / "src", root / "repo", root / "lib"]
    for base in bases:
        if not base.is_dir():
            continue
        for cand in (base.joinpath(*spec.split(".")).with_suffix(".py"),
                     base.joinpath(*spec.split(".")) / "__init__.py"):
            try:
                if cand.is_file():
                    return str(cand.resolve().relative_to(root.resolve())).replace("\\", "/")
            except (OSError, ValueError):
                continue
    return None


def _resolve_js(root: Path, src_rel: str, spec: str) -> str | None:
    if not spec.startswith("."):
        return None
    base = (root / src_rel).parent / spec
    for cand in [base, *(base.with_suffix(e) for e in JS_EXTS), *(base / ("index" + e) for e in JS_EXTS)]:
        try:
            if cand.is_file():
                return str(cand.resolve().relative_to(root.resolve())).replace("\\", "/")
        except (OSError, ValueError):
            continue
    return None


def build_graph(root: Path) -> Graph:
    root = root.resolve()
    g = Graph()
    modules = _py_module_map(root)
    files: list[tuple[str, str]] = []
    for p in iter_files(root):
        if p.suffix.lower() in ({PY_EXT} | JS_EXT):
            rel = str(p.relative_to(root)).replace("\\", "/")
            files.append((rel, read_text(p)))
    g.nodes = sorted(rel for rel, _ in files)
    known = set(g.nodes)
    for rel, text in files:
        if rel.endswith(PY_EXT):
            for spec in _py_specs(text):
                dst = _resolve_py(rel, spec, modules) or _resolve_fallback(root, rel, spec)
                if dst and dst in known:
                    g.edges.append((rel, dst))
                elif dst is None:
                    g.external.setdefault(rel, []).append(spec)
                else:
                    g.unresolved.setdefault(rel, []).append(spec)
        else:
            for spec in JS_IMPORT_RE.findall(text):
                dst = _resolve_js(root, rel, spec)
                if dst and dst in known:
                    g.edges.append((rel, dst))
                elif dst is None and spec.startswith("."):
                    g.unresolved.setdefault(rel, []).append(spec)
                elif dst is None:
                    g.external.setdefault(rel, []).append(spec)
    g.edges = sorted(set(g.edges))
    g.cycles = find_cycles(g.nodes, g.edges)
    return g


def find_cycles(nodes: list[str], edges: list[tuple[str, str]]) -> list[list[str]]:
    adj: dict[str, list[str]] = {n: [] for n in nodes}
    for a, b in edges:
        adj.setdefault(a, []).append(b)
    color: dict[str, int] = {}
    stack: list[str] = []
    found: list[list[str]] = []
    seen: set[frozenset[str]] = set()

    def dfs(u: str) -> None:
        color[u] = 1
        stack.append(u)
        for v in adj.get(u, []):
            if color.get(v, 0) == 0:
                dfs(v)
            elif color.get(v) == 1:
                cycle = stack[stack.index(v):]
                key = frozenset(cycle)
                if key not in seen:
                    seen.add(key)
                    found.append([*cycle, v])
        stack.pop()
        color[u] = 2

    for n in nodes:
        if color.get(n, 0) == 0:
            dfs(n)
    return found


def focus(graph: Graph, target: str, depth: int = 1) -> dict:
    target = target.replace("\\", "/")
    adj: dict[str, list[str]] = {}
    radj: dict[str, list[str]] = {}
    for a, b in graph.edges:
        adj.setdefault(a, []).append(b)
        radj.setdefault(b, []).append(a)

    def walk(start: str, mapping: dict[str, list[str]]) -> list[str]:
        frontier = {start}
        seen: set[str] = set()
        for _ in range(max(1, depth)):
            nxt: set[str] = set()
            for n in frontier:
                for m in mapping.get(n, []):
                    if m not in seen and m != start:
                        seen.add(m)
                        nxt.add(m)
            frontier = nxt
        return sorted(seen)

    return {
        "target": target,
        "depends_on": walk(target, adj),
        "imported_by": walk(target, radj),
    }


def print_graph(g: Graph) -> None:
    data = g.to_dict()
    print(f"=== proj graph（{data['nodes']} 节点 / {data['edges']} 内部依赖）")
    if data["top_imported"]:
        print("  被依赖最多：")
        for item in data["top_imported"][:5]:
            print(f"    {item['indegree']:>3}  {item['path']}")
    if g.cycles:
        print(f"  [FAIL] 循环依赖 {len(g.cycles)} 组：")
        for cyc in g.cycles[:5]:
            print("    " + " -> ".join(cyc))
    else:
        print("  [OK] 无循环依赖")
    if g.unresolved:
        print(f"  [WARN] 未解析的内部引用 {sum(len(v) for v in g.unresolved.values())} 处")


def print_focus(data: dict) -> None:
    print(f"=== proj graph --focus {data['target']}")
    print(f"  依赖（下游）：{', '.join(data['depends_on']) or '-'}")
    print(f"  被依赖（上游）：{', '.join(data['imported_by']) or '-'}")


def write_json(path: str, data: dict) -> None:
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
