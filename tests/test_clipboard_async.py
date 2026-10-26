# tests/test_clipboard_async.py
"""U04：翻译/改写/纠错不能在 GUI 线程同步请求，否则界面最长冻结 30 s。

审查证据（2026-10-25-strict-audit）：`run_action` 在 GUI 线程直接调用 API，
250 ms 替身延迟期间 25 ms 心跳一次都没有执行；真实超时 30 s 时界面完全卡死。
"""
from __future__ import annotations

import time

import pytest


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


class _SlowApi:
    """故意慢的 API 替身；记录调用线程。"""

    last_error = ""

    def __init__(self, delay: float = 0.25):
        self.delay = delay
        self.threads: list[int] = []

    def translate(self, text):
        import threading

        self.threads.append(threading.get_ident())
        time.sleep(self.delay)
        return f"译:{text}"

    def rewrite(self, text):
        return f"改:{text}"

    def clipboard_proof(self, text):
        return f"校:{text}"


class _Ctx:
    def __init__(self, api):
        self._api = api

    def tr(self, key, /, **params):
        return key

    def get_service(self, name):
        return self._api if name == "ai_client" else None


def _make_widget(qapp, api):
    from plugins.clipboard.plugin import ClipboardWidget

    widget = ClipboardWidget(_Ctx(api))
    widget.clip_preview.setPlainText("原句")
    return widget


def test_run_action_does_not_block_gui(qapp):
    """U04：请求进行中，GUI 计时器仍要能跑（说明没有占住主线程）。"""
    from PySide6.QtCore import QTimer

    api = _SlowApi(delay=0.25)
    widget = _make_widget(qapp, api)

    ticks: list[int] = []
    timer = QTimer()
    timer.setInterval(20)
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start()

    started = time.perf_counter()
    widget.run_action("translate")
    elapsed = time.perf_counter() - started
    assert elapsed < 0.1, f"run_action 同步阻塞了 {elapsed:.2f}s（应立刻返回）"

    deadline = time.perf_counter() + 3
    while time.perf_counter() < deadline and widget.clip_result.toPlainText() != "译:原句":
        qapp.processEvents()
        time.sleep(0.01)
    timer.stop()

    assert len(ticks) >= 3, f"请求期间心跳只跑了 {len(ticks)} 次，界面被冻住"
    assert widget.clip_result.toPlainText() == "译:原句"


def test_api_call_runs_off_gui_thread(qapp):
    """U04：真正发请求的必须是工作线程，不是 GUI 线程。"""
    import threading

    api = _SlowApi(delay=0.05)
    widget = _make_widget(qapp, api)

    widget.run_action("translate")
    deadline = time.perf_counter() + 3
    while time.perf_counter() < deadline and widget.clip_result.toPlainText() != "译:原句":
        qapp.processEvents()
        time.sleep(0.01)

    assert api.threads, "API 没有被调用"
    assert api.threads[0] != threading.get_ident(), "API 仍在 GUI 线程里执行"


def test_buttons_disabled_while_running(qapp):
    """U04：请求期间按钮禁用，结束后恢复（避免连点发出多个请求）。"""
    api = _SlowApi(delay=0.2)
    widget = _make_widget(qapp, api)

    widget.run_action("translate")
    assert not widget.translate_btn.isEnabled(), "请求期间应禁用按钮"

    deadline = time.perf_counter() + 3
    while time.perf_counter() < deadline and not widget.translate_btn.isEnabled():
        qapp.processEvents()
        time.sleep(0.01)
    assert widget.translate_btn.isEnabled(), "结束后必须恢复按钮"
