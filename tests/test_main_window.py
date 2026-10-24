# tests/test_main_window.py
"""P10-B6：外壳在"插件页构造失败 / 重复退出 / 未知页"下不能崩，也不能静默。

审计 H11：`_default_page()` 返回的可能是**构建失败**的插件页，`_show_page()` 随即
`self._page_widgets[page_id]` 抛 KeyError；该路径在启动期 `bind_plugins()` 上
（不在 event_bus 的异常隔离里）⇒ 单个插件页失败会直接打断启动。

用例在离屏 Qt 下跑（conftest 里设 `QT_QPA_PLATFORM=offscreen`），配置目录换到 tmp_path。
"""
from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def shell(qapp, tmp_path, config_env):
    """真实 MainWindow + 隔离配置目录；不加载任何插件（插件链路由 smoke 覆盖）。"""
    from app.main_window import MainWindow
    from core.kernel import Kernel

    kernel = Kernel(root_dir=tmp_path)
    kernel.boot()
    window = MainWindow(kernel)
    kernel.attach_ui(window.ui)
    window.bind_plugins()
    yield window
    kernel.shutdown()


def _broken_factory():
    raise RuntimeError("插件页工厂炸了")


def test_failing_plugin_page_does_not_raise_keyerror(shell):
    shell._plugin_factories["plugin:broken"] = (_broken_factory, False)
    shell._show_page("plugin:broken")          # 修复前这里抛 KeyError
    assert shell.stack.currentWidget() is shell._page_widgets["plugins"], "应回退到可用的内置页"
    assert "plugin:broken" in shell._failed_pages


def test_default_page_skips_failed_pages(shell):
    shell._plugin_factories["plugin:broken"] = (_broken_factory, False)
    shell._show_page("plugin:broken")
    assert shell._default_page() != "plugin:broken", "默认页不能指向构建失败的页"


def test_failed_page_is_retried_after_reload(shell):
    shell._plugin_factories["plugin:broken"] = (_broken_factory, False)
    shell._show_page("plugin:broken")
    shell._collect_plugin_factories()          # 插件重载：失败标记应清空，允许重试
    assert "plugin:broken" not in shell._failed_pages


def test_unknown_page_reports_instead_of_silence(shell):
    before = shell.statusbar.currentMessage()
    shell.show_page("plugin:does-not-exist")   # 修复前静默无反应
    message = shell.statusbar.currentMessage()
    assert message and message != before, "不可用页应给用户一句提示"
    assert "不可用" in message or "unavailable" in message


def test_quit_app_is_idempotent(shell, monkeypatch):
    from PySide6.QtWidgets import QApplication

    quits: list[int] = []
    monkeypatch.setattr(QApplication, "quit", staticmethod(lambda *a, **k: quits.append(1)))
    events: list = []
    shell.kernel.events.subscribe("kernel.shutdown", lambda *a, **k: events.append(1))

    shell.quit_app()
    shell.quit_app()

    assert len(events) == 1, f"kernel.shutdown 只应广播一次，实际 {len(events)} 次"
    assert len(quits) == 1, f"QApplication.quit 只应调用一次，实际 {len(quits)} 次"


def _register_demo_plugin_page(shell):
    """注册一个"已激活插件"的页面，让默认页逻辑真的会指向插件页。

    没有插件页时 `_default_page()` 恰好也返回 "plugins"，测试会假通过。
    """
    from PySide6.QtWidgets import QLabel

    from core.plugin_manager import PluginRecord, PluginState
    from core.sdk.plugin_base import PluginManifest

    shell.kernel.plugins.records["demo"] = PluginRecord(
        manifest=PluginManifest(id="demo"), state=PluginState.ACTIVE)
    shell.ui.add_page("demo_label", lambda: QLabel("demo"), plugin_id="demo")
    shell.build_extension_menus()


@pytest.mark.parametrize("builtin_page", ["plugins", "settings"])
def test_plugin_toggle_does_not_jump_away_from_builtin_page(shell, builtin_page):
    """在「插件管理」/「设置」页切换插件开关时，外壳重建导航不得把用户弹走。

    回归：`_rebuild_nav` 的 `available` 只统计**插件页**，内置页永远被判"不可用"
    ⇒ 每次 `plugins.changed`（=点一次启停开关）都跳回默认插件页。
    """
    _register_demo_plugin_page(shell)
    shell._on_page_requested(builtin_page)
    assert shell.stack.currentWidget() is shell._page_widgets[builtin_page]

    shell.kernel.events.emit("plugins.changed", "demo")     # 等价于点一次启停开关

    assert shell.stack.currentWidget() is shell._page_widgets[builtin_page], \
        f"重建导航后应仍停在 {builtin_page} 页"
    assert shell._explicit_page == builtin_page
