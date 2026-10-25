# plugins/reference/note_picker.py
"""总库备注挑选面板：顶部筛选框 + 可滚动列表（约 5 条高）+ 当前选中打勾。

用户要求：多条备注不能撑爆界面，所以面板固定高度、内部滚动，最多显示 5 条。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPushButton, QVBoxLayout)

ROWS = 5            # 可见行数
ROW_HEIGHT = 44     # 每行高度（标签 + 首行摘要 + 时间）


class NotePicker(QDialog):
    """从总库多条备注里挑一条（选中的是 note_id，供子库"跟随"）。"""

    def __init__(self, notes, current_id: str = "", tr=None, parent=None) -> None:
        super().__init__(parent)
        self._tr = tr or (lambda key, **kwargs: key)
        self._notes = list(notes)
        self.selected_note_id = current_id
        self.new_note_requested = False

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
        layout.addWidget(self.list_widget)

        self.hint = QLabel()
        self.hint.setObjectName("hintLabel")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)

        actions = QHBoxLayout()
        self.new_btn = QPushButton(self._tr("picker.new_master"))
        self.new_btn.clicked.connect(self._on_new_clicked)
        self.cancel_btn = QPushButton(self._tr("action.cancel"))
        self.cancel_btn.clicked.connect(self.reject)
        self.ok_btn = QPushButton(self._tr("picker.pick"))
        self.ok_btn.setDefault(True)
        self.ok_btn.clicked.connect(self._accept_current)
        actions.addWidget(self.new_btn)
        actions.addStretch(1)
        actions.addWidget(self.cancel_btn)
        actions.addWidget(self.ok_btn)
        layout.addLayout(actions)

        self._reload()

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
            item = QListWidgetItem(f"{note.label or self._tr('picker.untitled')}\n{summary}")
            item.setData(Qt.UserRole, note.note_id)
            item.setToolTip(f"{note.updated}\n{note.text}")
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
