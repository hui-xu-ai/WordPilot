# plugins/reference/page_panel.py
"""参考文献助手右栏（400 px）：只读详情 + 「编辑」切表单。

从 `library_page` 拆出，保持单文件在阈值内；所有权仍归页面（函数把控件挂到 host 上）。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QStackedWidget, QTextEdit, QVBoxLayout, QWidget)

PANEL_WIDTH = 400
#: 详情行的字段顺序与 i18n 键映射（`cite_label` 的文案是「编号」）
READ_FIELDS = ("cite_label", "title", "authors", "year", "container", "doi", "type", "tags")
FIELD_LABEL_KEYS = {"cite_label": "field.label"}


def build_panel(host) -> QWidget:
    """构建右栏并把它需要的控件挂到 `host` 上（页面类保持薄）。"""
    panel = QWidget()
    panel.setMinimumWidth(PANEL_WIDTH)
    panel.setMaximumWidth(PANEL_WIDTH)
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(16, 0, 0, 0)
    layout.setSpacing(8)

    host.panel_title = QLabel("")
    host.panel_title.setObjectName("cardTitle")
    host.panel_title.setWordWrap(True)
    layout.addWidget(host.panel_title)

    host.panel_stack = QStackedWidget()

    # ---- 只读视图 ----
    read = QWidget()
    read_layout = QVBoxLayout(read)
    read_layout.setContentsMargins(0, 0, 0, 0)
    form_read = QFormLayout()
    form_read.setSpacing(8)
    host.read_labels: dict[str, QLabel] = {}
    for key in READ_FIELDS:
        value = QLabel("")
        value.setWordWrap(True)
        value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        host.read_labels[key] = value
        form_read.addRow(QLabel(host.tr(FIELD_LABEL_KEYS.get(key, f"field.{key}"))), value)
    read_layout.addLayout(form_read)

    notes_row = QHBoxLayout()
    notes_row.setContentsMargins(0, 0, 0, 0)
    notes_row.setSpacing(6)
    host.notes_source_label = QLabel("")
    host.notes_source_label.setObjectName("hintLabel")
    notes_row.addWidget(host.notes_source_label, 1)
    host.notes_btn = QPushButton("")
    host.notes_btn.setObjectName("referenceChip")
    host.notes_btn.setCursor(Qt.PointingHandCursor)
    host.notes_btn.clicked.connect(host.open_notes)
    notes_row.addWidget(host.notes_btn, 0)
    read_layout.addLayout(notes_row)

    host.read_note_title = QLabel(host.tr("field.note"))
    host.read_note_title.setObjectName("hintLabel")
    read_layout.addWidget(host.read_note_title)
    host.read_note = QLabel("")
    host.read_note.setWordWrap(True)
    host.read_note.setTextInteractionFlags(Qt.TextSelectableByMouse)
    host.read_note.setMinimumHeight(80)
    host.read_note.setAlignment(Qt.AlignTop | Qt.AlignLeft)
    read_layout.addWidget(host.read_note, 1)

    actions = QHBoxLayout()
    actions.addStretch(1)
    host.bind_btn = QPushButton(host.tr("action.bind_orphan"))
    host.bind_btn.clicked.connect(host.bind_orphan)
    host.edit_btn = QPushButton(host.tr("action.edit"))
    host.edit_btn.clicked.connect(host.start_edit)
    actions.addWidget(host.bind_btn)
    actions.addWidget(host.edit_btn)
    read_layout.addLayout(actions)
    host.panel_stack.addWidget(read)

    # ---- 编辑视图 ----
    edit = QWidget()
    edit_layout = QVBoxLayout(edit)
    edit_layout.setContentsMargins(0, 0, 0, 0)
    form = QFormLayout()
    form.setSpacing(6)
    host.key_label = QLabel("")
    host.key_label.setObjectName("hintLabel")
    host.key_label.setWordWrap(True)
    form.addRow(host.tr("field.key"), host.key_label)

    host.label_edit = QLineEdit()
    host.title_edit = QLineEdit()
    host.authors_edit = QLineEdit()
    host.year_edit = QLineEdit()
    host.container_edit = QLineEdit()
    host.doi_edit = QLineEdit()
    host.type_edit = QLineEdit()
    host.tags_edit = QLineEdit()
    for widget, key in ((host.label_edit, "field.label"), (host.title_edit, "field.title"),
                        (host.authors_edit, "field.authors"), (host.year_edit, "field.year"),
                        (host.container_edit, "field.container"), (host.doi_edit, "field.doi"),
                        (host.type_edit, "field.type"), (host.tags_edit, "field.tags")):
        widget.setPlaceholderText(host.tr(key))
        form.addRow(host.tr(key), widget)
    host.starred_check = QCheckBox(host.tr("field.starred"))
    form.addRow("", host.starred_check)
    edit_layout.addLayout(form)

    host.note_edit = QTextEdit()
    host.note_edit.setPlaceholderText(host.tr("field.note"))
    host.note_edit.setMinimumHeight(90)
    edit_layout.addWidget(host.note_edit, 1)

    edit_actions = QHBoxLayout()
    edit_actions.addStretch(1)
    host.cancel_btn = QPushButton(host.tr("action.cancel"))
    host.cancel_btn.clicked.connect(host.cancel_edit)
    host.save_btn = QPushButton(host.tr("action.save"))
    host.save_btn.setProperty("accent", True)
    host.save_btn.clicked.connect(host.save_current)
    host.delete_btn = QPushButton(host.tr("action.delete"))
    host.delete_btn.clicked.connect(host.delete_current)
    edit_actions.addWidget(host.delete_btn)
    edit_actions.addWidget(host.cancel_btn)
    edit_actions.addWidget(host.save_btn)
    edit_layout.addLayout(edit_actions)
    host.panel_stack.addWidget(edit)

    layout.addWidget(host.panel_stack, 1)
    return panel


class PanelEditMixin:
    """条目编辑面板的进入/退出（从 library_page 抽出控制文件行数）。"""

    def cancel_edit(self) -> None:
        self.panel_stack.setCurrentIndex(0)

    def start_new(self) -> None:
        self._mode = "refs"
        action = self.filter_actions.get("all")
        if action is not None:
            action.setChecked(True)
            self._only_checked("all")
        self._current_key = ""
        self._orphan = None
        self._refresh_table()
        for widget in (self.label_edit, self.title_edit, self.authors_edit, self.year_edit,
                       self.container_edit, self.doi_edit, self.type_edit, self.tags_edit):
            widget.clear()
        self.starred_check.setChecked(False)
        self.note_edit.clear()
        self.key_label.setText(self.tr("new.hint"))
        self.panel_title.setText(self.tr("panel.new"))
        self.panel_stack.setCurrentIndex(1)
        self.title_edit.setFocus()

