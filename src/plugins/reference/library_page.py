# plugins/reference/library_page.py
"""参考文献助手主页面：**默认就是全库文献表格**，选中行在右栏查看/编辑。

布局对齐 Zotero / EndNote 的使用习惯（用户明确要求"别搞复杂"）：

    ┌ 搜索框   (全部)(有备注)(星标)(缺元数据)(孤立)   [打开CSV] [⋯] ┐
    ├──────────── 表格（占满主体） ────────────┬──── 右栏 400px ────┤
    │ 编号 标题 作者 年份 期刊 DOI 标签 备注摘要 │ 只读详情 / 点「编辑」变表单 │
    └──────────────────────────────────────────┴────────────────────┘

- 未选中行：右栏显示一行汇总，不占主体；
- 「孤立」筛选：表格列出文档里指向"库里没有的文献"的锚点，右栏用来把锚点绑到某条文献；
- 「打开CSV」直接调 Excel；保存后靠文件指纹自动重载（`ReferenceStore` 已有）。
"""
from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFormLayout,
                               QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QMenu, QProgressBar, QPushButton, QSplitter,
                               QStackedWidget, QTableWidget, QTableWidgetItem, QTextEdit,
                               QToolButton, QVBoxLayout, QWidget)

from plugins.reference.page_libraries import (FILTERS as _FILTERS_FROM_PAGE_LIBRARIES,
                                             LibraryMixin, group_separator)
from plugins.reference.page_toolbar import ToolbarMixin
from plugins.reference.page_panel import (PANEL_WIDTH, PanelChromeMixin,
                                          PanelEditMixin, build_panel)
from plugins.reference.page_scan import ScanMixin
from plugins.reference.reference_store import (Anchor, Reference, ReferenceStore,
                                               ReferenceStoreError)

if TYPE_CHECKING:
    from core.sdk.context import PluginContext
    from plugins.reference.reference_index import ReferenceIndex

logger = logging.getLogger(__name__)

# 方案要求默认 4 列：元数据细节放右栏，表格只给"扫读"需要的信息
_TABLE_COLUMNS = ("cite_label", "title", "author_year", "note")
_NOTE_COLUMN = _TABLE_COLUMNS.index("note")
_ORPHAN_COLUMNS = ("doc_name", "cite_label", "anchor_key")
_FILTERS = _FILTERS_FROM_PAGE_LIBRARIES


