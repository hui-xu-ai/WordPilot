# main_window.py
"""应用外壳（前端）。

信息架构（无边框 Fluent 布局）：
    ┌─────────────────────────────────────────────┐
    │ 自绘标题栏（图标+标题 / 最小化 最大化 关闭）│
    ├──────┬──────────────────────────────────────┤
    │ 图标 │ 内容区（QStackedWidget）             │
    │ 导航 │  插件页（智能纠错/翻译改写/图片工具…）│
    │ 轨   │  插件管理 / 设置（轨底内置直达）     │
    │ ──── │                                      │
    │ 置顶 │                                      │
    │ 插件 │                                      │
    │ 设置 │                                      │
    ├──────┴──────────────────────────────────────┤
    │ 状态栏（右下角 QSizeGrip 拖拽缩放）         │
    └─────────────────────────────────────────────┘

设计约定：
- 无边框窗口：标题栏自绘（app/title_bar.py），缩放靠状态栏右下角 QSizeGrip；
- 左侧是可折叠图标导航轨（NavRail）：窄态纯图标 + tooltip，
  汉堡键展开为「图标 + 文字」；插件页直接挂在轨上，点图标即达，
  插件条目可按住拖动排序，松手即保存（plugins.order）；
- 智能纠错是核心功能，但它也是普通插件：页面由 proofread 插件注册，
  禁用插件后侧边栏入口随之消失（外壳零硬编码）；
- 设置是主窗口内的一个页面（不再是弹窗），保存后立即热生效；
- 图标全部是 assets/icons/ 下的 SVG 线条稿，经 icon_factory 按主题令牌染色；
- 外壳不认识任何插件内部模块，功能一律经 ctx.ui 贡献点挂载；
- 插件启停/排序变化经 `plugins.changed` 事件驱动导航重建；
- 所有可见文案走 i18n（self.tr），切换语言时 retranslateUi() 统一刷新；
- 新增一个功能时，本文件应当零改动。
"""
import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QMainWindow, QMenu,
                               QSizeGrip, QStackedWidget, QSystemTrayIcon,
                               QVBoxLayout, QWidget)

