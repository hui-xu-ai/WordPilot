# plugins/reference/bind_dialog.py
"""把"未绑定的引用锚点"绑到库里已有文献的选择框（悬浮窗里的「绑定已有文献」）。"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QVBoxLayout)

if TYPE_CHECKING:
    from plugins.reference.reference_store import ReferenceStore


class ReferencePicker(QDialog):
    """搜索 + 单选一条文献；`selected_key` 为空表示用户取消。"""

    def __init__(self, store: "ReferenceStore", tr, parent=None) -> None:
        super().__init__(parent)
        self._store = store
        self._tr = tr
        self.selected_key = ""
        self.setWindowTitle(tr("bind.title"))
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(8)

        layout.addWidget(QLabel(tr("bind.hint")))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(tr("bind.search"))
        self.search_edit.textChanged.connect(self._refresh)
        layout.addWidget(self.search_edit)

        self.list = QListWidget()
        layout.addWidget(self.list, 1)
        self.list.itemDoubleClicked.connect(lambda _item: self._accept_current())

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText(tr("bind.ok"))
        self.buttons.button(QDialogButtonBox.Cancel).setText(tr("bind.cancel"))
        self.buttons.accepted.connect(self._accept_current)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self._refresh(self.search_edit.text())

    def _refresh(self, text: str) -> None:
        needle = (text or "").strip().lower()
        self.list.clear()
        for ref in sorted(self._store.all(), key=lambda item: item.title or item.ref_key):
            haystack = f"{ref.title} {ref.authors} {ref.year} {ref.container} {ref.doi}".lower()
            if needle and needle not in haystack:
                continue
            label = ref.title or ref.ref_key
            item = QListWidgetItem(f"[{ref.cite_label}] {label}" if ref.cite_label else label)
            item.setData(Qt.UserRole, ref.ref_key)
            item.setToolTip(ref.ref_key)
            self.list.addItem(item)
        if self.list.count() == 0:
            self.list.addItem(self._tr("bind.empty"))
            self.list.item(0).setFlags(Qt.NoItemFlags)

    def _accept_current(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        key = item.data(Qt.UserRole)
        if not key:
            return
        self.selected_key = key
        self.accept()
