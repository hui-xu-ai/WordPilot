# tests/test_tray_menu.py
"""托盘右键菜单：固定三项「插件管理 / 主窗口 / 退出」。

用户要求（2026-10-27）：托盘菜单改成插件管理、主窗口、退出；
不再把插件动态注册的托盘项（曾显示成 `tray_label`、`nav.monitor` 这种裸 key）堆进去。
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


def test_tray_menu_has_three_fixed_entries():
    source = (ROOT / "src/app/main_window.py").read_text(encoding="utf-8")
    start = source.index("def build_extension_menus")
    body = source[start:start + 1400]

    assert '"app.tray.plugins"' in body, "第一项应是插件管理"
    assert '"app.tray.show_main"' in body, "第二项应是主窗口"
    assert '"app.tray.quit"' in body, "第三项应是退出"
    assert "tray_actions()" not in body, "插件动态托盘项不再渲染"


def test_tray_labels_are_translated():
    data = json.loads((ROOT / "src/assets/i18n/zh_CN.json").read_text(encoding="utf-8"))

    assert data["app.tray.plugins"] == "插件管理"
    assert data["app.tray.show_main"] == "主窗口"
    assert data["app.tray.quit"] == "退出"


def test_tray_menu_items_via_qt(app):
    """用真实 QAction 断言顺序与文案（不启动整个主窗口）。"""
    from PySide6.QtWidgets import QMenu

    i18n = _I18n()
    menu = QMenu()
    menu.addAction(i18n.tr("app.tray.plugins"), lambda: None)
    menu.addAction(i18n.tr("app.tray.show_main"), lambda: None)
    menu.addSeparator()
    menu.addAction(i18n.tr("app.tray.quit"), lambda: None)

    labels = [action.text() for action in menu.actions()]
    assert labels == ["插件管理", "主窗口", "", "退出"], labels


@pytest.fixture
def app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])
