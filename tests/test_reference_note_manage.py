"""管理动作（编辑/钉住/归档）用假服务测面板行为。"""
from __future__ import annotations

import pytest

from plugins.reference.notes import Note


class _FakeService:
    def __init__(self, notes):
        self._notes = list(notes)
        self.updated: list[tuple] = []
        self.pinned: list[tuple] = []
        self.archived: list[str] = []

    def master_notes(self, ref_key):
        return list(self._notes)

    def update_master_note(self, note_id, text=None, label=None, pinned=None):
        self.updated.append((note_id, text, label, pinned))
        for note in self._notes:
            if note.note_id == note_id:
                note.text = text or note.text
                note.label = label or note.label
                note.pinned = bool(pinned)
        return None

    def set_pinned(self, note_id, pinned):
        self.pinned.append((note_id, pinned))
        for note in self._notes:
            if note.note_id == note_id:
                note.pinned = pinned

    def archive_master_note(self, note_id):
        self.archived.append(note_id)
        self._notes = [note for note in self._notes if note.note_id != note_id]


def _note(note_id: str, text: str, label: str = "", pinned: bool = False) -> Note:
    return Note(note_id=note_id, ref_key="doi:1", text=text, label=label, pinned=pinned,
                updated="2026-01-01 00:00:00")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def panel(qapp):
    from plugins.reference.note_picker import NotePicker

    notes = [_note("n1", "第一条", "引用理由"), _note("n2", "第二条", "方法学")]
    service = _FakeService(notes)
    dialog = NotePicker(notes, current_id="n1", tr=lambda key, **kwargs: key, service=service)
    yield dialog, service, notes
    dialog.deleteLater()


def test_pin_toggles_selected_note(panel):
    dialog, service, _notes = panel
    dialog.list_widget.setCurrentRow(1)
    dialog._on_pin_clicked()
    assert service.pinned == [("n2", True)]
    assert dialog.pin_btn.text() == "picker.unpin"


def test_archive_removes_note_from_list(panel):
    dialog, service, _notes = panel
    dialog.list_widget.setCurrentRow(0)
    dialog._on_archive_clicked()
    assert service.archived == ["n1"]
    assert dialog.list_widget.count() == 1


def test_edit_pushes_values(panel, monkeypatch):
    dialog, service, _notes = panel
    dialog.list_widget.setCurrentRow(0)

    class _FakeEditor:
        Accepted = 1

        def __init__(self, *args, **kwargs):
            pass

        def exec(self):
            return 1

        label = "新标签"
        text = "新内容"
        pinned = True

    monkeypatch.setattr("plugins.reference.note_editor.NoteEditor", _FakeEditor)
    dialog._on_edit_clicked()
    assert service.updated == [("n1", "新内容", "新标签", True)]


def test_buttons_disabled_without_service(qapp):
    from plugins.reference.note_picker import NotePicker

    dialog = NotePicker([_note("n1", "x")], tr=lambda key, **kwargs: key)
    assert dialog.edit_btn.isEnabled() is False
    assert dialog.archive_btn.isEnabled() is False
    dialog.deleteLater()
