# main_window.py
"""应用外壳（前端）。

信息架构（现代桌面软件布局）：
    ┌──────┬──────────────────────────────────────┐
    │ 图标 │ 内容区（QStackedWidget）             │
    │ 导航 │  仪表盘(校对状态+参数+清洗+API监控) / │
    │ 轨   │  插件页(翻译改写/图片工具…) / 插件管理│
    │ ──── │                                      │
    │ 置顶 │                                      │
    │ 设置 │                                      │
    ├──────┴──────────────────────────────────────┤
    │ 状态栏                                      │
    └─────────────────────────────────────────────┘

设计约定：
- 左侧是可折叠图标导航轨（NavRail）：窄态纯图标 + tooltip，
  汉堡键展开为「图标 + 文字」；插件页直接挂在轨上，点图标即达；
- 智能校对是核心功能：校对状态、参数、缓存清洗、API 监控全部收在仪表盘；
- 图标全部是 assets/icons/ 下的 SVG 线条稿，经 icon_factory 按主题令牌染色；
- 外壳不认识任何插件内部模块，功能一律经 ctx.ui 贡献点挂载；
- 插件启停/排序变化经 `plugins.changed` 事件驱动导航重建；
- 所有可见文案走 i18n（self.tr），切换语言时 retranslateUi() 统一刷新；
- 新增一个功能时，本文件应当零改动。
"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QApplication, QFormLayout, QGroupBox,
                               QHBoxLayout, QLabel, QMainWindow, QMenu,
                               QMessageBox, QPushButton, QSlider, QSpinBox,
                               QStackedWidget, QSystemTrayIcon, QVBoxLayout,
                               QWidget)

from __version__ import __version__
from app import icon_factory
from app.monitor_window import MonitorWindow
from app.nav_rail import NavRail
from app.plugin_manager_page import PluginManagerPage
from app.settings_dialog import SettingsDialog
from app.ui_registry import QtUiRegistry
from core.kernel import Kernel
from core.services.api_logger import clear_api_log
from core.services.config_manager import ConfigManager
from infra.utils import resource_path


class MainWindow(QMainWindow):
    WINDOW_ID = "app.main"

    def __init__(self, kernel: Kernel):
        super().__init__()
        self.kernel = kernel
        self.config: ConfigManager = kernel.config
        self.ignore_mgr = kernel.services.get("ignore")
        self.i18n = kernel.services.get("i18n")
        self._window_id = self.WINDOW_ID
        self.setWindowTitle(self._window_title())
        self.setMinimumSize(680, 480)

        # UI 贡献点收集器：插件加载后由 build_extension_menus 统一渲染
        self.ui = QtUiRegistry(self)
        self.ui.bind_i18n(self.i18n)
        self._proofread = None
        self._unsubscribes: list = []

        # 页面注册表：page_id -> stack 中的 QWidget（内置页 + 插件页）
        self._page_widgets: dict[str, QWidget] = {}
        # 插件页工厂缓存：惰性构造（首次切到该页才建）；page_id = plugin:<插件id>
        self._plugin_factories: dict[str, tuple] = {}   # page_id -> (factory, built)

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
        return self.tr("app.window_title", name=self.tr("app.name"),
                       version=__version__, author=self.tr("app.author"))

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
        """插件加载完成后调用：绑定能力、渲染贡献点、同步初始状态。"""
        self._proofread = self.kernel.capabilities.get("proofread")
        self.kernel.events.subscribe("proofread.toggled", self._on_proof_toggled)
        self.build_extension_menus()
        if self._proofread is not None:
            self._sync_proof_state(self._proofread.is_enabled())

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

    def _on_proof_toggled(self, _event, enabled):
        self._sync_proof_state(bool(enabled))

    def _sync_proof_state(self, on: bool):
        """只更新仪表盘上的校对状态外观，不回环触发 toggled 信号。"""
        self.dashboard_status.setText(
            self.tr("app.proof.on") if on else self.tr("app.proof.off"))
        self.dashboard_status.setProperty("state", "ok" if on else "off")
        self.dashboard_status.style().unpolish(self.dashboard_status)
        self.dashboard_status.style().polish(self.dashboard_status)
        self.proof_toggle.blockSignals(True)
        self.proof_toggle.setChecked(on)
        self.proof_toggle.setText(self.tr("app.proof.on") if on
                                  else self.tr("app.proof.off"))
        self.proof_toggle.setProperty("state", "ok" if on else "off")
        self.proof_toggle.style().unpolish(self.proof_toggle)
        self.proof_toggle.style().polish(self.proof_toggle)
        self.proof_toggle.blockSignals(False)

    # ---------------- 界面：骨架 ----------------
    def init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        body = QHBoxLayout(central)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        # 左侧可折叠图标导航轨
        self.nav = NavRail()
        self.nav.set_collapsed(bool(self.config.get("nav.collapsed", True)))
        self.nav.pageRequested.connect(self._on_page_requested)
        self.nav.collapsedChanged.connect(
            lambda collapsed: self.config.set("nav.collapsed", collapsed))
        body.addWidget(self.nav)

        # 内容区
        self.stack = QStackedWidget()
        body.addWidget(self.stack, 1)

        # 内置页：仪表盘（智能校对总面板）+ 插件管理
        self._add_builtin_page("dashboard", self._build_dashboard_page())
        self._add_builtin_page("plugins", PluginManagerPage(
            self.kernel, parent=None, i18n=self.i18n))

        # API 监控已并入仪表盘；托盘保留一个入口，点开即跳到仪表盘
        self.ui.add_tray_action("app.nav.monitor", lambda: self.show_page("dashboard"),
                                order=50, plugin_id="app")

        # 状态栏
        self.statusbar = self.statusBar()
        self.statusbar.showMessage(self._ready_message())

    # ---------------- 界面：页面 ----------------
    def _add_builtin_page(self, page_id: str, widget: QWidget) -> None:
        self.stack.addWidget(widget)
        self._page_widgets[page_id] = widget

    def _build_dashboard_page(self) -> QWidget:
        """仪表盘：智能校对的总面板（状态 + 参数 + 清洗 + API 监控）。"""
        page = QWidget()
        page.setObjectName("pageDashboard")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        # ---- 校对状态 ----
        status_group = QGroupBox()
        self.proof_group = status_group
        status_layout = QVBoxLayout(status_group)
        status_row = QHBoxLayout()
        self.dashboard_status = QLabel()
        self.dashboard_status.setObjectName("dashboardStatus")
        self.dashboard_status.setProperty("state", "ok")
        status_row.addWidget(self.dashboard_status, 0, Qt.AlignLeft)
        status_row.addStretch()
        # 校对开关放在这里（顶部工具栏已取消，不再重复显示）
        self.proof_toggle = QPushButton()
        self.proof_toggle.setObjectName("proofToggle")
        self.proof_toggle.setCheckable(True)
        self.proof_toggle.setChecked(True)
        self.proof_toggle.toggled.connect(self.toggle_proof)
        status_row.addWidget(self.proof_toggle)
        status_layout.addLayout(status_row)
        self.dashboard_hint = QLabel()
        self.dashboard_hint.setObjectName("hintLabel")
        self.dashboard_hint.setWordWrap(True)
        status_layout.addWidget(self.dashboard_hint)

        params_layout = QFormLayout()
        debounce_layout = QHBoxLayout()
        debounce_slider = QSlider(Qt.Horizontal)
        debounce_slider.setRange(200, 5000)
        debounce_slider.setValue(self.config.get("debounce_ms", 500))
        debounce_label = QLabel(f"{debounce_slider.value()} ms")
        debounce_slider.valueChanged.connect(lambda v: (
            self.config.set("debounce_ms", v),
            debounce_label.setText(f"{v} ms")
        ))
        debounce_layout.addWidget(debounce_slider)
        debounce_layout.addWidget(debounce_label)
        self.debounce_label = QLabel()
        params_layout.addRow(self.debounce_label, debounce_layout)

        ctx_spin = QSpinBox()
        ctx_spin.setRange(0, 5)
        ctx_spin.setValue(self.config.get("context_before", 0))
        ctx_spin.valueChanged.connect(lambda v: (self.config.set("context_before", v),
                                                 self.config.set("context_after", v)))
        self.context_label = QLabel()
        params_layout.addRow(self.context_label, ctx_spin)
        status_layout.addLayout(params_layout)
        layout.addWidget(status_group)

        # ---- 缓存清洗（智能校对的维护功能，与校对收在一起） ----
        cleanup_group = QGroupBox()
        self.cleanup_group = cleanup_group
        cleanup_layout = QHBoxLayout(cleanup_group)
        self.cleanup_ignore_btn = QPushButton()
        self.cleanup_log_btn = QPushButton()
        self.cleanup_all_btn = QPushButton()
        self.cleanup_ignore_btn.clicked.connect(self.clear_ignore_only)
        self.cleanup_log_btn.clicked.connect(self.clear_api_log_only)
        self.cleanup_all_btn.clicked.connect(self.clear_all_cache)
        for btn in (self.cleanup_ignore_btn, self.cleanup_log_btn, self.cleanup_all_btn):
            cleanup_layout.addWidget(btn)
        layout.addWidget(cleanup_group)

        # ---- API 监控（并入仪表盘，不再是独立页面） ----
        monitor_group = QGroupBox()
        self.monitor_group = monitor_group
        monitor_layout = QVBoxLayout(monitor_group)
        self.monitor_widget = MonitorWindow(i18n=self.i18n)
        self.monitor_widget.close_btn.setVisible(False)    # 内嵌页不需要关闭按钮
        self.monitor_widget.setWindowTitle("")
        monitor_layout.addWidget(self.monitor_widget)
        layout.addWidget(monitor_group, 1)
        return page

    def _collect_plugin_factories(self) -> None:
        """登记插件页工厂（不构造）；控件在首次切换到该页时才创建。

        同时回收已卸载插件留下的页面控件。
        """
        self._plugin_factories.clear()
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
            # 单个插件页构造失败不应拖垮整个外壳
            self._plugin_factories[page_id] = (factory, True)
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
        """用户在插件管理面板里排的顺序。"""
        value = self.config.get("plugins", {}) or {}
        order = value.get("order", [])
        return order if isinstance(order, list) else []

    def _rebuild_nav(self) -> None:
        pages = [
            {"id": "dashboard", "text": self.tr("app.nav.dashboard"),
             "icon": self._nav_icon("dashboard", checkable=True)},
        ]
        # 插件页按用户排序（plugins.order）排，未进列表的保持注册顺序在后
        order = self._plugin_order()
        entries = []
        for label, _factory, icon, plugin_id in self.ui.pages():
            page_id = f"plugin:{plugin_id}"
            entry = self._plugin_factories.get(page_id)
            if entry is not None and entry[1] and page_id not in self._page_widgets:
                continue   # 构造失败过的页不再上轨道
            entries.append((plugin_id, {"id": page_id, "text": label,
                                        "icon": self._nav_icon(icon, checkable=True)}))
        entries.sort(key=lambda item: (order.index(item[0])
                                       if item[0] in order else len(order)))
        pages.extend(item[1] for item in entries)
        pages.append({"id": "plugins", "text": self.tr("app.nav.plugins"),
                      "icon": self._nav_icon("puzzle", checkable=True)})

        # 动作型入口（导航助手等独立窗口插件）同样按用户顺序排
        action_entries = []
        for label, handler, icon, plugin_id in self.ui.tools():
            action_entries.append((plugin_id, {"text": label,
                                               "icon": self._nav_icon(icon, checkable=False),
                                               "handler": handler}))
        action_entries.sort(key=lambda item: (order.index(item[0])
                                              if item[0] in order else len(order)))
        actions = [item[1] for item in action_entries]

        bottom = [
            {"text": self.tr("app.toolbar.topmost"),
             "icon": self._nav_icon("pin", checkable=True),
             "checkable": True,
             "checked": bool(self.config.get("main_on_top", False)),
             "handler": self.toggle_main_top},
            {"text": self.tr("app.menu.settings"),
             "icon": self._nav_icon("settings", checkable=False),
             "handler": self.open_settings},
        ]
        self.nav.set_entries(pages, actions, bottom)
        self.nav.set_toggle_icon(self._nav_icon("menu", checkable=False),
                                 self.tr("app.nav.expand"))
        # 重建后保持当前页
        current = self.nav.current_page() or "dashboard"
        self._on_page_requested(current)

    def _on_page_requested(self, page_id: str) -> None:
        widget = self._ensure_page_built(page_id)
        if widget is None:
            page_id = "dashboard"
            widget = self._page_widgets[page_id]
        self.nav.set_current_page(page_id)
        self.stack.setCurrentWidget(widget)

    def show_page(self, page_id: str) -> None:
        """切到指定页并抬出主窗口。"""
        if page_id in self._page_widgets or page_id in self._plugin_factories:
            self._on_page_requested(page_id)
            self.show()
            self.raise_()

    # ---------------- 界面：多语言 ----------------
    def retranslateUi(self):        # noqa: N802 - Qt 命名惯例
        """切换语言后刷新全部可见文案。"""
        self.setWindowTitle(self._window_title())

        # 仪表盘
        self.proof_group.setTitle(self.tr("app.proof.group"))
        self.dashboard_hint.setText(self.tr("app.dashboard.hint"))
        self.debounce_label.setText(self.tr("app.proof.debounce_label"))
        self.context_label.setText(self.tr("app.proof.context_label"))
        self.cleanup_group.setTitle(self.tr("app.cleanup.group"))
        self.cleanup_ignore_btn.setText(self.tr("app.cleanup.ignore_short"))
        self.cleanup_log_btn.setText(self.tr("app.cleanup.log_short"))
        self.cleanup_all_btn.setText(self.tr("app.cleanup.all_short"))
        self.monitor_group.setTitle(self.tr("app.nav.monitor"))

        # 监控与插件页：实现 retranslateUi 的页面统一刷新
        self.monitor_widget.retranslateUi()
        plugins_page = self._page_widgets.get("plugins")
        if plugins_page is not None and hasattr(plugins_page, "retranslateUi"):
            plugins_page.retranslateUi()
        for page_id, widget in self._page_widgets.items():
            if page_id.startswith("plugin:") and hasattr(widget, "retranslateUi"):
                widget.retranslateUi()

        self.tray_icon.setToolTip(self.tr("app.tray.tooltip"))
        self.statusbar.showMessage(self._ready_message())

        if self._proofread is not None:
            self._sync_proof_state(self._proofread.is_enabled())
        else:
            self._sync_proof_state(self.proof_toggle.isChecked())
        self.build_extension_menus()   # 插件入口文案同样需要重翻译

        # 已打开的常驻窗口同样要重翻译
        wm = self.kernel.services.get("window_manager")
        if wm is not None:
            window = wm.get("app.settings")
            if window is not None and hasattr(window, "retranslateUi"):
                window.retranslateUi()

    # ---------------- 业务：校对开关 ----------------
    def toggle_proof(self, enabled):
        if self._proofread is not None:
            self._proofread.set_enabled(enabled)
        else:
            self._sync_proof_state(enabled)

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
        wm = self.kernel.services.get("window_manager")
        if wm is not None:
            wm.close_all(force=True)
        for unsubscribe in self._unsubscribes:
            try:
                unsubscribe()
            except Exception:
                pass
        self._unsubscribes.clear()
        self.kernel.shutdown()
        self.tray_icon.hide()
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

    def open_settings(self):
        wm = self.kernel.services.get("window_manager")
        holder = {}

        def _factory():
            dlg = SettingsDialog(self.config, self,
                                 theme=self.kernel.services.get("theme"),
                                 i18n=self.i18n,
                                 skins=self.kernel.services.get("skins"))
            # 模态窗口关闭后会被窗口服务销毁，结果必须在销毁前记录
            dlg.finished.connect(lambda result: holder.__setitem__("result", result))
            return dlg

        if wm is not None:
            from core.services.window_manager import Mode

            wm.open("app.settings", _factory, owner="app", mode=Mode.MODAL,
                    remember_geometry=False)
            accepted = holder.get("result") == 1   # QDialog.Accepted
        else:
            accepted = _factory().exec()

        if accepted:
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

    # ---------------- 清理缓存 ----------------
    def clear_ignore_only(self):
        self.ignore_mgr.clear_ignore()
        QMessageBox.information(self, self.tr("app.cleanup.title_done"),
                                self.tr("app.cleanup.done_ignore"))
        self.statusbar.showMessage(self.tr("app.cleanup.status_ignore"), 3000)

    def clear_api_log_only(self):
        clear_api_log()
        QMessageBox.information(self, self.tr("app.cleanup.title_done"),
                                self.tr("app.cleanup.done_log"))
        self.statusbar.showMessage(self.tr("app.cleanup.status_log"), 3000)

    def clear_all_cache(self):
        self.ignore_mgr.clear_ignore()
        clear_api_log()
        QMessageBox.information(self, self.tr("app.cleanup.title_done"),
                                self.tr("app.cleanup.done_all"))
        self.statusbar.showMessage(self.tr("app.cleanup.status_all"), 3000)

    def showEvent(self, event):
        super().showEvent(event)
        wm = self.kernel.services.get("window_manager")
        if wm is not None:
            wm.refresh_topmost(self._window_id)
