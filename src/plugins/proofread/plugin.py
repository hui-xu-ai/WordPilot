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

import re

from typing import TYPE_CHECKING

from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtWidgets import QApplication, QMessageBox

import logging

from core.sdk.plugin_base import PluginBase

logger = logging.getLogger(__name__)
from core.services.skin_service import STATE_TOKENS
from core.services.window_manager import CloseBehavior, Layer, Mode
from core.services.word_bridge import get_word_app
from core.widgets.pet_window import WINDOW_ID as PET_WINDOW_ID
from core.widgets.pet_window import PetWindow
from plugins.proofread.state_sync import push_proofread_state
from plugins.proofread.writeback import apply_correction
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
        # B02：ProofreadPlugin 是普通 Python 对象，不是 QObject。直接把 self 当 parent
        # 会让 QThread 构造抛 TypeError（被外层 except 吞掉 → worker 根本没创建，
        # 热键校对因此毫无响应）。非 QObject 一律按"无父对象"处理。
        from PySide6.QtCore import QObject

        if not isinstance(parent, QObject):
            parent = None
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
        self._ok_generation = 0
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
        self.monitor.proofing_failed.connect(self._on_proofing_failed)
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
        self._register_pet_actions()


    def _open_pet(self, config, api, ignore_mgr, i18n) -> PetWindow | None:
        """打开桌宠：**优先内核宿主**；宿主成功就绝不再让 WindowManager 建第二个。"""
        theme = self.ctx.get_service("theme")
        skins = self.ctx.get_service("skins")

        hosted = self._ensure_hosted_pet(config, api, ignore_mgr, theme, skins, i18n)
        if hosted is not None:
            return hosted

        # 兜底：宿主不可用时才退回 WindowManager（老路径）
        windows = self.ctx.windows
        if windows is None:
            return None

        def _factory() -> PetWindow:
            pet = PetWindow(
                config, api=api, ignore_mgr=ignore_mgr,
                correction_handler=self.apply_correction,
                theme=theme, skins=skins, i18n=i18n, tr=self.tr,
                tools_provider=self._plugin_tools,
            )
            push_proofread_state(self, self._enabled, pet)   # 初始状态对齐
            pet.proofreadToggleRequested.connect(self.toggle)
            pet.proofreadToggleRequested.connect(
                lambda: pet.set_proofread_enabled(self._enabled))
            pet.settingsRequested.connect(self._open_settings)
            pet.bubbleToggled.connect(self._on_bubble_toggled)
            return pet

        try:
            windows.open(
                PET_WINDOW_ID, _factory, owner="proofread",
                mode=Mode.SINGLETON,
                layer=Layer.OVERLAY,
                behavior=CloseBehavior.HIDE,
                remember_geometry=False,
            )
        except Exception:
            self._log_exception(self.tr("error.open_pet"))
            return None
        pet = windows.get(PET_WINDOW_ID)
        if pet is not None:
            pet.show()
            pet.raise_()
        return pet

    def _ensure_hosted_pet(self, config, api, ignore_mgr, theme, skins, i18n) -> PetWindow | None:
        """请内核宿主创建/显示宠物，并接好信号（只接一次）。"""
        try:
            from core.services.pet_host import PetHost

            pet = PetHost.instance().ensure_shown(
                config=config, api=api, ignore_mgr=ignore_mgr,
                correction_handler=self.apply_correction,
                theme=theme, skins=skins, i18n=i18n, tr=self.tr,
                tools_provider=self._plugin_tools,
            )
        except Exception:                      # noqa: BLE001 - 宿主失败退回旧路径
            logger.exception("内核宿主创建宠物失败，回退 WindowManager")
            return None
        if pet is None:
            return None
        if not getattr(pet, "_wp_signals_connected", False):
            try:
                pet.proofreadToggleRequested.connect(self.toggle)
                pet.settingsRequested.connect(self._open_settings)
                pet.bubbleToggled.connect(self._on_bubble_toggled)
                pet._wp_signals_connected = True
            except Exception:                  # noqa: BLE001
                logger.debug("连接宠物信号失败", exc_info=True)
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

    def _register_pet_actions(self) -> None:
        """把本插件自己的快捷入口注册到桌宠右键菜单（停用即自动消失）。"""
        try:
            self.ctx.ui.add_pet_action(self.ctx.id, self.tr("pet.menu.toggle_proofread"),
                                       self._toggle_proofread, order=10)
        except Exception:                      # noqa: BLE001
            logger.debug("注册桌宠快捷项失败", exc_info=True)
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
        """idle为中性休息，ok才表示已校对通过，error表示仍有待处理建议。"""
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
        """建议是否待处理独立于气泡显隐；设置刷新不打断正在检查或通过反馈。"""
        if not self._enabled:              # 宠物显隐归内核，插件启用与否只影响校对功能
            self.set_state("disabled")
            return
        if self._has_pending_suggestion():
            self.set_state("error")
        elif self._state not in ("busy", "ok"):
            self.set_state("idle")

    def _has_pending_suggestion(self) -> bool:
        pet = self.floating_btn
        if pet is None:
            return False
        check = getattr(pet, "has_pending_suggestion", None)
        return bool(check()) if callable(check) else bool(getattr(pet, "_pending_suggestion", None))

    # ---------------- 能力 ----------------
    def is_enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, on: bool) -> None:
        self._enabled = bool(on)
        if self.monitor is not None:
            self.monitor.proof_enabled = self._enabled
        self._apply_visibility()
        self._push_state_to_pet()
        if self.ctx is not None:
            self.ctx.events.emit("proofread.toggled", self._enabled)

    def _push_state_to_pet(self) -> None:
        """把当前启停状态推给桌宠窗口（实现见 state_sync）。"""
        push_proofread_state(self, self._enabled, getattr(self, "floating_btn", None))

    def toggle(self) -> None:
        self.set_enabled(not self._enabled)

    def _apply_visibility(self) -> None:
        """只切换**校对状态**，绝不碰宠物窗口（宠物归内核，启停校对不该让它消失）。"""
        self._sync_state()

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
        if not self._enabled:
            self.set_state("disabled")
        elif has_diff:
            self.set_state("error")
        else:
            self._show_ok()

    def _show_ok(self) -> None:
        """正确表情：停留 2 秒后静默回到打盹（期间来了新结果就不回落）。"""
        if not getattr(self, "_enabled", True):
            self.set_state("disabled")
            return
        self._ok_generation = getattr(self, "_ok_generation", 0) + 1
        generation = self._ok_generation
        self.set_state("ok")
        QTimer.singleShot(2000, lambda: self._ok_revert(generation))

    def _ok_revert(self, generation=None) -> None:
        if generation is not None and generation != self._ok_generation:
            return
        if self._state == "ok":
            self.set_state("idle")

    def _on_bubble_toggled(self, visible: bool) -> None:
        """收起只改变可见性；应用/忽略已清除待处理建议时才回落。"""
        if not visible and self._state == "error" and not self._has_pending_suggestion():
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
            self._on_proofing_started()
            worker = _CursorProofWorker(api, current)   # 不能把 plugin 当 QObject 父对象
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
            self._on_proofing_failed(reason)
            return
        has_diff = corrected.strip() != original.strip()
        if has_diff:
            self._on_suggestion(original, corrected, None, None)
        else:
            self._show_ok()

    def apply_correction(self, corrected_text: str, start: int | None = None,
                         end: int | None = None, original_text: str | None = None) -> None:
        """把建议写回 Word（实现见 writeback.apply_correction）。"""
        apply_correction(self, corrected_text, start=start, end=end,
                         original_text=original_text)

    def _warn_stale_suggestion(self) -> None:
        warn_stale(self.tr)


    def _notify_manual_edit(self) -> None:
        notify_manual_edit(self.tr)


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
        if self._enabled:
            if self.floating_btn is not None:
                self.floating_btn.set_suggestion("", "")
            self.set_state("busy")

    def _on_proofing_failed(self, reason: str) -> None:
        """检查失败没有通过语义；暂停期间迟到的结果也保持灰色。"""
        self.set_state("idle" if getattr(self, "_enabled", True) else "disabled")

    def _status(self, message: str, timeout: int = 3000) -> None:
        if self.ctx is not None:
            self.ctx.ui.status(message, timeout)
