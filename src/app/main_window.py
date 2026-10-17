# main_window.py
"""应用外壳（前端）。

信息架构（现代桌面软件常见布局）：
    ┌─────────────────────────────────────────────┐
    │ 顶部工具栏：校对开关 / 置顶钉 | 清理 / 设置   │
    ├──────────┬──────────────────────────────────┤
    │ 左侧导航 │ 内容区（QStackedWidget）          │
    │ 仪表盘   │  仪表盘 / 剪贴板工具 / 插件 /     │
    │ 剪贴板   │  API 监控 / …插件贡献页           │
    │ 插件     │                                  │
    │ API 监控 │                                  │
    ├──────────┴──────────────────────────────────┤
    │ 状态栏                                      │
    └─────────────────────────────────────────────┘

职责边界：
- 只负责窗口、托盘、工具栏、导航、设置、剪贴板工具等「与具体功能无关」的骨架；
- 不再 import 任何插件内部模块，具体功能由 plugins/ 下的插件
  通过 ctx.ui 贡献点（工具菜单项 / 托盘菜单项 / 页面）挂载进来；
- 需要调用插件能力时，一律通过 kernel.capabilities 取，
  插件未加载时外壳仍然可用（按钮自动降级为只改自己的状态）；
- 所有可见文案走 i18n（self.tr），切换语言时由 retranslateUi() 统一刷新。

新增一个功能时，本文件应当零改动。
"""
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
                               QGroupBox, QPushButton, QTextEdit, QSlider, QSpinBox,
                               QSystemTrayIcon, QMenu, QMessageBox, QApplication,
                               QLabel, QListWidget, QListWidgetItem, QStackedWidget,
                               QToolBar, QToolButton, QFrame, QSizePolicy)

from __version__ import __version__
from core.kernel import Kernel
from core.services.api_logger import clear_api_log
from core.services.config_manager import ConfigManager
from core.services.word_bridge import get_word_app, WordNotRunningError
from app.monitor_window import MonitorWindow
from app.settings_dialog import SettingsDialog
from app.ui_registry import QtUiRegistry
from infra.utils import resource_path


