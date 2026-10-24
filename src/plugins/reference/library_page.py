# plugins/reference/library_page.py
"""参考文献助手主页面：管理**整个文献库**（浏览/搜索/编辑/导入导出/孤立锚点）。

设计取舍：
- 表格 + 右侧编辑面板，一次只编辑一条（避免大表单），编辑完写回 CSV；
- 所有写入走 `ReferenceStore`（原子写 + 备份 + 外部替换检测），页面本身不碰文件；
- 任何操作失败都在页面顶部状态条提示，不弹模态框打断。
"""
from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QFileDialog, QFormLayout, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPushButton, QSplitter, QTableWidget,
                               QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget)

from plugins.reference.reference_store import (Reference, ReferenceStore,
                                               ReferenceStoreError)

if TYPE_CHECKING:
    from core.sdk.context import PluginContext
    from plugins.reference.reference_index import ReferenceIndex

logger = logging.getLogger(__name__)

PLUGIN_ID = "reference"
_TABLE_COLUMNS = ("cite_label", "title", "authors", "year", "container", "note", "tags")


class LibraryPage(QWidget):
    """文献库管理页。"""

    def __init__(self, ctx: "PluginContext", store: ReferenceStore,
                 index: "ReferenceIndex", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._store = store
        self._index = index
        self._current_key = ""
        self._build_ui()
        self.reload()

    # ---------------- 文案 ----------------
    def tr(self, key: str, /, **params) -> str:
        """`ctx.tr()` **已经**自动加 `<plugin_id>.` 前缀，这里绝不能再加一次。

        曾经写成 `f"{PLUGIN_ID}.{key}"` ⇒ 界面全是 `reference.reference.page_label`
        这类键名（用户看到的就是一屏拉丁字母），属于低级错误，测试已钉住。
        """
        try:
            return self._ctx.tr(key, **params)
        except Exception:
            return key

    # ---------------- 构建界面 ----------------
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        self.title_label = QLabel()
        self.title_label.setObjectName("cardTitle")
        layout.addWidget(self.title_label)

        top = QHBoxLayout()
        top.setSpacing(8)
        self.search_edit = QLineEdit()
        self.search_edit.textChanged.connect(self._apply_filter)
        top.addWidget(self.search_edit, 1)
        self.new_btn = QPushButton()
        self.import_btn = QPushButton()
        self.export_btn = QPushButton()
        self.open_dir_btn = QPushButton()
        for button in (self.new_btn, self.import_btn, self.export_btn, self.open_dir_btn):
            top.addWidget(button)
        self.new_btn.clicked.connect(self.start_new)
        self.import_btn.clicked.connect(self.import_csv)
        self.export_btn.clicked.connect(self.export_csv)
        self.open_dir_btn.clicked.connect(self.open_directory)
        layout.addLayout(top)

        self.status_label = QLabel("")
        self.status_label.setObjectName("hintLabel")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        splitter = QSplitter(Qt.Horizontal)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, len(_TABLE_COLUMNS))
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        left_layout.addWidget(self.table, 1)

        self.orphan_label = QLabel()
        self.orphan_label.setObjectName("hintLabel")
        left_layout.addWidget(self.orphan_label)
        self.orphan_list = QListWidget()
        self.orphan_list.setMaximumHeight(110)
        left_layout.addWidget(self.orphan_list)
        self.bind_orphan_btn = QPushButton()
        self.bind_orphan_btn.clicked.connect(self.bind_orphan)
        left_layout.addWidget(self.bind_orphan_btn)
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(12, 0, 0, 0)
        form = QFormLayout()
        form.setSpacing(8)
        self.key_label = QLabel("")
        self.key_label.setObjectName("hintLabel")
        form.addRow(self.tr("field.key"), self.key_label)
        self.label_edit = QLineEdit()
        self.title_edit = QLineEdit()
        self.authors_edit = QLineEdit()
        self.year_edit = QLineEdit()
        self.container_edit = QLineEdit()
        self.doi_edit = QLineEdit()
        self.type_edit = QLineEdit()
        self.tags_edit = QLineEdit()
        for widget, key in ((self.label_edit, "field.label"), (self.title_edit, "field.title"),
                            (self.authors_edit, "field.authors"), (self.year_edit, "field.year"),
                            (self.container_edit, "field.container"), (self.doi_edit, "field.doi"),
                            (self.type_edit, "field.type"), (self.tags_edit, "field.tags")):
            widget.setPlaceholderText(self.tr(key))
            form.addRow(self.tr(key), widget)
        self.starred_check = QCheckBox(self.tr("field.starred"))
        form.addRow("", self.starred_check)
        right_layout.addLayout(form)

        self.note_edit = QTextEdit()
        self.note_edit.setMinimumHeight(110)
        self.note_edit.setPlaceholderText(self.tr("field.note"))
        right_layout.addWidget(self.note_edit, 1)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.save_btn = QPushButton()
        self.delete_btn = QPushButton()
        self.save_btn.setProperty("accent", True)
        self.save_btn.clicked.connect(self.save_current)
        self.delete_btn.clicked.connect(self.delete_current)
        actions.addWidget(self.save_btn)
        actions.addWidget(self.delete_btn)
        right_layout.addLayout(actions)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)

        self.retranslateUi()

    def retranslateUi(self) -> None:        # noqa: N802 - Qt 命名惯例
        self.title_label.setText(self.tr("page_label"))
        self.search_edit.setPlaceholderText(self.tr("search.placeholder"))
        self.new_btn.setText(self.tr("action.new"))
        self.import_btn.setText(self.tr("action.import"))
        self.export_btn.setText(self.tr("action.export"))
        self.open_dir_btn.setText(self.tr("action.open_dir"))
        self.save_btn.setText(self.tr("action.save"))
        self.delete_btn.setText(self.tr("action.delete"))
        self.bind_orphan_btn.setText(self.tr("action.bind_orphan"))
        self.table.setHorizontalHeaderLabels([self.tr(f"col.{name}") for name in _TABLE_COLUMNS])

    # ---------------- 数据 ----------------
    def reload(self) -> None:
        try:
            self._store.reload_if_changed()
            self._index.ensure(self._store)
        except ReferenceStoreError as exc:
            self.status_label.setText(str(exc))
        self._refresh_table()
        self._refresh_orphans()

    def _refresh_table(self) -> None:
        references = sorted(self._store.all(),
                            key=lambda ref: (ref.cite_label.isdigit() is False,
                                             int(ref.cite_label) if ref.cite_label.isdigit() else 0,
                                             ref.title))
        self.table.setRowCount(len(references))
        for row, ref in enumerate(references):
            values = (ref.cite_label, ref.title, ref.authors, ref.year, ref.container,
                      self._summary(ref.note), "; ".join(ref.tags))
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.UserRole, ref.ref_key)
                self.table.setItem(row, column, item)
        self.status_label.setText(self.tr("status.counts", total=len(references),
                                          with_note=sum(1 for ref in references if ref.note.strip()),
                                          orphans=len(self._store.orphan_anchors())))
        self._apply_filter(self.search_edit.text())

    @staticmethod
    def _summary(note: str, limit: int = 40) -> str:
        text = " ".join((note or "").split())
        return text if len(text) <= limit else text[:limit] + "…"

    def _refresh_orphans(self) -> None:
        orphans = self._store.orphan_anchors()
        self.orphan_label.setText(self.tr("orphan.title", count=len(orphans)))
        self.orphan_list.clear()
        for anchor in orphans:
            item = QListWidgetItem(f"{anchor.doc_name} · {anchor.cite_label or '?'} → {anchor.anchor_key}")
            item.setData(Qt.UserRole, anchor)
            self.orphan_list.addItem(item)

    def _apply_filter(self, text: str) -> None:
        needle = (text or "").strip().lower()
        for row in range(self.table.rowCount()):
            if not needle:
                self.table.setRowHidden(row, False)
                continue
            haystack = " ".join(self.table.item(row, column).text().lower()
                                for column in range(self.table.columnCount())
                                if self.table.item(row, column) is not None)
            self.table.setRowHidden(row, needle not in haystack)

    def _on_selection_changed(self) -> None:
        items = self.table.selectedItems()
        if not items:
            return
        key = self.table.item(items[0].row(), 0).data(Qt.UserRole)
        ref = self._store.get(key)
        if ref is not None:
            self._load_into_editor(ref)

    def _load_into_editor(self, ref: Reference) -> None:
        self._current_key = ref.ref_key
        self.key_label.setText(ref.ref_key)
        self.label_edit.setText(ref.cite_label)
        self.title_edit.setText(ref.title)
        self.authors_edit.setText(ref.authors)
        self.year_edit.setText(ref.year)
        self.container_edit.setText(ref.container)
        self.doi_edit.setText(ref.doi)
        self.type_edit.setText(ref.type)
        self.tags_edit.setText("; ".join(ref.tags))
        self.starred_check.setChecked(ref.starred)
        self.note_edit.setPlainText(ref.note)

    def start_new(self) -> None:
        self._current_key = ""
        self.key_label.setText(self.tr("new.hint"))
        for widget in (self.label_edit, self.title_edit, self.authors_edit, self.year_edit,
                       self.container_edit, self.doi_edit, self.type_edit, self.tags_edit):
            widget.clear()
        self.starred_check.setChecked(False)
        self.note_edit.clear()
        self.title_edit.setFocus()

    def _collect(self) -> Reference:
        key = self._current_key or f"manual:{uuid.uuid4().hex[:8]}"
        return Reference(
            ref_key=key,
            provider="manual" if not self._current_key else "",
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

    def save_current(self) -> None:
        reference = self._collect()
        if not reference.title and not reference.note:
            self.status_label.setText(self.tr("status.need_title"))
            return
        try:
            saved = self._store.upsert(reference, overwrite_note=True)
        except ReferenceStoreError as exc:
            self.status_label.setText(str(exc))
            return
        self._current_key = saved.ref_key
        self.key_label.setText(saved.ref_key)
        self.reload()
        self.status_label.setText(self.tr("status.saved"))

    def delete_current(self) -> None:
        if not self._current_key:
            return
        try:
            self._store.delete(self._current_key)
        except ReferenceStoreError as exc:
            self.status_label.setText(str(exc))
            return
        self.start_new()
        self.reload()
        self.status_label.setText(self.tr("status.deleted"))

    def bind_orphan(self) -> None:
        item = self.orphan_list.currentItem()
        if item is None:
            self.status_label.setText(self.tr("orphan.pick_first"))
            return
        anchor = item.data(Qt.UserRole)
        target = self._current_key
        if not target:
            self.status_label.setText(self.tr("orphan.pick_reference"))
            return
        try:
            self._store.bind_anchor(anchor.doc_name, anchor.cite_label, target,
                                    first_para=anchor.first_para)
        except ReferenceStoreError as exc:
            self.status_label.setText(str(exc))
            return
        self.reload()
        self.status_label.setText(self.tr("orphan.bound", key=target))

    # ---------------- 导入导出 ----------------
    def import_csv(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(self, self.tr("action.import"), "",
                                                    "CSV (*.csv)")
        if not path:
            return
        try:
            self._store.replace_from_file(path)
        except ReferenceStoreError as exc:
            self.status_label.setText(str(exc))
            return
        self.reload()
        self.status_label.setText(self.tr("status.imported", name=Path(path).name))

    def export_csv(self) -> None:
        path, _filter = QFileDialog.getSaveFileName(self, self.tr("action.export"),
                                                    "references.csv", "CSV (*.csv)")
        if not path:
            return
        try:
            self._store.export_to_file(path)
        except ReferenceStoreError as exc:
            self.status_label.setText(str(exc))
            return
        self.status_label.setText(self.tr("status.exported", name=Path(path).name))

    def open_directory(self) -> None:
        directory = self._store.ensure_directory() or self._store.directory
        try:
            os.startfile(str(directory))       # noqa: S606 - 用户主动打开自己的数据目录
        except Exception as exc:               # noqa: BLE001 - 打开失败只提示
            self.status_label.setText(self.tr("status.open_failed", error=exc))
