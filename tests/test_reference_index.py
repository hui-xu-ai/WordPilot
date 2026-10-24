# tests/test_reference_index.py
"""派生索引：CSV 变了要重建，重建后查询结果与 CSV 完全一致，且索引可随时删除重建。"""
from __future__ import annotations

from pathlib import Path

import pytest

from plugins.reference.reference_index import ReferenceIndex
from plugins.reference.reference_store import Reference, ReferenceStore


@pytest.fixture
def store_index(tmp_path: Path):
    store = ReferenceStore(directory=tmp_path)
    index = ReferenceIndex(db_path=tmp_path / "index.db")
    yield store, index
    index.close()


def test_ensure_builds_once_then_reuses(store_index):
    store, index = store_index
    store.upsert(Reference(ref_key="zotero:934", title="Global biodiversity"))

    assert index.ensure(store) is True, "首次必须重建"
    assert index.ensure(store) is False, "源没变不该重建"
    assert index.count() == 1


def test_index_follows_csv_changes(store_index):
    store, index = store_index
    store.upsert(Reference(ref_key="zotero:1", title="A"))
    index.ensure(store)
    assert index.count() == 1

    store.upsert(Reference(ref_key="zotero:2", title="B"))
    assert index.ensure(store) is True, "CSV 变了必须重建"
    assert index.count() == 2


def test_lookup_by_key_and_alias(store_index):
    store, index = store_index
    store.upsert(Reference(ref_key="zotero:934", title="T", aliases=["doi:10.1/x"]))
    index.ensure(store)

    payload = index.get_payload("doi:10.1/x")
    assert payload is not None and payload["title"] == "T"
    assert index.resolve_key("doi:10.1/x") == "zotero:934"
    assert index.get_payload("nope") is None


def test_anchor_lookup(store_index):
    store, index = store_index
    store.bind_anchor("论文.docx", "1", "zotero:934")
    index.ensure(store)

    assert index.anchor_target("论文.docx", "1") == "zotero:934"
    assert index.anchor_target("论文.docx", "2") is None


def test_search_matches_title_author_note(store_index):
    store, index = store_index
    store.upsert(Reference(ref_key="zotero:1", title="浮游生物多样性", authors="Tittensor"))
    store.upsert(Reference(ref_key="zotero:2", title="无关条目", note="核心参考"))
    index.ensure(store)

    assert [row["ref_key"] for row in index.search("浮游")] == ["zotero:1"]
    assert [row["ref_key"] for row in index.search("Tittensor")] == ["zotero:1"]
    assert [row["ref_key"] for row in index.search("核心")] == ["zotero:2"]


def test_index_can_be_rebuilt_from_scratch(store_index):
    store, index = store_index
    store.upsert(Reference(ref_key="zotero:1", title="A"))
    index.ensure(store)
    index.close()

    index.path.unlink(missing_ok=True)
    fresh = ReferenceIndex(db_path=index.path)
    try:
        assert fresh.ensure(store) is True
        assert fresh.count() == 1
        assert fresh.get_payload("zotero:1")["title"] == "A"
    finally:
        fresh.close()
