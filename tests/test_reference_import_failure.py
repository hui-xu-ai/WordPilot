# tests/test_reference_import_failure.py
"""R3：导入写失败不能记成"已完成"。

审查复现：CSV 临时不可写时扫描，首次导入 0 条；解除占用后再扫同一文档，
因为已被标记为"导入完成"，`cached=1` 直接跳过，持久库仍是 0 条。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


def _make_plugin(tmp_path, monkeypatch, failing: bool):
    from plugins.reference import plugin as plugin_module

    plugin = plugin_module.ReferencePlugin.__new__(plugin_module.ReferencePlugin)
    plugin._bib_cache = {}
    plugin._scan_cache = None
    plugin._watch = None
    plugin._page = None
    plugin.tr = lambda key, **kw: key
    plugin._data_dir = tmp_path
    plugin._libraries = None
    plugin._active_name = "A"

    class _Store:
        def __init__(self):
            self.written = 0

        def upsert(self, reference, **kw):
            if failing:
                raise plugin_module.ReferenceStoreError("CSV 被占用")
            self.written += 1
            return reference

    plugin._store = _Store()

    class _Result:
        references = [object(), object()]
        provider_citations: list = []

    class _FakeDocEntry:
        pass

    monkeypatch.setattr(plugin_module.os.path, "exists", lambda p: True)
    monkeypatch.setattr(plugin_module.os.path, "getmtime", lambda p: 1.0)

    docx = pytest.importorskip("plugins.reference.docx_scan")
    monkeypatch.setattr(docx, "scan_docx", lambda path, name: _Result())
    monkeypatch.setattr(plugin, "_route_before_import", lambda *a, **k: True)
    return plugin


def test_failure_does_not_mark_cached(tmp_path, monkeypatch):
    plugin = _make_plugin(tmp_path, monkeypatch, failing=True)

    stats = {}
    plugin.scan_active_document = None            # 只测内部导入路径，避免依赖 Word
    from plugins.reference import plugin as plugin_module

    # 直接调用内部逻辑：拿不到 Word 时也应先走"解析 → 入库 → 标记"分支
    try:
        result = plugin._import_bibliography("C:/docs/论文.docx", "论文.docx")
    except Exception:
        result = None

    assert plugin._bib_cache == {}, "写失败后不能标记为已导入（否则重试被跳过）"
