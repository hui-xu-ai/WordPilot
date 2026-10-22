# core/widgets/monitor_panel.py
"""API 调用监控面板（内嵌式）。

由 app/monitor_window.py 下沉而来：原来是「可当窗口也可内嵌」的 QWidget，
现在只作为内嵌面板使用（独立窗口入口已由「智能纠错」插件页取代），
因此去掉了窗口标题与关闭按钮。

数据来自 core.services.api_logger；文案走内核语言包（monitor.*）。
显示期间每 4 秒自动刷新，隐藏即停表。
"""
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QTextEdit,
                               QVBoxLayout, QWidget)

from core.services.api_logger import get_all_logs, get_last_log


class MonitorPanel(QWidget):
    """最近一次调用 + 全部历史 + 手动刷新。"""

    def __init__(self, parent=None, i18n=None):
        super().__init__(parent)
        self.i18n = i18n

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # ---------- 最近一次调用记录区域 ----------
        self.last_title = QLabel()
        layout.addWidget(self.last_title)
        self.last_edit = QTextEdit()
        self.last_edit.setReadOnly(True)
        self.last_edit.setMaximumHeight(200)   # 限制高度，给历史留空间
        layout.addWidget(self.last_edit)

        # ---------- 全部历史记录区域 ----------
        self.history_title = QLabel()
        layout.addWidget(self.history_title)
        self.history_edit = QTextEdit()
        self.history_edit.setReadOnly(True)
        layout.addWidget(self.history_edit, 1)

        # ---------- 按钮行 ----------
        btn_layout = QHBoxLayout()
        self.refresh_btn = QPushButton()
        self.refresh_btn.clicked.connect(self.refresh)
        btn_layout.addWidget(self.refresh_btn)

        self.history_btn = QPushButton()
        self.history_btn.clicked.connect(self.show_history)
        btn_layout.addWidget(self.history_btn)
        btn_layout.addStretch(1)

        layout.addLayout(btn_layout)

        # 定时器：面板显示时启动，隐藏时停止
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)

        self.retranslateUi()

    # ---------------- 多语言 ----------------
    def tr(self, key: str, /, **params) -> str:
        if self.i18n is None:
            return key
        return self.i18n.tr(key, **params)

    def retranslateUi(self):        # noqa: N802 - Qt 命名惯例
        self.last_title.setText(self.tr("monitor.last_title"))
        self.history_title.setText(self.tr("monitor.history_title"))
        self.refresh_btn.setText(self.tr("monitor.refresh"))
        self.history_btn.setText(self.tr("monitor.history"))
        self.refresh()
        self.show_history()

    def showEvent(self, event):
        super().showEvent(event)
        self.timer.start(4000)   # 每 4 秒刷新
        self.refresh()           # 显示时立即刷新一次

    def hideEvent(self, event):
        super().hideEvent(event)
        self.timer.stop()

    def refresh(self):
        """更新最近一次调用记录（不影响历史区域）"""
        info = self.tr("monitor.header") + "\n" + "=" * 40 + "\n"
        last = get_last_log()
        if last:
            info += self.tr("monitor.time", value=last.get('time', self.tr("monitor.unknown"))) + "\n"
            info += self.tr("monitor.action", value=last.get('action', self.tr("monitor.unknown"))) + "\n"
            info += self.tr("monitor.input", value=last.get('input', '')) + "\n"
            info += self.tr("monitor.output", value=last.get('output', '')) + "\n"
            tokens = last.get('tokens', {})
            info += self.tr("monitor.tokens",
                            prompt=tokens.get('prompt', 0),
                            completion=tokens.get('completion', 0),
                            total=tokens.get('total', 0)) + "\n"
            status = self.tr("monitor.success") if last.get('success') else self.tr("monitor.failed")
            info += self.tr("monitor.status", value=status) + "\n"
        else:
            info += self.tr("monitor.none")
        self.last_edit.setPlainText(info)

    def show_history(self):
        """加载全部历史记录到专用区域（不被定时刷新覆盖）"""
        logs = get_all_logs()
        if not logs:
            self.history_edit.setPlainText(self.tr("monitor.history_none"))
            return
        text = self.tr("monitor.history_count", count=len(logs)) + "\n" + "=" * 40 + "\n"
        for i, log in enumerate(reversed(logs), 1):  # 倒序遍历，序号从1开始
            text += f"[{i}] {log.get('time','')} | {log.get('action','')} | "
            text += f"总tokens: {log.get('tokens',{}).get('total',0)} | "
            status = self.tr("monitor.success") if log.get('success') else self.tr("monitor.failed")
            text += f"{status}\n"
        self.history_edit.setPlainText(text)
