# tests/test_proofread_plugin.py
"""P10-B8：proofread 的哨兵值与线程退出。

审计 H13/H14：
- 热键路径用 `-1, -1` 作"没有范围"的哨兵，而 `apply_correction` 只判 `is not None`
  ⇒ `doc.Range(-1, -1)` 必报 COM 错（或更糟：从文档开头覆盖）；
- `deactivate` 里 `monitor.wait(2000)` 的返回值被丢掉就 `self.monitor = None`，
  线程若卡在 HTTP 上会在运行中被销毁（`QThread: Destroyed while thread is still running` 直接 abort）。
"""
from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def plugin_cls():
    from plugins.proofread.plugin import ProofreadPlugin

    return ProofreadPlugin


@pytest.mark.parametrize(("start", "end"), [
    (None, None),
    (-1, -1),
    (-5, 10),
    (10, 10),
    (20, 10),
    ("x", 5),
])
def test_normalize_span_treats_invalid_as_no_range(plugin_cls, start, end):
    assert plugin_cls.normalize_span(start, end) == (None, None)


@pytest.mark.parametrize(("start", "end"), [(5, 12), (0, 3), ("7", "9")])
def test_normalize_span_keeps_valid_range(plugin_cls, start, end):
    got = plugin_cls.normalize_span(start, end)
    assert got == (int(start), int(end))


class _RunningMonitor:
    """永远"还没结束"的监控线程替身。"""

    def __init__(self) -> None:
        self.stopped = False
        self.waited = []

    def stop(self) -> None:
        self.stopped = True

    def wait(self, ms: int) -> bool:
        self.waited.append(ms)
        return False


def test_deactivate_keeps_reference_when_thread_will_not_stop(plugin_cls):
    plugin = plugin_cls.__new__(plugin_cls)          # 不跑 __init__（要 ctx/Qt）
    monitor = _RunningMonitor()
    plugin.monitor = monitor
    plugin.floating_btn = None
    plugin._dashboard_page = None
    plugin._unsubscribes = []
    plugin.ctx = None

    plugin.deactivate()

    assert monitor.stopped, "应先请求线程停止"
    assert monitor.waited == [2000, 3000], "应给出两段等待"
    assert plugin.monitor is monitor, "线程没结束就不能丢引用（否则运行中的 QThread 被销毁）"


def test_deactivate_releases_reference_after_thread_stops(plugin_cls):
    class _StopsMonitor(_RunningMonitor):
        def wait(self, ms: int) -> bool:
            self.waited.append(ms)
            return True

    plugin = plugin_cls.__new__(plugin_cls)
    plugin.monitor = _StopsMonitor()
    plugin.floating_btn = None
    plugin._dashboard_page = None
    plugin._unsubscribes = []
    plugin.ctx = None

    plugin.deactivate()
    assert plugin.monitor is None


# ── B8b：热键校对的异步执行体 ───────────────────────────────────────
def _bare_plugin(plugin_cls):
    plugin = plugin_cls.__new__(plugin_cls)
    plugin._state = "busy"
    plugin.floating_btn = None
    plugin.ctx = None
    plugin._cursor_worker = None
    plugin.set_state = lambda state: setattr(plugin, "_state", state)
    plugin._log_exception = lambda message: None
    return plugin


def test_cursor_proof_worker_emits_result(plugin_cls):
    from plugins.proofread.plugin import _CursorProofWorker

    class FakeApi:
        last_error = ""

        def proof_text(self, text):
            return text.upper()

    worker = _CursorProofWorker(FakeApi(), "abc")
    seen: list[tuple] = []
    worker.done.connect(lambda *args: seen.append(args))
    worker.run()                       # 直接跑 run()：不启动真线程
    assert seen == [("abc", "ABC", "")]


def test_cursor_proof_worker_reports_failure(plugin_cls):
    from plugins.proofread.plugin import _CursorProofWorker

    class BadApi:
        last_error = "HTTP 401"

        def proof_text(self, text):
            return None

    worker = _CursorProofWorker(BadApi(), "abc")
    seen: list[tuple] = []
    worker.done.connect(lambda *args: seen.append(args))
    worker.run()
    assert seen == [("abc", None, "HTTP 401")]


