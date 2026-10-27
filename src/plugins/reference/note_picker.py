# plugins/reference/note_picker.py
"""总库备注挑选面板：顶部筛选框 + 可滚动列表（约 5 条高）+ 当前选中打勾。

用户要求：多条备注不能撑爆界面，所以面板固定高度、内部滚动，最多显示 5 条。
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPushButton, QVBoxLayout)

ROWS = 5            # 可见行数
ROW_HEIGHT = 44     # 每行高度（标签 + 首行摘要 + 时间）


class NotePicker(QDialog):
    """从总库多条备注里挑一条（选中的是 note_id，供子库"跟随"）。"""

    def __init__(self, notes, current_id: str = "", tr=None, parent=None,
                 service=None) -> None:
        super().__init__(parent)
        self._tr = tr or (lambda key, **kwargs: key)
        self._notes = list(notes)
        self._service = service
        self.selected_note_id = current_id
        self.new_note_requested = False
        self.fork_requested = False

        self.setWindowTitle(self._tr("picker.title"))
        self.setMinimumWidth(380)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText(self._tr("picker.filter"))
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(self._apply_filter)
        layout.addWidget(self.filter_edit)

        self.list_widget = QListWidget()
        self.list_widget.setObjectName("notePickerList")
        self.list_widget.setFixedHeight(ROWS * ROW_HEIGHT)
        self.list_widget.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.list_widget.itemDoubleClicked.connect(lambda _item: self._accept_current())
        self.list_widget.currentItemChanged.connect(lambda *_: self._sync_buttons())
        layout.addWidget(self.list_widget)

        self.hint = QLabel()
        self.hint.setObjectName("hintLabel")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)

        actions = QHBoxLayout()
        self.edit_btn = QPushButton(self._tr("picker.edit"))
        self.edit_btn.clicked.connect(self._on_edit_clicked)
        self.pin_btn = QPushButton(self._tr("picker.pin"))
        self.pin_btn.clicked.connect(self._on_pin_clicked)
        self.archive_btn = QPushButton(self._tr("picker.archive"))
        self.archive_btn.clicked.connect(self._on_archive_clicked)
        self.fork_btn = QPushButton(self._tr("picker.fork"))
        self.fork_btn.setToolTip(self._tr("picker.fork_tip"))
        self.fork_btn.clicked.connect(self._on_fork_clicked)
        self.new_btn = QPushButton(self._tr("picker.new_master"))
        self.new_btn.clicked.connect(self._on_new_clicked)
        self.cancel_btn = QPushButton(self._tr("action.cancel"))
        self.cancel_btn.clicked.connect(self.reject)
        self.ok_btn = QPushButton(self._tr("picker.pick"))
        self.ok_btn.setDefault(True)
        self.ok_btn.clicked.connect(self._accept_current)
        actions.addWidget(self.new_btn)
        actions.addWidget(self.edit_btn)
        actions.addWidget(self.pin_btn)
        actions.addWidget(self.archive_btn)
        actions.addWidget(self.fork_btn)
        actions.addStretch(1)
        actions.addWidget(self.cancel_btn)
        actions.addWidget(self.ok_btn)
        layout.addLayout(actions)

        self._reload()
        self._sync_buttons()

    # ---------------- 内部 ----------------
    def _visible_notes(self):
        keyword = self.filter_edit.text().strip().lower()
        if not keyword:
            return self._notes
        return [note for note in self._notes
                if keyword in (note.label or "").lower() or keyword in (note.text or "").lower()]

    def _reload(self) -> None:
        self.list_widget.clear()
        notes = self._visible_notes()
        for note in notes:
            summary = (note.text or "").replace("\n", " ").strip()
            if len(summary) > 46:
                summary = summary[:46] + "…"
            item = QListWidgetItem(f"{note.label or self._tr('picker.untitled')}\n"
                                   f"{note.meta_line()}\n{summary}")
            item.setData(Qt.UserRole, note.note_id)
            item.setToolTip(f"{note.meta_line()}\n{note.updated}\n{note.text}")
            item.setSizeHint(QSize(0, ROW_HEIGHT + 14))     # 多一行文献标识，加高避免挤压
            self.list_widget.addItem(item)
            if note.note_id == self.selected_note_id:
                self.list_widget.setCurrentItem(item)
        if self.list_widget.currentItem() is None and self.list_widget.count():
            self.list_widget.setCurrentRow(0)
        template = self._tr("picker.hint", count=len(notes))
        self.hint.setText(template.replace("{count}", str(len(notes))))

    def _apply_filter(self, _text: str) -> None:
        self._reload()

    def _accept_current(self) -> None:
        item = self.list_widget.currentItem()
        if item is not None:
            self.selected_note_id = item.data(Qt.UserRole)
        self.accept()

    def _on_new_clicked(self) -> None:
        self.new_note_requested = True
        self.accept()

    # ---------------- 管理动作 ----------------
    def _selected_note(self):
        item = self.list_widget.currentItem()
        if item is None or self._service is None:
            return None
        note_id = item.data(Qt.UserRole)
        return next((note for note in self._notes if note.note_id == note_id), None)

    def _refresh_from_service(self) -> None:
        """管理动作后重新拉一遍列表（保持筛选关键字与选中项）。"""
        if self._service is None:
            return
        current = self.selected_note_id
        ref_keys = {note.ref_key for note in self._notes}
        if ref_keys:
            self._notes = self._service.master_notes(next(iter(ref_keys)))
        self.selected_note_id = current
        self._reload()
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        note = self._selected_note()
        has_note = note is not None
        self.edit_btn.setEnabled(has_note)
        self.archive_btn.setEnabled(has_note)
        self.pin_btn.setEnabled(has_note)
        if has_note:
            self.pin_btn.setText(self._tr("picker.unpin") if note.pinned else self._tr("picker.pin"))

    def _on_edit_clicked(self) -> None:
        note = self._selected_note()
        if note is None:
            return
        from plugins.reference.note_editor import NoteEditor

        editor = NoteEditor(tr=self._tr, label=note.label, text=note.text,
                            pinned=note.pinned, parent=self)
        if editor.exec() != editor.Accepted:
            return
        self._service.update_master_note(note.note_id, text=editor.text,
                                         label=editor.label, pinned=editor.pinned)
        self.selected_note_id = note.note_id
        self._refresh_from_service()

    def _on_pin_clicked(self) -> None:
        note = self._selected_note()
        if note is None:
            return
        self._service.set_pinned(note.note_id, not note.pinned)
        self.selected_note_id = note.note_id
        self._refresh_from_service()

    def _on_archive_clicked(self) -> None:
        note = self._selected_note()
        if note is None:
            return
        self._service.archive_master_note(note.note_id)
        self.selected_note_id = ""
        self._refresh_from_service()

    def _on_fork_clicked(self) -> None:
        """把选中的那条复制成子库自己的备注（之后各改各的）。"""
        self._accept_current()
        self.fork_requested = True
