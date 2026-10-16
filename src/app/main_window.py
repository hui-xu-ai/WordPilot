# main_window.py
import win32com.client as win32
import win32con
import win32gui
from PySide6.QtCore import Qt, Signal, Slot, QTimer, QPoint  # --修改15  置顶  新增QTimer
from PySide6.QtGui import QFont, QIcon, QPixmap, QPainter, QColor
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
                               QGroupBox, QPushButton, QTextEdit, QSlider, QSpinBox,
                               QSystemTrayIcon, QMenu, QMessageBox, QCheckBox, QApplication,
                               QLabel, QDialog, QMenuBar)  # 新增QDialog  QMenuBar 图片裁剪居中工具

from core.services.api_client import DeepSeekClient
from core.services.api_logger import clear_api_log
from core.services.config_manager import CONFIG_DIR
from core.services.config_manager import ConfigManager
from infra.hotkey_manager import HotkeyManager
from core.services.ignore_cache import IgnoreCacheManager
from app.widgets.image_reshaper_widget import ImageReshaperWidget  # 图片裁剪居中工具
from app.monitor_window import MonitorWindow
from app.settings_dialog import SettingsDialog
from app.suggestion_window import FloatingButton
from infra.utils import resource_path  # 或直接定义在文件内   #  -修改22
from plugins.proofread.word_monitor import WordMonitor, clear_error_marks, TokenizedSentence, split_trailing_chars, proof_sentence
from plugins.proofread.word_monitor import get_sentence_range
from plugins.navigator.word_navigator_dialog import WordNavigatorDialog

CONFIG_FILE = CONFIG_DIR / "Image.docx"


class MainWindow(QMainWindow):
    def __init__(self, config: ConfigManager, api: DeepSeekClient, ignore_mgr: IgnoreCacheManager):
        super().__init__()
        self.config = config
        self.api = DeepSeekClient(self.config)
        self.ignore_mgr = ignore_mgr
        self.setWindowTitle("Word智能伴侣 v3.0 - Xu Hui")
        self.setMinimumSize(600, 400)

        self.proof_enabled = True
        self.init_ui()
        self.setup_tray()
        self.apply_style()

        # 监控线程
        # self.monitor = WordMonitor(config, api)           --修改17  
        self.monitor = WordMonitor(config, api, ignore_mgr)   # 原来没传 ignore_mgr     --修改17    
        self.monitor.debug_signal.connect(self.show_debug)
        self.monitor.suggestion_ready.connect(self.handle_suggestion)
        self.monitor.status_update.connect(lambda s: self.statusbar.showMessage(s, 3000))            
        self.monitor.start()

        # 悬浮按钮
        self.floating_btn = FloatingButton(self.config, api=self.api, ignore_mgr=self.ignore_mgr, main_window=self)     #--修改17   --修改18 添加 main_window=self
        self.floating_btn.main_window = self   # 关键绑定       #--修改18
        if not self.config.get("show_floating_button", True):
            self.floating_btn.hide()

        # 热键
        self.hotkey_manager = HotkeyManager(config, self)

        # 初始不置顶      --修改17
        if self.config.get("main_on_top", False):         #  不置顶--修改17        
            self.top_check.blockSignals(True)
            self.top_check.setChecked(True)
            self.top_check.blockSignals(False)

        # 将悬浮按钮初始定位到主窗口正中央     --修改17      
        def center_floating_btn():
            if self.floating_btn.isVisible():
                center = self.geometry().center()          # 主窗口中心点（相对于屏幕）
                self.floating_btn.move(center.x() - 16, center.y() - 16)   # 按钮尺寸 32x32，偏移一半使其居中
        QTimer.singleShot(100, center_floating_btn)        # 延迟 100ms 等待主窗口完成布局

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

# 测试按钮（保留方便测试）
        # test_btn = QPushButton("🧪 测试浮动窗口")
        # test_btn.clicked.connect(self.test_float_window)
        # main_layout.addWidget(test_btn)

        # self.float_status_label = QLabel("浮动窗口：未显示")
        # self.float_status_label.setStyleSheet("color: gray; font-size: 11px;")
        # main_layout.addWidget(self.float_status_label)

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

        # 工具菜单
        tools_menu = None
        for action in menu_bar.actions():
            if action.text() == "工具":
                tools_menu = action.menu()
                break
        if tools_menu is None:
            tools_menu = menu_bar.addMenu("工具")
        # monitor_action = tools_menu.addAction("📊 API 监控")
        # monitor_action.triggered.connect(self.show_monitor)
        tools_menu.addAction("📑 Word导航助手", self.show_word_navigator)    #  Word导航助手
        tools_menu.addAction("🖼️ 图片工具箱", self.show_image_tool)          # 新增：图片裁剪居中工具   2026.05.18
        tools_menu.addAction("📊 API 监控", self.show_monitor)    #  Word导航助手      2026.05.14

        # 状态栏
        self.statusbar = self.statusBar()
        self.statusbar.showMessage("就绪 | 作者: Xu Hui")

