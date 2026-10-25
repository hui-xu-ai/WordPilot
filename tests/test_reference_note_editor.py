# tests/test_reference_note_editor.py
"""总库备注编辑框：标签/正文/钉住 + 空内容不通过。"""
from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def editor(qapp):
    from plugins.reference.note_editor import NoteEditor

    dialog = NoteEditor(tr=lambda key, **kwargs: key, label="引用理由", text="原内容", pinned=True)
    yield dialog
    dialog.deleteLater()


def test_reads_initial_values(editor):
    assert editor.label == "引用理由"
    assert editor.text == "原内容"
    assert editor.pinned is True


def test_empty_text_is_rejected(qapp):
    from plugins.reference.note_editor import NoteEditor

    dialog = NoteEditor(tr=lambda key, **kwargs: key)
    dialog.text_edit.setPlainText("   ")
    dialog._on_ok()
    assert dialog.result() != 1      # 1 = QDialog.Accepted
    dialog.deleteLater()
