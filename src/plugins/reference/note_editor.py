# plugins/reference/note_editor.py
"""新建 / 编辑一条**总库备注**：短标签 + 正文 + 是否钉住。"""
from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QTextEdit, QVBoxLayout)


class NoteEditor(QDialog):
    """总库备注的编辑框（子库不直接编辑总库条目，改总库要显式打开这里）。"""

    def __init__(self, tr=None, label: str = "", text: str = "", pinned: bool = False,
                 parent=None) -> None:
        super().__init__(parent)
        self._tr = tr or (lambda key, **kwargs: key)
        self.setWindowTitle(self._tr("editor.title"))
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        self.label_edit = QLineEdit(label)
        self.label_edit.setPlaceholderText(self._tr("editor.label_placeholder"))
        layout.addWidget(QLabel(self._tr("editor.label")))
        layout.addWidget(self.label_edit)

        self.text_edit = QTextEdit(text)
        self.text_edit.setPlaceholderText(self._tr("editor.text_placeholder"))
        self.text_edit.setMinimumHeight(120)
        layout.addWidget(QLabel(self._tr("editor.text")))
        layout.addWidget(self.text_edit, 1)

        self.pinned_check = QCheckBox(self._tr("editor.pinned"))
        self.pinned_check.setChecked(bool(pinned))
        layout.addWidget(self.pinned_check)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.cancel_btn = QPushButton(self._tr("action.cancel"))
        self.cancel_btn.clicked.connect(self.reject)
        self.ok_btn = QPushButton(self._tr("action.save"))
        self.ok_btn.setDefault(True)
        self.ok_btn.clicked.connect(self._on_ok)
        actions.addWidget(self.cancel_btn)
        actions.addWidget(self.ok_btn)
        layout.addLayout(actions)

    # ---------------- 结果 ----------------
    @property
    def label(self) -> str:
        return self.label_edit.text().strip()

    @property
    def text(self) -> str:
        return self.text_edit.toPlainText().strip()

    @property
    def pinned(self) -> bool:
        return self.pinned_check.isChecked()

    def _on_ok(self) -> None:
        if not self.text:
            self.text_edit.setFocus()
            self.text_edit.setPlaceholderText(self._tr("editor.required"))
            return
        self.accept()
