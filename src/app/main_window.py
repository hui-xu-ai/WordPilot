# main_window.py
"""应用外壳（前端）。

职责边界：
- 只负责窗口、托盘、菜单栏、设置、剪贴板工具等「与具体功能无关」的骨架；
- 不再 import 任何插件内部模块，具体功能由 plugins/ 下的插件
  通过 ctx.ui 贡献点（工具菜单项 / 托盘菜单项）挂载进来；
- 需要调用插件能力时，一律通过 kernel.capabilities 取，
  插件未加载时外壳仍然可用（按钮自动降级为只改自己的状态）。

新增一个功能时，本文件应当零改动。
"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
                               QGroupBox, QPushButton, QTextEdit, QSlider, QSpinBox,
                               QSystemTrayIcon, QMenu, QMessageBox, QCheckBox, QApplication,
                               QLabel)

from __version__ import APP_TITLE
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
        self._window_id = self.WINDOW_ID
        self.setWindowTitle(APP_TITLE)
        self.setMinimumSize(600, 400)

        # UI 贡献点收集器：插件加载后由 build_extension_menus 统一渲染
        self.ui = QtUiRegistry(self)
        # 插件能力（插件加载后由 bind_plugins 绑定）
        self._proofread = None

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

        # 初始不置顶      --修改17
        if self.config.get("main_on_top", False):         #  不置顶--修改17
            self.top_check.blockSignals(True)
            self.top_check.setChecked(True)
            self.top_check.blockSignals(False)

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
        self.tray_menu.addAction("显示主窗口", self.show)
        self.tray_menu.addAction("退出", self.quit_app)

    def _on_proof_toggled(self, _event, enabled):
        self._sync_proof_button(bool(enabled))

    def _sync_proof_button(self, on: bool):
        """只更新按钮外观，不回环触发 toggled 信号。"""
        self.proof_btn.blockSignals(True)
        self.proof_btn.setChecked(on)
        self.proof_btn.setText("🔴 监听中" if on else "⚪ 已暂停")
        self.proof_btn.blockSignals(False)

#  修改3
    def init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        # 实时校对控制组
        proof_group = QGroupBox("实时校对控制")
        proof_layout = QFormLayout()
        self.proof_btn = QPushButton("🔴 监听中")
        self.proof_btn.setCheckable(True)
        self.proof_btn.setChecked(True)
        self.proof_btn.toggled.connect(self.toggle_proof)
        proof_layout.addRow("状态：", self.proof_btn)

        debounce_layout = QHBoxLayout()
        debounce_slider = QSlider(Qt.Horizontal)
        debounce_slider.setRange(200, 5000)
        debounce_slider.setValue(self.config.get("debounce_ms", 500))
        debounce_slider.setToolTip("检测到句子结束后的延迟（毫秒），避免过快触发校对")
        debounce_label = QLabel(f"{debounce_slider.value()} ms")
        debounce_slider.valueChanged.connect(lambda v: (
            self.config.set("debounce_ms", v),
            debounce_label.setText(f"{v} ms")
        ))
        debounce_layout.addWidget(debounce_slider)
        debounce_layout.addWidget(debounce_label)
        proof_layout.addRow("句子检测频率：", debounce_layout)
# --修改16 增加提示

        ctx_spin = QSpinBox()
        ctx_spin.setRange(0, 5)
        ctx_spin.setValue(self.config.get("context_before", 0))         #--默认上下文
        ctx_spin.valueChanged.connect(lambda v: (self.config.set("context_before", v),
                                                  self.config.set("context_after", v)))
        proof_layout.addRow("上下文(前后句)：", ctx_spin)

        self.top_check = QCheckBox("主窗口置顶")
        self.top_check.setChecked(self.config.get("main_on_top", False))
        self.top_check.stateChanged.connect(self.toggle_main_top)
        proof_layout.addRow(self.top_check)
        proof_group.setLayout(proof_layout)
        main_layout.addWidget(proof_group)

        # 剪贴板处理组
        cb_group = QGroupBox("剪贴板处理")
        cb_layout = QVBoxLayout()
        btn_load = QPushButton("📋 读取剪贴板")
        btn_load.clicked.connect(self.load_clipboard)
        cb_layout.addWidget(btn_load)
        self.clip_preview = QTextEdit()
        self.clip_preview.setPlaceholderText("剪贴板内容...")
        cb_layout.addWidget(self.clip_preview)
        btn_row = QHBoxLayout()
        self.correct_btn = QPushButton("🛠 纠错")
        self.translate_btn = QPushButton("🌐 翻译")
        self.rewrite_btn = QPushButton("✏️ 改写")
        self.correct_btn.clicked.connect(lambda: self.clipboard_action("proof"))
        self.translate_btn.clicked.connect(lambda: self.clipboard_action("translate"))
        self.rewrite_btn.clicked.connect(lambda: self.clipboard_action("rewrite"))
        btn_row.addWidget(self.correct_btn)
        btn_row.addWidget(self.translate_btn)
        btn_row.addWidget(self.rewrite_btn)
        cb_layout.addLayout(btn_row)
        self.clip_result = QTextEdit()
        self.clip_result.setPlaceholderText("结果将显示在此...")
        cb_layout.addWidget(self.clip_result)
        res_btn_row = QHBoxLayout()
        btn_insert = QPushButton("📝 插入Word")
        btn_copy = QPushButton("📋 复制结果")
        btn_insert.clicked.connect(self.insert_to_word)
        btn_copy.clicked.connect(lambda: QApplication.clipboard().setText(self.clip_result.toPlainText()))
        res_btn_row.addWidget(btn_insert)
        res_btn_row.addWidget(btn_copy)
        cb_layout.addLayout(res_btn_row)
        cb_group.setLayout(cb_layout)
        main_layout.addWidget(cb_group)

        # 菜单栏
        menu_bar = self.menuBar()
        settings_action = menu_bar.addAction("⚙ 设置")
        settings_action.triggered.connect(self.open_settings)

        # 创建清理缓存清理子菜单       #--修改18
        cleanup_menu = QMenu("🧹 清理缓存", self)
        cleanup_menu.addAction("清空所有记录的忽略句子 (ignore_db.json)", self.clear_ignore_only)       #--修改18
        cleanup_menu.addAction("清空 API 调用日志 (api_calls.log)", self.clear_api_log_only)  #--修改18
        cleanup_menu.addAction("清空全部（忽略列表 + API 日志）", self.clear_all_cache)         #--修改18
        # 将子菜单挂载到菜单栏动作
        cleanup_action = menu_bar.addAction("🧹 清理缓存")
        cleanup_action.setMenu(cleanup_menu)

        # 工具菜单：外壳先挂自带项，插件贡献项在加载后由 build_extension_menus 追加
        self.tools_menu = menu_bar.addMenu("工具")
        self.ui.add_tool("📊 API 监控", self.show_monitor, order=50, plugin_id="app")

        # 状态栏
        self.statusbar = self.statusBar()
        self.statusbar.showMessage("就绪 | 作者: Xu Hui")

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

#--修改8
    def clipboard_action(self, action):
        text = self.clip_preview.toPlainText().strip()
        if not text:
            self.clip_result.setPlainText("⚠️ 请先点击“读取剪贴板”加载文本。")
            return
        if action == "proof":
            result = self.api.clipboard_proof(text)
            if result == text:
                self.clip_result.setPlainText("✅ 无需修正，文本已是最佳。")
            else:
                self.clip_result.setPlainText(result)
        elif action == "translate":
            result = self.api.translate(text)
            if result:
                self.clip_result.setPlainText(result)
            else:
                self.clip_result.setPlainText("❌ 翻译失败，请检查API Key与网络。")
        elif action == "rewrite":
            result = self.api.rewrite(text)
            if result:
                self.clip_result.setPlainText(result)
            else:
                self.clip_result.setPlainText("❌ 改写失败，请检查API Key与网络。")
        else:
            self.clip_result.setPlainText("未知操作")

    def insert_to_word(self):
        text = self.clip_result.toPlainText()
        if not text:
            return
        try:
            word = get_word_app()
            word.Selection.TypeText(text)
        except WordNotRunningError as exc:
            QMessageBox.warning(self, "错误", str(exc))

    def setup_tray(self):
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(QIcon(resource_path("assets/tray_icon.png")))
        self.tray_icon.setToolTip("Word智能伴侣")
        # 菜单内容由 build_extension_menus 在插件加载完成后填充
        self.tray_menu = QMenu()
        self.tray_icon.setContextMenu(self.tray_menu)
        self.tray_icon.activated.connect(self.on_tray_activated)    # --修改22
        self.tray_icon.show()

    def on_tray_activated(self, reason):    # --修改22
        if reason == QSystemTrayIcon.DoubleClick:
            self.show()

    def _on_hidden(self):
        """关闭转隐藏后的提示（关闭语义由 WindowManager 统一裁决）。"""
        if hasattr(self, "tray_icon") and self.tray_icon.isVisible():
            self.tray_icon.showMessage("程序仍在后台运行", "双击托盘图标可恢复窗口",
                                       QSystemTrayIcon.Information, 2000)
        else:
            # 托盘不可用时无法恢复，直接退出
            self.quit_app()

    def quit_app(self):
        # 先强制关闭全部受管窗口，再停内核退出，避免残留窗口阻塞退出链
        wm = self.kernel.services.get("window_manager")
        if wm is not None:
            wm.close_all(force=True)
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

#  修改3
    def open_settings(self):
        wm = self.kernel.services.get("window_manager")
        holder = {}

        def _factory():
            dlg = SettingsDialog(self.config, self, theme=self.kernel.services.get("theme"))
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
            # 刷新字体
            self.apply_style()

    def cleanup_cache(self):        #--修改18
        menu = QMenu(self)
        action_ignore = menu.addAction("清空所有记录的忽略句子")
        action_log = menu.addAction("清空 API 调用日志")
        action_all = menu.addAction("清空全部")
        action_ignore.triggered.connect(self.clear_ignore_only)
        action_log.triggered.connect(self.clear_api_log_only)
        action_all.triggered.connect(self.clear_all_cache)
        # 弹出菜单
        btn = self.sender()
        if btn:
            menu.exec(btn.mapToGlobal(btn.pos()))
        else:
            menu.exec(self.mapToGlobal(QPoint(0, 0)))

    def clear_ignore_only(self):         #--修改18
        self.ignore_mgr.clear_ignore()
        QMessageBox.information(self, "完成", "所有记录的忽略句子已清空。")
        self.statusbar.showMessage("所有记录的忽略句子已清空", 3000)

    def clear_api_log_only(self):        #--修改18
        clear_api_log()
        QMessageBox.information(self, "完成", "API 调用日志已清空。")
        self.statusbar.showMessage("API 调用日志已清空", 3000)

    def clear_all_cache(self):       #--修改18
        self.ignore_mgr.clear_ignore()
        clear_api_log()
        QMessageBox.information(self, "完成", "所有缓存已清空。")
        self.statusbar.showMessage("所有缓存已清空", 3000)

    def show_debug(self, msg):
        self.statusbar.showMessage(f"调试: {msg}", 2000)

    def show_monitor(self):
        """API 调用监控：单例窗口，关闭转隐藏（由 WindowManager 治理）。"""
        wm = self.kernel.services.get("window_manager")
        if wm is None:
            return
        from core.services.window_manager import CloseBehavior, Mode

        wm.open("app.monitor", lambda: MonitorWindow(), owner="app",
                mode=Mode.SINGLETON, behavior=CloseBehavior.HIDE,
                remember_geometry=True)

    def showEvent(self, event):
        super().showEvent(event)
        # 置顶由 WindowManager 在窗口显示后统一施加（含延迟兜底）
        wm = self.kernel.services.get("window_manager")
        if wm is not None:
            wm.refresh_topmost(self._window_id)
