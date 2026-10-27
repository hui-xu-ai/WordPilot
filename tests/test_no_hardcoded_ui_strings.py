# tests/test_no_hardcoded_ui_strings.py
"""界面文案必须走语言包：源码里不允许出现传给控件的硬编码中文/英文字面量。

用户要求（2026-10-27）：「软件还有大量文字硬编码成中文，没有按规范放进独立语言包」。
本测试用 AST 扫描真实 UI 调用点（QLabel/setText/setToolTip/QMessageBox 等），
发现中文字面量直接失败，防止再往代码里塞死文案。

排除：docstring、注释、logger/异常信息（开发可见，不是 UI 文案）。
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CJK = re.compile(r"[\u4e00-\u9fff]")
SKIP_DIRS = {"__pycache__", "plugins_user"}

TEXT_SETTERS = {"setText", "setPlaceholderText", "setToolTip", "setWindowTitle", "setStatusTip",
                "setTabText", "addTab", "addItem", "addItems", "addAction", "setTitle",
                "setMessage", "showMessage", "information", "warning", "question", "critical",
                "getText", "getItem", "setDetailedText", "setWhatsThis", "setAccessibleName"}
WIDGET_CTORS = {"QLabel", "QPushButton", "QCheckBox", "QRadioButton", "QToolButton", "QGroupBox"}
DIALOGS = {"QMessageBox", "QInputDialog"}
LOG_OWNERS = {"logger", "log", "print", "logging"}


def _api(call: ast.Call) -> str:
    func = call.func
    name = getattr(func, "attr", None) or getattr(func, "id", "")
    owner = getattr(func, "value", None)
    owner_name = getattr(owner, "id", "") or getattr(owner, "attr", "")
    if owner_name in LOG_OWNERS:
        return ""
    if owner_name in DIALOGS:
        return owner_name
    if name in TEXT_SETTERS or name in WIDGET_CTORS:
        return name
    return ""


def _collect(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    parent: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent[child] = node

    def in_docstring(node: ast.AST) -> bool:
        ctx = parent.get(node)
        while ctx is not None and not isinstance(ctx, ast.stmt):
            ctx = parent.get(ctx)
        return (isinstance(ctx, ast.Expr) and isinstance(ctx.value, ast.Constant)
                and isinstance(ctx.value.value, str))

    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value.strip()
            if not CJK.search(text) or len(text) < 2 or in_docstring(node):
                continue
            ctx = parent.get(node)
            if isinstance(ctx, ast.Call):
                api = _api(ctx)
                if api:
                    found.append(f"{path.name}:{node.lineno} [{api}] {text[:50]}")
        elif isinstance(node, ast.JoinedStr):
            text = "".join(part.value for part in node.values
                           if isinstance(part, ast.Constant) and isinstance(part.value, str))
            if not CJK.search(text):
                continue
            ctx = parent.get(node)
            if isinstance(ctx, ast.Call):
                api = _api(ctx)
                if api:
                    found.append(f"{path.name}:{node.lineno} [{api}] {text[:50]}")
    return found


def test_no_hardcoded_ui_strings():
    offenders: list[str] = []
    for path in sorted((ROOT / "src").rglob("*.py")):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        offenders.extend(_collect(path))

    assert not offenders, "界面文案必须写进语言包（用 self.tr(\"key\")）：\n" + "\n".join(offenders)


@pytest.mark.parametrize("plugin", ["clipboard", "image_toolbox", "navigator", "proofread",
                                    "reference"])
def test_plugin_description_is_localised(plugin):
    import json

    zh = json.loads((ROOT / f"src/plugins/{plugin}/i18n/zh_CN.json").read_text(encoding="utf-8"))
    en = json.loads((ROOT / f"src/plugins/{plugin}/i18n/en_US.json").read_text(encoding="utf-8"))

    assert zh.get("plugin.description"), f"{plugin} 缺中文简介"
    assert en.get("plugin.description"), f"{plugin} 缺英文简介"
    assert zh["plugin.description"] != en["plugin.description"], "简介没翻译"


def test_plugin_list_uses_i18n_description():
    source = (ROOT / "src/app/plugin_manager_page.py").read_text(encoding="utf-8")

    assert "_display_description(" in source
    assert 'f"{record.manifest.id}.plugin.description"' in source
