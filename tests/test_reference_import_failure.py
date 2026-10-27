# tests/test_reference_import_failure.py
"""R3：导入写失败不能记成"已完成"。

审查复现：CSV 临时不可写时扫描，首次导入 0 条；解除占用后再扫同一文档，
因为已被标记为"导入完成"，`cached=1` 直接跳过，持久库仍是 0 条。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


def _make_plugin(tmp_path, monkeypatch, failing: bool, providers=(), references=None):
    from plugins.reference import plugin as plugin_module
    from plugins.reference.reference_store import Reference

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
            self.items = []

        def upsert(self, reference, **kw):
            if failing:
                raise plugin_module.ReferenceStoreError("CSV 被占用")
            self.written += 1
            self.items.append(reference)
            return reference

    plugin._store = _Store()

    if references is None:
        references = [Reference(ref_key="bib:1", title="列表文献一", cite_label="1"),
                      Reference(ref_key="bib:2", title="列表文献二", cite_label="2")]

    class _Result:
        pass

    result = _Result()
    result.references = list(references)
    result.provider_citations = list(providers)

    monkeypatch.setattr(plugin_module.os.path, "exists", lambda p: True)
    monkeypatch.setattr(plugin_module.os.path, "getmtime", lambda p: 1.0)

    docx = pytest.importorskip("plugins.reference.docx_scan")
    monkeypatch.setattr(docx, "scan_docx", lambda path, name: result)
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


def test_only_bibliography_entries_are_imported(tmp_path, monkeypatch):
    """域引用只在"同一篇已在文末列表里"时用于补全，多出来的不入库。

    用户现场：Zotero 文档文末列表 25 条（编号 1..25），域内 18 条元数据（无编号、键是 doi），
    两边都入库 → 子库出现大量"无编号且与有编号重复"的条目。
    """
    from plugins.reference.docx_scan import ScanCitation
    from plugins.reference.reference_store import Reference

    providers = [ScanCitation(anchor_key="c1", kind="zotero",
                              reference=Reference(ref_key="doi:extra", title="列表里没有的文献"))]
    plugin = _make_plugin(tmp_path, monkeypatch, failing=False, providers=providers)

    plugin._import_bibliography("C:/docs/论文.docx", "论文.docx")

    assert plugin._store.written == 2, "只入文末列表的两条，多余的域引用丢弃"


def test_provider_metadata_fills_matching_entry(tmp_path, monkeypatch):
    """域元数据与列表同一篇（按标题匹配）→ 只补字段，不新增条目。"""
    from plugins.reference.docx_scan import ScanCitation
    from plugins.reference.reference_store import Reference

    providers = [ScanCitation(anchor_key="c1", kind="zotero",
                              reference=Reference(ref_key="doi:10.1002/1", title="列表文献一",
                                                  doi="10.1002/1", authors="Zhang Z H"))]
    plugin = _make_plugin(tmp_path, monkeypatch, failing=False, providers=providers)

    plugin._import_bibliography("C:/docs/论文.docx", "论文.docx")

    assert plugin._store.written == 2, "不能因为域里有元数据就多写一条"
    merged = next(item for item in plugin._store.items if item.title == "列表文献一")
    assert merged.doi == "10.1002/1" and merged.authors == "Zhang Z H", "缺的字段应被补全"
