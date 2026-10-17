# main_window.py
"""应用外壳（前端）。

职责边界：
- 只负责窗口、托盘、菜单栏、设置、剪贴板工具等「与具体功能无关」的骨架；
- 不再 import 任何插件内部模块，具体功能由 plugins/ 下的插件
  通过 ctx.ui 贡献点（工具菜单项 / 托盘菜单项）挂载进来；
- 需要调用插件能力时，一律通过 kernel.capabilities 取，
  插件未加载时外壳仍然可用（按钮自动降级为只改自己的状态）；
- 所有可见文案走 i18n（self.tr），切换语言时由 retranslateUi() 统一刷新。

新增一个功能时，本文件应当零改动。
"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
                               QGroupBox, QPushButton, QTextEdit, QSlider, QSpinBox,
                               QSystemTrayIcon, QMenu, QMessageBox, QCheckBox, QApplication,
                               QLabel)

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

    def __init__(self, kernel: Kernel):
        super().__init__()
        self.kernel = kernel
        self.config: ConfigManager = kernel.config
        self.api = kernel.services.get("ai_client")
        self.ignore_mgr = kernel.services.get("ignore")
        self.i18n = kernel.services.get("i18n")
        self._window_id = self.WINDOW_ID
        self.setWindowTitle(self._window_title())
        self.setMinimumSize(600, 400)

        # UI 贡献点收集器：插件加载后由 build_extension_menus 统一渲染
        self.ui = QtUiRegistry(self)
        self.ui.bind_i18n(self.i18n)
        # 插件能力（插件加载后由 bind_plugins 绑定）
        self._proofread = None
        self._unsubscribes: list = []

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
            self.top_check.blockSignals(True)
            self.top_check.setChecked(True)
            self.top_check.blockSignals(False)

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
        """把插件注册的界面入口渲染成菜单项与托盘项。

        可重复调用：每次先 clear 再重建（切换语言、插件增减都会触发），
        否则菜单项会重复追加。
        """
        self.tools_menu.clear()
        for label, handler, icon in self.ui.tools():
            action = self.tools_menu.addAction(label, handler)
            if icon is not None:
                action.setIcon(icon)

        self.tray_menu.clear()
        for label, handler in self.ui.tray_actions():
            self.tray_menu.addAction(label, handler)
        self.tray_menu.addSeparator()
        self.tray_menu.addAction(self.tr("app.tray.show_main"), self.show)
        self.tray_menu.addAction(self.tr("app.tray.quit"), self.quit_app)

    def _on_proof_toggled(self, _event, enabled):
        self._sync_proof_button(bool(enabled))

    def _sync_proof_button(self, on: bool):
        """只更新按钮外观，不回环触发 toggled 信号。"""
        self.proof_btn.blockSignals(True)
        self.proof_btn.setChecked(on)
        self.proof_btn.setText(self.tr("app.proof.on") if on else self.tr("app.proof.off"))
        self.proof_btn.blockSignals(False)

    # ---------------- 界面 ----------------
    def init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        # 实时校对控制组
        self.proof_group = QGroupBox(self.tr("app.proof.group"))
        proof_layout = QFormLayout()
        self.proof_btn = QPushButton(self.tr("app.proof.on"))
        self.proof_btn.setCheckable(True)
        self.proof_btn.setChecked(True)
        self.proof_btn.toggled.connect(self.toggle_proof)
        proof_layout.addRow(self.tr("app.proof.status_label"), self.proof_btn)

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
        proof_layout.addRow(self.tr("app.proof.debounce_label"), debounce_layout)

        ctx_spin = QSpinBox()
        ctx_spin.setRange(0, 5)
        ctx_spin.setValue(self.config.get("context_before", 0))
        ctx_spin.valueChanged.connect(lambda v: (self.config.set("context_before", v),
                                                 self.config.set("context_after", v)))
        proof_layout.addRow(self.tr("app.proof.context_label"), ctx_spin)

        self.top_check = QCheckBox(self.tr("app.proof.topmost"))
        self.top_check.setChecked(self.config.get("main_on_top", False))
        self.top_check.stateChanged.connect(self.toggle_main_top)
        proof_layout.addRow(self.top_check)
        self.proof_group.setLayout(proof_layout)
        main_layout.addWidget(self.proof_group)

        # 剪贴板处理组
        self.clip_group = QGroupBox(self.tr("app.clipboard.group"))
        cb_layout = QVBoxLayout()
        self.btn_load = QPushButton(self.tr("app.clipboard.load"))
        self.btn_load.clicked.connect(self.load_clipboard)
        cb_layout.addWidget(self.btn_load)
        self.clip_preview = QTextEdit()
        self.clip_preview.setPlaceholderText(self.tr("app.clipboard.placeholder"))
        cb_layout.addWidget(self.clip_preview)
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
        cb_layout.addLayout(btn_row)
        self.clip_result = QTextEdit()
        self.clip_result.setPlaceholderText(self.tr("app.clipboard.result_placeholder"))
        cb_layout.addWidget(self.clip_result)
        res_btn_row = QHBoxLayout()
        self.btn_insert = QPushButton(self.tr("app.clipboard.insert"))
        self.btn_copy = QPushButton(self.tr("app.clipboard.copy"))
        self.btn_insert.clicked.connect(self.insert_to_word)
        self.btn_copy.clicked.connect(
            lambda: QApplication.clipboard().setText(self.clip_result.toPlainText()))
        res_btn_row.addWidget(self.btn_insert)
        res_btn_row.addWidget(self.btn_copy)
        cb_layout.addLayout(res_btn_row)
        self.clip_group.setLayout(cb_layout)
        main_layout.addWidget(self.clip_group)

        # 菜单栏
        menu_bar = self.menuBar()
        self.settings_action = menu_bar.addAction(self.tr("app.menu.settings"))
        self.settings_action.triggered.connect(self.open_settings)

        self.cleanup_menu = QMenu(self.tr("app.menu.cleanup"), self)
        self.cleanup_menu.addAction(self.tr("app.cleanup.ignore"), self.clear_ignore_only)
        self.cleanup_menu.addAction(self.tr("app.cleanup.log"), self.clear_api_log_only)
        self.cleanup_menu.addAction(self.tr("app.cleanup.all"), self.clear_all_cache)
        self.cleanup_action = menu_bar.addAction(self.tr("app.menu.cleanup"))
        self.cleanup_action.setMenu(self.cleanup_menu)

        # 工具菜单：外壳先挂自带项，插件贡献项在加载后由 build_extension_menus 追加
        self.tools_menu = menu_bar.addMenu(self.tr("app.menu.tools"))
        self.ui.add_tool("menu.monitor", self.show_monitor, order=50, plugin_id="app")

        # 状态栏
        self.statusbar = self.statusBar()
        self.statusbar.showMessage(self._ready_message())

    def retranslateUi(self):        # noqa: N802 - Qt 命名惯例
        """切换语言后刷新全部可见文案（菜单重建 + 控件重设文本）。"""
        self.setWindowTitle(self._window_title())
        self.proof_group.setTitle(self.tr("app.proof.group"))
        self.clip_group.setTitle(self.tr("app.clipboard.group"))
        self.top_check.setText(self.tr("app.proof.topmost"))
        self.btn_load.setText(self.tr("app.clipboard.load"))
        self.clip_preview.setPlaceholderText(self.tr("app.clipboard.placeholder"))
        self.correct_btn.setText(self.tr("app.clipboard.proof"))
        self.translate_btn.setText(self.tr("app.clipboard.translate"))
        self.rewrite_btn.setText(self.tr("app.clipboard.rewrite"))
        self.clip_result.setPlaceholderText(self.tr("app.clipboard.result_placeholder"))
        self.btn_insert.setText(self.tr("app.clipboard.insert"))
        self.btn_copy.setText(self.tr("app.clipboard.copy"))

        self.settings_action.setText(self.tr("app.menu.settings"))
        self.cleanup_action.setText(self.tr("app.menu.cleanup"))
        self.cleanup_menu.setTitle(self.tr("app.menu.cleanup"))
        for index, key in enumerate(("app.cleanup.ignore", "app.cleanup.log", "app.cleanup.all")):
            actions = self.cleanup_menu.actions()
            if index < len(actions):
                actions[index].setText(self.tr(key))
        self.tools_menu.setTitle(self.tr("app.menu.tools"))

        self.tray_icon.setToolTip(self.tr("app.tray.tooltip"))
        self.statusbar.showMessage(self._ready_message())

        if self._proofread is not None:
            self._sync_proof_button(self._proofread.is_enabled())
        else:
            self._sync_proof_button(self.proof_btn.isChecked())
        self.build_extension_menus()   # 插件入口同样需要重翻译

    def toggle_proof(self, enabled):
        if self._proofread is not None:
            self._proofread.set_enabled(enabled)
        else:
            # 校对插件未加载时，仅保持按钮自身状态
            self._sync_proof_button(enabled)

    def toggle_main_top(self, state=None):
        """主窗口置顶：只改配置，实际置顶由 WindowManager 按 Layer 策略施加。"""
        self.config.set("main_on_top", self.top_check.isChecked())
        wm = self.kernel.services.get("window_manager")
        if wm is not None:
            wm.refresh_topmost(self._window_id)

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
                                 i18n=self.i18n)
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
            # 主题可能已变更（设置页内部已应用），这里同步外壳
            self.apply_style()
            self.retranslateUi()

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
        """API 调用监控：单例窗口，关闭转隐藏（由 WindowManager 治理）。"""
        wm = self.kernel.services.get("window_manager")
        if wm is None:
            return
        from core.services.window_manager import CloseBehavior, Mode

        wm.open("app.monitor", lambda: MonitorWindow(i18n=self.i18n), owner="app",
                mode=Mode.SINGLETON, behavior=CloseBehavior.HIDE,
                remember_geometry=True)

    def showEvent(self, event):
        super().showEvent(event)
        # 置顶由 WindowManager 在窗口显示后统一施加（含延迟兜底）
        wm = self.kernel.services.get("window_manager")
        if wm is not None:
            wm.refresh_topmost(self._window_id)
