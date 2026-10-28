# plugins/reference/page_libraries.py
"""页面里的「仓库下拉 + 切换重绑」（从 library_page 拆出，保持单文件在阈值内）。"""
from __future__ import annotations

from core.services.ui_labels import library_label

from core.services.i18n import message

from PySide6.QtWidgets import QComboBox


FILTERS = (("all", "filter.all"), ("note", "filter.note"), ("star", "filter.star"),
            ("meta", "filter.meta"), ("orphan", "filter.orphan"),
            ("added", "filter.added"), ("removed", "filter.removed"))

class LibraryMixin:
    """宿主需提供 `_libraries / _active_library / _on_switch_library / _store / _index`。"""

    def build_filter_chips(self, toolbar, filters) -> None:
        """筛选 chips（全部/有备注/星标/…）；从 library_page 抽出来控制文件行数。"""
        from PySide6.QtWidgets import QButtonGroup, QToolButton

        group = QButtonGroup(self)
        group.setExclusive(True)
        buttons: dict = {}
        for index, (name, _key) in enumerate(filters):
            button = QToolButton()
            button.setCheckable(True)
            button.setObjectName("filterChip")
            button.setProperty("chip", True)
            button.clicked.connect(lambda _checked, value=name: self.set_filter(value))
            group.addButton(button, index)
            buttons[name] = button
            toolbar.addWidget(button)
        self.filter_group = group
        self.filter_buttons = buttons
        self.filter_buttons["all"].setChecked(True)

    def build_filter_menu(self, toolbar, filters) -> None:
        """筛选下拉（全部/有备注/星标/…）：低频操作不占工具栏一行。"""
        from PySide6.QtWidgets import QMenu, QToolButton

        button = QToolButton()
        button.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(button)
        actions: dict = {}
        for name, _key in filters:
            action = menu.addAction("")
            action.setCheckable(True)
            action.triggered.connect(lambda _checked, value=name: self._select_filter(value))
            actions[name] = action
        actions["all"].setChecked(True)
        button.setMenu(menu)
        self.filter_btn = button
        self.filter_menu = menu
        self.filter_actions = actions
        toolbar.addWidget(button)

    def _select_filter(self, name: str) -> None:
        """菜单项被点：互斥选中并应用筛选。"""
        for key, action in self.filter_actions.items():
            action.setChecked(key == name)
        self.set_filter(name)

    def build_library_combo(self, toolbar) -> QComboBox:
        combo = QComboBox()
        combo.setObjectName("libraryCombo")
        combo.setMinimumWidth(120)
        fallback = getattr(self, "_active_library", "") or message("library.default_name")
        names = self._libraries or [fallback]
        tr = self._label_tr()
        for name in names:
            # 显示用翻译后的名字；itemData 保留**后端原值**，切换时不改动数据
            combo.addItem(library_label(name, tr), name)
        active = getattr(self, "_active_library", "")
        if active:
            index = combo.findData(active)
            if index >= 0:
                combo.setCurrentIndex(index)
        combo.currentTextChanged.connect(self._on_library_changed)
        self.library_combo = combo
        toolbar.addWidget(combo)
        return combo

    def _label_tr(self):
        """显示映射用的 tr：**应用级**语言服务（插件 tr 会加 `reference.` 前缀）。"""
        ctx = getattr(self, "_ctx", None)
        if ctx is not None:
            try:
                service = ctx.get_service("i18n")
                if service is not None:
                    return service.tr
            except Exception:                  # noqa: BLE001 - 取不到就用兜底
                pass
        return getattr(self, "tr", None)

    def _library_names(self) -> list[str]:
        """当前库名列表：优先问插件（新建/删除后快照是旧的）。"""
        plugin = getattr(self, "_plugin", None)
        libraries = getattr(plugin, "_libraries", None)
        if libraries is not None:
            try:
                names = libraries.names()
                if names:
                    return list(names)
            except Exception:                  # noqa: BLE001 - 注册表读失败就用快照
                pass
        return list(getattr(self, "_libraries", None) or [])

    def rebuild_library_combo(self, active: str = "") -> None:
        """库列表变化（新建/删除）后重填下拉，并选中当前库。"""
        combo = getattr(self, "library_combo", None)
        if combo is None:
            return
        names = self._library_names()
        plugin = getattr(self, "_plugin", None)
        active = (active or getattr(plugin, "_active_name", "")
                  or getattr(self, "_active_library", "") or combo.currentText())
        tr = self._label_tr()
        blocked = combo.blockSignals(True)
        try:
            combo.clear()
            for name in names:
                # 显示走语言包，itemData 永远是后端原值
                combo.addItem(library_label(name, tr), name)
            index = combo.findData(active)
            if index >= 0:
                combo.setCurrentIndex(index)
        finally:
            combo.blockSignals(blocked)

    def publish_note_to_master(self) -> None:
        """把**当前选中文献**的子库备注发布到总库（单条更新，随时可用）。"""
        handler = getattr(self, "_on_publish_note", None)
        # 表格选中项记在 `_current_key`；`_selected_key` 是死字段（从未赋值），别读它
        key = (getattr(self, "_current_key", "") or getattr(self, "_selected_key", "") or "")
        if handler is None or not key:
            self.append_status(self.tr("master.need_selection"))
            return
        note = handler(key)
        if note is None:
            self.append_status(self.tr("master.note_empty"))
            return
        self.append_status(self.tr("master.publish_note_done"))

    def confirm_publish_all_master(self) -> None:
        """整库更新到总库：二次确认后把当前子库所有非空备注推上去（不自动同步）。"""
        handler = getattr(self, "_on_publish_all_notes", None)
        if handler is None:
            return
        from PySide6.QtWidgets import QMessageBox

        name = getattr(self, "_active_library", "") or self.library_combo.currentText().strip()
        answer = QMessageBox.question(
            self, self.tr("master.publish_all_title"),
            self.tr("master.publish_all_confirm", name=name))
        if answer != QMessageBox.Yes:
            return
        published, duplicate, empty = handler()
        self.append_status(self.tr("master.publish_all_done", published=published,
                                   duplicate=duplicate, empty=empty))
        self.reload()

    def confirm_dedupe(self) -> None:
        """清理"无编号且与有编号条目重复"的历史条目（合并到有编号那条，保留别名）。"""
        handler = getattr(self, "_on_dedupe", None)
        if handler is None:
            return
        from PySide6.QtWidgets import QMessageBox

        answer = QMessageBox.question(self, self.tr("dedupe.title"), self.tr("dedupe.confirm"))
        if answer != QMessageBox.Yes:
            return
        merged, kept = handler()
        self.append_status(self.tr("dedupe.done", merged=merged, kept=kept))
        self.reload()

    def confirm_delete_library(self) -> None:
        """删除当前仓库：二次确认后移到 _trash（可回滚）。"""
        from PySide6.QtWidgets import QMessageBox

        name = self.library_combo.currentText().strip()
        if not name:
            return
        answer = QMessageBox.question(
            self, self.tr("library.delete_title"),
            self.tr("library.delete_confirm", name=name))
        if answer != QMessageBox.Yes:
            return
        handler = getattr(self, "_on_delete_library", None)
        if handler is None:
            return
        if not handler(name):
            QMessageBox.warning(self, self.tr("library.delete_title"),
                                self.tr("library.delete_failed"))
            return
        rebuild = getattr(self, "rebuild_library_combo", None)
        if callable(rebuild):
            plugin = getattr(self, "_plugin", None)
            rebuild(getattr(plugin, "_active_name", "") or "")
        reload_now = getattr(self, "reload", None)
        if callable(reload_now):
            reload_now()

    def confirm_clear_master(self) -> None:
        """删除总库备注：二次确认；备份后清空（可回滚），不影响各子库自己的备注。"""
        from PySide6.QtWidgets import QMessageBox

        handler = getattr(self, "_on_clear_master", None)
        if handler is None:
            return
        answer = QMessageBox.question(self, self.tr("master.clear_title"),
                                      self.tr("master.clear_confirm"))
        if answer != QMessageBox.Yes:
            return
        if handler():
            self.append_status(self.tr("master.cleared"))
        else:
            QMessageBox.warning(self, self.tr("master.clear_title"),
                                self.tr("master.clear_failed"))

    def prompt_new_library(self) -> None:
        """弹输入框新建仓库：命名 → 建库 → 自动切过去（用户最初要求的流程）。"""
        from PySide6.QtWidgets import QInputDialog, QMessageBox

        name, ok = QInputDialog.getText(self, self.tr("library.new_title"),
                                       self.tr("library.new_prompt"))
        name = (name or "").strip()
        if not ok or not name:
            return
        create = getattr(self, "_on_create_library", None)
        if create is None:
            return
        try:
            create(name)
        except ValueError as exc:
            QMessageBox.warning(self, self.tr("library.new_title"), str(exc))
            return
        rebuild = getattr(self, "rebuild_library_combo", None)
        if callable(rebuild):
            rebuild(name)                     # 新建后自动选中新库
        self._active_library = name
        reload_now = getattr(self, "reload", None)
        if callable(reload_now):
            reload_now()

    def _finish_editing(self) -> None:
        """R4：切库前统一收尾——有修改就保存/保留草稿，并把表单与选中项清干净。

        不清的话，切到空库后表单里还挂着上一个库的条目，点保存会把旧文献写进新库。
        """
        try:
            if getattr(self, "panel_stack", None) is not None and self.panel_stack.currentIndex() == 1:
                self.cancel_edit()
        except Exception:                      # noqa: BLE001 - 收尾失败不该阻塞切库
            pass
        for attr in ("_current_key", "_orphan", "_selected_key"):
            try:
                setattr(self, attr, "" if attr != "_orphan" else None)
            except Exception:                  # noqa: BLE001
                pass
        clear = getattr(self, "_clear_panel", None)
        if callable(clear):
            try:
                clear()
            except Exception:                  # noqa: BLE001
                pass

    def _on_library_changed(self, name: str) -> None:
        # `name` 是显示文案，真正的库名在 itemData 里（显示与后端解耦）
        combo = getattr(self, "library_combo", None)
        backend = combo.currentData() if combo is not None else None
        name = str(backend or name or "")
        if not name or name == self._active_library or self._on_switch_library is None:
            return
        self._finish_editing()          # R4：先收尾，避免把旧库草稿带进新库
        self._active_library = name
        try:
            self._on_switch_library(name)
        except Exception as exc:               # noqa: BLE001 - 切换失败要能看见
            self.append_status(self.tr("status.scan_failed", error=str(exc)))

    def rebind(self, store, index, note_service, active_name: str = "") -> None:
        """切换到另一个仓库后就地重绑数据层（不重建页面）。"""
        self._finish_editing()          # R4：数据层换了，旧的选中项/草稿必须一起清
        self._store = store
        self._index = index
        self._note_service = note_service
        if active_name:
            self._active_library = active_name
            # **必须重填下拉**：新建仓库走的正是这条路（确认框 → create_library →
            # switch_library → rebind）。只 setCurrentText 时，新库不在条目里，
            # 用户看到"文件夹有、界面列表里没有"（用户现场）。
            rebuild = getattr(self, "rebuild_library_combo", None)
            if callable(rebuild):
                rebuild(active_name)
            else:
                self.library_combo.blockSignals(True)
                self.library_combo.setCurrentText(active_name)
                self.library_combo.blockSignals(False)
        self._selected_key = ""
        self.reload()
        hint = self.tr("status.library", name=active_name).replace("{name}", active_name)
        self.append_status(hint)


def group_separator():
    """工具栏分区之间的竖线（纯视觉）。"""
    from PySide6.QtWidgets import QFrame

    line = QFrame()
    line.setFrameShape(QFrame.VLine)
    line.setObjectName("toolbarSeparator")
    line.setFixedWidth(1)
    return line
