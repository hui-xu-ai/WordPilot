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
