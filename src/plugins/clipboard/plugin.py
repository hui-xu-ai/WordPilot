# plugins/clipboard/plugin.py
"""翻译改写工具插件。

原本是主窗口内置功能，GUI 重设计后下沉为普通插件：
- 经 ctx.ui.add_page 在主界面图标轨上占一个导航页，点击图标直达；
- 页面提供三种处理：翻译 / 改写 / 纠错；
- Ctrl+Shift+T / Ctrl+Shift+R 两个全局热键由本插件注册（owner=clipboard，
  插件卸载时由热键服务统一回收）；
- 所有文案走插件语言包（clipboard.* 命名空间）。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel,
                               QPushButton, QTextEdit, QVBoxLayout, QWidget)

from core.sdk.plugin_base import PluginBase

if TYPE_CHECKING:
    from core.sdk.context import PluginContext


class ClipboardWidget(QWidget):
    """翻译改写页：上方输入与结果，底部一排操作按钮。

    布局（按钮全部沉底，顺序固定）：
        ┌ 输入（剪贴板内容） ┐
        ├ 结果              ┤
        └ [读取剪贴板][复制结果][翻译][改写][纠错] ┘
    """

    def __init__(self, ctx: "PluginContext", parent=None) -> None:
        super().__init__(parent)
        self._ctx = ctx

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(10)

        # 输入区
        self.input_label = QLabel()
        self.input_label.setObjectName("cardTitle")
        layout.addWidget(self.input_label)
        self.clip_preview = QTextEdit()
        layout.addWidget(self.clip_preview, 1)

        # 结果区
        self.result_label = QLabel()
        self.result_label.setObjectName("cardTitle")
        layout.addWidget(self.result_label)
        self.clip_result = QTextEdit()
        layout.addWidget(self.clip_result, 1)

        # 底部按钮排：读取剪贴板、复制结果、翻译、改写、纠错
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.btn_load = QPushButton()
        self.btn_load.setProperty("accent", True)
        self.btn_load.clicked.connect(self.load_clipboard)
        self.btn_copy = QPushButton()
        self.btn_copy.clicked.connect(
            lambda: QApplication.clipboard().setText(self.clip_result.toPlainText()))
        self.translate_btn = QPushButton()
        self.translate_btn.clicked.connect(lambda: self.run_action("translate"))
        self.rewrite_btn = QPushButton()
        self.rewrite_btn.clicked.connect(lambda: self.run_action("rewrite"))
        self.correct_btn = QPushButton()
        self.correct_btn.clicked.connect(lambda: self.run_action("correct"))
        for btn in (self.btn_load, self.btn_copy, self.translate_btn,
                    self.rewrite_btn, self.correct_btn):
            btn_row.addWidget(btn)
        layout.addLayout(btn_row)

        self.retranslateUi()

    # ---------------- 多语言 ----------------
    def tr(self, key: str, /, **params) -> str:
        return self._ctx.tr(key, **params)

    def retranslateUi(self):        # noqa: N802 - Qt 命名惯例
        self.input_label.setText(self.tr("input_label"))
        self.clip_preview.setPlaceholderText(self.tr("placeholder"))
        self.result_label.setText(self.tr("result_label"))
        self.clip_result.setPlaceholderText(self.tr("result_placeholder"))
        self.btn_load.setText(self.tr("load"))
        self.btn_copy.setText(self.tr("copy"))
        self.translate_btn.setText(self.tr("translate"))
        self.rewrite_btn.setText(self.tr("rewrite"))
        self.correct_btn.setText(self.tr("correct"))

    # ---------------- 业务 ----------------
    def load_clipboard(self):
        self.clip_preview.setPlainText(QApplication.clipboard().text())

    def run_action(self, action: str):
        """执行翻译/改写/纠错；输入为空时自动读取剪贴板。"""
        text = self.clip_preview.toPlainText().strip()
        if not text:
            self.load_clipboard()
            text = self.clip_preview.toPlainText().strip()
        if not text:
            self.clip_result.setPlainText(self.tr("empty_tip"))
            return

        api = self._ctx.get_service("ai_client")
        if api is None:
            self.clip_result.setPlainText(self.tr("fail_no_api"))
            return

        def _fail(prefix_key: str) -> str:
            """失败文案带上具体原因，避免用户只看到"失败"两个字。"""
            reason = (getattr(api, "last_error", "") or "").strip()
            base = self.tr(prefix_key)
            return f"{base}（{reason}）" if reason else base

        if action == "translate":
            result = api.translate(text)
            self.clip_result.setPlainText(result or _fail("fail_translate"))
        elif action == "rewrite":
            result = api.rewrite(text)
            self.clip_result.setPlainText(result or _fail("fail_rewrite"))
        elif action == "correct":
            result = api.clipboard_proof(text)
            self.clip_result.setPlainText(result or _fail("fail_correct"))


class ClipboardPlugin(PluginBase):
    def activate(self, ctx: "PluginContext") -> None:
        super().activate(ctx)
        self._widget: ClipboardWidget | None = None
        self._side_panel: QWidget | None = None
        ctx.ui.add_page("page_label", self._build_page, order=15,
                        icon="clipboard", plugin_id="clipboard")
        # 插件自己的设置（翻译/改写提示词）放右侧抽屉：主界面「设置」只留全局项
        ctx.ui.add_side_panel("side_label", self._build_side_panel,
                              width=380, plugin_id="clipboard")

        hotkeys = ctx.get_service("hotkeys")
        if hotkeys is not None:
            hotkeys.register("translate", "ctrl+shift+t",
                             lambda: self._hotkey_action("translate"),
                             owner="clipboard")
            hotkeys.register("rewrite", "ctrl+shift+r",
                             lambda: self._hotkey_action("rewrite"),
                             owner="clipboard")
        ctx.logger.info("翻译改写工具插件已激活")

    def _build_page(self) -> QWidget:
        self._widget = ClipboardWidget(self.ctx)
        return self._widget

    # ---------------- 右侧抽屉：本插件设置 ----------------
    def _build_side_panel(self) -> QWidget:
        """翻译/改写提示词（`{text}` 原文、`{target_lang}` 目标语言占位符）。

        幂等：外壳按插件缓存一次，重复调用返回同一控件。
        """
        if self._side_panel is not None:
            return self._side_panel
        config = self.ctx.get_service("config")
        panel = QWidget()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)

        def _card(title_key: str, hint_key: str, config_key: str) -> QTextEdit:
            card = QWidget()
            card.setObjectName("card")
            card_lay = QVBoxLayout(card)
            card_lay.setContentsMargins(14, 12, 14, 12)
            card_lay.setSpacing(8)
            title = QLabel(self.tr(title_key))
            title.setObjectName("cardTitle")
            card_lay.addWidget(title)
            hint = QLabel(self.tr(hint_key))
            hint.setObjectName("hintLabel")
            hint.setWordWrap(True)
            card_lay.addWidget(hint)
            edit = QTextEdit()
            edit.setMinimumHeight(96)
            edit.setPlainText(str(config.get(config_key, "") or ""))
            card_lay.addWidget(edit)
            lay.addWidget(card)
            return edit

        self.translate_prompt_edit = _card("settings.prompt.translate",
                                           "settings.prompt.translate_hint",
                                           "translate_prompt")
        self.rewrite_prompt_edit = _card("settings.prompt.rewrite",
                                         "settings.prompt.rewrite_hint",
                                         "rewrite_prompt")

        self.save_prompt_btn = QPushButton(self.tr("settings.save"))
        self.save_prompt_btn.clicked.connect(self.save_plugin_settings)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.save_prompt_btn)
        lay.addLayout(row)
        lay.addStretch(1)
        self._side_panel = panel
        return panel

    def save_plugin_settings(self) -> None:
        """一次写盘并广播 `settings.saved`（内核据此重建 AI 客户端）。"""
        config = self.ctx.get_service("config")
        payload = {
            "translate_prompt": self.translate_prompt_edit.toPlainText(),
            "rewrite_prompt": self.rewrite_prompt_edit.toPlainText(),
        }
        if config.update(payload):
            self.ctx.events.emit("settings.saved", None)
            self.ctx.ui.status(self.tr("settings.saved"), 2000)
        else:
            self.ctx.ui.status(self.tr("settings.save_failed"), 3000)

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
        else:
            # 成功静默、失败必须出声，否则用户以为热键没生效
            reason = (getattr(api, "last_error", "") or "").strip()
            self.ctx.ui.status(self.tr("fail_hotkey") + (f"：{reason}" if reason else ""), 5000)
