# tests/test_reference_label_popup.py
"""修"点文献引用没有弹窗"。

现场：EndNote 文档的引用是**空壳域 / 纯文本编号**（`provider_citations == 0`），
`kind == "label_fallback"`；但 `_entry_for_item` 对这种情况直接 `return None`，
于是点引用毫无反应。库里其实有同编号的文献（`cite_label` 相同）。
"""
from __future__ import annotations

import pytest


class _FakeStore:
    def __init__(self, by_label=None, by_key=None):
        self._by_label = by_label or {}
        self._by_key = by_key or {}
        self.upserts = 0

    def get(self, key):
        return self._by_key.get(key)

    def find_by_label(self, label):
        return self._by_label.get(label)

    def anchor_for(self, doc_name, cite_label):
        return None

    def upsert(self, reference, **kw):
        self.upserts += 1
        return reference


def _plugin(store):
    from plugins.reference.plugin import ReferencePlugin

    plugin = ReferencePlugin.__new__(ReferencePlugin)
    plugin._store = store
    plugin._dismissed = set()
    plugin._identity_of = lambda key: key
    return plugin


def test_label_fallback_resolves_by_citation_number():
    from plugins.reference.reference_store import Reference

    ref = Reference(ref_key="bib:1", cite_label="3", title="文献三", note="项目备注")
    plugin = _plugin(_FakeStore(by_label={"3": ref}))

    entry = plugin._entry_for_item({"anchor_key": "doc:论文.docx:label:3",
                                    "doc_name": "论文.docx", "label": "3",
                                    "kind": "label_fallback"})

    assert entry is not None, "库里同编号有文献时必须弹窗"
    assert entry.note == "项目备注"


def test_label_fallback_without_match_stays_silent():
    plugin = _plugin(_FakeStore(by_label={}))

    entry = plugin._entry_for_item({"anchor_key": "doc:论文.docx:label:9",
                                    "doc_name": "论文.docx", "label": "9",
                                    "kind": "label_fallback"})

    assert entry is None, "库里没有就仍然不打扰"
