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
