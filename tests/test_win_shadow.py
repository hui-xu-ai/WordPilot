# tests/test_win_shadow.py
"""DWM 原生阴影的消息处理（P10 后续 UI 项）。

无边框窗口靠 DWM 才有系统阴影：补 `WS_THICKFRAME` + `DwmExtendFrameIntoClientArea(1,1,1,1)`，
再在原生事件里把 `WM_NCCALCSIZE` 压成 0（否则会出现一圈系统边框）。
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

import pytest

from infra import win_shadow


def _msg(message: int, wparam: int = 1, hwnd: int = 0):
    """构造一条原生消息并返回其地址（模拟 Qt 传进来的 message 指针）。"""
    msg = wintypes.MSG()
    msg.hwnd = hwnd
    msg.message = message
    msg.wParam = wparam
    return msg


@pytest.mark.skipif(sys.platform != "win32", reason="仅 Windows 有 DWM")
def test_nccalcsize_is_swallowed_for_normal_window():
    msg = _msg(win_shadow.WM_NCCALCSIZE, wparam=1)
    handled = win_shadow.handle_native_event(b"windows_generic_MSG", ctypes.addressof(msg))
    assert handled == (True, 0), "非最大化窗口应吞掉 WM_NCCALCSIZE，把非客户区压成 0"


@pytest.mark.skipif(sys.platform != "win32", reason="仅 Windows 有 DWM")
def test_wparam_zero_is_left_to_qt():
    """wParam==0 表示"没有客户区可算"，此时不该拦。"""
    msg = _msg(win_shadow.WM_NCCALCSIZE, wparam=0)
    assert win_shadow.handle_native_event(b"windows_generic_MSG", ctypes.addressof(msg)) is None


@pytest.mark.skipif(sys.platform != "win32", reason="仅 Windows 有 DWM")
def test_other_messages_are_left_to_qt():
    msg = _msg(0x000F)      # WM_PAINT
    assert win_shadow.handle_native_event(b"windows_generic_MSG", ctypes.addressof(msg)) is None


def test_non_windows_event_types_are_ignored():
    assert win_shadow.handle_native_event(b"xcb_generic_event_t", 0) is None


def test_enable_native_shadow_returns_false_on_non_windows(monkeypatch):
    monkeypatch.setattr(win_shadow.sys, "platform", "linux")
    assert win_shadow.enable_native_shadow(object()) is False