class LibraryPage(ToolbarMixin, PanelChromeMixin, PanelEditMixin, ScanMixin,
                  LibraryMixin, QWidget):
    """文献库管理页。"""

    def __init__(self, ctx: "PluginContext", store: ReferenceStore,
                 index: "ReferenceIndex", parent: QWidget | None = None,
                 plugin=None, note_service=None, libraries=None,
                 active_library: str = "", on_switch_library=None,
                 on_create_library=None, on_delete_library=None,
                 on_clear_master=None, on_publish_note=None,
                 on_publish_all_notes=None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._store = store
        self._index = index
        self._plugin = plugin
        self._note_service = note_service
        self._selected_key = ""        # R7：按钮读它，必须先初始化
        self._libraries = list(libraries or [])
        self._active_library = active_library
        self._on_switch_library = on_switch_library
        self._on_create_library = on_create_library
        self._on_delete_library = on_delete_library
        self._on_clear_master = on_clear_master
        self._on_publish_note = on_publish_note
        self._on_publish_all_notes = on_publish_all_notes
        self._auto_scanned = False
        self._refresh_timer = None
        self._diff_added: set[str] = set()
        self._diff_removed: set[str] = set()
        self._current_key = ""
        self._mode = "refs"                      # refs | orphans
        self._orphan: Anchor | None = None
        self._build_ui()
        self.reload()
        self.start_auto_refresh()
        try:                               # 同进程写入（悬浮窗/本页保存）立即刷新
            self._store.subscribe(self._on_store_changed)
        except Exception:                  # noqa: BLE001
            pass

    # ---------------- 文案 ----------------
    def tr(self, key: str, /, **params) -> str:
        """`ctx.tr()` **已经**自动加 `<plugin_id>.` 前缀，这里绝不能再加一次。"""
        try:
            return self._ctx.tr(key, **params)
        except Exception:
            return key

    # ---------------- 构建界面 ----------------
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        self.build_toolbar(layout, _FILTERS)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)          # 不确定态：扫描时长取决于文档大小
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(4)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.status_label = QLabel("")
        self.status_label.setObjectName("hintLabel")
        layout.addWidget(self.status_label)

        # ---- 表格 + 右栏 ----
        splitter = QSplitter(Qt.Horizontal)
        self._splitter = splitter
        self.table = QTableWidget(0, len(_TABLE_COLUMNS))
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.DoubleClicked | QTableWidget.EditKeyPressed)
        self.table.itemChanged.connect(self._on_note_edited)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(28)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        splitter.addWidget(self.table)

        splitter.addWidget(self._build_panel())
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([720, PANEL_WIDTH])
        splitter.setChildrenCollapsible(False)   # 拖到 0 会把手柄也藏起来
        splitter.setHandleWidth(6)
        layout.addWidget(splitter, 1)

        self.footer_label = QLabel("")
        self.footer_label.setObjectName("hintLabel")
        self.footer_label.setWordWrap(True)
        layout.addWidget(self.footer_label)

        self.retranslateUi()

    def _build_panel(self) -> QWidget:
        return build_panel(self)

    def retranslateUi(self) -> None:        # noqa: N802 - Qt 命名惯例
        self.search_edit.setPlaceholderText(self.tr("search.placeholder"))
        self.filter_btn.setText(self.tr("filter.menu"))
        for name, key in _FILTERS:
            self.filter_actions[name].setText(self.tr(key))
        self.filter_actions["orphan"].setText(self.tr("filter.orphan"))
        self._refresh_doc_label()
        self.new_lib_btn.setText(self.tr("library.new"))
        self.new_lib_btn.setToolTip(self.tr("library.new.tip"))
        self.del_lib_btn.setText(self.tr("library.delete"))
        self.del_lib_btn.setToolTip(self.tr("library.delete.tip"))
        self.scan_btn.setText(self.tr("action.sync_doc"))
        self.scan_btn.setToolTip(self.tr("action.scan.tip"))
        self.refresh_btn.setText(self.tr("action.reload"))
        self.refresh_btn.setToolTip(self.tr("action.reload.tip"))
        self.excel_action.setText(self.tr("action.open_csv"))
        self.publish_note_action.setText(self.tr("master.publish_note"))
        self.publish_all_action.setText(self.tr("master.publish_all"))
        self.clear_master_action.setText(self.tr("master.clear"))
        self.more_btn.setText(self.tr("action.more"))
        self.more_btn.setToolTip(self.tr("action.more"))
        self.import_action.setText(self.tr("action.import"))
        self.export_action.setText(self.tr("action.export"))
        self.dir_action.setText(self.tr("action.open_dir"))
        self.edit_btn.setText(self.tr("action.edit"))
        self.bind_btn.setText(self.tr("action.bind_orphan"))
        self.cancel_btn.setText(self.tr("action.cancel"))
        self.save_btn.setText(self.tr("action.save"))
        self.delete_btn.setText(self.tr("action.delete"))
        self.read_note_title.setText(self.tr("field.note"))
        self.table.setHorizontalHeaderLabels([self.tr(f"col.{name}") for name in _TABLE_COLUMNS])
        self._refresh_panel_title()

    # ---------------- 数据 ----------------
    def reload_now(self) -> None:
        """手动刷新（工具栏按钮）：重读 CSV 并重建索引。"""
        self.reload()
        self.append_status(self.tr("status.reloaded"))

    def start_auto_refresh(self) -> None:
        """页面可见时定时同步：CSV 被外部改动、悬浮窗写完备注都能及时反映。"""
        from PySide6.QtCore import QTimer

        if self._refresh_timer is not None:
            return
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(2000)
        self._refresh_timer.timeout.connect(self._auto_sync)
        self._refresh_timer.start()

    def _on_store_changed(self) -> None:
        """库在本进程内被写盘（悬浮窗存备注、本页保存）→ 立刻刷新表格与统计。"""
        self._refresh_table()
        self._update_status()

    def _auto_sync(self) -> None:
        """定时同步：靠文件指纹判断，没变时几乎零开销。"""
        try:
            changed = self._store.reload_if_changed()
        except Exception:                  # noqa: BLE001 - 自动刷新失败不打扰用户
            return
        if changed:
            self._refresh_table()
            self._update_status()

    def reload(self) -> None:
        try:
            self._store.reload_if_changed()
            self._index.ensure(self._store)
        except ReferenceStoreError as exc:
            self.status_label.setText(str(exc))
        self._refresh_table()
        self._update_status()

    def _references(self) -> list[Reference]:
        return sorted(self._store.all(),
                      key=lambda ref: (not ref.cite_label.isdigit(),
                                       int(ref.cite_label) if ref.cite_label.isdigit() else 0,
                                       ref.title))

    def _refresh_table(self) -> None:
        if self._mode == "orphans":
            self._fill_orphans()
        else:
            self._fill_references()
        self._apply_filter(self.search_edit.text())

    def _fill_references(self) -> None:
        # 填充期间必须挡住 itemChanged：备注列可编辑后，setItem 会逐行触发保存，
        # 边填边写库既慢又会把"省略后的摘要"当成备注写坏（实测会把表格填卡住）。
        self.table.blockSignals(True)
        try:
            self._fill_references_locked()
        finally:
            self.table.blockSignals(False)

    def _fill_references_locked(self) -> None:
        self.table.setColumnCount(len(_TABLE_COLUMNS))
        self.table.setHorizontalHeaderLabels([self.tr(f"col.{name}") for name in _TABLE_COLUMNS])
        references = self._references()
        self.table.setRowCount(len(references))
        for row, ref in enumerate(references):
            author_year = " · ".join(part for part in (ref.authors, ref.year) if part)
            values = (ref.cite_label, ref.title, author_year, self._summary(ref.note))
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.UserRole, ref.ref_key)
                    if ref.starred:
                        item.setText(f"★ {value}" if value else "★")
                if column == _NOTE_COLUMN:          # 备注列直接编辑
                    item.setFlags(item.flags() | Qt.ItemIsEditable)
                    item.setData(Qt.UserRole, ref.ref_key)
                    item.setToolTip(self.tr("col.note.edit_tip"))
                self.table.setItem(row, column, item)

    def _fill_orphans(self) -> None:
        self.table.blockSignals(True)
        try:
            self._fill_orphans_locked()
        finally:
            self.table.blockSignals(False)

    def _fill_orphans_locked(self) -> None:
        self.table.setColumnCount(len(_ORPHAN_COLUMNS))
        self.table.setHorizontalHeaderLabels([self.tr(f"col.{name}") for name in _ORPHAN_COLUMNS])
        orphans = self._store.orphan_anchors()
        self.table.setRowCount(len(orphans))
        for row, anchor in enumerate(orphans):
            for column, value in enumerate((anchor.doc_name, anchor.cite_label, anchor.anchor_key)):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.UserRole, anchor.anchor_key)
                self.table.setItem(row, column, item)

    @staticmethod
    def _summary(note: str, limit: int = 40) -> str:
        text = " ".join((note or "").split())
        return text if len(text) <= limit else text[:limit] + "…"

    def _update_status(self) -> None:
        references = self._references()
        self.status_label.setText(self.tr(
            "status.counts",
            total=len(references),
            with_note=sum(1 for ref in references if ref.note.strip()),
            orphans=len(self._store.orphan_anchors())))

    def _apply_filter(self, text: str) -> None:
        needle = (text or "").strip().lower()
        for row in range(self.table.rowCount()):
            haystack = " ".join(self.table.item(row, column).text().lower()
                                for column in range(self.table.columnCount())
                                if self.table.item(row, column) is not None)
            self.table.setRowHidden(row, bool(needle and needle not in haystack))

    def _only_checked(self, name: str) -> None:
        """菜单里的筛选互斥：选中一个就取消其它。"""
        for key, action in self.filter_actions.items():
            if key != name:
                action.setChecked(False)

    def set_filter(self, name: str) -> None:
        """顶部 chips：全部 / 有备注 / 星标 / 缺元数据 / 孤立。"""
        action = self.filter_actions.get(name)
        if action is not None:
            action.setChecked(True)
            self._only_checked(name)
        self._mode = "orphans" if name == "orphan" else "refs"
        self._refresh_table()
        if self._mode == "refs":
            keep: list[Reference] = []
            for row in range(self.table.rowCount()):
                key = self.table.item(row, 0).data(Qt.UserRole)
                ref = self._store.get(key)
                if ref is None:
                    continue
                if name == "note" and not ref.note.strip():
                    continue
                if name == "star" and not ref.starred:
                    continue
                if name == "added" and key not in self._diff_added:
                    continue
                if name == "removed" and key not in self._diff_removed:
                    continue
                if name == "meta" and (ref.title and ref.authors):
                    continue
                keep.append(ref)
            keep_keys = {ref.ref_key for ref in keep}
            for row in range(self.table.rowCount()):
                key = self.table.item(row, 0).data(Qt.UserRole)
                self.table.setRowHidden(row, key not in keep_keys)
        self._current_key = ""
        self._orphan = None
        self.panel_stack.setCurrentIndex(0)
        self._clear_panel()

    def open_csv(self) -> None:
        """用 Excel（系统默认程序）打开主表，改完保存后回到页面会自动重载。"""
        try:
            self._store.ensure_directory()
            target = self._store.refs_path
            if not target.exists():
                self._store.upsert(Reference(ref_key="manual:placeholder", title=""))
                self._store.delete("manual:placeholder")
            os.startfile(str(target))        # noqa: S606 - 用户主动打开自己的数据文件
        except Exception as exc:             # noqa: BLE001
            self.status_label.setText(self.tr("status.open_failed", error=exc))

    # ---------------- 右栏 ----------------
    def _refresh_panel_title(self) -> None:
        if self._mode == "orphans":
            self.panel_title.setText(self.tr("panel.orphan_title"))
        elif self._current_key:
            self.panel_title.setText(self.tr("panel.detail"))
        else:
            self.panel_title.setText(self.tr("panel.summary"))

    def open_notes(self) -> None:
        """打开总库备注面板（挑选 / 新建 / 转为子库备注）。"""
        if self._note_service is None:
            return
        key = self._selected_key
        if not key:
            return
        from plugins.reference.note_editor import NoteEditor
        from plugins.reference.note_picker import NotePicker

        ref_key = self._store.resolve_key(key)
        dialog = NotePicker(self._note_service.master_notes(ref_key),
                            current_id=self._note_service.inherit_id(ref_key),
                            tr=self.tr, parent=self,
                            service=self._note_service)
        if dialog.exec() != dialog.Accepted:
            return
        if dialog.new_note_requested:
            editor = NoteEditor(tr=self.tr, parent=self)
            if editor.exec() != editor.Accepted:
                return
            # 新建的是**总库**备注：它不会自动进子库，用户仍要显式采用（C2）
            self._note_service.create_master_note(ref_key, editor.text, editor.label)
        elif dialog.fork_requested:
            picked = next((note for note in self._note_service.master_notes(ref_key)
                           if note.note_id == dialog.selected_note_id), None)
            if picked is not None:
                self._note_service.save_child_note(ref_key, picked.text)
        elif dialog.selected_note_id:
            # 采用：复制文本进子库并记录来源 ID（不再"跟随"）
            self._note_service.adopt_master_note(ref_key, dialog.selected_note_id)
        self.reload()
        selected = self._store.get(self._selected_key) if self._selected_key else None
        if selected is not None:
            self._show_reference(selected)

    def _refresh_notes_ui(self, key: str) -> None:
        """右栏备注区：来源徽标 + 总库条数（只有 1 条时不需要挑选）。"""
        if self._note_service is None or not hasattr(self, "notes_btn"):
            return
        ref_key = self._store.resolve_key(key)
        notes = self._note_service.master_notes(ref_key)
        _text, source, _hit = self._note_service.effective(ref_key)
        labels = {"child": "overlay.note_child", "available": "overlay.note_available",
                  "none": "overlay.note_none"}
        self.notes_source_label.setText(self.tr(labels.get(source, "")))
        template = self.tr("overlay.master_count")
        self.notes_btn.setText(template.replace("{count}", str(len(notes))))
        self.notes_btn.setVisible(bool(notes))
        # 子库采用的是总库某条、而总库后来变了 → 只提示差异（不自动改子库）
        diff = self._note_service.adopted_diff(ref_key)
        if hasattr(self, "notes_diff_label"):
            self.notes_diff_label.setText(self.tr("overlay.master_diff") if diff else "")
            self.notes_diff_label.setVisible(bool(diff))

    def _clear_panel(self) -> None:
        for key, label in self.read_labels.items():
            label.setText("")
        self.read_note.setText("")
        self.key_label.setText("")
        self._refresh_panel_title()
        has_selection = bool(self._current_key or self._orphan)
        self.edit_btn.setVisible(has_selection and self._orphan is None)
        self.bind_btn.setVisible(self._orphan is not None)

    def _on_selection_changed(self) -> None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return
        row = rows[0].row()
        key = self.table.item(row, 0).data(Qt.UserRole)
        if self._mode == "orphans":
            self._orphan = next((anchor for anchor in self._store.orphan_anchors()
                                 if anchor.anchor_key == key), None)
            self._current_key = ""
            self.panel_stack.setCurrentIndex(0)
            self._clear_panel()
            if self._orphan is not None:
                self.read_note_title.setText(self.tr("panel.orphan_hint"))
                self.read_note.setText(f"{self._orphan.doc_name} · {self._orphan.anchor_key}")
                self.panel_title.setText(self.tr("panel.orphan_title"))
            return

        self._orphan = None
        self._current_key = key
        ref = self._store.get(key)
        if ref is not None:
            self._show_reference(ref)

    def _show_reference(self, ref: Reference) -> None:
        self._current_key = ref.ref_key
        self._refresh_notes_ui(ref.ref_key)
        for key, label in self.read_labels.items():
            label.setText(str(getattr(ref, key, "") or "—"))
        self.read_note.setText(ref.note or self.tr("panel.no_note"))
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
        self.panel_stack.setCurrentIndex(0)
        self.read_note_title.setText(self.tr("field.note"))
        self.edit_btn.setVisible(True)
        self.bind_btn.setVisible(False)
        self._refresh_panel_title()

    def start_edit(self) -> None:
        if not self._current_key:
            return
        self.panel_stack.setCurrentIndex(1)

    def _on_note_edited(self, item) -> None:
        """表格里直接改备注 → 写回当前库（备注是用户资产，改动立即落盘）。"""
        if item is None or item.column() != _NOTE_COLUMN:
            return
        key = item.data(Qt.UserRole)
        if not key:
            return
        text = item.text().strip()
        current = self._store.get(key)
        if current is not None and current.note.strip() == text:
            return
        try:
            self._store.set_note(key, note=text)
        except Exception as exc:                   # noqa: BLE001 - 失败要能看见
            self.append_status(self.tr("status.save_failed", error=str(exc)))
            return
        self._refresh_notes_ui(key)
        self.append_status(self.tr("status.saved"))

    def save_current(self) -> None:
        reference = self._collect()
        if not reference.title and not reference.note:
            self.status_label.setText(self.tr("status.need_title"))
            return
        try:
            # R10：人工编辑按原样写入（允许清空字段、取消星标）；upsert 是"导入补全"语义
            saved = self._store.replace_from_edit(reference)
        except ReferenceStoreError as exc:
            self.status_label.setText(str(exc))
            return
        self._current_key = saved.ref_key
        self.key_label.setText(saved.ref_key)
        self.reload()
        self._show_reference(saved)
        self.status_label.setText(self.tr("status.saved"))

    def delete_current(self) -> None:
        if not self._current_key:
            return
        try:
            self._store.delete(self._current_key)
        except ReferenceStoreError as exc:
            self.status_label.setText(str(exc))
            return
        self._current_key = ""
        self.reload()
        self._clear_panel()
        self.status_label.setText(self.tr("status.deleted"))

    def bind_orphan(self, selected_key: str | None = None) -> None:
        """把选中的孤立锚点绑到某条文献：默认弹选择框，也可直接传 ref_key（便于测试）。"""
        if self._orphan is None:
            self.status_label.setText(self.tr("orphan.pick_first"))
            return
        if selected_key is None:
            from plugins.reference.bind_dialog import ReferencePicker

            picker = ReferencePicker(self._store, self.tr, self)
            if not picker.exec() or not picker.selected_key:
                return
            selected_key = picker.selected_key
        try:
            self._store.bind_anchor(self._orphan.doc_name, self._orphan.cite_label,
                                    selected_key, first_para=self._orphan.first_para)
        except ReferenceStoreError as exc:
            self.status_label.setText(str(exc))
            return
        self.status_label.setText(self.tr("orphan.bound", key=selected_key))
        self.reload()

    # ---------------- 导入导出 ----------------
    def import_csv(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(self, self.tr("action.import"), "", "CSV (*.csv)")
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
        directory = self._store.directory
        try:
            self._store.ensure_directory()
            os.startfile(str(directory))      # noqa: S606 - 用户主动打开自己的数据目录
        except Exception as exc:              # noqa: BLE001
            self.status_label.setText(self.tr("status.open_failed", error=exc))
