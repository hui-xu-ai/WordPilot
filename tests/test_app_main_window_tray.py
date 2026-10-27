# tests/test_app_main_window_tray.py
"""托盘右键菜单：已启用插件的快速启动 + 主窗口 + 退出。

用户口径（2026-10-27）：右键宠物图标列出的是**已经启动**的插件（未启用的不出现），
用来快速跳到插件页；最后是主窗口与退出。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


class _I18n:
    def __init__(self):
        self.data = json.loads(
            (ROOT / "src/assets/i18n/zh_CN.json").read_text(encoding="utf-8"))

    def tr(self, key, **params):
        return self.data.get(key, key)

    def prompt_tr(self, key, **params):
        return self.data.get(key, key)


@pytest.fixture
def app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


class _FakeUi:
    """只实现托盘渲染用到的 pages()：模拟内核已按「已启用插件」过滤后的结果。"""

    def __init__(self, pages):
        self._pages = pages

    def pages(self):
        return self._pages


class _FakeWindow:
    """把 MainWindow 里与托盘相关的方法摘出来测，不构造整个窗口。"""

    from app.main_window import MainWindow

    tray_plugin_entries = MainWindow.tray_plugin_entries
    build_extension_menus = MainWindow.build_extension_menus

    def __init__(self, pages):
        self.ui = _FakeUi(pages)
        self.i18n = _I18n()
        self.shown: list[str] = []
        self.tray_menu = None

    def tr(self, key, **params):
        return self.i18n.tr(key, **params)

    def _nav_icon(self, name, checkable):
        from PySide6.QtGui import QIcon

        return QIcon()

    def show_page(self, page_id):
        self.shown.append(page_id)

    def show_main_window(self):
        self.shown.append("main")

    def quit_app(self):
        self.shown.append("quit")

    # build_extension_menus 末尾还要重建导航轨；替身里空实现即可
    def _collect_plugin_factories(self):
        pass

    def _rebuild_nav(self):
        pass


def test_only_active_plugin_pages_become_tray_entries():
    window = _FakeWindow([
        ("智能纠错", object(), "check", "proofread"),
        ("翻译改写", object(), None, "clipboard"),
        ("内核兜底项", object(), None, None),        # plugin_id=None → 跳过
    ])

    entries = window.tray_plugin_entries()

    assert [label for label, _page, _icon in entries] == ["智能纠错", "翻译改写"]
    assert [page for _label, page, _icon in entries] == ["plugin:proofread", "plugin:clipboard"]


def test_menu_order_is_plugins_then_main_then_quit(app):
    window = _FakeWindow([
        ("智能纠错", object(), "check", "proofread"),
        ("翻译改写", object(), None, "clipboard"),
    ])

    window.build_extension_menus()
    labels = [action.text() for action in window.tray_menu.actions()]

    assert labels == ["智能纠错", "翻译改写", "", "主窗口", "退出"], labels


def test_no_plugins_means_no_leading_separator(app):
    window = _FakeWindow([])

    window.build_extension_menus()
    labels = [action.text() for action in window.tray_menu.actions()]

    assert labels == ["主窗口", "退出"]


def test_clicking_plugin_entry_opens_its_page(app):
    window = _FakeWindow([("智能纠错", object(), "check", "proofread")])
    window.build_extension_menus()

    window.tray_menu.actions()[0].trigger()

    assert window.shown == ["plugin:proofread"]


def test_disabled_plugins_do_not_show_up():
    """内核在 `retain_only(active_ids)` 里已剔除停用插件，托盘只消费过滤后的结果。"""
    source = (ROOT / "src/app/pages_mixin.py").read_text(encoding="utf-8")

    assert "retain_only(active_ids)" in source
    assert "PluginState.ACTIVE" in source


def test_tray_ignores_legacy_dynamic_actions():
    source = (ROOT / "src/app/main_window.py").read_text(encoding="utf-8")
    start = source.index("def build_extension_menus")
    body = source[start:start + 1200]

    assert "tray_actions()" not in body, "不再渲染 add_tray_action 注册的动态项"


def test_fixed_labels_are_translated():
    data = json.loads((ROOT / "src/assets/i18n/zh_CN.json").read_text(encoding="utf-8"))

    assert data["app.tray.show_main"] == "主窗口"
    assert data["app.tray.quit"] == "退出"
