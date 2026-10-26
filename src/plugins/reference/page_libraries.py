# plugins/reference/page_libraries.py
"""页面里的「仓库下拉 + 切换重绑」（从 library_page 拆出，保持单文件在阈值内）。"""
from __future__ import annotations

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
        combo.addItems(self._libraries or [getattr(self, "_active_library", "") or "默认库"])
        if getattr(self, "_active_library", ""):
            combo.setCurrentText(self._active_library)
        combo.currentTextChanged.connect(self._on_library_changed)
        self.library_combo = combo
        toolbar.addWidget(combo)
        return combo

    def rebuild_library_combo(self) -> None:
        """库列表变化（新建/删除）后重填下拉，保持当前选中项。"""
        combo = getattr(self, "library_combo", None)
        if combo is None:
            return
        names = list(getattr(self, "_libraries", None) or [])
        active = getattr(self, "_active_library", "") or combo.currentText()
        blocked = combo.blockSignals(True)
        try:
            combo.clear()
            combo.addItems(names)
            if active in names:
                combo.setCurrentText(active)
        finally:
            combo.blockSignals(blocked)

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
            rebuild()

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
        self.build_library_combo_reload() if hasattr(self, "build_library_combo_reload") else None

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
