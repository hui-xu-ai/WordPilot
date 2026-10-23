"""proj imports —— 用 AST 找"导入了却没用"的名字（Python，零依赖）。

口径：
- `import os` / `import os.path` / `from x import y as z` 都按绑定名统计；
- 使用 = `ast.Name(Load)` + 字符串注解里的前向引用 + `__all__` 里列出的名字；
- 跳过：`from __future__ import ...`、`import *`、带 `# noqa` 的导入行；
- 动态导入（`importlib.import_module`）不在此列。
"""
from __future__ import annotations

import ast
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .common import iter_files

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass
class UnusedImport:
    file: str
    line: int
    name: str
    source: str


@dataclass
class ImportReport:
    files_scanned: int = 0
    files_with_unused: int = 0
    unused: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _annotation_strings(tree: ast.AST) -> set[str]:
    """收集字符串注解里的前向引用（`x: "Foo"`）。"""
    names: set[str] = set()
    for node in ast.walk(tree):
        ann = None
        if isinstance(node, (ast.arg, ast.AnnAssign)):
            ann = node.annotation
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            ann = node.returns
        if isinstance(ann, ast.Constant) and isinstance(ann.value, str):
            names.update(_WORD.findall(ann.value))
    return names


def _all_exports(tree: ast.AST) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = [t for t in node.targets if isinstance(t, ast.Name) and t.id == "__all__"]
            if targets and isinstance(node.value, (ast.List, ast.Tuple)):
                for elt in node.value.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        out.add(elt.value)
    return out


def _has_noqa(lines: list[str], lineno: int) -> bool:
    if 1 <= lineno <= len(lines):
        return "noqa" in lines[lineno - 1].lower()
    return False


def check_file(path: Path, root: Path) -> list[UnusedImport]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(text)
    except (OSError, SyntaxError):
        return []
    rel = str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
    lines = text.splitlines()

    bindings: list[tuple[str, int, str]] = []  # (name, line, source_text)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if _has_noqa(lines, node.lineno):
                continue
            for alias in node.names:
                bound = alias.asname or alias.name.split(".")[0]
                suffix = f" as {alias.asname}" if alias.asname else ""
                bindings.append((bound, node.lineno, f"import {alias.name}{suffix}"))
        elif isinstance(node, ast.ImportFrom):
            if node.module == "__future__" or _has_noqa(lines, node.lineno):
                continue
            if any(a.name == "*" for a in node.names):
                continue
            for alias in node.names:
                bound = alias.asname or alias.name
                suffix = f" as {alias.asname}" if alias.asname else ""
                bindings.append((bound, node.lineno, f"from {node.module or '.'} import {alias.name}{suffix}"))

    if not bindings:
        return []

    used: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            used.add(node.id)
    used |= _annotation_strings(tree)
    used |= _all_exports(tree)

    return [UnusedImport(rel, line, name, source)
            for name, line, source in bindings if name not in used]


def find_unused(root: Path) -> ImportReport:
    root = root.resolve()
    report = ImportReport()
    seen: set[tuple[str, int, str]] = set()
    for path in iter_files(root):
        if path.suffix != ".py":
            continue
        report.files_scanned += 1
        for item in check_file(path, root):
            key = (item.file, item.line, item.name)
            if key not in seen:
                seen.add(key)
                report.unused.append(asdict(item))
    report.files_with_unused = len({u["file"] for u in report.unused})
    return report


def print_imports(report: ImportReport) -> None:
    print(f"=== proj imports：{report.files_scanned} 个 Python 文件")
    if not report.unused:
        print("  [OK] 未发现未使用的 import")
        return
    print(f"  [WARN] {len(report.unused)} 处未使用 import（{report.files_with_unused} 个文件）：")
    for u in report.unused[:80]:
        print(f"    {u['file']}:{u['line']}  {u['name']}  （{u['source']}）")
    if len(report.unused) > 80:
        print(f"    …（省略 {len(report.unused) - 80} 处）")


def write_json(path: str, data: dict) -> None:
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
