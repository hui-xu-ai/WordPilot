# plugins/clipboard/plugin.py
"""剪贴板工具插件。

原本是主窗口内置功能，GUI 重设计后下沉为普通插件：
- 经 ctx.ui.add_page 在主界面图标轨上占一个导航页，点击图标直达；
- Ctrl+Shift+T / Ctrl+Shift+R 两个全局热键由本插件注册（owner=clipboard，
  插件卸载时由热键服务统一回收）；
- 所有文案走插件语言包（clipboard.* 命名空间）。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import (QApplication, QHBoxLayout, QMessageBox,
                               QPushButton, QTextEdit, QVBoxLayout, QWidget)

from core.sdk.plugin_base import PluginBase
from core.services.word_bridge import WordNotRunningError, get_word_app

if TYPE_CHECKING:
    from core.sdk.context import PluginContext


class ClipboardWidget(QWidget):
    """剪贴板工具页：读取 -> 纠错/翻译/改写 -> 复制/插入 Word。"""

    def __init__(self, ctx: "PluginContext", parent=None) -> None:
        super().__init__(parent)
        self._ctx = ctx

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        self.btn_load = QPushButton()
        self.btn_load.setProperty("accent", True)
        self.btn_load.clicked.connect(self.load_clipboard)
        layout.addWidget(self.btn_load)

        self.clip_preview = QTextEdit()
        layout.addWidget(self.clip_preview)

        btn_row = QHBoxLayout()
        self.translate_btn = QPushButton()
        self.rewrite_btn = QPushButton()
        self.translate_btn.clicked.connect(lambda: self.run_action("translate"))
        self.rewrite_btn.clicked.connect(lambda: self.run_action("rewrite"))
        for btn in (self.translate_btn, self.rewrite_btn):
            btn_row.addWidget(btn)
        layout.addLayout(btn_row)

        self.clip_result = QTextEdit()
        layout.addWidget(self.clip_result)

        res_row = QHBoxLayout()
        self.btn_insert = QPushButton()
        self.btn_copy = QPushButton()
        self.btn_insert.clicked.connect(self.insert_to_word)
        self.btn_copy.clicked.connect(
            lambda: QApplication.clipboard().setText(self.clip_result.toPlainText()))
        res_row.addWidget(self.btn_insert)
        res_row.addWidget(self.btn_copy)
        layout.addLayout(res_row)

        self.retranslateUi()

    # ---------------- 多语言 ----------------
    def tr(self, key: str, /, **params) -> str:
        return self._ctx.tr(key, **params)

    def retranslateUi(self):        # noqa: N802 - Qt 命名惯例
        self.btn_load.setText(self.tr("load"))
        self.clip_preview.setPlaceholderText(self.tr("placeholder"))
        self.translate_btn.setText(self.tr("translate"))
        self.rewrite_btn.setText(self.tr("rewrite"))
        self.clip_result.setPlaceholderText(self.tr("result_placeholder"))
        self.btn_insert.setText(self.tr("insert"))
        self.btn_copy.setText(self.tr("copy"))

    # ---------------- 业务 ----------------
    def load_clipboard(self):
        self.clip_preview.setPlainText(QApplication.clipboard().text())

    def run_action(self, action: str):
        """执行纠错/翻译/改写；输入为空时自动读取剪贴板。"""
        text = self.clip_preview.toPlainText().strip()
        if not text:
            self.load_clipboard()
            text = self.clip_preview.toPlainText().strip()
        if not text:
            self.clip_result.setPlainText(self.tr("empty_tip"))
            return

        api = self._ctx.get_service("ai_client")
        if api is None:
            return
        if action == "translate":
            result = api.translate(text)
            self.clip_result.setPlainText(result or self.tr("fail_translate"))
        elif action == "rewrite":
            result = api.rewrite(text)
            self.clip_result.setPlainText(result or self.tr("fail_rewrite"))

    def insert_to_word(self):
        text = self.clip_result.toPlainText()
        if not text:
            return
        try:
            word = get_word_app()
            word.Selection.TypeText(text)
        except WordNotRunningError as exc:
            QMessageBox.warning(self, self.tr("error_title"), str(exc))


class ClipboardPlugin(PluginBase):
    def activate(self, ctx: "PluginContext") -> None:
        super().activate(ctx)
        self._widget: ClipboardWidget | None = None
        ctx.ui.add_page("page_label", self._build_page, order=15,
                        icon="clipboard", plugin_id="clipboard")

        hotkeys = ctx.get_service("hotkeys")
        if hotkeys is not None:
            hotkeys.register("translate", "ctrl+shift+t",
                             lambda: self._hotkey_action("translate"),
                             owner="clipboard")
            hotkeys.register("rewrite", "ctrl+shift+r",
                             lambda: self._hotkey_action("rewrite"),
                             owner="clipboard")
        ctx.logger.info("剪贴板工具插件已激活")

    def _build_page(self) -> QWidget:
        self._widget = ClipboardWidget(self.ctx)
        return self._widget

    def _hotkey_action(self, action: str) -> None:
        """全局热键：页面已打开就操作页面文本，否则直接处理剪贴板内容
        并把结果写回剪贴板。"""
        if self._widget is not None:
            self._widget.run_action(action)
            return
        text = QApplication.clipboard().text().strip()
        if not text:
            return
        api = self.ctx.get_service("ai_client")
        if api is None:
            return
        result = api.translate(text) if action == "translate" else api.rewrite(text)
        if result:
            QApplication.clipboard().setText(result)
            self.ctx.ui.status(self.tr("hotkey_done"), 3000)
