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

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (QApplication, QComboBox, QHBoxLayout, QLabel,
                               QPushButton, QTextEdit, QVBoxLayout, QWidget, QGridLayout)

from core.sdk.plugin_base import PluginBase


def _display_language(combo, value: str) -> str:
    """配置值 → 下拉显示文案（auto 显示成「自动互译」，用户才看得懂）。"""
    for index in range(combo.count()):
        if combo.itemData(index) == value:
            return combo.itemText(index)
    combo.addItem(value, value)
    return value


def _language_value(combo) -> str:
    """下拉显示文案 → 配置值（用户自填的语言原样返回）。"""
    text = combo.currentText().strip()
    for index in range(combo.count()):
        if combo.itemText(index) == text and combo.itemData(index):
            return str(combo.itemData(index))
    return text or "auto"
from core.widgets.prompt_version_bar import PromptVersionBar

if TYPE_CHECKING:
    from core.sdk.context import PluginContext


class _ActionWorker(QThread):
    """把一次 API 调用搬出 GUI 线程（U04：同步请求最长会冻结界面 30s）。"""

    done = Signal(str)                 # 成功：结果文本
    failed = Signal(str)               # 失败：带原因的文案

    def __init__(self, call, fail_text: str) -> None:
        # 无父对象：父页面销毁时不能连带析构运行中的 QThread（同 U03 的教训）
        super().__init__(None)
        self._call = call
        self._fail_text = fail_text

    def run(self) -> None:             # noqa: D401 - QThread 入口
        try:
            result = self._call()
        except Exception as exc:       # noqa: BLE001 - 后台异常必须回传
            self.failed.emit(f"{self._fail_text}（{exc}）")
            return
        if result:
            self.done.emit(str(result))
        else:
            self.failed.emit(self._fail_text)


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
        self._worker: _ActionWorker | None = None

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

        # 底部按钮区，按**语义**分两行（用户反馈：3+2 的切法没有逻辑）：
        #   第 1 行 = 剪贴板操作：读取剪贴板 / 复制结果
        #   第 2 行 = AI 操作：翻译 / 改写 / 纠错
        # 用 6 列栅格实现：上行两个按钮各跨 3 列，下行三个按钮各跨 2 列，左边缘对齐。
        btn_row = QGridLayout()
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
        btn_row.addWidget(self.btn_load, 0, 0, 1, 3)        # 第 1 行：读取
        btn_row.addWidget(self.btn_copy, 0, 3, 1, 3)        # 第 1 行：复制
        btn_row.addWidget(self.translate_btn, 1, 0, 1, 2)   # 第 2 行：翻译
        btn_row.addWidget(self.rewrite_btn, 1, 2, 1, 2)     # 第 2 行：改写
        btn_row.addWidget(self.correct_btn, 1, 4, 1, 2)     # 第 2 行：纠错
        for column in range(6):
            btn_row.setColumnStretch(column, 1)
        layout.addLayout(btn_row)

        self.retranslateUi()

    # ---------------- 多语言 ----------------
    def tr(self, key: str, /, **params) -> str:
        return self._ctx.tr(key, **params)

    def retranslateUi(self):        # noqa: N802 - Qt 命名惯例
        for bar in getattr(self, "_prompt_bars", []):
            bar.retranslate()
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

    def _set_busy(self, busy: bool) -> None:
        """请求期间禁用动作按钮：避免连点发出多个请求，也给出"正在处理"的反馈。"""
        for button in (self.btn_load, self.btn_copy, self.translate_btn,
                       self.rewrite_btn, self.correct_btn):
            button.setEnabled(not busy)

    def run_action(self, action: str):
        """执行翻译/改写/纠错：输入为空时自动读剪贴板，请求走工作线程（U04）。"""
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

        calls = {
            "translate": (api.translate, "fail_translate"),
            "rewrite": (api.rewrite, "fail_rewrite"),
            "correct": (api.clipboard_proof, "fail_correct"),
        }
        entry = calls.get(action)
        if entry is None:
            return
        call, fail_key = entry

        def _fail_text() -> str:
            """失败文案带上具体原因，避免用户只看到"失败"两个字。"""
            reason = (getattr(api, "last_error", "") or "").strip()
            base = self.tr(fail_key)
            return f"{base}（{reason}）" if reason else base

        self.clip_result.setPlainText(self.tr("running"))
        self._set_busy(True)
        worker = _ActionWorker(lambda: call(text), _fail_text())
        worker.done.connect(self._on_action_done)
        worker.failed.connect(self._on_action_failed)
        worker.finished.connect(self._on_action_finished)
        self._worker = worker                     # 持引用，防被回收
        worker.start()

    def _on_action_done(self, result: str) -> None:
        self.clip_result.setPlainText(result)

    def _on_action_failed(self, message: str) -> None:
        self.clip_result.setPlainText(message)

    def _on_action_finished(self) -> None:
        self._set_busy(False)
        self._worker = None


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
            i18n = self.ctx.get_service("i18n") if getattr(self, "ctx", None) else None
            # 显示**生效文本**：config 有值用 config，否则显示系统默认提示词
            # （以前只读 config，config 为空时框里空白，用户以为默认丢了）
            from core.services import prompt_store

            edit.setPlainText(prompt_store.effective_text(
                config_key, config, getattr(i18n, "prompt_tr", None)))
            card_lay.addWidget(edit)
            bar = PromptVersionBar(config_key, config, self.tr,
                                   read_text=edit.toPlainText,
                                   apply_text=edit.setPlainText, parent=card,
                                   prompt_tr=getattr(i18n, "prompt_tr", None))
            bar.retranslate()
            card_lay.addWidget(bar)
            self._prompt_bars.append(bar)
            lay.addWidget(card)
            return edit

        self._prompt_bars: list = []
        # 翻译目标语言（本插件自己的配置）：提示词里的 {target_lang}
        lang_card = QWidget()
        lang_card.setObjectName("card")
        lang_lay = QVBoxLayout(lang_card)
        lang_lay.setContentsMargins(14, 12, 14, 12)
        lang_lay.setSpacing(8)
        self.translate_language_title = QLabel(self.tr("settings.prompt.translatelang"))
        self.translate_language_title.setObjectName("cardTitle")
        lang_lay.addWidget(self.translate_language_title)
        self.translate_language_edit = QComboBox()
        self.translate_language_edit.setEditable(True)
        for display_key, value in (("settings.prompt.translatelang_auto", "auto"),
                                   ("settings.prompt.translatelang_zh", "中文"),
                                   ("settings.prompt.translatelang_en", "英文"),
                                   ("settings.prompt.translatelang_ja", "日文"),
                                   ("日文", "日文"), ("韩文", "韩文"),
                                   ("法文", "法文"), ("德文", "德文")):
            self.translate_language_edit.addItem(self.tr(display_key), value)
        self.translate_language_edit.setCurrentText(str(
            _display_language(self.translate_language_edit,
                              str(config.get("translate_language", "auto") or "auto"))))
        self.translate_language_edit.setToolTip(self.tr("settings.prompt.translatelang_tip"))
        lang_lay.addWidget(self.translate_language_edit)
        lay.addWidget(lang_card)

        self.translate_prompt_edit = _card("settings.prompt.translate",
                                           "settings.prompt.translate_hint",
                                           "translate_prompt")
        self.rewrite_prompt_edit = _card("settings.prompt.rewrite",
                                         "settings.prompt.rewrite_hint",
                                         "rewrite_prompt")
        # 本页还有一个「纠错」按钮：整段文本校对，提示词与桌宠逐句校对分开管理
        self.correct_prompt_edit = _card("settings.prompt.correct",
                                         "settings.prompt.correct_hint",
                                         "correct_prompt")

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
            "correct_prompt": self.correct_prompt_edit.toPlainText(),
            "translate_language": _language_value(self.translate_language_edit),
        }
        if config.update(payload):
            for bar in getattr(self, "_prompt_bars", []):
                bar.commit()                 # 保存即生效，并更新当前提示词版本
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
