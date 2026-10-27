# plugins/reference/page_panel.py
"""参考文献助手右栏（400 px）：只读详情 + 「编辑」切表单。

从 `library_page` 拆出，保持单文件在阈值内；所有权仍归页面（函数把控件挂到 host 上）。
"""
from __future__ import annotations

import uuid

from plugins.reference.reference_store import Reference

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QStackedWidget, QTextEdit, QVBoxLayout, QWidget)

PANEL_WIDTH = 400
#: 详情行的字段顺序与 i18n 键映射（`cite_label` 的文案是「编号」）
READ_FIELDS = ("cite_label", "title", "authors", "year", "container", "doi", "type", "tags")
FIELD_LABEL_KEYS = {"cite_label": "field.label"}


def retranslate_panel(host) -> None:
    """切换界面语言后重译右栏：字段行标签、备注标题、只读/编辑占位符。"""
    for key, label in getattr(host, "read_row_labels", {}).items():
        label.setText(host.tr(FIELD_LABEL_KEYS.get(key, f"field.{key}")))
    for key, label in getattr(host, "edit_row_labels", {}).items():
        label.setText(host.tr(key))
    for widget, key in ((getattr(host, "label_edit", None), "field.label"),
                        (getattr(host, "title_edit", None), "field.title"),
                        (getattr(host, "authors_edit", None), "field.authors"),
                        (getattr(host, "year_edit", None), "field.year"),
                        (getattr(host, "container_edit", None), "field.container"),
                        (getattr(host, "doi_edit", None), "field.doi"),
                        (getattr(host, "type_edit", None), "field.type"),
                        (getattr(host, "tags_edit", None), "field.tags"),
                        (getattr(host, "note_edit", None), "field.note")):
        if widget is not None:
            widget.setPlaceholderText(host.tr(key))
    if hasattr(host, "read_note_title"):
        host.read_note_title.setText(host.tr("field.note"))
    if hasattr(host, "starred_check"):
        host.starred_check.setText(host.tr("field.starred"))
    for name, key in (("bind_btn", "action.bind_orphan"), ("edit_btn", "action.edit")):
        button = getattr(host, name, None)
        if button is not None:
            button.setText(host.tr(key))


def build_panel(host) -> QWidget:
    """构建右栏并把它需要的控件挂到 `host` 上（页面类保持薄）。"""
    panel = QWidget()
    host.panel_widget = panel          # 折叠时要隐藏的是**整块**右栏，不只是里面的 stack
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
    #: 字段 → 左侧标签控件；切换界面语言时要重新 setText（否则一直是建面板时的语言）
    host.read_row_labels: dict[str, QLabel] = {}
    for key in READ_FIELDS:
        value = QLabel("")
        value.setWordWrap(True)
        value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        host.read_labels[key] = value
        label = QLabel(host.tr(FIELD_LABEL_KEYS.get(key, f"field.{key}")))
        host.read_row_labels[key] = label
        form_read.addRow(label, value)
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
    host.edit_row_labels: dict[str, QLabel] = {}
    for widget, key in ((host.label_edit, "field.label"), (host.title_edit, "field.title"),
                        (host.authors_edit, "field.authors"), (host.year_edit, "field.year"),
                        (host.container_edit, "field.container"), (host.doi_edit, "field.doi"),
                        (host.type_edit, "field.type"), (host.tags_edit, "field.tags")):
        widget.setPlaceholderText(host.tr(key))
        label = QLabel(host.tr(key))
        host.edit_row_labels[key] = label
        form.addRow(label, widget)
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


class PanelChromeMixin:
    """工具栏上的「当前文件」与右栏折叠（从 library_page 抽出控制行数）。"""

    def set_current_document(self, name: str) -> None:
        """界面上的"当前文件"（Word 里切换文档时由监听信号推过来）。"""
        self.current_document = name or ""
        self._refresh_doc_label()

    def toggle_panel(self, checked: bool | None = None) -> None:
        """显示/隐藏整块右侧详情栏；隐藏后表格自动占满剩余宽度。"""
        panel = getattr(self, "panel_widget", None) or getattr(self, "panel_stack", None)
        if panel is None or not hasattr(self, "_splitter"):
            return
        if self.panel_toggle.isChecked():
            panel.setVisible(True)
            sizes = self._splitter.sizes()
            restore = getattr(self, "_panel_width", 0) or PANEL_WIDTH
            self._splitter.setSizes([max(sizes[0], 320), restore])
        else:
            sizes = self._splitter.sizes()
            self._panel_width = sizes[1] or PANEL_WIDTH
            panel.setVisible(False)
        self._update_panel_toggle()

    def _update_panel_toggle(self) -> None:
        """按钮文字说清"点它会隐藏/显示详情"，避免只有个看不懂的短词。"""
        button = getattr(self, "panel_toggle", None)
        if button is None:
            return
        key = "panel.hide" if button.isChecked() else "panel.show"
        button.setText(self.tr(key))
        button.setToolTip(self.tr("panel.toggle.tip"))

    def _refresh_doc_label(self) -> None:
        """显示当前文件：过长时中间省略（配合最小宽度与 tooltip，避免"显示不全"）。"""
        name = self.current_document or "—"
        metrics = self.doc_label.fontMetrics()
        available = max(self.doc_label.width() - 8, 120)
        shown = metrics.elidedText(name, Qt.ElideMiddle, available)
        template = self.tr("toolbar.current_doc")
        self.doc_label.setText(template.replace("{name}", shown))
        self.doc_label.setToolTip(name)

    def _collect(self) -> Reference:
        key = self._current_key or f"manual:{uuid.uuid4().hex[:8]}"
        return Reference(
            ref_key=key,
            provider="" if self._current_key else "manual",
            cite_label=self.label_edit.text().strip(),
            title=self.title_edit.text().strip(),
            authors=self.authors_edit.text().strip(),
            year=self.year_edit.text().strip(),
            container=self.container_edit.text().strip(),
            doi=self.doi_edit.text().strip(),
            type=self.type_edit.text().strip(),
            tags=[tag.strip() for tag in self.tags_edit.text().split(";") if tag.strip()],
            starred=self.starred_check.isChecked(),
            note=self.note_edit.toPlainText(),
        )

