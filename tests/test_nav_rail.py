# tests/test_nav_rail.py
"""侧边导航轨的分区背景必须真的画出来（回归「标题栏与侧边栏分不清」）。

根因：`QWidget` 的**子类**默认不绘制样式表背景（Qt 只给纯 QWidget 实例自动开启
`WA_StyledBackground`），所以 `QWidget#navRail` 的背景/右边框在真机上不生效，
而离屏 `grab()` 又能画出来 ⇒ 本地自测看不出来，用户侧连续返工。
现在改成"开属性 + 自绘"双保险，这里两条都钉住。
"""
from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_nav_rail_paints_stylesheet_background(qapp):
    from PySide6.QtCore import Qt

    from app.nav_rail import NavRail

    assert NavRail().testAttribute(Qt.WA_StyledBackground)


def test_injected_rail_color_is_painted(qapp):
    from app.nav_rail import NavRail
    from core.services.theme_service import ThemeService

    class _Cfg(dict):
        def get(self, key, default=None):
            return dict.get(self, key, default)

    theme = ThemeService(_Cfg({"theme": "light"}))
    theme.reload()
    rail_color = theme.token("color.bg.rail")

    rail = NavRail()
    rail.set_colors(rail_color, theme.token("color.border.strong"))
    rail.resize(60, 400)
    img = rail.grab().toImage()
    color = img.pixelColor(img.width() // 2, img.height() // 2).name()
    assert color == rail_color, f"侧栏自绘颜色应为 {rail_color}，实际 {color}"


def test_no_structural_divider_when_border_is_none(qapp):
    """极淡分层：border 传 None 时侧栏右边不应出现分割线（用户要求去掉这条线）。"""
    from app.nav_rail import NavRail

    rail = NavRail()
    rail.set_colors("#f7f8fa", None)
    rail.resize(60, 400)
    img = rail.grab().toImage()
    edge = img.pixelColor(img.width() - 1, img.height() // 2).name()
    assert edge == "#f7f8fa", f"右侧最后 1 列应仍是底色，实际 {edge}"


def test_titlebar_and_rail_tokens_differ():
    """两个分区必须来自不同令牌，相同就会重现"分不清"。"""
    from core.services.theme_service import ThemeService

    class _Cfg(dict):
        def get(self, key, default=None):
            return dict.get(self, key, default)

    theme = ThemeService(_Cfg({"theme": "light"}))
    theme.reload()
    assert theme.token("color.bg.titlebar") != theme.token("color.bg.rail")
