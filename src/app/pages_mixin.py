# app/pages_mixin.py
"""主窗口的「页面与导航」能力（从 main_window.py 拆出，见 QUALITY 巨文件债）。

职责边界：
- 插件页工厂登记 + 惰性构造（`_collect_plugin_factories` / `_ensure_page_built`）；
- 导航轨重建与排序（`_rebuild_nav` / `_plugin_order` / `_default_page`）；
- 切页（`_show_page` / `_on_page_requested` / `show_page`）；
- 右侧「次要界面」抽屉联动（ADR-002：`_sync_side_panel` / `toggle_side_panel`）。

依赖宿主对象（MainWindow）提供：`stack` / `nav` / `ui` / `kernel` / `config` /
`side_panel` / `_page_widgets` / `_plugin_factories` / `_failed_pages` /
`_side_panel_widgets` / `_explicit_page`，以及 `tr()` / `_theme_token()` / `toggle_main_top`。
mixin 不定义 `__init__`，不持有状态。
"""
from __future__ import annotations

import logging

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QWidget

from app import icon_factory
from core.plugin_manager import PluginState

logger = logging.getLogger(__name__)


class PagesMixin:
    """页面注册、导航轨与抽屉联动。"""

    # ---------------- 界面：页面 ----------------
    def _add_builtin_page(self, page_id: str, widget: QWidget) -> None:
        self.stack.addWidget(widget)
        self._page_widgets[page_id] = widget

    def _collect_plugin_factories(self) -> None:
        """登记插件页工厂（不构造）；控件在首次切换到该页时才创建。

        同时回收已卸载插件留下的页面控件。
        """
        self._plugin_factories.clear()
        self._failed_pages.clear()          # 插件重载/启停后允许重试构造
        # 兜底：清掉无归属（plugin_id=None）或已停用插件的贡献点，避免残留点不动的入口
        active_ids = {pid for pid, rec in self.kernel.plugins.records.items()
                      if rec.state == PluginState.ACTIVE}
        self.ui.retain_only(active_ids)
        # 次要面板缓存同步收敛：不再注册的插件把缓存控件丢掉
        live_panels = set(self.ui.side_panels())
        for pid in list(self._side_panel_widgets):
            if pid not in live_panels:
                widget = self._side_panel_widgets.pop(pid)
                widget.hide()
                widget.deleteLater()
        for label, factory, _icon, plugin_id in self.ui.pages():
            self._plugin_factories[f"plugin:{plugin_id}"] = (factory, False)
        for page_id in [pid for pid in self._page_widgets if pid.startswith("plugin:")]:
            if page_id not in self._plugin_factories:
                widget = self._page_widgets.pop(page_id)
                self.stack.removeWidget(widget)
                widget.deleteLater()

    def _ensure_page_built(self, page_id: str) -> QWidget | None:
        """惰性构造插件页；内置页在 init_ui 中已建好。构造失败返回 None。"""
        widget = self._page_widgets.get(page_id)
        if widget is not None:
            return widget
        entry = self._plugin_factories.get(page_id)
        if entry is None:
            return None
        factory, built = entry
        if built:
            return None
        try:
            widget = factory()
        except Exception:
            # 单个插件页构造失败不应拖垮整个外壳：记日志 + 标记失败页（可重试）+ 状态栏可见
            logger.exception("插件页构造失败: %s", page_id)
            self._failed_pages.add(page_id)
            self._plugin_factories[page_id] = (factory, True)
            self.statusBar().showMessage(self.tr("app.nav.page_build_failed"), 5000)
            return None
        self._plugin_factories[page_id] = (factory, True)
        self.stack.addWidget(widget)
        self._page_widgets[page_id] = widget
        return widget

    # ---------------- 界面：导航轨 ----------------
    def _nav_icon(self, name: str | None, checkable: bool) -> QIcon:
        """按主题令牌染色；name 为空或图标缺失时返回空图标。"""
        if not name or not isinstance(name, str):
            return QIcon()
        secondary = self._theme_token("color.text.secondary", "#616161")
        accent = self._theme_token("color.accent", "#0078d4")
        if checkable:
            return icon_factory.checkable_icon(name, secondary, accent)
        return icon_factory.icon(name, secondary)

    def _plugin_order(self) -> list[str]:
        """用户排的插件顺序（侧边栏拖动或插件管理面板都会写这里）。"""
        value = self.config.get("plugins", {}) or {}
        order = value.get("order", [])
        return order if isinstance(order, list) else []

    def _rebuild_nav(self) -> None:
        pages = []
        # 插件页按用户排序（plugins.order）排，未进列表的保持注册顺序在后；
        # 插件条目可直接在轨上拖动排序
        order = self._plugin_order()
        entries = []
        for label, _factory, icon, plugin_id in self.ui.pages():
            page_id = f"plugin:{plugin_id}"
            entry = self._plugin_factories.get(page_id)
            if entry is not None and entry[1] and page_id not in self._page_widgets:
                continue   # 构造失败过的页不再上轨道
            entries.append((plugin_id, {"id": page_id, "text": label,
                                        "icon": self._nav_icon(icon, checkable=True),
                                        "draggable": True, "drag_key": plugin_id}))
        entries.sort(key=lambda item: (order.index(item[0])
                                       if item[0] in order else len(order)))
        pages.extend(item[1] for item in entries)

        # 动作型入口（独立窗口插件）同样按用户顺序排、可拖动
        action_entries = []
        for label, handler, icon, plugin_id in self.ui.tools():
            action_entries.append((plugin_id, {"text": label,
                                               "icon": self._nav_icon(icon, checkable=False),
                                               "handler": handler,
                                               "draggable": True, "drag_key": plugin_id}))
        action_entries.sort(key=lambda item: (order.index(item[0])
                                              if item[0] in order else len(order)))
        actions = [item[1] for item in action_entries]

        bottom = [
            {"text": self.tr("app.toolbar.topmost"),
             "icon": self._nav_icon("pin", checkable=True),
             "checkable": True,
             "checked": bool(self.config.get("main_on_top", False)),
             "handler": self.toggle_main_top},
            {"id": "plugins", "text": self.tr("app.nav.plugins"),
             "icon": self._nav_icon("puzzle", checkable=True)},
            {"id": "settings", "text": self.tr("app.menu.settings"),
             "icon": self._nav_icon("settings", checkable=True)},
        ]
        self.nav.set_entries(pages, actions, bottom)
        self.nav.set_toggle_icon(self._nav_icon("menu", checkable=False),
                                 self.tr("app.nav.expand"))
        # 重建后保持当前页：优先用户显式选择的，其次第一个插件页，最后「插件管理」
        # 可用页 = 插件页 + 内置页（`plugins` / `settings` 在 bottom 里）。
        # 历史实现只统计插件页 ⇒ 内置页永远被判"不可用"，于是每次 plugins.changed
        # （点一次启停开关、改排序、装插件）都把用户从插件管理页弹回插件页。
        available = [entry["id"] for entry in pages if entry.get("id")]
        available += [entry["id"] for entry in bottom if entry.get("id")]
        current = self._explicit_page
        if current not in available:
            current = self._default_page()
        self._show_page(current)

    def _default_page(self) -> str:
        """内容区第一页：第一个**可用**插件页；都不可用时落到「插件管理」。

        不能直接返回 `_plugin_factories` 的第一项：那可能是构造失败过的页，
        随后 `_show_page` 会去 `_page_widgets` 取它并抛 KeyError（启动期直接崩）。
        """
        for page_id in self._plugin_factories:
            if page_id in self._failed_pages:
                continue
            return page_id
        return "plugins"

    def _on_nav_order_changed(self, keys: list[str]) -> None:
        """侧边栏拖动排序落盘：keys 是插件 id 的新顺序。"""
        self.kernel.plugins.set_order([k for k in keys if isinstance(k, str)])

    def _show_page(self, page_id: str) -> None:
        """内部切页：不改用户显式选择，用于导航重建后的复位。"""
        widget = self._ensure_page_built(page_id)
        if widget is None:
            page_id = self._default_page()
            widget = self._page_widgets.get(page_id) or self._page_widgets.get("plugins")
        if widget is None:
            logger.error("没有可显示的页面（请求 %s）", page_id)
            return
        self.nav.set_current_page(page_id)
        self.stack.setCurrentWidget(widget)
        self._sync_side_panel(page_id)

    # ---------------- 右侧「次要界面」抽屉（ADR-002） ----------------
    @staticmethod
    def _plugin_id_of(page_id: str) -> str | None:
        return page_id.split(":", 1)[1] if page_id.startswith("plugin:") else None

    def _sync_side_panel(self, page_id: str) -> None:
        """按当前页切换抽屉内容；该页没有注册次要界面时整块隐藏。"""
        plugin_id = self._plugin_id_of(page_id)
        entry = self.ui.side_panel_for(plugin_id) if plugin_id else None
        if entry is None:
            self.side_panel.set_content(None)
            return
        title, factory, width = entry
        widget = self._side_panel_widgets.get(plugin_id)
        if widget is None:
            try:
                widget = factory()
            except Exception:
                logger.exception("次要面板构造失败: %s", plugin_id)
                self.side_panel.set_content(None)
                return
            if widget is None:
                self.side_panel.set_content(None)
                return
            self._side_panel_widgets[plugin_id] = widget
        self.side_panel.set_content(widget, title)
        self.side_panel.set_content_width(width)

    def toggle_side_panel(self) -> None:
        """展开/收起抽屉（Ctrl+Shift+B）；当前页没有次要界面时给一句提示。"""
        if not self.side_panel.has_content():
            self.statusBar().showMessage(self.tr("app.nav.page_unavailable"), 3000)
            return
        self.side_panel.toggle()

    def _on_page_requested(self, page_id: str) -> None:
        """用户点击导航或外部直达：记住显式选择后再切页。"""
        self._explicit_page = page_id
        self._show_page(page_id)

    def show_page(self, page_id: str) -> None:
        """切到指定页并抬出主窗口；目标不可用时给出提示（不要静默无反应）。"""
        if page_id in self._page_widgets or page_id in self._plugin_factories:
            self._on_page_requested(page_id)
            self.show()
            self.raise_()
            return
        logger.warning("show_page 目标不可用: %s", page_id)
        self.statusBar().showMessage(self.tr("app.nav.page_unavailable"), 5000)
