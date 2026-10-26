# tests/test_reference_overlay_tabs.py
"""组合引用（[1,2,3]）用编号标签切换：一次只显示一张卡片，草稿保留。

方案要求：不要一次展开多张可编辑卡片；切换引用时要保留已输入内容。
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


def test_only_one_card_visible_for_combined_citation(overlay):
    from PySide6.QtWidgets import QApplication

    overlay.show_entries(_entries(), (10, 10))
    QApplication.processEvents()

    assert len(overlay._cards) == 3, "卡片都建好（这样草稿不会丢）"
    visible = [card for card in overlay._cards if card.isVisible()]
    assert len(visible) == 1, "但只显示一张"
    assert overlay._cards[0].isVisible()


def test_tabs_switch_and_keep_draft(overlay):
    from PySide6.QtWidgets import QApplication

    overlay.show_entries(_entries(), (10, 10))
    QApplication.processEvents()
    overlay._cards[0].note_edit.setPlainText("第一条草稿")

    overlay._select_card(1)
    QApplication.processEvents()

    assert overlay._cards[1].isVisible() and not overlay._cards[0].isVisible()
    assert overlay._cards[0].note_edit.toPlainText() == "第一条草稿", "切换后草稿仍在"

    overlay._select_card(0)
    assert overlay._cards[0].isVisible()


def test_single_entry_has_no_tabs(overlay):
    from PySide6.QtWidgets import QApplication

    overlay.show_entries(_entries()[:1], (10, 10))
    QApplication.processEvents()

    assert overlay._tabs_layout.count() == 0, "单条引用不需要切换标签"


def test_switching_flushes_pending_draft_before_rebuild(overlay):
    from PySide6.QtWidgets import QApplication

    overlay.show_entries(_entries(), (10, 10))
    QApplication.processEvents()
    overlay._cards[0].note_edit.setPlainText("点下一个引用前敲的字")

    overlay._rebuild(_entries())          # 模拟切到另一组引用
    QApplication.processEvents()

    assert overlay._store.get("doi:1").note == "点下一个引用前敲的字"
