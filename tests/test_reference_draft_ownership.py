# tests/test_reference_draft_ownership.py
"""R4/R5/R6：切库要收尾草稿、切引用前要落盘、后台不直接动页面。

审查复现：
- R4 编辑 A 条目 → 切到空 B → 点保存，B 多出 A 条目的副本；
- R5 浮层输入后 800ms 内点下一个引用，重建把定时器和文字一起丢掉；
- R6 扫描线程里直接调用 LibraryPage.rebind（操作 QComboBox/表格）。
"""
from __future__ import annotations

import pytest


class _Ctx:
    def tr(self, key, /, **params):
        return key

    def get_service(self, name):
        return None


@pytest.fixture
def page(tmp_path):
    from PySide6.QtWidgets import QApplication
    from plugins.reference.library_page import LibraryPage
    from plugins.reference.notes import NoteService, NoteStore
    from plugins.reference.reference_index import ReferenceIndex
    from plugins.reference.reference_store import Reference, ReferenceStore

    app = QApplication.instance() or QApplication([])
    store = ReferenceStore(directory=tmp_path / "库A")
    store.upsert(Reference(ref_key="doi:1", title="A 的文献"))
    index = ReferenceIndex(db_path=tmp_path / "库A" / "index.db")
    widget = LibraryPage(_Ctx(), store, index,
                         note_service=NoteService(store, NoteStore(directory=tmp_path)))
    yield widget, store
    index.close()
    widget.deleteLater()


def test_switch_finishes_editing(page, monkeypatch):
    """R4：切库前必须收尾编辑并清掉选中项，避免旧草稿写进新库。"""
    widget, store = page
    widget._current_key = "doi:1"
    widget._selected_key = "doi:1"

    called: list[str] = []
    monkeypatch.setattr(widget, "_on_switch_library", lambda name: called.append(name),
                        raising=False)
    widget._active_library = "库A"
    widget._on_library_changed("库B")

    assert called == ["库B"]
    assert widget._current_key == "", "切库后不能还挂着旧条目"
    assert widget._selected_key == ""


def test_rebind_clears_editing_state(page):
    """R4：扫描/删除导致的 rebind 也要清状态（不走下拉信号）。"""
    widget, store = page
    widget._current_key = "doi:1"
    widget._selected_key = "doi:1"

    widget.rebind(store, widget._index, widget._note_service, "库B")

    assert widget._current_key == ""
    assert widget._selected_key == ""


def test_overlay_flushes_draft_before_rebuild(tmp_path):
    """R5：重建卡片前先把未保存草稿落盘（否则切引用丢字）。"""
    from PySide6.QtWidgets import QApplication
    from plugins.reference.overlay import OverlayEntry, ReferenceOverlay
    from plugins.reference.notes import NoteService, NoteStore
    from plugins.reference.reference_store import Reference, ReferenceStore

    app = QApplication.instance() or QApplication([])
    store = ReferenceStore(directory=tmp_path / "库A")
    store.upsert(Reference(ref_key="doi:1", title="A"))
    overlay = ReferenceOverlay(store, tr=lambda key, **kw: key,
                               note_service=NoteService(store, NoteStore(directory=tmp_path)))
    entry = OverlayEntry(anchor_key="doi:1", label="1", doc_name="论文A.docx", note="",
                         force_edit=True)   # 有编辑框才会挂 800ms 自动保存
    overlay.show_entries([entry], (10, 10))
    app.processEvents()

    card = overlay._cards[0]
    card.note_edit.setPlainText("刚敲的内容")
    overlay._rebuild([entry])          # 模拟"点下一个引用"

    assert store.get("doi:1").note == "刚敲的内容", "重建前应已落盘"
