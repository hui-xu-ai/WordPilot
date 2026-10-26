# tests/test_reference_no_name_errors.py
"""回归：插件里所有"委托到抽出的模块"的方法，都必须真的可用。

教训（用户现场）：`_import_bibliography()` 委托 `bib_import.import_bibliography`，
但抽模块时漏了 import —— 点引用时抛 `NameError`，Qt 把槽里异常吞掉，
表现就是"点了完全没反应"。日志里能看到 `NameError: name 'import_bibliography'
is not defined`。
"""
from __future__ import annotations

import pytest


def test_bib_import_symbol_is_available():
    import plugins.reference.plugin as plugin_module

    assert hasattr(plugin_module, "import_bibliography"), \
        "plugin 模块必须导入 bib_import.import_bibliography（否则点引用时 NameError）"


def test_import_bibliography_runs_without_name_error(tmp_path, monkeypatch):
    from plugins.reference import plugin as plugin_module

    plugin = plugin_module.ReferencePlugin.__new__(plugin_module.ReferencePlugin)
    plugin._bib_cache = {}
    plugin._store = None
    plugin._ensure_scan_library = lambda path: None

    # 文件不存在时应当安静返回（不抛 NameError）
    plugin._import_bibliography(str(tmp_path / "不存在.docx"), "不存在.docx")


def test_delegated_helpers_exist():
    """抽出去的方法都要能调到（这些是最容易被"抽模块"删掉的一类）。"""
    from plugins.reference import plugin as plugin_module

    for name in ("import_bibliography", "hint_missing_reference",
                 "connect_watch", "apply_document_name", "push_current_document",
                 "pet_anchor_point", "pet_preferred"):
        assert hasattr(plugin_module, name), f"plugin 缺少委托符号：{name}"


def test_reference_plugin_class_has_pet_helpers():
    from plugins.reference.plugin import ReferencePlugin

    for name in ("_pet_anchor_point", "_pet_preferred", "_push_current_document",
                 "_connect_doc_signal", "_on_document_changed"):
        assert hasattr(ReferencePlugin, name), f"插件缺少方法：{name}"
