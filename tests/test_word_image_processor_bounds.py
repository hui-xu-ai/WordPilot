# tests/test_image_content_bounds.py
"""B08：黑白图内容检测不能因 int16 溢出把深色内容当空白。

审查证据：白底黑块 (2,3)-(6,7) 被判为空白 (0,0,-1,-1)，
因为 `int16(-255) ** 2 == -511`，平方和变成负数。
"""
from __future__ import annotations

import numpy as np
import pytest


@pytest.fixture
def processor():
    from plugins.image_toolbox.word_image_processor import WordImageProcessor

    return WordImageProcessor.__new__(WordImageProcessor)      # 跳过 COM 初始化


def _image(background, box, box_color, size=(10, 10)):
    arr = np.full((size[0], size[1], 3), background, dtype=np.uint8)
    (top, left), (bottom, right) = box
    arr[top:bottom + 1, left:right + 1] = box_color
    from PIL import Image

    return Image.fromarray(arr, mode="RGB")


def test_black_box_on_white_is_detected(processor):
    img = _image(255, ((2, 3), (6, 7)), 0)
    left, top, right, bottom = processor._detect_content_bounds(img, (255, 255, 255))

    assert (left, top) == (3, 2), f"内容左上角错：{(left, top)}"
    assert (right, bottom) == (7, 6), f"内容右下角错：{(right, bottom)}"


def test_white_box_on_black_is_detected(processor):
    img = _image(0, ((1, 1), (4, 5)), 255)
    got = processor._detect_content_bounds(img, (0, 0, 0))
    assert got == (1, 1, 5, 4), f"反色场景错：{got}"   # (left, top, right, bottom)


def test_blank_image_returns_empty_marker(processor):
    img = _image(255, ((0, 0), (-1, -1)), 255)       # 全白
    img = _image(255, ((0, 0), (0, 0)), 255)
    assert processor._detect_content_bounds(img, (255, 255, 255)) == (0, 0, -1, -1)
