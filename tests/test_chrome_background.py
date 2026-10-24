# tests/test_chrome_background.py
"""外壳分区控件的背景必须**真的画出来**（回归「标题栏与侧边栏分不清」，连续两轮返工）。

根因链：
1. `QWidget` 的**子类**默认不绘制样式表里的 `background-color` / `border`
   （Qt 只给纯 QWidget 实例自动开启 `WA_StyledBackground`）⇒ `QWidget#titleBar` 规则在真机上不生效；
2. 离屏 `grab()` 又恰好能画出背景 ⇒ 本地自测看不到问题；
3. 真机实测像素是纯白 ⇒ 用户连续两轮反馈"还是分不清"。

现在的实现是双保险：开属性（QSS 生效）+ `set_colors()` 自绘（不依赖 QSS）。
这里两个都钉住。
"""
from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _make(factory):
    if factory == "title_bar":
        from app.title_bar import TitleBar

        return TitleBar(None)
    if factory == "nav_rail":
        from app.nav_rail import NavRail

        return NavRail()
    from app.side_panel import SidePanelHost

    return SidePanelHost(width=300)


@pytest.mark.parametrize("factory,label", [
    ("title_bar", "TitleBar"),
    ("nav_rail", "NavRail"),
    ("side_panel", "SidePanelHost"),
])
def test_widget_paints_stylesheet_background(qapp, factory, label):
    from PySide6.QtCore import Qt

    widget = _make(factory)
    assert widget.testAttribute(Qt.WA_StyledBackground), \
        f"{label} 必须开启 WA_StyledBackground，否则 QSS 背景在真机上不绘制"


@pytest.mark.parametrize("factory,label,expected", [
    ("title_bar", "标题栏", "#bfc3d1"),
    ("nav_rail", "侧边栏", "#e9e9ef"),
])
def test_injected_colors_are_actually_painted(qapp, factory, label, expected):
    """不加载任何 QSS，只用注入的颜色抓图核对：自绘这条路必须能画出来。"""
    widget = _make(factory)
    widget.set_colors(expected, "#c6c6cf")
    widget.resize(240, 40 if factory == "title_bar" else 240)
    img = widget.grab().toImage()
    color = img.pixelColor(img.width() // 2, img.height() // 2).name()
    assert color == expected, f"{label} 自绘颜色应为 {expected}，实际 {color}"


def test_titlebar_and_rail_get_different_tokens(qapp):
    """两个分区必须来自**不同**令牌；相同就会重现"分不清"。"""
    from core.services.theme_service import ThemeService

    class _Cfg(dict):
        def get(self, key, default=None):
            return dict.get(self, key, default)

    theme = ThemeService(_Cfg({"theme": "light"}))
    theme.reload()
    assert theme.token("color.bg.titlebar") != theme.token("color.bg.rail")
