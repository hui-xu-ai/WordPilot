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
