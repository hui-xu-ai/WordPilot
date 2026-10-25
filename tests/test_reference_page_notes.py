"""右栏总库备注入口的测试：徽标随来源变化、条数显示、挑选后跟随。"""
from __future__ import annotations

import pytest

from plugins.reference.library_page import LibraryPage
from plugins.reference.notes import NoteService, NoteStore
from plugins.reference.reference_index import ReferenceIndex
from plugins.reference.reference_store import Reference, ReferenceStore
from tests.test_reference_library_page import _Ctx


@pytest.fixture
def page(tmp_path):
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    store = ReferenceStore(directory=tmp_path)
    notes = NoteStore(directory=tmp_path)
    index = ReferenceIndex(db_path=tmp_path / "index.db")
    service = NoteService(store, notes)
    widget = LibraryPage(_Ctx(), store, index, note_service=service)
    yield widget, store, notes, service
    widget.deleteLater()
    index.close()


def _ref(key: str = "doi:1", note: str = "") -> Reference:
    return Reference(ref_key=key, title="论文标题", note=note)


def test_notes_row_shows_source_and_count(page):
    widget, store, notes, _service = page
    store.upsert(_ref())
    first = notes.add("doi:1", "第一条")
    notes.add("doi:1", "第二条")
    widget.reload()
    widget._show_reference(store.get("doi:1"))

    assert widget.notes_btn.text().endswith("2 条")
    assert widget.notes_source_label.text() != ""       # 有多条且未挑 → 提示去挑
    assert widget.notes_btn.isVisibleTo(widget)
    assert first.note_id


def test_single_master_note_is_auto_and_count_is_one(page):
    widget, store, notes, _service = page
    store.upsert(_ref())
    notes.add("doi:1", "唯一一条")
    widget.reload()
    widget._show_reference(store.get("doi:1"))
    assert widget.notes_btn.text().endswith("1 条")


def test_child_note_badge(page):
    widget, store, notes, _service = page
    store.upsert(_ref(note="子库自己写的"))
    notes.add("doi:1", "总库那条")
    widget.reload()
    widget._show_reference(store.get("doi:1"))
    assert "子库" in widget.notes_source_label.text()


def test_inherit_persists_from_page(page):
    widget, store, notes, service = page
    store.upsert(_ref())
    picked = notes.add("doi:1", "挑这条")
    notes.add("doi:1", "别的那条")
    service.set_inherit("doi:1", picked.note_id)

    reopened = ReferenceStore(directory=store.directory)
    assert reopened.get("doi:1").inherit_note_id == picked.note_id
