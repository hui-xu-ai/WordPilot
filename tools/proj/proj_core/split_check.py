"""proj split-check —— 用 AST 证明一次"纯搬移"拆分是等价的（stdlib only）。

为什么需要它：AI 遇到"行数超阈值"时，最省力的动作是**删代码**或**按行切文件**，
两者都会把"搬家"和"改逻辑"混在一起，坏了也定位不到。本命令把"纯搬移"变成**可机械证明**的事：

1. **符号守恒**：旧文件每个顶层 def/class/赋值，在新目录里**恰好出现一次**，且 `ast.dump` 完全一致
   ⇒ 缺 = 漏搬；重 = 新旧两份并存（复制粘贴）；不一致 = 顺手改了逻辑（不属纯搬移）。
2. **出口守恒**：旧文件的公开名（不以 `_` 开头，或 `__all__` 列出）必须由新目录的 `__init__.py`
   重新导出，否则外部 `import` 路径当场断。
3. **环检测（可选）**：`--cycles` 复用 `proj graph`，只报落在本次拆分范围内的环
   （循环 import 往往说明边界划错了，不是用 `TYPE_CHECKING` 能糊过去的）。

**它不检查**：运行时行为（用 pytest）、未定义名/失效 import（用 `ruff check --select F821,F401`）。
用法：
  python tools/proj/proj.py split-check . --src src/pkg/plugin.py --dst src/pkg/plugin_parts
  python tools/proj/proj.py split-check . --src src/pkg/plugin.py --src-rev HEAD --dst src/pkg/plugin_parts --cycles
"""
from __future__ import annotations

import ast
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .common import run_git
from .graph import build_graph, find_cycles

_UNITS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


@dataclass
class Unit:
    name: str
    fp: str
    file: str
    lineno: int


@dataclass
class SplitReport:
    src: str = ""
    dst: str = ""
    src_lines: int = 0
    dst_files: list[str] = field(default_factory=list)
    src_units: int = 0
    dst_units: int = 0
    missing: list[str] = field(default_factory=list)
    duplicated: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    new: list[str] = field(default_factory=list)
    not_exported: list[str] = field(default_factory=list)
    wildcard_export: bool = False
    cycles: list[list[str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return bool(self.missing or self.duplicated or self.changed or self.not_exported
                    or self.cycles or self.errors)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["failed"] = self.failed
        return d


def _assign_names(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Assign):
        return [t.id for t in node.targets if isinstance(t, ast.Name)]
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return [node.target.id]
    return []


def collect_units(label: str, text: str) -> list[Unit]:
    """顶层符号 → 归一化指纹（ast.dump 不含行号，纯结构与内容）。"""
    tree = ast.parse(text)
    out: list[Unit] = []
    for node in tree.body:
        if isinstance(node, _UNITS):
            out.append(Unit(node.name, ast.dump(node), label, getattr(node, "lineno", 0)))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            names = _assign_names(node)
            if "__all__" in names:
                continue
            for name in names:
                out.append(Unit(name, ast.dump(node), label, getattr(node, "lineno", 0)))
    return out


def _declared_all(text: str) -> set[str]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set()
    names: set[str] = set()
    for node in tree.body:
        if (isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets)
                and isinstance(node.value, (ast.List, ast.Tuple))):
            names |= {e.value for e in node.value.elts
                      if isinstance(e, ast.Constant) and isinstance(e.value, str)}
    return names


def facade_exports(text: str) -> tuple[set[str], bool]:
    """`__init__.py` 的再导出面：`from .x import y` 的绑定名 + `__all__`；`import *` 记 wildcard。"""
    tree = ast.parse(text)
    names: set[str] = set()
    wildcard = False
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    wildcard = True
                else:
                    names.add(alias.asname or alias.name)
        elif (isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets)
                and isinstance(node.value, (ast.List, ast.Tuple))):
            names |= {e.value for e in node.value.elts
                      if isinstance(e, ast.Constant) and isinstance(e.value, str)}
    return names, wildcard


def _dst_files(dst: Path) -> list[Path]:
    if dst.is_dir():
        return sorted(p for p in dst.rglob("*.py") if "__pycache__" not in p.parts)
    return [dst]


