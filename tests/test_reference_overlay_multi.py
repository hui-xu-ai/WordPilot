# tests/test_reference_overlay_multi.py
"""组合引用：**多张卡片直接堆叠显示**（用户反馈编号标签切换太麻烦，已撤回）。

保留的行为：卡片都在、备注可分别编辑、重建前草稿会落盘。
"""
from __future__ import annotations

import pytest


@pytest.fixture
def overlay(tmp_path):
    from PySide6.QtWidgets import QApplication
    from plugins.reference.notes import NoteService, NoteStore
    from plugins.reference.overlay import ReferenceOverlay
    from plugins.reference.reference_store import Reference, ReferenceStore

    app = QApplication.instance() or QApplication([])
    store = ReferenceStore(directory=tmp_path / "库A")
    for index in (1, 2, 3):
        store.upsert(Reference(ref_key=f"doi:{index}", title=f"文献{index}"))
    widget = ReferenceOverlay(store, tr=lambda key, **kw: key,
                              note_service=NoteService(store, NoteStore(directory=tmp_path)))
    yield widget
    widget.deleteLater()


def _entries():
    from plugins.reference.overlay import OverlayEntry

    return [OverlayEntry(anchor_key=f"doi:{i}", label=str(i), title=f"文献{i}",
                         doc_name="论文.docx", force_edit=True) for i in (1, 2, 3)]


def test_all_cards_are_visible(overlay):
    """组合引用要一次看到全部卡片，不用点标签切换。"""
    from PySide6.QtWidgets import QApplication

    overlay.show_entries(_entries(), (10, 10))
    QApplication.processEvents()

    assert len(overlay._cards) == 3
    assert [card.isVisible() for card in overlay._cards] == [True, True, True]
    assert overlay._tabs_layout.count() == 0 if hasattr(overlay, "_tabs_layout") else True


def test_each_card_keeps_its_own_note(overlay):
    from PySide6.QtWidgets import QApplication

    overlay.show_entries(_entries(), (10, 10))
    QApplication.processEvents()
    overlay._cards[0].note_edit.setPlainText("第一条")
    overlay._cards[2].note_edit.setPlainText("第三条")

    overlay._cards[0].flush()
    overlay._cards[2].flush()

    assert overlay._store.get("doi:1").note == "第一条"
    assert overlay._store.get("doi:3").note == "第三条"


def test_rebuild_flushes_pending_drafts(overlay):
    from PySide6.QtWidgets import QApplication

    overlay.show_entries(_entries(), (10, 10))
    QApplication.processEvents()
    overlay._cards[1].note_edit.setPlainText("切换前敲的字")

    overlay._rebuild(_entries())
    QApplication.processEvents()

    assert overlay._store.get("doi:2").note == "切换前敲的字"
