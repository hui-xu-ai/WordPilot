# plugins/proofread/plugin.py
"""智能纠错插件。（原名「实时校对」，P9 与侧边栏「智能纠错」统一）

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

from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtWidgets import QApplication, QMessageBox

import logging

from core.sdk.plugin_base import PluginBase

logger = logging.getLogger(__name__)
from core.services.skin_service import STATE_TOKENS
from core.services.window_manager import CloseBehavior, Layer, Mode
from core.services.word_bridge import get_word_app
from plugins.proofread.pet_window import WINDOW_ID as PET_WINDOW_ID
from plugins.proofread.pet_window import PetWindow
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


class _CursorProofWorker(QThread):
    """热键校对的后台执行体：只把**网络请求**搬出 GUI 线程，COM 读取仍在主线程。

    历史实现里 `Ctrl+Shift+Z` 在 GUI 线程同步调 `api.proof_text()`（HTTP，timeout=30），
    按一次热键整个界面冻结到超时。
    """

    done = Signal(str, object, str)      # (原句, 纠正结果或 None, 失败原因)

    def __init__(self, api, original: str, parent=None) -> None:
        super().__init__(parent)
        self._api = api
        self._original = original

    def run(self) -> None:               # noqa: D401 - QThread 入口
        try:
            corrected = self._api.proof_text(self._original)
            reason = "" if corrected is not None else (getattr(self._api, "last_error", "") or "")
        except Exception as exc:  # noqa: BLE001  后台线程异常必须回传，不能静默
            corrected, reason = None, str(exc)
        self.done.emit(self._original, corrected, reason)


class ProofreadPlugin(PluginBase):
    """智能纠错：监听 Word 光标并给出修改建议。"""

    def __init__(self) -> None:
        super().__init__()
        self.monitor: WordMonitor | None = None
        self.floating_btn: PetWindow | None = None     # 字段名保留，外壳与其它插件按它取用
        self._dashboard_page = None                    # 智能纠错页（外壳惰性构造后回填）
        self._enabled = True
        self._state = "idle"
        self._cursor_worker: _CursorProofWorker | None = None

    # ---------------- 生命周期 ----------------
    def activate(self, ctx: "PluginContext") -> None:
        super().activate(ctx)
        config = ctx.get_service("config")
        api = ctx.get_service("ai_client")
        ignore_mgr = ctx.get_service("ignore")

        # 后台监听线程
        i18n = ctx.get_service("i18n")
        self.monitor = WordMonitor(config, api, ignore_mgr, i18n=i18n)
        self.monitor.debug_signal.connect(
            lambda msg: ctx.ui.status(self.tr("status.debug", msg=msg), 2000))
        self.monitor.suggestion_ready.connect(self._on_suggestion)
        self.monitor.proofing_started.connect(self._on_proofing_started)
        self.monitor.status_update.connect(lambda msg: ctx.ui.status(msg, 3000))
        self.monitor.start()

        # 桌宠窗口（桌宠本体 + 建议气泡合并在一个受管窗口里）
        self.floating_btn = self._open_pet(config, api, ignore_mgr, i18n)

        self._enabled = True
        self.monitor.proof_enabled = True
        self._apply_visibility()

        # 本插件负责的热键
        hotkeys = ctx.get_service("hotkeys")
        if hotkeys is not None:
            hotkeys.register("toggle_monitor", "ctrl+shift+e", self.toggle, owner="proofread")
            hotkeys.register("proof_cursor", "ctrl+shift+z", self.proof_cursor, owner="proofread")

        # 「智能纠错」主页面 + 托盘直达：随插件启停自动上轨/消失（P9 解耦）
        ctx.ui.add_page("nav.page", self._build_dashboard_page,
                        order=10, icon="dashboard", plugin_id="proofread")
        # ADR-002：参数/缓存清洗/API 监控属于次要界面，进右侧可折叠抽屉（默认收起）
        ctx.ui.add_side_panel("dashboard.side_label", self._build_side_panel,
                              width=380, plugin_id="proofread")
        ctx.ui.add_tray_action("nav.monitor", self._open_dashboard_page,
                               order=50, plugin_id="proofread")

        # API Key 变更后刷新内部引用；语言/主题切换后刷新界面；
        # 设置保存后按新配置重新计算桌宠可见性
        self.subscribe("ai.client_changed", self._on_api_changed)
        self.subscribe("i18n.language_changed", self._on_language_changed)
        self.subscribe("theme.changed", self._on_theme_changed)
        self.subscribe("skin.changed", self._on_skin_changed)
        self.subscribe("settings.saved", self._on_settings_saved)
        ctx.register_capability("proofread", self)
        ctx.logger.info(self.tr("log.activated"))

    def _open_pet(self, config, api, ignore_mgr, i18n) -> PetWindow | None:
        """通过 WindowManager 打开桌宠，保证它是受管窗口而非野窗口。"""
        windows = self.ctx.windows
        theme = self.ctx.get_service("theme")
        skins = self.ctx.get_service("skins")

        def _factory() -> PetWindow:
            pet = PetWindow(
                config, api=api, ignore_mgr=ignore_mgr,
                correction_handler=self.apply_correction,
                theme=theme, skins=skins, i18n=i18n, tr=self.tr,
                tools_provider=self._plugin_tools,
            )
            pet.proofreadToggleRequested.connect(self.toggle)
            pet.settingsRequested.connect(self._open_settings)
            # 用户在气泡上点了确认/忽略 -> 气泡关闭 -> 表情从 error 回落到打盹
            pet.bubbleToggled.connect(self._on_bubble_toggled)
            return pet

        if windows is None:
            # 无窗口服务（离屏/测试环境）：仍构造出一个实例，只是不受管
            return _factory()

        try:
            windows.open(
                PET_WINDOW_ID, _factory, owner="proofread",
                mode=Mode.SINGLETON,
                layer=Layer.OVERLAY,
                # 隐藏而非销毁：桌宠要常驻桌面，关闭主窗口时也不该被抹掉位置
                behavior=CloseBehavior.HIDE,
                remember_geometry=False,
            )
        except Exception:
            self._log_exception(self.tr("error.open_pet"))
            return None
        pet = windows.get(PET_WINDOW_ID)
        if pet is not None:
            pet.show()
        return pet

    def _build_dashboard_page(self):
        """智能纠错页工厂：外壳首次切到该页时惰性构造，控件由外壳缓存。"""
        from plugins.proofread.dashboard_page import DashboardPage
        self._dashboard_page = DashboardPage(self)
        return self._dashboard_page

    def _build_side_panel(self):
        """右侧抽屉内容工厂（外壳按插件缓存）：页面必须先构造好才有控件可搬。"""
        page = self._dashboard_page
        if page is None:
            return None
        return page.build_secondary_panel()

    def _open_dashboard_page(self) -> None:
        """托盘「API 监控」-> 抬出主窗口并切到智能纠错页。"""
        main_window = self.ctx.ui.main_window() if self.ctx is not None else None
        if main_window is not None:
            main_window.show_page("plugin:proofread")

    def _open_settings(self) -> None:
        """桌宠右键菜单 -> 设置：走外壳已注册的设置入口。"""
        main_window = self.ctx.ui.main_window() if self.ctx is not None else None
        if main_window is not None and hasattr(main_window, "open_settings"):
            main_window.open_settings()

    def _plugin_tools(self) -> list:
        """桌宠右键菜单「插件」子菜单的数据：[(名称, 回调)]，现场取值。

        已启用插件的工作面板点击后弹出主界面并自动切到对应 Tab；
        窗口型插件（如导航助手）点击后直接打开自己的窗口。
        """
        if self.ctx is None:
            return []
        entries: list = []
        try:
            main_window = self.ctx.ui.main_window()
            for label, _factory, _icon, plugin_id in self.ctx.ui.pages():
                if plugin_id and main_window is not None:
                    entries.append(
                        (label, lambda pid=plugin_id: main_window.show_page(f"plugin:{pid}")))
            for label, handler, _icon, _pid in self.ctx.ui.tools():
                entries.append((label, handler))
        except Exception:
            return []
        return entries

    def deactivate(self) -> None:
        if self.monitor is not None:
            monitor = self.monitor
            stopped = False
            try:
                monitor.stop()
                # 必须确认线程真的结束了才能丢引用：仍在跑就销毁 QThread 会直接 abort 进程
                stopped = monitor.wait(2000) or monitor.wait(3000)
            except Exception:
                self._log_exception(self.tr("log.stop_monitor_failed"))
            if stopped:
                self.monitor = None
            else:
                # 线程仍卡在 HTTP/COM 上：保留引用（宁可泄漏，也不能销毁运行中的 QThread）
                if self.ctx is not None:
                    self.ctx.logger.error("监控线程未在 5s 内退出，保留引用以避免崩溃")
        if self.floating_btn is not None:
            try:
                self.floating_btn.close()
            except Exception:
                self._log_exception(self.tr("log.close_pet_failed"))
            self.floating_btn = None
        # 智能纠错页由外壳在 plugins.changed 后回收销毁，这里只清引用
        self._dashboard_page = None
        super().deactivate()

    def _log_exception(self, message: str) -> None:
        if self.ctx is not None:
            self.ctx.logger.exception(message)

    def retranslateUi(self):        # noqa: N802 - Qt 命名惯例
        """语言热切换后刷新本插件持有的界面文案。"""
        if self.floating_btn is not None:
            self.floating_btn.retranslateUi()

    # ---------------- 桌宠状态机 ----------------
    def set_state(self, state: str) -> None:
        """切换桌宠状态：idle(无错) / error(有错) / busy(校对中) / disabled(未启用)。"""
        state = state if state in STATE_TOKENS else "idle"
        if state == self._state:
            return
        self._state = state
        if self.floating_btn is not None:
            self.floating_btn.set_state(state)
        if self.ctx is not None:
            # 广播出去，将来外壳的状态栏/托盘也能跟着变
            self.ctx.events.emit("proofread.state_changed", state)

    def state(self) -> str:
        return self._state

    def _sync_state(self) -> None:
        """按当前开关与气泡内容推导应显示的状态。"""
        if self.ctx is None:
            return
        if not self._enabled or not bool(self.ctx.config.get("show_floating_button", True)):
            self.set_state("disabled")
            return
        if self.floating_btn is not None and self.floating_btn.bubble.isVisible():
            self.set_state("error")
        else:
            self.set_state("idle")

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
        self.set_state("idle" if visible else "disabled")

    def refresh_visibility(self) -> None:
        """设置项中「显示悬浮按钮」变化后由外壳或设置对话框调用。"""
        self._apply_visibility()

    # ---------------- 校对与替换 ----------------
    @staticmethod
    def normalize_span(start, end) -> tuple[int | None, int | None]:
        """把「无范围」的各种表示（None / -1 / 空区间）统一成 (None, None)。

        审计 H14：热键路径曾用 `-1, -1` 当"没有范围"的哨兵，而 `apply_correction`
        只判 `is not None`，于是 `doc.Range(-1, -1)` 必然报 COM 错（或从文档开头覆盖）。
        """
        if start is None or end is None:
            return None, None
        try:
            start, end = int(start), int(end)
        except (TypeError, ValueError):
            return None, None
        if start < 0 or end <= start:
            return None, None
        return start, end

    def _on_suggestion(self, original: str, corrected: str,
                       start: int | None, end: int | None) -> None:
        """收到校对结果：有差异 -> error（等用户决策）；无差异 -> ok（2 秒后回落打盹）。"""
        if self.floating_btn is None:
            return
        start, end = self.normalize_span(start, end)
        has_diff = bool(original and corrected and original.strip() != corrected.strip())
        # 控件层仍以 -1 表示"无范围"，归一化后在这里转换，避免把哨兵值传到 COM
        self.floating_btn.set_suggestion(original, corrected,
                                        -1 if start is None else start,
                                        -1 if end is None else end)
        if has_diff:
            self.set_state("error")
        else:
            self._show_ok()

    def _show_ok(self) -> None:
        """正确表情：停留 2 秒后静默回到打盹（期间来了新结果就不回落）。"""
        self.set_state("ok")
        QTimer.singleShot(2000, self._ok_revert)

    def _ok_revert(self) -> None:
        if self._state == "ok":
            self.set_state("idle")

    def _on_bubble_toggled(self, visible: bool) -> None:
        """气泡关闭（用户已在确认/忽略中做出抉择）-> 错误表情回落到打盹。"""
        if not visible and self._state == "error":
            self.set_state("idle")

    def proof_cursor(self) -> None:
        """对光标所在句立即做一次校对（热键 ctrl+shift+z）。

        COM 读取留在主线程（单句、毫秒级），**网络请求放后台线程**：
        否则按一次热键界面会冻结到 HTTP 超时（最长 30 秒）。
        """
        if self._cursor_worker is not None and self._cursor_worker.isRunning():
            return      # 连按去抖
        try:
            current = extract_current_sentence()
            if not current or len(current) < 4:
                return
            api = self.ctx.get_service("ai_client")
            if api is None:
                self._status(self.tr("status.api_missing"))
                return
            self.set_state("busy")
            worker = _CursorProofWorker(api, current, self)
            worker.done.connect(self._on_cursor_proof_done)
            worker.finished.connect(self._on_cursor_worker_finished)
            self._cursor_worker = worker
            worker.start()
        except Exception:
            self._log_exception(self.tr("log.proof_cursor_failed"))
            self.set_state("idle")

    def _on_cursor_worker_finished(self) -> None:
        self._cursor_worker = None

    def _on_cursor_proof_done(self, original: str, corrected, reason: str) -> None:
        """后台校对结果回到主线程：失败绝不落到 `_show_ok()`（那会显示成"检查过、无错"）。"""
        if corrected is None:
            self._log_exception(f"{self.tr('log.proof_cursor_failed')}（{reason}）")
            self.set_state("idle")
            return
        has_diff = corrected.strip() != original.strip()
        if has_diff:
            self._on_suggestion(original, corrected, None, None)
        else:
            self._show_ok()

    def apply_correction(self, corrected_text: str, start: int | None = None,
                         end: int | None = None, original_text: str | None = None) -> None:
        """把建议写回 Word。

        含图片/公式的句子无法安全地整体替换，只清除标记并提示手动修改；
        纯文本句则只替换主体部分，保留句尾的段落符等不可见字符。
        """
        import time as _time

        started = _time.perf_counter()
        word = None
        prev_screen_updating = None
        try:
            word = get_word_app()
            doc = word.ActiveDocument
            # 替换期间冻结重绘：Word 默认每次 COM 变更都重画，长句/大文档上很吃时间
            try:
                prev_screen_updating = word.ScreenUpdating
                word.ScreenUpdating = False
            except Exception:      # 某些受保护视图不允许改，忽略即可
                prev_screen_updating = None

            start, end = self.normalize_span(start, end)
            if start is not None and end is not None:
                sent_range = doc.Range(start, end)
            else:
                sent_range = get_sentence_range(word.Selection)

            if sent_range.Start >= sent_range.End:
                self._status(self.tr("status.sentence_not_found"))
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
                self._status(self.tr("status.marks_cleared"))
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
                self._status(self.tr("status.applied"))

        except Exception as exc:
            QMessageBox.warning(None, self.tr("error.replace_title"),
                                self.tr("error.replace_failed", error=exc))
        finally:
            if word is not None and prev_screen_updating is not None:
                try:
                    word.ScreenUpdating = prev_screen_updating
                except Exception:
                    pass
            logger.info("apply_correction 用时 %.0f ms", (_time.perf_counter() - started) * 1000)

    def _notify_manual_edit(self) -> None:
        parent = QApplication.activeWindow()
        msg_box = QMessageBox(parent)
        msg_box.setWindowTitle(self.tr("manual.title"))
        msg_box.setText(self.tr("manual.body"))
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

    def _on_language_changed(self, _event, _lang=None) -> None:
        self.retranslateUi()

    def _on_theme_changed(self, _event, _theme=None) -> None:
        """主题换了：状态色随令牌变，桌宠与气泡都要重绘。"""
        if self.ctx is None:
            return
        if self.floating_btn is not None:
            self.floating_btn.set_services(theme=self.ctx.get_service("theme"),
                                           skins=self.ctx.get_service("skins"))
        if self._dashboard_page is not None:
            self._dashboard_page.refresh_theme()

    def _on_skin_changed(self, _event, _skin=None) -> None:
        """换了皮肤包：桌宠按新分部件重绘。"""
        if self.floating_btn is None or self.ctx is None:
            return
        self.floating_btn.set_services(skins=self.ctx.get_service("skins"))

    def _on_settings_saved(self, _event, _payload=None) -> None:
        """设置保存后：例如「显示悬浮按钮」被勾选/取消，桌宠要跟着显隐。"""
        self.refresh_visibility()

    def _on_proofing_started(self) -> None:
        self.set_state("busy")

    def _status(self, message: str, timeout: int = 3000) -> None:
        if self.ctx is not None:
            self.ctx.ui.status(message, timeout)
