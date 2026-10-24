# tests/test_title_bar.py
"""自绘标题栏：最大化状态下的拖动换算（回归用户反馈「最大化后拖不动窗口」）。

拖拽本身需要真实鼠标事件（桌面验收），这里钉住纯函数换算：
鼠标在标题栏上的水平比例必须保持不变，窗口才不会"跳回原位置"。
"""
from __future__ import annotations

from app.title_bar import restored_position


def test_left_edge_keeps_mouse_on_left_edge():
    x, _y = restored_position(point_x=0, point_y=200, title_width=1920,
                              normal_width=900, title_height=36)
    assert x == 0


def test_middle_keeps_mouse_at_middle():
    """鼠标在标题栏正中拖动 ⇒ 还原后窗口中心对齐鼠标。"""
    x, _y = restored_position(point_x=960, point_y=200, title_width=1920,
                              normal_width=900, title_height=36)
    assert x == 960 - 450


def test_right_edge_clamped():
    """超出标题栏右边界（多屏/负坐标）时按 1.0 截断，不产生越界跳变。"""
    x, _y = restored_position(point_x=2500, point_y=200, title_width=1920,
                              normal_width=900, title_height=36)
    assert x == 2500 - 900


def test_y_follows_mouse_and_never_negative():
    _x, y = restored_position(point_x=100, point_y=10, title_width=1920,
                              normal_width=900, title_height=36)
    assert y == 0, "标题栏贴顶时不能算出负 y（窗口会被移到屏幕外）"
    _x, y2 = restored_position(point_x=100, point_y=400, title_width=1920,
                               normal_width=900, title_height=36)
    assert y2 == 400 - 18


def test_zero_title_width_does_not_divide_by_zero():
    x, _y = restored_position(point_x=10, point_y=10, title_width=0,
                              normal_width=900, title_height=36)
    assert isinstance(x, int)
