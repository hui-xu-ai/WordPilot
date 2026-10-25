# tests/test_reference_note_picker.py
"""总库备注挑选面板：筛选、5 条高、选中返回 note_id、可请求新建。"""
from __future__ import annotations

import pytest

from plugins.reference.notes import Note


def _note(note_id: str, text: str, label: str = "", updated: str = "2026-01-01 00:00:00") -> Note:
    return Note(note_id=note_id, ref_key="doi:1", text=text, label=label, updated=updated)


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def picker(qapp):
    from plugins.reference.note_picker import NotePicker

    notes = [_note("n1", "第一条备注内容", "引用理由"), _note("n2", "第二条备注内容", "方法学")]
    dialog = NotePicker(notes, current_id="n1", tr=lambda key, **kwargs: f"{key} {kwargs}" if kwargs else key)
    yield dialog
    dialog.deleteLater()


def test_lists_visible_rows_height(picker):
    """固定约 5 行高，内部滚动，不会撑爆悬浮窗。"""
    from plugins.reference.note_picker import ROWS, ROW_HEIGHT

    assert picker.list_widget.height() == ROWS * ROW_HEIGHT
    assert picker.list_widget.count() == 2


def test_filter_narrows_list(picker):
    picker.filter_edit.setText("方法学")
    assert picker.list_widget.count() == 1
    picker.filter_edit.setText("")
    assert picker.list_widget.count() == 2


def test_selection_returns_note_id(picker):
    picker.list_widget.setCurrentRow(1)
    picker._accept_current()
    assert picker.selected_note_id == "n2"


def test_current_note_is_preselected(picker):
    assert picker.list_widget.currentItem() is not None
    assert picker.list_widget.currentItem().data(0x0100) == "n1"      # Qt.UserRole


def test_new_note_requested_flag(picker):
    picker._on_new_clicked()
    assert picker.new_note_requested is True


def test_empty_notes_hint(picker):
    picker.filter_edit.setText("不存在的关键词")
    assert picker.list_widget.count() == 0
    assert "0" in picker.hint.text()