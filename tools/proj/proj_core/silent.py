"""proj silent —— 找出"被吞掉的异常"（裸 except / 只剩 pass 的 except）。

为什么：`except: pass` 是**审计盲区**——出了事日志里一个字都没有，只能靠猜。
真实事故（B-001/B-002）：启动路径里的 `AttributeError` 被外层裸 `except: pass` 吞掉，
`effective_text()` 的源码级脚本照样"通过"，而真实入口从未执行过那段迁移。

判据（可机械判定）：
- `except:`（裸，类型都不看）⇒ **FAIL**；
- `except X:` 体只有 `pass` / `...` ⇒ **FAIL**，除非 handler 行或 body 行带 `# silent-ok: <理由>`；
- 跳过：`try/except` 的 handler 体里有 `raise`/`return`/`continue`/`break`/日志/打印/赋值等任何痕迹。

输出：`{bare: [...], swallow: [...], files_scanned, marked}`；退出码 1 = 有裸 except 或未标记的吞异常。
"""
from __future__ import annotations

import ast
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .common import iter_files

MARKER = "silent-ok"


@dataclass
class Hit:
    file: str
    line: int
    kind: str  # bare | swallow
    detail: str


@dataclass
class SilentReport:
    files_scanned: int = 0
    bare: list[dict] = field(default_factory=list)
    swallow: list[dict] = field(default_factory=list)
    fallback: list[dict] = field(default_factory=list)
    marked: list[dict] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return bool(self.bare or self.swallow or self.fallback)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["failed"] = self.failed
        return d


def _is_swallow(body: list[ast.stmt]) -> bool:
    """handler 体是否"什么都没留下":只允许 pass / ...（空语句）。"""
    if not body:
        return True
    for stmt in body:
        if isinstance(stmt, ast.Pass):
            continue
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) \
                and stmt.value.value is Ellipsis:
            continue
        return False
    return True


_CONST_CALLS = {"dict", "list", "set", "tuple", "str", "int", "float", "bool"}


def _looks_like_error_payload(value: ast.AST) -> bool:
    """返回的是"错误对象"而不是"默认值"——那是有留痕的，不该算静默降级。"""
    for node in ast.walk(value):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value.lower()
            if text in {"error", "iserror", "err"} or "错误" in node.value or "失败" in node.value:
                return True
    return False


def _is_default_return(stmt: ast.stmt) -> bool:
    """只认"返回一份默认配置/默认对象"的形态（`return dict(DEFAULTS)` / `return DEFAULTS` / `return {...}`）。

    刻意**不收** `return None / [] / "" / ()`：那是绝大多数正常读取函数的合法兜底
    （实测：按"任何常量"判定会在本仓产生 9 处误报，全是 read_text/run_git 这类工具函数）；
    也不收"错误对象"（`return {"error": ...}` 是有留痕的），否则又会多出一批误报。
    """
    if not isinstance(stmt, ast.Return):
        return False
    value = stmt.value
    if value is None:
        return False
    if _looks_like_error_payload(value):
        return False
    if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) \
            and value.func.id in _CONST_CALLS and value.args:
        return True
    if isinstance(value, ast.Name) and value.id.isupper():
        return True
    return bool(isinstance(value, ast.Dict) and value.keys)


def _handler_breadth(handler: ast.ExceptHandler) -> str:
    """异常接纳面的宽窄：bare / broad / multi / narrow。"""
    htype = handler.type
    if htype is None:
        return "bare"
    if isinstance(htype, ast.Name) and htype.id in {"Exception", "BaseException"}:
        return "broad"
    if isinstance(htype, ast.Tuple) and len(htype.elts) >= 2:
        return "multi"
    return "narrow"


def _logs_somewhere(body: list[ast.stmt]) -> bool:
    """handler 里是否有任何"留痕"动作（日志/打印/上报）。"""
    trace_attrs = {"warning", "warn", "error", "exception", "info", "debug", "critical", "print"}
    for stmt in body:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr in trace_attrs:
                return True
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print":
                return True
    return False


def scan_text(label: str, text: str) -> list[Hit]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    lines = text.splitlines()
    hits: list[Hit] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        for handler in node.handlers:
            lineno = getattr(handler, "lineno", 0)
            body_line = getattr(handler.body[0], "lineno", lineno) if handler.body else lineno
            marked = any(0 < ln <= len(lines) and MARKER in lines[ln - 1]
                         for ln in (lineno, body_line))
            kind = ""
            detail = ""
            if _is_swallow(handler.body):
                kind = "bare" if handler.type is None else "swallow"
                detail = "裸 except（连异常类型都不看）" if kind == "bare" else "异常被吞掉（body 只有 pass/...）"
            elif not _logs_somewhere(handler.body) and all(_is_default_return(stmt) for stmt in handler.body):
                breadth = _handler_breadth(handler)
                if breadth in ("bare", "broad", "multi"):
                    kind = "fallback"
                    detail = f"静默降级（{breadth}）：只 return 兜底值、无日志 ⇒ 调用方分不清“没有”与“坏了”"
            if not kind:
                continue
            hits.append(Hit(label, lineno, "marked" if marked else kind, detail))
    return hits


