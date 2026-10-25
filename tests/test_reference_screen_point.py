# tests/test_reference_screen_point.py
"""悬浮窗定位：Word 换算失败时必须退回鼠标位置，而不是永远落在屏幕左上角。"""
from __future__ import annotations

import pytest

import plugins.reference.word_watch as ww


class _Selection:
    def __init__(self, x: int = 120, y: int = 80, fail: bool = False) -> None:
        self._x, self._y, self._fail = x, y, fail

    def Information(self, kind: int) -> int:
        if self._fail:
            raise RuntimeError("Word 拒绝")
        return self._x if kind == 5 else self._y


class _Window:
    def __init__(self, scale: float = 2.0) -> None:
        self._scale = scale

    def PointsToScreenPixelsX(self, value: float) -> int:
        return int(value * self._scale)

    def PointsToScreenPixelsY(self, value: float) -> int:
        return int(value * self._scale)


class _Word:
    def __init__(self, scale: float = 2.0) -> None:
        self.ActiveWindow = _Window(scale)


@pytest.fixture(autouse=True)
def _no_qt(monkeypatch):
    monkeypatch.setattr(ww, "_device_ratio", lambda: 1.0)


def test_uses_word_point_with_client_origin(monkeypatch):
    monkeypatch.setattr(ww, "_client_origin", lambda: (100, 50))
    assert ww.screen_point(_Word(), _Selection()) == (100 + 240, 50 + 160)


def test_falls_back_to_cursor_when_word_fails(monkeypatch):
    monkeypatch.setattr(ww, "_client_origin", lambda: (100, 50))
    monkeypatch.setattr(ww, "_cursor_point", lambda: (830, 420))
    assert ww.screen_point(_Word(), _Selection(fail=True)) == (830, 420)


def test_zero_position_also_falls_back(monkeypatch):
    """Information 返回 0/0（折叠光标在页面起点）也算失败，别把弹窗钉在左上角。"""
    monkeypatch.setattr(ww, "_client_origin", lambda: (0, 0))
    monkeypatch.setattr(ww, "_cursor_point", lambda: (640, 360))
    assert ww.screen_point(_Word(), _Selection(x=0, y=0)) == (640, 360)


def test_last_resort_near_word_window(monkeypatch):
    monkeypatch.setattr(ww, "_client_origin", lambda: (300, 200))
    monkeypatch.setattr(ww, "_cursor_point", lambda: None)
    assert ww.screen_point(_Word(), _Selection(fail=True)) == (420, 280)


def test_dpi_scaling_converts_to_logical_pixels(monkeypatch):
    monkeypatch.setattr(ww, "_client_origin", lambda: (0, 0))
    monkeypatch.setattr(ww, "_device_ratio", lambda: 2.0)
    assert ww.screen_point(_Word(), _Selection()) == (120, 80)