#  修改3
    def toggle_proof(self, enabled):
        self.proof_enabled = enabled
        self.monitor.proof_enabled = enabled if hasattr(self.monitor, 'proof_enabled') else enabled
        self.proof_btn.setText("🔴 监听中" if enabled else "⚪ 已暂停")
        if enabled:
            # self.float_status_label.setText("浮动窗口：未显示")--测试--修改12
            # 根据配置决定是否显示悬浮按钮
            if self.config.get("show_floating_button", True):
                self.floating_btn.show()
        else:
            self.floating_btn.hide()
            # self.float_status_label.setText("浮动窗口：已关闭")       --测试--修改12

    def toggle_main_top(self, state=None):   # 参数保留但不再依赖        --修改15
        on = self.top_check.isChecked()
        self.config.set("main_on_top", on)
        self.set_topmost(on)

    def set_topmost(self, on: bool):        #--修改15  置顶
        hwnd = int(self.winId())
        if on:
            win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                                  win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)
        else:
            win32gui.SetWindowPos(hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0,
                                  win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)

#  测试按钮（保留方便测试）
    # def test_float_window(self):
        # original = "今天天气很好，我们出去完吧。"
        # corrected = "今天天气很好，我们出去玩吧。"
        # self.handle_suggestion(original, corrected)

    def handle_suggestion(self, original: str, corrected: str, start: int, end: int):
        self.floating_btn.set_suggestion(original, corrected, start, end)
        # self.float_status_label.setText("浮动窗口：显示建议")       --测试--修改12


#--修改21
    def apply_correction(self, corrected_text, start=None, end=None, original_text=None):
        try:
            word = win32.GetActiveObject("Word.Application")
            doc = word.ActiveDocument

            if start is not None and end is not None:
                sent_range = doc.Range(start, end)
            else:                
                sent_range = get_sentence_range(word.Selection)

            if sent_range.Start >= sent_range.End:
                self.statusbar.showMessage("未找到句子", 3000)
                return

            tokenized = TokenizedSentence(sent_range)
            has_object = any(b['type'] == 'object' for b in tokenized.get_blocks())
            sent_text = sent_range.Text

            # 分离主体和尾部
            body_text, tail_text = split_trailing_chars(sent_text)

            if has_object:
                # 有图片/公式：弹窗提示手动修改，仅清除标记
                clear_error_marks(sent_range.Start, sent_range.End)
                msg_box = QMessageBox(self)
                msg_box.setWindowTitle("提示")
                msg_box.setText("句子中含有图片或公式，请对照建议手动修改。")
                msg_box.setStandardButtons(QMessageBox.Ok)
                msg_box.setModal(False)
                msg_box.show()
                QTimer.singleShot(2000, msg_box.close)
                self.statusbar.showMessage("已清除标记，请手动修改", 3000)
            else:
                # 纯文本：直接替换主体，保留尾部
                clear_error_marks(sent_range.Start, sent_range.End)

                # 确定要替换的范围（仅主体部分）
                if tail_text:
                    replace_range = doc.Range(sent_range.Start, sent_range.Start + len(body_text))
                else:
                    replace_range = sent_range.Duplicate

                # 去掉 corrected_text 中可能包含的尾部字符
                corrected_body = corrected_text
                if tail_text and corrected_text.endswith(tail_text):
                    corrected_body = corrected_text[:-len(tail_text)]

                replace_range.Text = corrected_body
                self.statusbar.showMessage("修改已应用", 3000)

        except Exception as e:
            QMessageBox.warning(self, "错误", f"替换失败：{e}")