class MainWindow(QMainWindow):
    WINDOW_ID = "app.main"

    #: 左侧导航内置页（id, i18n key）；内容页在 _build_pages 中按同顺序构造
    BUILTIN_PAGES = (
        ("dashboard", "app.nav.dashboard", "⌂"),
        ("clipboard", "app.nav.clipboard", "📋"),
        ("plugins", "app.nav.plugins", "🧩"),
        ("monitor", "app.nav.monitor", "📊"),
    )

    def __init__(self, kernel: Kernel):
        super().__init__()
        self.kernel = kernel
        self.config: ConfigManager = kernel.config
        self.api = kernel.services.get("ai_client")
        self.ignore_mgr = kernel.services.get("ignore")
        self.i18n = kernel.services.get("i18n")
        self._window_id = self.WINDOW_ID
        self.setWindowTitle(self._window_title())
        self.setMinimumSize(680, 480)

        # UI 贡献点收集器：插件加载后由 build_extension_menus 统一渲染
        self.ui = QtUiRegistry(self)
        self.ui.bind_i18n(self.i18n)
        # 插件能力（插件加载后由 bind_plugins 绑定）
        self._proofread = None
        self._unsubscribes: list = []
        # 页面注册表：nav id -> 内容 QWidget（内置页 + 插件页共用 QStackedWidget）
        self._page_index: dict[str, int] = {}
        # 插件贡献页：(nav item, stack widget)，重建时先整体移除再追加
        self._plugin_page_widgets: list[tuple] = []

        self.init_ui()
        self.setup_tray()
        self.apply_style()

        # 把状态栏输出能力提供给插件（插件通过 ctx.ui.status 调用）
        self.ui.set_status_handler(self.statusbar.showMessage)

        # 外壳自带的全局热键（功能类热键由各自插件注册）
        self.hotkeys = kernel.services.get("hotkeys")
        if self.hotkeys is not None:
            self.hotkeys.register("translate", "ctrl+shift+t",
                                  lambda: self.clipboard_action("translate"))
            self.hotkeys.register("rewrite", "ctrl+shift+r",
                                  lambda: self.clipboard_action("rewrite"))

        # 初始置顶状态
        if self.config.get("main_on_top", False):
            self.top_action.blockSignals(True)
            self.top_action.setChecked(True)
            self.top_action.blockSignals(False)

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

    def _subscribe_events(self):
        """订阅内核事件；保存退订闭包，窗口销毁时注销。"""
        self._unsubscribes.append(
            self.kernel.events.subscribe("i18n.language_changed",
                                         lambda _e, _p: self.retranslateUi())
        )
        self._unsubscribes.append(
            self.kernel.events.subscribe("theme.changed", lambda _e, _p: self.apply_style())
        )

    # ---------------- 插件集成 ----------------
    def bind_plugins(self):
        """插件加载完成后调用：绑定能力、渲染贡献点、同步初始状态。"""
        self._proofread = self.kernel.capabilities.get("proofread")
        self.kernel.events.subscribe("proofread.toggled", self._on_proof_toggled)
        self.build_extension_menus()
        if self._proofread is not None:
            self._sync_proof_button(self._proofread.is_enabled())

    def build_extension_menus(self):
        """把插件注册的界面入口渲染成托盘项与插件页列表。

        可重复调用：每次先 clear 再重建（切换语言、插件增减都会触发），
        否则菜单项会重复追加。
        """
        self.tray_menu.clear()
        for label, handler in self.ui.tray_actions():
            self.tray_menu.addAction(label, handler)
        self.tray_menu.addSeparator()
        self.tray_menu.addAction(self.tr("app.tray.show_main"), self.show)
        self.tray_menu.addAction(self.tr("app.tray.quit"), self.quit_app)

        self._rebuild_plugins_page()
        self._rebuild_plugin_pages()

    def _on_proof_toggled(self, _event, enabled):
        self._sync_proof_button(bool(enabled))

    def _sync_proof_button(self, on: bool):
        """只更新开关外观，不回环触发 toggled 信号。"""
        self.proof_action.blockSignals(True)
        self.proof_action.setChecked(on)
        self.proof_action.setText(self.tr("app.proof.on") if on else self.tr("app.proof.off"))
        self.proof_action.blockSignals(False)
        self.dashboard_status.setText(
            self.tr("app.proof.on") if on else self.tr("app.proof.off"))
        self.dashboard_status.setProperty("state", "ok" if on else "off")
        self.dashboard_status.style().unpolish(self.dashboard_status)
        self.dashboard_status.style().polish(self.dashboard_status)

    # ---------------- 界面：骨架 ----------------
    def init_ui(self):
        self._build_toolbar()

        central = QWidget()
        self.setCentralWidget(central)
        body = QHBoxLayout(central)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        # 左侧导航
        self.nav_list = QListWidget()
        self.nav_list.setObjectName("navList")
        self.nav_list.setFixedWidth(132)
        self.nav_list.setIconSize(QSize(22, 22))
        self.nav_list.setFrameShape(QFrame.NoFrame)
        body.addWidget(self.nav_list)

        divider = QFrame()
        divider.setFrameShape(QFrame.VLine)
        divider.setObjectName("navDivider")
        body.addWidget(divider)

        # 内容区
        self.stack = QStackedWidget()
        body.addWidget(self.stack, 1)
        self._build_pages()

        self.nav_list.currentRowChanged.connect(self.stack.setCurrentIndex)
        if self.nav_list.count():
            self.nav_list.setCurrentRow(0)

        # API 监控现在是一个内容页，托盘里仍保留一个快捷入口
        self.ui.add_tray_action("menu.monitor", self.show_monitor, order=50, plugin_id="app")

        # 状态栏
        self.statusbar = self.statusBar()
        self.statusbar.showMessage(self._ready_message())

    def _build_toolbar(self):
        self.toolbar = QToolBar(self.tr("app.toolbar.title"))
        self.toolbar.setObjectName("mainToolBar")
        self.toolbar.setMovable(False)
        self.toolbar.setFloatable(False)
        self.toolbar.setIconSize(QSize(18, 18))
        self.addToolBar(Qt.TopToolBarArea, self.toolbar)

        # 校对开关
        self.proof_action = QAction(self.tr("app.proof.on"), self)
        self.proof_action.setCheckable(True)
        self.proof_action.setChecked(True)
        self.proof_action.toggled.connect(self.toggle_proof)
        self.toolbar.addAction(self.proof_action)

        # 置顶钉
        self.top_action = QAction(self.tr("app.toolbar.topmost"), self)
        self.top_action.setCheckable(True)
        self.top_action.toggled.connect(self.toggle_main_top)
        self.toolbar.addAction(self.top_action)

        self.toolbar.addSeparator()

        # 右侧：清理 + 设置
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        spacer.setObjectName("toolbarSpacer")
        self.toolbar.addWidget(spacer)

        self.cleanup_button = QToolButton()
        self.cleanup_button.setObjectName("toolbarCleanup")
        self.cleanup_button.setText(self.tr("app.menu.cleanup"))
        self.cleanup_button.setPopupMode(QToolButton.InstantPopup)
        self.cleanup_menu = QMenu(self.cleanup_button)
        self.cleanup_menu.addAction(self.tr("app.cleanup.ignore"), self.clear_ignore_only)
        self.cleanup_menu.addAction(self.tr("app.cleanup.log"), self.clear_api_log_only)
        self.cleanup_menu.addAction(self.tr("app.cleanup.all"), self.clear_all_cache)
        self.cleanup_button.setMenu(self.cleanup_menu)
        self.toolbar.addWidget(self.cleanup_button)

        self.settings_action = QAction(self.tr("app.menu.settings"), self)
        self.settings_action.triggered.connect(self.open_settings)
        self.toolbar.addAction(self.settings_action)

    # ---------------- 界面：页面 ----------------
    def _build_pages(self):
        """按 BUILTIN_PAGES 顺序构建内置页，再追加插件贡献页。"""
        builders = {
            "dashboard": self._build_dashboard_page,
            "clipboard": self._build_clipboard_page,
            "plugins": self._build_plugins_page,
            "monitor": self._build_monitor_page,
        }
        for page_id, key, icon in self.BUILTIN_PAGES:
            self._add_page(page_id, key, icon, builders[page_id]())

    def _add_page(self, page_id: str, title_key: str, icon: str, widget: QWidget) -> None:
        index = self.stack.addWidget(widget)
        self._page_index[page_id] = index
        item = QListWidgetItem(f"{icon}  {self.tr(title_key)}")
        item.setTextAlignment(Qt.AlignHCenter)
        item.setData(Qt.UserRole, page_id)
        self.nav_list.addItem(item)

    def show_page(self, page_id: str) -> None:
        """切到指定页（id 或序号）。"""
        row = self._page_index.get(page_id)
        if row is not None:
            self.nav_list.setCurrentRow(row)
            self.show()
            self.raise_()

    def _build_dashboard_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("pageDashboard")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # 校对状态卡片
        status_group = QGroupBox(self.tr("app.proof.group"))
        self.proof_group = status_group
        status_layout = QVBoxLayout(status_group)
        self.dashboard_status = QLabel(self.tr("app.proof.on"))
        self.dashboard_status.setObjectName("dashboardStatus")
        self.dashboard_status.setProperty("state", "ok")
        status_layout.addWidget(self.dashboard_status, 0, Qt.AlignLeft)
        self.dashboard_hint = QLabel(self.tr("app.dashboard.hint"))
        self.dashboard_hint.setObjectName("hintLabel")
        self.dashboard_hint.setWordWrap(True)
        status_layout.addWidget(self.dashboard_hint)
        layout.addWidget(status_group)

        # 校对参数
        params_group = QGroupBox(self.tr("app.dashboard.params"))
        self.params_group = params_group
        params_layout = QFormLayout(params_group)

        debounce_layout = QHBoxLayout()
        debounce_slider = QSlider(Qt.Horizontal)
        debounce_slider.setRange(200, 5000)
        debounce_slider.setValue(self.config.get("debounce_ms", 500))
        debounce_slider.setToolTip(self.tr("app.proof.debounce_tip"))
        debounce_label = QLabel(f"{debounce_slider.value()} ms")
        debounce_slider.valueChanged.connect(lambda v: (
            self.config.set("debounce_ms", v),
            debounce_label.setText(f"{v} ms")
        ))
        debounce_layout.addWidget(debounce_slider)
        debounce_layout.addWidget(debounce_label)
        self.debounce_label = QLabel(self.tr("app.proof.debounce_label"))
        params_layout.addRow(self.debounce_label, debounce_layout)

        ctx_spin = QSpinBox()
        ctx_spin.setRange(0, 5)
        ctx_spin.setValue(self.config.get("context_before", 0))
        ctx_spin.valueChanged.connect(lambda v: (self.config.set("context_before", v),
                                                 self.config.set("context_after", v)))
        self.context_label = QLabel(self.tr("app.proof.context_label"))
        params_layout.addRow(self.context_label, ctx_spin)

        layout.addWidget(params_group)
        layout.addStretch()
        return page

    def _build_clipboard_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("pageClipboard")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        self.btn_load = QPushButton(self.tr("app.clipboard.load"))
        self.btn_load.clicked.connect(self.load_clipboard)
        layout.addWidget(self.btn_load)

        self.clip_preview = QTextEdit()
        self.clip_preview.setPlaceholderText(self.tr("app.clipboard.placeholder"))
        layout.addWidget(self.clip_preview)

        btn_row = QHBoxLayout()
        self.correct_btn = QPushButton(self.tr("app.clipboard.proof"))
        self.translate_btn = QPushButton(self.tr("app.clipboard.translate"))
        self.rewrite_btn = QPushButton(self.tr("app.clipboard.rewrite"))
        self.correct_btn.clicked.connect(lambda: self.clipboard_action("proof"))
        self.translate_btn.clicked.connect(lambda: self.clipboard_action("translate"))
        self.rewrite_btn.clicked.connect(lambda: self.clipboard_action("rewrite"))
        btn_row.addWidget(self.correct_btn)
        btn_row.addWidget(self.translate_btn)
        btn_row.addWidget(self.rewrite_btn)
        layout.addLayout(btn_row)

        self.clip_result = QTextEdit()
        self.clip_result.setPlaceholderText(self.tr("app.clipboard.result_placeholder"))
        layout.addWidget(self.clip_result)

        res_btn_row = QHBoxLayout()
        self.btn_insert = QPushButton(self.tr("app.clipboard.insert"))
        self.btn_copy = QPushButton(self.tr("app.clipboard.copy"))
        self.btn_insert.clicked.connect(self.insert_to_word)
        self.btn_copy.clicked.connect(
            lambda: QApplication.clipboard().setText(self.clip_result.toPlainText()))
        res_btn_row.addWidget(self.btn_insert)
        res_btn_row.addWidget(self.btn_copy)
        layout.addLayout(res_btn_row)
        return page

    def _build_plugins_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("pagePlugins")
        self.plugins_layout = QVBoxLayout(page)
        self.plugins_layout.setContentsMargins(16, 16, 16, 16)
        self.plugins_layout.setSpacing(8)
        self.plugins_title = QLabel(self.tr("app.plugins.group"))
        self.plugins_title.setObjectName("sectionTitle")
        self.plugins_layout.addWidget(self.plugins_title)
        self.plugins_list = QVBoxLayout()
        self.plugins_list.setSpacing(6)
        self.plugins_layout.addLayout(self.plugins_list)
        self.plugins_layout.addStretch()
        return page

    def _rebuild_plugins_page(self) -> None:
        """按 ui.tools() 重建插件页列表（先清空再追加，保证可重复调用）。"""
        while self.plugins_list.count():
            item = self.plugins_list.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        tools = self.ui.tools()
        if not tools:
            empty = QLabel(self.tr("app.plugins.empty"))
            empty.setObjectName("hintLabel")
            self.plugins_list.addWidget(empty)
            return
        for label, handler, _icon in tools:
            row = QWidget()
            row.setObjectName("pluginRow")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(10, 8, 10, 8)
            name = QLabel(label)
            name.setObjectName("pluginName")
            row_layout.addWidget(name)
            row_layout.addStretch()
            open_btn = QPushButton(self.tr("app.plugins.open"))
            open_btn.setProperty("accent", True)
            open_btn.clicked.connect(lambda _c=False, h=handler: h())
            row_layout.addWidget(open_btn)
            self.plugins_list.addWidget(row)

    def _build_monitor_page(self) -> QWidget:
        self.monitor_widget = MonitorWindow(i18n=self.i18n)
        self.monitor_widget.close_btn.setVisible(False)    # 内嵌页不需要关闭按钮
        self.monitor_widget.setWindowTitle("")             # 窗口标题交给导航
        return self.monitor_widget

    def _rebuild_plugin_pages(self) -> None:
        """重建插件贡献的导航页（add_page 贡献点）。

        插件页一律追加在内置页之后，移除时只动尾部，内置页的序号不会被打乱。
        """
        for item, widget in self._plugin_page_widgets:
            row = self.nav_list.row(item)
            if row >= 0:
                self.nav_list.takeItem(row)
            self.stack.removeWidget(widget)
            widget.deleteLater()
        self._plugin_page_widgets.clear()
        self._page_index = {key: value for key, value in self._page_index.items()
                            if not key.startswith("plugin_page:")}
        for label, factory, icon in self.ui.pages():
            try:
                widget = factory()
            except Exception:
                # 单个插件页构造失败不应拖垮整个外壳
                continue
            index = self.stack.addWidget(widget)
            page_id = f"plugin_page:{index}"
            self._page_index[page_id] = index
            prefix = icon if isinstance(icon, str) else "▣"
            item = QListWidgetItem(f"{prefix}  {label}")
            item.setTextAlignment(Qt.AlignHCenter)
            item.setData(Qt.UserRole, page_id)
            self.nav_list.addItem(item)
            self._plugin_page_widgets.append((item, widget))

    # ---------------- 界面：多语言 ----------------
    def retranslateUi(self):        # noqa: N802 - Qt 命名惯例
        """切换语言后刷新全部可见文案（菜单重建 + 控件重设文本）。"""
        self.setWindowTitle(self._window_title())

        self.toolbar.setWindowTitle(self.tr("app.toolbar.title"))
        self.top_action.setText(self.tr("app.toolbar.topmost"))
        self.cleanup_button.setText(self.tr("app.menu.cleanup"))
        self.cleanup_menu.setTitle(self.tr("app.menu.cleanup"))
        for index, key in enumerate(("app.cleanup.ignore", "app.cleanup.log", "app.cleanup.all")):
            actions = self.cleanup_menu.actions()
            if index < len(actions):
                actions[index].setText(self.tr(key))
        self.settings_action.setText(self.tr("app.menu.settings"))

        # 左侧导航
        for row in range(self.nav_list.count()):
            item = self.nav_list.item(row)
            page_id = item.data(Qt.UserRole)
            for pid, key, icon in self.BUILTIN_PAGES:
                if pid == page_id:
                    item.setText(f"{icon}  {self.tr(key)}")
                    break

        # 仪表盘
        self.proof_group.setTitle(self.tr("app.proof.group"))
        self.params_group.setTitle(self.tr("app.dashboard.params"))
        self.dashboard_hint.setText(self.tr("app.dashboard.hint"))
        self.debounce_label.setText(self.tr("app.proof.debounce_label"))
        self.context_label.setText(self.tr("app.proof.context_label"))

        # 剪贴板页
        self.btn_load.setText(self.tr("app.clipboard.load"))
        self.clip_preview.setPlaceholderText(self.tr("app.clipboard.placeholder"))
        self.correct_btn.setText(self.tr("app.clipboard.proof"))
        self.translate_btn.setText(self.tr("app.clipboard.translate"))
        self.rewrite_btn.setText(self.tr("app.clipboard.rewrite"))
        self.clip_result.setPlaceholderText(self.tr("app.clipboard.result_placeholder"))
        self.btn_insert.setText(self.tr("app.clipboard.insert"))
        self.btn_copy.setText(self.tr("app.clipboard.copy"))

        # 插件页
        self.plugins_title.setText(self.tr("app.plugins.group"))

        # 监控页
        self.monitor_widget.retranslateUi()

        self.tray_icon.setToolTip(self.tr("app.tray.tooltip"))
        self.statusbar.showMessage(self._ready_message())

        if self._proofread is not None:
            self._sync_proof_button(self._proofread.is_enabled())
        else:
            self._sync_proof_button(self.proof_action.isChecked())
        self.build_extension_menus()   # 插件入口同样需要重翻译

        # 已打开的常驻窗口同样要重翻译（设置页是模态的，通常不会同时存在）
        wm = self.kernel.services.get("window_manager")
        if wm is not None:
            for wid in ("app.settings",):
                window = wm.get(wid)
                if window is not None and hasattr(window, "retranslateUi"):
                    window.retranslateUi()

    # ---------------- 业务：校对开关 ----------------
    def toggle_proof(self, enabled):
        if self._proofread is not None:
            self._proofread.set_enabled(enabled)
        else:
            # 校对插件未加载时，仅保持开关自身状态
            self._sync_proof_button(enabled)

    def toggle_main_top(self, checked=False):
        """主窗口置顶：只改配置，实际置顶由 WindowManager 按 Layer 策略施加。"""
        self.config.set("main_on_top", bool(checked))
        wm = self.kernel.services.get("window_manager")
        if wm is not None:
            wm.refresh_topmost(self._window_id)

    # ---------------- 业务：剪贴板 ----------------
    def load_clipboard(self):
        text = QApplication.clipboard().text()
        self.clip_preview.setPlainText(text)

    def clipboard_action(self, action):
        text = self.clip_preview.toPlainText().strip()
        if not text:
            self.clip_result.setPlainText(self.tr("app.clipboard.empty_tip"))
            return
        if action == "proof":
            result = self.api.clipboard_proof(text)
            if result == text:
                self.clip_result.setPlainText(self.tr("app.clipboard.no_change"))
            else:
                self.clip_result.setPlainText(result)
        elif action == "translate":
            result = self.api.translate(text)
            if result:
                self.clip_result.setPlainText(result)
            else:
                self.clip_result.setPlainText(self.tr("app.clipboard.fail_translate"))
        elif action == "rewrite":
            result = self.api.rewrite(text)
            if result:
                self.clip_result.setPlainText(result)
            else:
                self.clip_result.setPlainText(self.tr("app.clipboard.fail_rewrite"))
        else:
            self.clip_result.setPlainText(self.tr("app.clipboard.unknown_action"))

    def insert_to_word(self):
        text = self.clip_result.toPlainText()
        if not text:
            return
        try:
            word = get_word_app()
            word.Selection.TypeText(text)
        except WordNotRunningError as exc:
            QMessageBox.warning(self, self.tr("app.clipboard.error_title"), str(exc))

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
            # 托盘不可用时无法恢复，直接退出
            self.quit_app()

    def quit_app(self):
        # 先强制关闭全部受管窗口，再停内核退出，避免残留窗口阻塞退出链
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
        """全局外观由主题服务统一渲染；这里只处理主题管不到的窗口级项。"""
        theme = self.kernel.services.get("theme")
        if theme is not None:
            theme.reload()

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
            self.api = self.kernel.reload_ai_client()
            # 热键组合可能已变更，重新绑定
            if self.hotkeys is not None:
                self.hotkeys.apply()
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

    def show_monitor(self):
        """API 监控已并入主界面内容页，这里切过去即可（保留入口供托盘调用）。"""
        self.show_page("monitor")

    def showEvent(self, event):
        super().showEvent(event)
        # 置顶由 WindowManager 在窗口显示后统一施加（含延迟兜底）
        wm = self.kernel.services.get("window_manager")
        if wm is not None:
            wm.refresh_topmost(self._window_id)