def test_cursor_proof_failure_does_not_show_ok(plugin_cls):
    plugin = _bare_plugin(plugin_cls)
    called: list[int] = []
    plugin._show_ok = lambda: called.append(1)

    plugin._on_cursor_proof_done("原句", None, "网络错误")

    assert called == [], "失败不能显示成'检查过无错'"
    assert plugin._state == "idle"


def test_cursor_proof_with_diff_goes_through_suggestion(plugin_cls):
    plugin = _bare_plugin(plugin_cls)
    got: list[tuple] = []
    plugin._on_suggestion = lambda original, corrected, start, end: got.append(
        (original, corrected, start, end))

    plugin._on_cursor_proof_done("原句", "改后的句子", "")

    assert got == [("原句", "改后的句子", None, None)], "应走建议路径，且不带伪区间"


def test_cursor_proof_without_diff_shows_ok(plugin_cls):
    plugin = _bare_plugin(plugin_cls)
    called: list[int] = []
    plugin._show_ok = lambda: called.append(1)

    plugin._on_cursor_proof_done("原句", "原句", "")

    assert called == [1]


# 用户确认的状态语义：有建议持续提醒；只有真实通过才短暂显示绿勾。
def _state_plugin(plugin_cls, pending=False):
    from types import SimpleNamespace

    plugin = plugin_cls()
    plugin.floating_btn = SimpleNamespace(
        pending=pending, set_state=lambda state: None,
        bubble=SimpleNamespace(isVisible=lambda: False),
    )
    plugin.floating_btn.has_pending_suggestion = lambda: plugin.floating_btn.pending
    return plugin


def test_closing_bubble_keeps_unhandled_suggestion_red(plugin_cls):
    plugin = _state_plugin(plugin_cls, pending=True)
    plugin.set_state("error")
    plugin._on_bubble_toggled(False)
    assert plugin.state() == "error"


def test_handled_suggestion_returns_to_neutral(plugin_cls):
    plugin = _state_plugin(plugin_cls, pending=False)
    plugin.set_state("error")
    plugin._on_bubble_toggled(False)
    assert plugin.state() == "idle"


def test_settings_refresh_preserves_pending_suggestion(plugin_cls):
    from types import SimpleNamespace

    plugin = _state_plugin(plugin_cls, pending=True)
    plugin.ctx = SimpleNamespace()
    plugin.set_state = lambda state: setattr(plugin, "_state", state)
    plugin.set_state("error")
    plugin.refresh_visibility()
    assert plugin.state() == "error"


def test_resume_restores_hidden_pending_suggestion(plugin_cls):
    plugin = _state_plugin(plugin_cls, pending=True)
    plugin.set_enabled(False)
    assert plugin.state() == "disabled"
    plugin.set_enabled(True)
    assert plugin.state() == "error"


def test_old_success_timer_does_not_shorten_new_success(plugin_cls, monkeypatch):
    from plugins.proofread import plugin as module

    timers = []
    monkeypatch.setattr(module.QTimer, "singleShot", lambda ms, callback: timers.append((ms, callback)))
    plugin = _state_plugin(plugin_cls)
    plugin._show_ok()
    plugin._show_ok()
    assert [ms for ms, _ in timers] == [2000, 2000]
    timers[0][1]()
    assert plugin.state() == "ok"
    timers[1][1]()
    assert plugin.state() == "idle"


def test_paused_plugin_does_not_turn_green_on_late_success(plugin_cls, monkeypatch):
    from plugins.proofread import plugin as module

    monkeypatch.setattr(module.QTimer, "singleShot", lambda *args: None)
    plugin = _state_plugin(plugin_cls)
    plugin.set_enabled(False)
    plugin._show_ok()
    assert plugin.state() == "disabled"


def test_automatic_failure_returns_to_neutral_not_success(plugin_cls):
    plugin = _state_plugin(plugin_cls)
    plugin.set_state("busy")
    plugin._on_proofing_failed("HTTP 401")
    assert plugin.state() == "idle"


def test_new_check_discards_previous_suggestion_before_failure(plugin_cls):
    plugin = _state_plugin(plugin_cls, pending=True)
    plugin.floating_btn.set_suggestion = lambda *args: setattr(plugin.floating_btn, "pending", False)
    plugin._on_proofing_started()
    assert plugin.state() == "busy"
    assert not plugin.floating_btn.pending
    plugin._on_proofing_failed("HTTP 401")
    plugin.refresh_visibility()
    assert plugin.state() == "idle", "失败后刷新设置不能复活上次校对的红色建议"