def check_split(root: Path, src: str, dst: str, src_rev: str | None = None,
                allow_change: tuple[str, ...] = (), strict: bool = False,
                check_cycles: bool = False) -> SplitReport:
    root = root.resolve()
    report = SplitReport(src=src, dst=dst)
    src_rel = Path(src).as_posix()

    if src_rev:
        text = run_git(root, "show", f"{src_rev}:{src_rel}")
        if text is None:
            report.errors.append(f"git show {src_rev}:{src_rel} 失败（路径需相对仓库根）")
            return report
    else:
        src_path = (root / src_rel) if not Path(src).is_absolute() else Path(src)
        if not src_path.exists():
            report.errors.append(f"src 不存在：{src}（旧文件已删？用 --src-rev HEAD 对比提交版本）")
            return report
        text = src_path.read_text(encoding="utf-8", errors="replace")

    report.src_lines = len(text.splitlines())
    try:
        src_units = collect_units(src_rel, text)
    except SyntaxError as exc:
        report.errors.append(f"src 解析失败（第 {exc.lineno} 行）：{exc.msg}")
        return report
    report.src_units = len(src_units)

    dst_path = (root / dst) if not Path(dst).is_absolute() else Path(dst)
    if not dst_path.exists():
        report.errors.append(f"dst 不存在：{dst}")
        return report
    files = _dst_files(dst_path)
    if not files:
        report.errors.append(f"dst 下没有 .py 文件：{dst}")
        return report

    dst_units: dict[str, list[Unit]] = {}
    for path in files:
        label = path.resolve().relative_to(root).as_posix()
        report.dst_files.append(label)
        try:
            parsed = collect_units(label, path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError as exc:
            report.errors.append(f"{label} 解析失败（第 {exc.lineno} 行）：{exc.msg}")
            continue
        for unit in parsed:
            dst_units.setdefault(unit.name, []).append(unit)
    report.dst_units = sum(len(v) for v in dst_units.values())

    for unit in src_units:
        found = dst_units.get(unit.name, [])
        if not found:
            report.missing.append(f"{unit.name}（原 {unit.file}:{unit.lineno}）")
        elif len(found) > 1:
            where = ", ".join(f"{u.file}:{u.lineno}" for u in found)
            report.duplicated.append(f"{unit.name}（{where}）")
        elif found[0].fp != unit.fp and unit.name not in allow_change:
            report.changed.append(f"{unit.name}（{found[0].file}:{found[0].lineno}）")

    src_names = {u.name for u in src_units}
    report.new = sorted(n for n in dst_units if n not in src_names and not n.startswith("__"))

    public = {u.name for u in src_units if not u.name.startswith("_")} | _declared_all(text)
    if dst_path.is_dir():
        init = dst_path / "__init__.py"
        if not init.exists():
            report.errors.append("dst 目录缺 __init__.py：外部 import 路径会断（需要 facade 再导出）")
        else:
            exported, wildcard = facade_exports(init.read_text(encoding="utf-8", errors="replace"))
            report.wildcard_export = wildcard
            if not wildcard:
                report.not_exported = sorted(public - exported)
    else:
        # 单文件目标：符号守恒已覆盖出口（这个文件本身就是模块），无需再导出检查。
        report.wildcard_export = False

    if check_cycles:
        graph = build_graph(root)
        scope = {p for p in report.dst_files} | {src_rel}
        for cycle in find_cycles(graph.nodes, graph.edges):
            hit = [n for n in cycle if n in scope]
            if hit:
                report.cycles.append(cycle)

    if strict and report.new:
        report.errors.append(f"--strict：dst 出现了旧文件没有的新符号（{', '.join(report.new)}）——不属纯搬移")
    return report


def print_split(report: SplitReport) -> None:
    print(f"=== proj split-check：{report.src} → {report.dst}")
    if report.errors:
        for err in report.errors:
            print(f"  [FAIL] {err}")
    if report.src_units:
        print(f"  基线：{report.src_lines} 行 / {report.src_units} 个顶层符号"
              f" → {len(report.dst_files)} 个文件 / {report.dst_units} 个符号")
    for name in report.missing:
        print(f"  [FAIL] 漏搬：{name}")
    for name in report.duplicated:
        print(f"  [FAIL] 重复（新旧两份并存）：{name}")
    for name in report.changed:
        print(f"  [FAIL] 语义被改（不属纯搬移）：{name}")
    for name in report.not_exported:
        print(f"  [FAIL] 未再导出（外部 import 会断）：{name}")
    for cycle in report.cycles:
        print(f"  [FAIL] 拆分引入循环 import：{' → '.join(cycle)}")
    for name in report.new:
        print(f"  [WARN] 新增符号（纯搬移不应有）：{name}")
    if report.wildcard_export:
        print("  [WARN] facade 用了 import *，出口守恒无法核对")
    if not report.failed:
        print("  [OK] 纯搬移等价：符号与语义一致、出口已再导出")
        print("  [下一步] ruff check --select F821,F401 <dst> && pytest -q")
    else:
        print("  [提示] 不是纯搬移就不该这么用：改逻辑请另开一个提交，别和搬家混在一起")
