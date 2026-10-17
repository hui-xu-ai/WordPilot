# monitor_window.py     
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QTextEdit,
                               QPushButton, QHBoxLayout, QLabel)

from core.services.api_logger import get_last_log, get_all_logs


class MonitorWindow(QWidget):           #--修改16
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("API 调用监控")
        self.resize(450, 500)

        layout = QVBoxLayout(self)

        # ---------- 最近一次调用记录区域 ----------
        layout.addWidget(QLabel("📌 最近一次调用记录："))
        self.last_edit = QTextEdit()
        self.last_edit.setReadOnly(True)
        self.last_edit.setMaximumHeight(200)   # 限制高度，给历史留空间
        layout.addWidget(self.last_edit)

        # ---------- 全部历史记录区域 ----------
        layout.addWidget(QLabel("📋 全部历史记录："))
        self.history_edit = QTextEdit()
        self.history_edit.setReadOnly(True)
        layout.addWidget(self.history_edit)

        # ---------- 按钮行 ----------
        btn_layout = QHBoxLayout()
        self.refresh_btn = QPushButton("手动刷新")
        self.refresh_btn.clicked.connect(self.refresh)
        btn_layout.addWidget(self.refresh_btn)

        self.history_btn = QPushButton("查看全部历史")
        self.history_btn.clicked.connect(self.show_history)
        btn_layout.addWidget(self.history_btn)

        self.close_btn = QPushButton("关闭")
        self.close_btn.clicked.connect(self.close)
        btn_layout.addWidget(self.close_btn)

        layout.addLayout(btn_layout)

        # 定时器：窗口显示时启动，隐藏/关闭时停止
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)

    def showEvent(self, event):
        super().showEvent(event)
        self.timer.start(4000)   # 每 4 秒刷新
        self.refresh()           # 打开时立即刷新一次

    def hideEvent(self, event):
        super().hideEvent(event)
        self.timer.stop()

    # 关闭语义由 WindowManager 统一裁决（HIDE：转隐藏并触发上面的 hideEvent 停表），
    # 本窗口不再自己 event.ignore()，避免与窗口治理互相打架

    def refresh(self):
        """更新最近一次调用记录（不影响历史区域）"""
        info = "最近一次调用记录：\n" + "=" * 40 + "\n"
        last = get_last_log()
        if last:
            info += f"时间：{last.get('time', '未知')}\n"
            info += f"操作：{last.get('action', '未知')}\n"
            info += f"输入（前200字）：{last.get('input', '')}\n"
            info += f"输出（前200字）：{last.get('output', '')}\n"
            tokens = last.get('tokens', {})
            info += f"Token消耗明细 → 提示词: {tokens.get('prompt', 0)} | 输出: {tokens.get('completion', 0)} | 总共消耗: {tokens.get('total', 0)}\n"
            info += f"状态：{'成功' if last.get('success') else '失败'}\n"
        else:
            info += "暂无调用记录"
        self.last_edit.setPlainText(info)

    def show_history(self):
        """加载全部历史记录到专用区域（不被定时刷新覆盖）"""
        logs = get_all_logs()
        if not logs:
            self.history_edit.setPlainText("暂无历史记录")
            return
        text = f"共 {len(logs)} 条记录：\n" + "=" * 40 + "\n"
        # for i, log in enumerate(logs, 1):          # 顺序遍历
        for i, log in enumerate(reversed(logs), 1):  # 倒序遍历，序号从1开始
            text += f"[{i}] {log.get('time','')} | {log.get('action','')} | "
            text += f"总tokens: {log.get('tokens',{}).get('total',0)} | "
            text += f"{'成功' if log.get('success') else '失败'}\n"
        self.history_edit.setPlainText(text)