## ---修改18 智能纠错函数
    def proof_sentence_range(self, sentence_range, original_text=None):

        return proof_sentence(sentence_range, self.api, self.config)


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
        if text:
            try:
                word = win32.GetActiveObject("Word.Application")
                word.Selection.TypeText(text)
            except:
                QMessageBox.warning(self, "错误", "Word 未运行或无法访问")

    def setup_tray(self):
        pixmap = QPixmap(16, 16)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setBrush(QColor("#4a9eff"))
        painter.drawEllipse(2, 2, 12, 12)
        painter.end()
        icon = QIcon(pixmap)

        # self.tray_icon = QSystemTrayIcon(icon, self)
        self.tray_icon = QSystemTrayIcon(self)      # --修改22
        icon_path = resource_path("assets/tray_icon.png")  # --修改22
        self.tray_icon.setIcon(QIcon(icon_path))    # --修改22
        
        self.tray_icon.setToolTip("Word智能伴侣")
        menu = QMenu()
        menu.addAction("Word导航助手", self.show_word_navigator)      #  显示 word 导航助手 2026.05.14
        menu.addAction("图片工具箱", self.show_image_tool)      #  # 新增：图片裁剪居中工具  2026.05.18
        menu.addAction("API 监控", self.show_monitor)      #  显示 API 监控 2026.05.14
        menu.addAction("显示主窗口", self.show)
        menu.addAction("退出", self.quit_app)
        self.tray_icon.setContextMenu(menu)
        self.tray_icon.activated.connect(self.on_tray_activated)    # --修改22
        self.tray_icon.show()

    # def closeEvent(self, event):        # --修改18
        # # 停止监控线程
        # if hasattr(self, 'monitor') and self.monitor.isRunning():
            # self.monitor.stop()
            # self.monitor.wait(2000)      # 等待最多2秒
        # # 如果有其他线程也需停止
    def closeEvent(self, event):        # --修改22
        # 如果托盘图标正常且可见，则隐藏窗口（线程继续运行）
        if hasattr(self, 'tray_icon') and self.tray_icon.isVisible():
            self.hide()
            self.tray_icon.showMessage("程序仍在后台运行", "双击托盘图标可恢复窗口", QSystemTrayIcon.Information, 2000)
            event.ignore()   # 阻止窗口关闭
        else:
            if hasattr(self, 'monitor') and self.monitor.isRunning():
                self.monitor.stop()
                self.monitor.wait(2000)      # 等待最多2秒
                event.accept()
    def on_tray_activated(self, reason):    # --修改22
        if reason == QSystemTrayIcon.DoubleClick:
            # 恢复窗口时重新启动监控线程
            self.show()

    def quit_app(self):
        self.monitor.stop()
        self.floating_btn.close()   # 关闭悬浮窗
        self.tray_icon.hide()
        QApplication.quit()

    def apply_style(self):
        font = QFont(self.config.get("font_family", "Microsoft YaHei"), self.config.get("font_size", 12))
        self.setFont(font)

#  修改3
    def open_settings(self):
        dlg = SettingsDialog(self.config, self)
        if dlg.exec():
            # 重新创建 API 对象使新 Key 生效，并同步到监控线程与悬浮按钮
            self.api = DeepSeekClient(self.config)
            if hasattr(self, "monitor") and self.monitor is not None:
                self.monitor.api = self.api
            if hasattr(self, "floating_btn") and self.floating_btn is not None:
                self.floating_btn.api = self.api
            # 根据设置显示/隐藏悬浮按钮（不重建）
            self.floating_btn.setVisible(self.config.get("show_floating_button", True))
            # 刷新字体
            self.apply_style()


    # def cleanup_cache(self):          #--修改18
        # reply = QMessageBox.question(self, "清理缓存", "将清除所有缓存和忽略列表，确定吗？")
        # if reply == QMessageBox.Yes:
            # self.ignore_mgr.clear_all()
            # QMessageBox.information(self, "完成", "缓存已清理。")
    def cleanup_cache(self):        #--修改18  整个替换上面那个  
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

#--修改9  API调用监控 monitor_window.py
    def show_monitor(self):
        if not hasattr(self, 'monitor_win') or self.monitor_win is None:            
            self.monitor_win = MonitorWindow()      
            # self.monitor_win.setAttribute(Qt.WA_DeleteOnClose)        #  2026.05.14  关闭不销毁
            self.monitor_win.destroyed.connect(
                lambda: setattr(self, 'monitor_win', None)
            )
        self.monitor_win.show()           
        self.monitor_win.raise_()
        
#   Word导航助手       
    def show_word_navigator(self):
        # 如果对话框还在，直接激活
        if hasattr(self, 'word_nav_dialog') and self.word_nav_dialog is not None:
            try:
                self.word_nav_dialog.raise_()
                self.word_nav_dialog.activateWindow()
                return
            except Exception:
                self.word_nav_dialog = None   # 异常则丢弃，下次重建

        # 创建非模态对话框
        try:
            dialog = WordNavigatorDialog(self)
            dialog.setAttribute(Qt.WA_DeleteOnClose)
            dialog.destroyed.connect(lambda: setattr(self, 'word_nav_dialog', None))
            self.word_nav_dialog = dialog
            dialog.show()          # 非模态显示
        except Exception as e:
            QMessageBox.critical(self, "错误", f"无法打开 Word 导航助手：\n{e}")
            self.word_nav_dialog = None


#   Word图片裁剪居中工具箱       2026.05.18
    def show_image_tool(self):
        # 检查配置文件是否存在
        if not CONFIG_FILE.exists():
            QMessageBox.critical(
                self, "配置缺失",
                f"找不到必要文件：{CONFIG_FILE}\n请确保 config 文件夹包含 Image.docx。"
            )
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("图片工具箱")
        dialog.setMinimumSize(420, 480)   # 调大最小尺寸
        dialog.resize(420, 480)           # 初始尺寸
        layout = QVBoxLayout(dialog)
        tool = ImageReshaperWidget(config_dir=str(CONFIG_DIR))
        layout.addWidget(tool)
        dialog.setLayout(layout)
        dialog.exec()
        # 对话框关闭后立即清理，确保文档释放
        tool.cleanup()

#--修改15  置顶        
    def showEvent(self, event):
        super().showEvent(event)
        if self.config.get("main_on_top", False):
            self.set_topmost(True)
            QTimer.singleShot(50, lambda: self.set_topmost(True))
            
            
        