def find_silent(root: Path) -> SilentReport:
    root = root.resolve()
    report = SilentReport()
    for path in iter_files(root):
        if path.suffix != ".py":
            continue
        report.files_scanned += 1
        try:
            rel = str(path.resolve().relative_to(root)).replace("\\", "/")
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for hit in scan_text(rel, text):
            row = asdict(hit)
            if hit.kind == "bare":
                report.bare.append(row)
            elif hit.kind == "marked":
                report.marked.append(row)
            elif hit.kind == "fallback":
                report.fallback.append(row)
            else:
                report.swallow.append(row)
    return report


def counts_by_file(report: SilentReport) -> dict[str, dict[str, int]]:
    """按 (文件 → 各类计数) 汇总；baseline 棘轮用它做对比（行号会漂，所以只数个数）。"""
    out: dict[str, dict[str, int]] = {}
    for kind, rows in (("bare", report.bare), ("swallow", report.swallow), ("fallback", report.fallback)):
        for row in rows:
            bucket = out.setdefault(row["file"], {"bare": 0, "swallow": 0, "fallback": 0})
            bucket[kind] += 1
    return out


def load_baseline(path: Path) -> dict[str, dict[str, int]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    counts = data.get("counts") if isinstance(data, dict) else None
    return counts if isinstance(counts, dict) else {}


def write_baseline(path: Path, counts: dict[str, dict[str, int]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "counts": counts}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def compare_baseline(current: dict[str, dict[str, int]],
                     baseline: dict[str, dict[str, int]]) -> list[dict]:
    """返回"超出基线"的条目：新文件、或某类计数变大。"""
    over: list[dict] = []
    for path, kinds in sorted(current.items()):
        base = baseline.get(path, {})
        for kind, count in kinds.items():
            was = int(base.get(kind, 0))
            if count > was:
                over.append({"file": path, "kind": kind, "now": count, "baseline": was})
    return over


def print_silent(report: SilentReport, over: list[dict] | None = None,
                 baseline_path: str = "") -> None:
    total = len(report.bare) + len(report.swallow) + len(report.fallback)
    if baseline_path and over is not None:
        # 基线（棘轮）模式：存量是已接受的债务，只报增量，避免每次刷 90 行噪音
        print(f"=== proj silent：{report.files_scanned} 个 Python 文件（基线模式 {baseline_path}）")
        print(f"  存量债务 {total} 处（基线已接受，不追究；改完一处就重跑 --update-baseline 降基线）")
        if report.marked:
            print(f"  [INFO] 另已标记豁免 {len(report.marked)} 处（# silent-ok）")
        if over:
            print(f"  [FAIL] 本次**新增/增长** {len(over)} 处：")
            for item in over[:20]:
                print(f"    + {item['file']} {item['kind']}：{item['baseline']} → {item['now']}")
            print("  新代码不要再加；确实无害的清理请写 `# silent-ok: <理由>`")
        else:
            print("  [OK] 未增长 —— 新代码不要再加")
        return
    print(f"=== proj silent：{report.files_scanned} 个 Python 文件")
    if report.bare:
        for h in report.bare[:40]:
            print(f"  [FAIL] 裸 except：{h['file']}:{h['line']}")
        if len(report.bare) > 40:
            print(f"    …（另有 {len(report.bare) - 40} 处）")
    if report.swallow:
        for h in report.swallow[:40]:
            print(f"  [FAIL] 吞异常：{h['file']}:{h['line']}")
        if len(report.swallow) > 40:
            print(f"    …（另有 {len(report.swallow) - 40} 处）")
        print("  改法：`except Exception: logger.warning(..., exc_info=True)`；")
        print("  确实无害的清理才可静默，并在**同一行**写 `# silent-ok: <理由>`")
    if report.fallback:
        for h in report.fallback[:40]:
            print(f"  [FAIL] 静默降级：{h['file']}:{h['line']}  {h.get('detail', '')}")
        if len(report.fallback) > 40:
            print(f"    …（另有 {len(report.fallback) - 40} 处）")
        print("  判据：接纳面宽/多类型 + 只 return 兜底值 + 无任何日志 ⇒ 调用方分不清“没有”与“坏了”")
        print("  改法：抛专用异常（如 ConfigError）由上层提示，或至少 logger.warning；有意降级写 `# silent-ok: <理由>`")
    if report.marked:
        print(f"  [INFO] 已标记豁免 {len(report.marked)} 处（# silent-ok）")
    if not report.failed:
        print("  [OK] 无裸 except、无未标记的吞异常、无静默降级")
        return
    print(f"  合计 FAIL={total}")


def write_json(path: str, data: dict) -> None:
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
