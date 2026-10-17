# monitor_window.py
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QTextEdit,
                               QPushButton, QHBoxLayout, QLabel)

from core.services.api_logger import get_last_log, get_all_logs


class MonitorWindow(QWidget):
    """API 调用监控窗口（单例，关闭转隐藏由 WindowManager 治理）。"""

    def __init__(self, parent=None, i18n=None):
        super().__init__(parent)
        self.i18n = i18n
        self.setWindowTitle(self.tr("monitor.title"))
        self.resize(450, 500)

        layout = QVBoxLayout(self)

        # ---------- 最近一次调用记录区域 ----------
        self.last_title = QLabel(self.tr("monitor.last_title"))
        layout.addWidget(self.last_title)
        self.last_edit = QTextEdit()
        self.last_edit.setReadOnly(True)
        self.last_edit.setMaximumHeight(200)   # 限制高度，给历史留空间
        layout.addWidget(self.last_edit)

        # ---------- 全部历史记录区域 ----------
        self.history_title = QLabel(self.tr("monitor.history_title"))
        layout.addWidget(self.history_title)
        self.history_edit = QTextEdit()
        self.history_edit.setReadOnly(True)
        layout.addWidget(self.history_edit)

        # ---------- 按钮行 ----------
        btn_layout = QHBoxLayout()
        self.refresh_btn = QPushButton(self.tr("monitor.refresh"))
        self.refresh_btn.clicked.connect(self.refresh)
        btn_layout.addWidget(self.refresh_btn)

        self.history_btn = QPushButton(self.tr("monitor.history"))
        self.history_btn.clicked.connect(self.show_history)
        btn_layout.addWidget(self.history_btn)

        self.close_btn = QPushButton(self.tr("monitor.close"))
        self.close_btn.clicked.connect(self.close)
        btn_layout.addWidget(self.close_btn)

        layout.addLayout(btn_layout)

        # 定时器：窗口显示时启动，隐藏/关闭时停止
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)

    # ---------------- 多语言 ----------------
    def tr(self, key: str, /, **params) -> str:
        if self.i18n is None:
            return key
        return self.i18n.tr(key, **params)

    def retranslateUi(self):        # noqa: N802 - Qt 命名惯例
        self.setWindowTitle(self.tr("monitor.title"))
        self.last_title.setText(self.tr("monitor.last_title"))
        self.history_title.setText(self.tr("monitor.history_title"))
        self.refresh_btn.setText(self.tr("monitor.refresh"))
        self.history_btn.setText(self.tr("monitor.history"))
        self.close_btn.setText(self.tr("monitor.close"))
        self.refresh()
        self.show_history()

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
