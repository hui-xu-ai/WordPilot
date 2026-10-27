# app/plugin_manager_page.py
"""插件管理面板（内置页，卡片网格风格）。

规则（与微内核约定一致）：
- 这里列出所有「已发现」的插件（内置 + 用户目录安装的）；
- 卡片右上角的开关 = 启用 + 显示在左侧导航轨 + 上桌宠右键菜单（同一开关）；
- 用户安装的插件与默认关闭的插件，必须打开开关后才开始工作；
- 侧边栏顺序：直接在导航轨上拖动插件图标即可（面板里不再提供箭头）；
- 支持从文件夹或 zip 安装新插件（进 plugins_user/，安装后默认关闭）。

面板本身只调 PluginManager 的公开 API，不碰插件内部。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFileDialog, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPushButton,
                               QScrollArea, QVBoxLayout, QWidget)

from app import icon_factory
from core.plugin_manager import PluginState
from core.widgets import Switch

CARD_MIN_WIDTH = 300
CARD_HEIGHT = 124
CARD_ICON_SIZE = 32


class PluginManagerPage(QWidget):
    def __init__(self, kernel, parent=None, i18n=None) -> None:
        super().__init__(parent)
        self.kernel = kernel
        self.i18n = i18n
        self._building = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        # 工具行：搜索框 + 安装按钮
        # （页面不再放标题：侧边栏已经表明当前在哪一页，重复一遍纯属浪费空间）
        toolbar = QHBoxLayout()
        toolbar.setSpacing(10)
        self.search_edit = QLineEdit()
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setFixedWidth(240)
        self.search_edit.textChanged.connect(self._apply_filter)
        toolbar.addWidget(self.search_edit)
        toolbar.addStretch(1)

        self.install_btn = QPushButton()
        self.install_btn.setProperty("accent", True)
        self.install_btn.clicked.connect(self.install_plugin)
        toolbar.addWidget(self.install_btn)
        layout.addLayout(toolbar)

        # 卡片网格（可滚动，插件多了也不撑破窗口）
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.list_host = QWidget()
        self.grid = QGridLayout(self.list_host)
        self.grid.setContentsMargins(0, 4, 0, 0)
        self.grid.setSpacing(10)
        self.scroll.setWidget(self.list_host)
        layout.addWidget(self.scroll, 1)

        self._cards: list[tuple[QWidget, str, str]] = []   # (卡片, 名称, 描述)

        # 插件启停/安装后重建列表；页面构造早于插件加载，
        # 所以每次显示时也刷新一次，保证列表永远是最新的
        self.kernel.events.subscribe("plugins.changed", lambda _e, _p: self.rebuild())
        self.kernel.events.subscribe("theme.changed", lambda _e, _p: self.rebuild())
        self.retranslateUi()

    def showEvent(self, event) -> None:      # noqa: N802 - Qt 命名惯例
        super().showEvent(event)
        self.kernel.plugins.rescan()
        self.rebuild()

    # ---------------- 多语言 ----------------
    def tr(self, key: str, /, **params) -> str:
        if self.i18n is None:
            return key
        return self.i18n.tr(key, **params)

    def retranslateUi(self):        # noqa: N802 - Qt 命名惯例
        self.search_edit.setPlaceholderText(self.tr("plugins.search_placeholder"))
        self.install_btn.setText(self.tr("plugins.install"))
        self.rebuild()

    # ---------------- 主题辅助 ----------------
    def _token(self, key: str, default: str) -> str:
        theme = self.kernel.services.get("theme")
        return theme.token(key, default) if theme is not None else default

    def _plugin_icons(self) -> dict[str, str]:
        """插件 id -> 图标名（取插件注册页面/工具时声明的图标）。"""
        icons: dict[str, str] = {}
        ui = getattr(self.kernel, "ui", None)
        if ui is None:
            return icons
        try:
            for _label, _factory, icon, plugin_id in ui.pages():
                icons.setdefault(plugin_id, icon)
            for _label, _handler, icon, plugin_id in ui.tools():
                icons.setdefault(plugin_id, icon)
        except Exception:
            pass
        return icons

    # ---------------- 卡片网格 ----------------
    def rebuild(self) -> None:
        if self._building:
            return
        self._building = True
        try:
            while self.grid.count():
                item = self.grid.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.hide()            # 先藏再摘父级，避免闪出独立小窗口
                    widget.setParent(None)
                    widget.deleteLater()
            self._cards.clear()

            records = self.kernel.plugins.all()
            order = self._order()
            records.sort(key=lambda r: (order.index(r.manifest.id)
                                        if r.manifest.id in order else len(order),
                                        r.manifest.id))
            if not records:
                empty = QLabel(self.tr("plugins.empty"))
                empty.setObjectName("hintLabel")
                self.grid.addWidget(empty, 0, 0)
                return

            icons = self._plugin_icons()
            for index, record in enumerate(records):
                card = self._make_card(record, icons.get(record.manifest.id))
                row, col = divmod(index, 2)
                self.grid.addWidget(card, row, col)
                self._cards.append((card, self._display_name(record),
                                    record.manifest.description))
            self._apply_filter()
        finally:
            self._building = False

    def _display_name(self, record) -> str:
        """插件显示名：插件语言包的 `plugin.name` 优先，manifest.name 兜底。

        单一来源——导航轨、托盘、插件列表都用这套名字，避免同一个插件两种叫法。
        """
        key = f"{record.manifest.id}.plugin.name"
        translated = self.i18n.tr(key) if self.i18n is not None else key
        if translated and translated != key:
            return translated
        return record.manifest.name or record.manifest.id

    def _make_card(self, record, icon_name: str | None) -> QWidget:
        manifest = record.manifest
        card = QWidget()
        card.setObjectName("pluginCard")
        card.setMinimumWidth(CARD_MIN_WIDTH)
        card.setFixedHeight(CARD_HEIGHT)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 12, 14, 12)
        card_layout.setSpacing(6)

        # 顶行：图标 + 名称/版本 + 开关
        top = QHBoxLayout()
        top.setSpacing(10)
        icon_label = QLabel()
        icon_label.setFixedSize(CARD_ICON_SIZE, CARD_ICON_SIZE)
        icon = icon_factory.icon(icon_name or "puzzle",
                                 self._token("color.accent", "#0078d4"))
        icon_label.setPixmap(icon.pixmap(CARD_ICON_SIZE, CARD_ICON_SIZE))
        top.addWidget(icon_label, 0, Qt.AlignTop)

        name_col = QVBoxLayout()
        name_col.setSpacing(2)
        name = QLabel(self._display_name(record))
        name.setObjectName("pluginCardName")
        name_col.addWidget(name)
        meta = QLabel(f"v{manifest.version} · {manifest.author}")
        meta.setObjectName("hintLabel")
        name_col.addWidget(meta)
        top.addLayout(name_col, 1)

        toggle = Switch(accent=self._token("color.accent", "#0078d4"),
                        track_off=self._token("color.border.strong", "#c8c8c8"),
                        knob=self._token("color.on_accent", "#ffffff"))
        toggle.setChecked(record.state == PluginState.ACTIVE)
        toggle.setToolTip(self.tr("plugins.state.active")
                          if record.state == PluginState.ACTIVE
                          else self.tr("plugins.state.disabled"))
        toggle.toggled.connect(lambda on, pid=manifest.id: self._toggle(pid, on))
        top.addWidget(toggle, 0, Qt.AlignTop)
        card_layout.addLayout(top)

        # 描述 + 状态徽标
        desc_row = QHBoxLayout()
        desc = QLabel(manifest.description)
        desc.setObjectName("hintLabel")
        desc.setWordWrap(True)
        desc_row.addWidget(desc, 1)
        state_text = {
            PluginState.ACTIVE: self.tr("plugins.state.active"),
            PluginState.DISABLED: self.tr("plugins.state.disabled"),
            PluginState.FAILED: self.tr("plugins.state.failed"),
        }.get(record.state, "")
        badge = QLabel(state_text)
        badge.setObjectName("pluginCardBadge")
        if record.state == PluginState.ACTIVE:
            badge.setProperty("state", "active")
        elif record.state == PluginState.FAILED:
            badge.setProperty("state", "failed")
            if record.error:
                badge.setToolTip(record.error)
        desc_row.addWidget(badge, 0, Qt.AlignBottom)
        card_layout.addLayout(desc_row)
        return card

    def _apply_filter(self) -> None:
        keyword = self.search_edit.text().strip().lower()
        for card, name, desc in self._cards:
            visible = (not keyword or keyword in name.lower()
                       or keyword in desc.lower())
            card.setVisible(visible)

    # ---------------- 操作 ----------------
    def _order(self) -> list[str]:
        value = self.kernel.config.get("plugins", {}) or {}
        order = value.get("order", [])
        return order if isinstance(order, list) else []

    def _toggle(self, plugin_id: str, on: bool) -> None:
        if self._building:
            return
        self.kernel.plugins.set_enabled(plugin_id, on)
        # plugins.changed 事件会触发 rebuild；导航轨由主窗口监听同一事件重建

    def install_plugin(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, self.tr("plugins.install_title"), "",
            "Plugin package (*.zip);;All files (*)")
        if not path:
            # 也支持直接选一个包含 plugin.json 的文件夹
            path = QFileDialog.getExistingDirectory(self, self.tr("plugins.install_dir"))
        if not path:
            return
        try:
            manifest = self.kernel.plugins.install(Path(path))
        except Exception as exc:
            QMessageBox.warning(self, self.tr("plugins.install_fail_title"),
                                self.tr("plugins.install_fail", error=exc))
            return
        QMessageBox.information(self, self.tr("plugins.install_ok_title"),
                                self.tr("plugins.install_ok", name=manifest.name))
