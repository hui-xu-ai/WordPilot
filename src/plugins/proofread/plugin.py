# plugins/proofread/plugin.py
"""实时校对插件。

原本散落在 main_window 里的这部分逻辑全部收归本插件：
- 后台监听 Word 光标，自动校对当前句子（WordMonitor）；
- 悬浮按钮与建议卡片（UI 随插件一起启停）；
- 「应用修改」的 Word 替换逻辑；
- 相关全局热键。

对外暴露能力名 "proofread"，外壳上的监听按钮通过它来控制开关，
状态变化通过事件 "proofread.toggled" 广播回外壳。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from core.sdk.plugin_base import PluginBase
from core.services.word_bridge import get_word_app
from plugins.proofread.suggestion_window import FloatingButton
from plugins.proofread.word_monitor import (
    TokenizedSentence,
    WordMonitor,
    clear_error_marks,
    extract_current_sentence,
    get_sentence_range,
    split_trailing_chars,
)

if TYPE_CHECKING:
    from core.sdk.context import PluginContext


class ProofreadPlugin(PluginBase):
    """实时校对：监听 Word 光标并给出修改建议。"""

    def __init__(self) -> None:
        super().__init__()
        self.monitor: WordMonitor | None = None
        self.floating_btn: FloatingButton | None = None
        self._enabled = True

    # ---------------- 生命周期 ----------------
    def activate(self, ctx: "PluginContext") -> None:
        super().activate(ctx)
        config = ctx.get_service("config")
        api = ctx.get_service("ai_client")
        ignore_mgr = ctx.get_service("ignore")

        # 后台监听线程
        self.monitor = WordMonitor(config, api, ignore_mgr)
        self.monitor.debug_signal.connect(lambda msg: ctx.ui.status(f"调试: {msg}", 2000))
        self.monitor.suggestion_ready.connect(self._on_suggestion)
        self.monitor.status_update.connect(lambda msg: ctx.ui.status(msg, 3000))
        self.monitor.start()

        # 悬浮按钮 + 建议卡片
        self.floating_btn = FloatingButton(
            config,
            api=api,
            ignore_mgr=ignore_mgr,
            correction_handler=self.apply_correction,
        )

        self._enabled = True
        self.monitor.proof_enabled = True
        self._apply_visibility()

        # 本插件负责的热键
        hotkeys = ctx.get_service("hotkeys")
        if hotkeys is not None:
            hotkeys.register("toggle_monitor", "ctrl+shift+e", self.toggle, owner="proofread")
            hotkeys.register("proof_cursor", "ctrl+shift+z", self.proof_cursor, owner="proofread")

        # API Key 变更后刷新内部引用
        ctx.events.subscribe("ai.client_changed", self._on_api_changed)
        ctx.register_capability("proofread", self)
        ctx.logger.info("实时校对插件已激活")

    def deactivate(self) -> None:
        if self.monitor is not None:
            try:
                self.monitor.stop()
                self.monitor.wait(2000)
            except Exception:
                self._log_exception("停止监控线程失败")
            self.monitor = None
        if self.floating_btn is not None:
            try:
                self.floating_btn.close()
            except Exception:
                self._log_exception("关闭悬浮按钮失败")
            self.floating_btn = None
        super().deactivate()

    def _log_exception(self, message: str) -> None:
        if self.ctx is not None:
            self.ctx.logger.exception(message)

    # ---------------- 能力 ----------------
    def is_enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, on: bool) -> None:
        self._enabled = bool(on)
        if self.monitor is not None:
            self.monitor.proof_enabled = self._enabled
        self._apply_visibility()
        if self.ctx is not None:
            self.ctx.events.emit("proofread.toggled", self._enabled)

    def toggle(self) -> None:
        self.set_enabled(not self._enabled)

    def _apply_visibility(self) -> None:
        if self.floating_btn is None or self.ctx is None:
            return
        config = self.ctx.config
        visible = self._enabled and bool(config.get("show_floating_button", True))
        self.floating_btn.setVisible(visible)

    def refresh_visibility(self) -> None:
        """设置项中「显示悬浮按钮」变化后由外壳或设置对话框调用。"""
        self._apply_visibility()

    # ---------------- 校对与替换 ----------------
    def _on_suggestion(self, original: str, corrected: str, start: int, end: int) -> None:
        if self.floating_btn is not None:
            self.floating_btn.set_suggestion(original, corrected, start, end)

    def proof_cursor(self) -> None:
        """对光标所在句立即做一次校对（热键 ctrl+shift+z）。"""
        try:
            current = extract_current_sentence()
            if not current or len(current) < 4:
                return
            api = self.ctx.get_service("ai_client")
            corrected = api.proof_text(current)
            if corrected and corrected.strip() != current.strip():
                self._on_suggestion(current, corrected, -1, -1)
        except Exception:
            self._log_exception("光标校对失败")

    def apply_correction(self, corrected_text: str, start: int | None = None,
                         end: int | None = None, original_text: str | None = None) -> None:
        """把建议写回 Word。

        含图片/公式的句子无法安全地整体替换，只清除标记并提示手动修改；
        纯文本句则只替换主体部分，保留句尾的段落符等不可见字符。
        """
        try:
            word = get_word_app()
            doc = word.ActiveDocument

            if start is not None and end is not None:
                sent_range = doc.Range(start, end)
            else:
                sent_range = get_sentence_range(word.Selection)

            if sent_range.Start >= sent_range.End:
                self._status("未找到句子")
                return

            tokenized = TokenizedSentence(sent_range)
            has_object = any(b['type'] == 'object' for b in tokenized.get_blocks())
            sent_text = sent_range.Text

            # 分离主体和尾部
            body_text, tail_text = split_trailing_chars(sent_text)

            if has_object:
                # 有图片/公式：弹窗提示手动修改，仅清除标记
                clear_error_marks(sent_range.Start, sent_range.End)
                self._notify_manual_edit()
                self._status("已清除标记，请手动修改")
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
                self._status("修改已应用")

        except Exception as exc:
            QMessageBox.warning(None, "错误", f"替换失败：{exc}")

    def _notify_manual_edit(self) -> None:
        parent = QApplication.activeWindow()
        msg_box = QMessageBox(parent)
        msg_box.setWindowTitle("提示")
        msg_box.setText("句子中含有图片或公式，请对照建议手动修改。")
        msg_box.setStandardButtons(QMessageBox.Ok)
        msg_box.setModal(False)
        msg_box.show()
        QTimer.singleShot(2000, msg_box.close)

    # ---------------- 事件 ----------------
    def _on_api_changed(self, _event, client) -> None:
        if self.monitor is not None:
            self.monitor.api = client
        if self.floating_btn is not None:
            self.floating_btn.api = client

    def _status(self, message: str, timeout: int = 3000) -> None:
        if self.ctx is not None:
            self.ctx.ui.status(message, timeout)