from __version__ import __version__
from app import icon_factory
from app.nav_rail import NavRail
from app.plugin_manager_page import PluginManagerPage
from app.settings_page import SettingsPage
from app.title_bar import TitleBar
from app.ui_registry import QtUiRegistry
from core.kernel import Kernel
from core.plugin_manager import PluginState
from core.services.config_manager import ConfigManager
from infra.utils import resource_path

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    WINDOW_ID = "app.main"

    def __init__(self, kernel: Kernel):
        super().__init__()
        self.kernel = kernel
        self.config: ConfigManager = kernel.config
        self.i18n = kernel.services.get("i18n")
        self._window_id = self.WINDOW_ID
        # 无边框窗口：标题栏自绘；保留 Window flag 以拥有任务栏按钮
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setWindowTitle(self._window_title())
        self.setMinimumSize(720, 520)

        # UI 贡献点收集器：插件加载后由 build_extension_menus 统一渲染
        self.ui = QtUiRegistry(self)
        self.ui.bind_i18n(self.i18n)
        self._unsubscribes: list = []

        # 页面注册表：page_id -> stack 中的 QWidget（内置页 + 插件页）
        self._page_widgets: dict[str, QWidget] = {}
        # 插件页工厂缓存：惰性构造（首次切到该页才建）；page_id = plugin:<插件id>
        self._plugin_factories: dict[str, tuple] = {}   # page_id -> (factory, built)
        # 构造失败的插件页：默认页与回退都要跳过它，插件重载后可重试
        self._failed_pages: set[str] = set()
        # 用户显式选中的页（点击导航 / 外部直达）；None 表示走默认页逻辑。
        # 外壳构造时插件还没加载，此时默认页会先落到「插件管理」，
        # 等插件加载完重建导航，默认页应让位给第一个插件页（智能纠错）。
        self._explicit_page: str | None = None

        self.init_ui()
        self.setup_tray()
        self.apply_style()
        # 控件文本统一由 retranslateUi 填充（构造时只搭骨架，不写文案）
        self.retranslateUi()

        # 把状态栏输出能力提供给插件（插件通过 ctx.ui.status 调用）
        self.ui.set_status_handler(self.statusbar.showMessage)

        self._subscribe_events()

    # ---------------- 翻译辅助 ----------------
    def tr(self, key: str, /, **params) -> str:
        if self.i18n is None:
            return key
        return self.i18n.tr(key, **params)

    def _window_title(self) -> str:
        """标题栏只显示软件名：版本号与作者在「设置 → 关于」里（单一来源 __version__.py）。"""
        return self.tr("app.title")

    def _ready_message(self) -> str:
        return self.tr("app.status.ready", author=self.tr("app.author"))

    def _theme_token(self, key: str, default: str) -> str:
        theme = self.kernel.services.get("theme")
        if theme is None:
            return default
        return theme.token(key, default)

    def _subscribe_events(self):
        """订阅内核事件；保存退订闭包，窗口销毁时注销。"""
        self._unsubscribes.append(
            self.kernel.events.subscribe("i18n.language_changed",
                                         lambda _e, _p: self.retranslateUi())
        )
        self._unsubscribes.append(
            self.kernel.events.subscribe("theme.changed",
                                         lambda _e, _p: self.apply_style())
        )
        # 插件启用/禁用/排序/安装变化 -> 重建导航与托盘
        self._unsubscribes.append(
            self.kernel.events.subscribe("plugins.changed",
                                         lambda _e, _p: self.build_extension_menus())
        )

    # ---------------- 插件集成 ----------------
    def bind_plugins(self):
        """插件加载完成后调用：渲染贡献点（页面/工具/托盘全部来自插件注册）。"""
        self.build_extension_menus()

    def build_extension_menus(self):
        """把插件注册的界面入口渲染出来。

        可重复调用：每次先清再建（切换语言、插件启停/排序都会触发）。
        插件页控件只构造一次并缓存，这里只重建导航轨条目与托盘。
        """
        self.tray_menu.clear()
        for label, handler in self.ui.tray_actions():
            self.tray_menu.addAction(label, handler)
        self.tray_menu.addSeparator()
        self.tray_menu.addAction(self.tr("app.tray.show_main"), self.show)
        self.tray_menu.addAction(self.tr("app.tray.quit"), self.quit_app)

        self._collect_plugin_factories()
        self._rebuild_nav()

    # ---------------- 界面：骨架 ----------------
    def init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        shell = QVBoxLayout(central)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        # 自绘标题栏
        self.title_bar = TitleBar(self)
        self.title_bar.set_icon(QPixmap(resource_path("assets/tray_icon.png")))
        self.title_bar.minimizeRequested.connect(self.showMinimized)
        self.title_bar.maximizeToggled.connect(self._toggle_maximize)
        self.title_bar.closeRequested.connect(self.close)   # 关闭语义交给 WindowManager
        shell.addWidget(self.title_bar)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        shell.addLayout(body, 1)

        # 左侧可折叠图标导航轨
        self.nav = NavRail()
        self.nav.set_collapsed(bool(self.config.get("nav.collapsed", True)))
        self.nav.pageRequested.connect(self._on_page_requested)
        self.nav.collapsedChanged.connect(
            lambda collapsed: self.config.set("nav.collapsed", collapsed))
        self.nav.orderChanged.connect(self._on_nav_order_changed)
        body.addWidget(self.nav)

        # 内容区
        self.stack = QStackedWidget()
        body.addWidget(self.stack, 1)

        # 右侧「次要界面」抽屉（ADR-002）：默认收起，只留 36px 竖条
        from app.side_panel import DEFAULT_WIDTH, SidePanelHost

        self.side_panel = SidePanelHost(
            self,
            width=int(self.config.get("ui.side_panel.width", DEFAULT_WIDTH)),
            opened=bool(self.config.get("ui.side_panel.open", False)),
        )
        self.side_panel.toggled.connect(
            lambda opened: self.config.set("ui.side_panel.open", bool(opened)))
        body.addWidget(self.side_panel)
        self._side_panel_widgets: dict[str, QWidget] = {}

        shortcut = QShortcut(QKeySequence("Ctrl+Shift+B"), self)
        shortcut.activated.connect(self.toggle_side_panel)

        # 内置页：插件管理 + 设置（功能页全部由插件经贡献点注册）
        self._add_builtin_page("plugins", PluginManagerPage(
            self.kernel, parent=None, i18n=self.i18n))
        settings_page = SettingsPage(
            self.config, parent=None,
            theme=self.kernel.services.get("theme"),
            i18n=self.i18n,
            skins=self.kernel.services.get("skins"))
        settings_page.saved.connect(self._on_settings_saved)
        self._add_builtin_page("settings", settings_page)

        # 状态栏：右下角放 QSizeGrip（无边框窗口的缩放手柄）
        self.statusbar = self.statusBar()
        self.statusbar.showMessage(self._ready_message())
        grip = QSizeGrip(self)
        self.statusbar.addPermanentWidget(grip)

    def _toggle_maximize(self):
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()
        self._sync_max_icon()

    def _sync_max_icon(self):
        """最大化/还原时切换标题栏图标。"""
        name = "win-restore" if self.isMaximized() else "win-max"
        self.title_bar.set_maximized_icon(
            icon_factory.icon(name, self._theme_token("color.text.secondary", "#616161")),
            self.isMaximized())

    def changeEvent(self, event):        # noqa: N802 - Qt 命名惯例
        super().changeEvent(event)
        if event.type() == event.Type.WindowStateChange and hasattr(self, "title_bar"):
            self._sync_max_icon()

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

        # 动作型入口（导航助手等独立窗口插件）同样按用户顺序排、可拖动
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

    # ---------------- 界面：多语言 ----------------
    def retranslateUi(self):        # noqa: N802 - Qt 命名惯例
        """切换语言后刷新全部可见文案。"""
        title = self._window_title()
        self.setWindowTitle(title)
        if hasattr(self, "title_bar"):
            self.title_bar.set_title(title)
            self.title_bar.set_tooltips(
                self.tr("app.titlebar.minimize"),
                self.tr("app.titlebar.maximize"),
                self.tr("app.titlebar.close"))

        # 内置页与插件页：实现 retranslateUi 的页面统一刷新
        for page_id in ("plugins", "settings"):
            page = self._page_widgets.get(page_id)
            if page is not None and hasattr(page, "retranslateUi"):
                page.retranslateUi()
        for page_id, widget in self._page_widgets.items():
            if page_id.startswith("plugin:") and hasattr(widget, "retranslateUi"):
                widget.retranslateUi()

        self.tray_icon.setToolTip(self.tr("app.tray.tooltip"))
        self.statusbar.showMessage(self._ready_message())
        self.build_extension_menus()   # 插件入口文案同样需要重翻译

    def toggle_main_top(self, checked=False):
        """主窗口置顶：只改配置，实际置顶由 WindowManager 按 Layer 策略施加。"""
        self.config.set("main_on_top", bool(checked))
        wm = self.kernel.services.get("window_manager")
        if wm is not None:
            wm.refresh_topmost(self._window_id)

    # ---------------- 托盘 ----------------
    def setup_tray(self):
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(QIcon(resource_path("assets/tray_icon.png")))
        self.tray_icon.setToolTip(self.tr("app.tray.tooltip"))
        # 菜单内容由 build_extension_menus 在插件加载完成后填充
        self.tray_menu = QMenu()
        self.tray_icon.setContextMenu(self.tray_menu)
        self.tray_icon.activated.connect(self.on_tray_activated)
        self.tray_icon.show()

    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self.show()

    def _on_hidden(self):
        """关闭转隐藏后的提示（关闭语义由 WindowManager 统一裁决）。"""
        if hasattr(self, "tray_icon") and self.tray_icon.isVisible():
            self.tray_icon.showMessage(self.tr("app.tray.background_title"),
                                       self.tr("app.tray.background_body"),
                                       QSystemTrayIcon.Information, 2000)
        else:
            self.quit_app()

    def quit_app(self):
        """退出：幂等 + 关键清理放 finally，避免某一步抛异常就留下隐藏窗口与托盘图标。"""
        if getattr(self, "_quitting", False):
            return
        self._quitting = True
        try:
            for unsubscribe in self._unsubscribes:
                try:
                    unsubscribe()
                except Exception:
                    logger.exception("退出时退订失败")
            self._unsubscribes.clear()
            wm = self.kernel.services.get("window_manager")
            if wm is not None:
                wm.close_all(force=True)
            self.kernel.shutdown()
        finally:
            try:
                self.tray_icon.hide()
            except Exception:
                logger.exception("托盘图标隐藏失败")
            QApplication.quit()

    # ---------------- 外观 / 窗口治理 ----------------
    def apply_style(self):
        """全局外观由主题服务统一渲染；图标染色缓存随主题一起失效。"""
        theme = self.kernel.services.get("theme")
        if theme is not None:
            theme.reload()
        icon_factory.clear_cache()
        # 主题变了图标颜色也要跟着变（深/浅主题的次要文本色不同）
        if hasattr(self, "nav"):
            self._rebuild_nav()
        if hasattr(self, "title_bar"):
            secondary = self._theme_token("color.text.secondary", "#616161")
            self.title_bar.set_button_icons(
                icon_factory.icon("win-min", secondary),
                icon_factory.icon("win-max", secondary),
                icon_factory.icon("win-close", secondary))
            self._sync_max_icon()
        # 分区色自绘注入（不依赖 QSS 是否给 QWidget 子类绘制背景，见 title_bar.set_colors）
        # border 传 None：极淡分层下不再画标题栏底边 / 侧栏右边的结构线，只靠底色区分
        if hasattr(self, "title_bar"):
            self.title_bar.set_colors(
                self._theme_token("color.bg.titlebar", "#eff1f5"), None)
        if hasattr(self, "nav"):
            self.nav.set_colors(self._theme_token("color.bg.rail", "#f7f8fa"), None)

    def register_to_window_manager(self):
        """把主窗口纳入窗口治理（在 kernel.attach_ui 之后调用）。"""
        wm = self.kernel.services.get("window_manager")
        if wm is None:
            return None
        from core.services.window_manager import CloseBehavior, Layer

        wm.register(self._window_id, self, owner="app", layer=Layer.TOPMOST,
                    behavior=CloseBehavior.HIDE, remember_geometry=True,
                    on_hide=self._on_hidden)
        return wm

    # ---------------- 设置保存后的热应用 ----------------
    def _on_settings_saved(self):
        """设置页点保存：写盘已完成，这里负责让改动立即生效。"""
        # 重建 API 客户端，内核会广播 ai.client_changed 让各插件刷新引用
        self.kernel.reload_ai_client()
        # 热键组合可能已变更，按新配置重新绑定
        hotkeys = self.kernel.services.get("hotkeys")
        if hotkeys is not None:
            hotkeys.apply()
        # 界面语言：内核广播 i18n.language_changed，各常驻界面自行重翻译
        if self.i18n is not None:
            self.kernel.set_language(self.config.get("ui_language", "zh_CN"))
        # 桌宠皮肤：内核广播 skin.changed，桌宠窗口收到后重绘
        self.kernel.set_skin(self.config.get("skin", "default"))
        # 主题可能已变更（设置页内部已应用），这里同步外壳
        self.apply_style()
        self.retranslateUi()
        # 广播一次：需要响应设置变更的插件自行订阅（例如桌宠按需显示/隐藏），
        # 外壳不认识任何具体插件
        self.kernel.events.emit("settings.saved", None)

    def open_settings(self):
        """兼容旧调用（桌宠右键「设置」等）：设置现在是主窗口内的一页。"""
        self.show_page("settings")

    def showEvent(self, event):
        super().showEvent(event)
        # 无边框窗口在 Windows 上没有系统阴影：白底上看不出边界。
        # 首次显示时向 DWM 要回原生阴影（失败只是没阴影，不影响功能）。
        if not getattr(self, "_shadow_applied", False):
            self._shadow_applied = True
            if self.config.get("ui.native_shadow", True):
                from infra import win_shadow

                self._shadow_ok = win_shadow.enable_native_shadow(self)
        wm = self.kernel.services.get("window_manager")
        if wm is not None:
            wm.refresh_topmost(self._window_id)

    def nativeEvent(self, eventType, message):      # noqa: N802 - Qt 命名惯例
        """把 WM_NCCALCSIZE 压成 0：保留无边框外观，同时让 DWM 画阴影。"""
        from infra import win_shadow

        handled = win_shadow.handle_native_event(eventType, message)
        if handled is not None:
            return handled
        return super().nativeEvent(eventType, message)
