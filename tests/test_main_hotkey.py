# tests/test_hotkey_dispatch.py
"""U02：热键回调必须真正回到 GUI 线程执行。

审查证据（2026-10-25-strict-audit）：dispatcher 在 keyboard 工作线程里调用
`QTimer.singleShot(0, fn)`，该线程没有 Qt 事件循环，主线程处理 350ms 后回调 0 次。
"""
from __future__ import annotations

import threading

import pytest


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def test_dispatcher_runs_callback_on_gui_thread(qapp):
    from main import _gui_dispatcher

    dispatch = _gui_dispatcher()
    done = threading.Event()
    threads: list[int] = []

    def _callback():
        threads.append(threading.get_ident())
        done.set()

    worker = threading.Thread(target=lambda: dispatch(_callback))
    worker.start()
    worker.join(5)

    for _ in range(50):
        qapp.processEvents()
        if done.is_set():
            break

    assert done.is_set(), "回调没有在 GUI 线程被处理"
    assert threads == [threading.get_ident()], "回调必须回到主线程执行"


def test_dispatcher_survives_callback_exception(qapp):
    """回调抛异常不能把事件循环带崩（记录日志即可）。"""
    from main import _gui_dispatcher

    dispatch = _gui_dispatcher()
    marker: list[int] = []

    def _boom():
        marker.append(1)
        raise RuntimeError("boom")

    worker = threading.Thread(target=lambda: dispatch(_boom))
    worker.start()
    worker.join(5)
    for _ in range(30):
        qapp.processEvents()

    def _after():
        marker.append(2)

    dispatch(_after)
    for _ in range(30):
        qapp.processEvents()

    assert marker[:1] == [1] and marker[-1] == 2, f"异常后派发失效：{marker}"
