# tests/test_reference_diff_view.py
"""同步差异视图：扫描后"本次新增/已移除"能筛出来，已移除的备注不丢。"""
from __future__ import annotations

import pytest

from plugins.reference.library_page import LibraryPage
from plugins.reference.reference_index import ReferenceIndex
from plugins.reference.reference_store import Reference, ReferenceStore
from tests.test_reference_library_page import _Ctx


@pytest.fixture
def page(tmp_path):
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    store = ReferenceStore(directory=tmp_path)
    store.upsert(Reference(ref_key="doi:keep", title="保留的"))
    store.upsert(Reference(ref_key="doi:new", title="新增的"))
    store.upsert(Reference(ref_key="doi:gone", title="已移除的", note="这条备注不能丢"))
    index = ReferenceIndex(db_path=tmp_path / "index.db")
    widget = LibraryPage(_Ctx(), store, index)
    widget.reload()
    yield widget, store
    index.close()
    widget.deleteLater()


def test_filter_chips_exist(page):
    widget, _store = page
    assert "added" in widget.filter_actions
    assert "removed" in widget.filter_actions


def test_added_filter_shows_only_new(page):
    widget, _store = page
    widget.set_doc_diff({"doi:new"}, {"doi:gone"})
    widget.set_filter("added")
    visible = [widget.table.item(row, 0).data(0x0100)
               for row in range(widget.table.rowCount())
               if not widget.table.isRowHidden(row)]
    assert visible == ["doi:new"]


def test_removed_filter_keeps_reference_and_note(page):
    widget, store = page
    widget.set_doc_diff({"doi:new"}, {"doi:gone"})
    widget.set_filter("removed")
    visible = [widget.table.item(row, 0).data(0x0100)
               for row in range(widget.table.rowCount())
               if not widget.table.isRowHidden(row)]
    assert visible == ["doi:gone"]
    assert store.get("doi:gone").note == "这条备注不能丢", "移除只是状态，备注资产必须保留"


def test_status_reports_counts(page):
    widget, _store = page
    widget.set_doc_diff({"a", "b"}, {"c"})
    text = widget.footer_label.text()
    assert "2" in text and "1" in text, f"状态栏应显示新增/移除数量，实际：{text!r}"
