# tests/test_com_thread.py
"""S8：扫描/校对等非 GUI 线程附接 Word COM 前必须初始化 apartment。

审查证据：扫描线程里 OLE 探针报 -2147221008（CoInitialize has not been called）。
"""
from __future__ import annotations

import sys
import types


def test_com_init_calls_coinitialize(monkeypatch):
    from core.services import com_thread

    calls = []
    fake = types.SimpleNamespace(CoInitialize=lambda: calls.append("init"),
                                 CoUninitialize=lambda: calls.append("uninit"))
    monkeypatch.setitem(sys.modules, "pythoncom", fake)

    inited = com_thread.com_init()
    assert inited is True and calls == ["init"]

    com_thread.com_uninit(inited)
    assert calls == ["init", "uninit"]


def test_com_init_returns_false_when_already_initialized(monkeypatch):
    from core.services import com_thread

    calls = []

    def _boom():
        raise RuntimeError("already initialized")

    fake = types.SimpleNamespace(CoInitialize=_boom,
                                 CoUninitialize=lambda: calls.append("uninit"))
    monkeypatch.setitem(sys.modules, "pythoncom", fake)

    inited = com_thread.com_init()
    assert inited is False, "已初始化时应返回 False，避免多配一次 CoUninitialize"

    com_thread.com_uninit(inited)
    assert calls == [], "不是自己初始化的，不应去反初始化"


def test_com_init_without_pywin32_is_noop(monkeypatch):
    from core.services import com_thread

    monkeypatch.setitem(sys.modules, "pythoncom", None)     # 模拟 import 失败
    assert com_thread.com_init() is False
    com_thread.com_uninit(False)                             # 不应抛异常
