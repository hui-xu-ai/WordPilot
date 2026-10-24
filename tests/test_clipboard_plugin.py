# tests/test_clipboard_plugin.py
"""翻译改写插件：提示词设置已从主设置页迁到本插件右侧抽屉。

这里验证两条：① `activate()` 会注册抽屉；② 抽屉保存会写进配置并广播 `settings.saved`
（否则主设置里删掉提示词后，用户就再也改不到翻译/改写提示词了）。
"""
from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


class _Services(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)


class _Ui:
    def __init__(self):
        self.pages = []
        self.side_panels = []
        self.statuses = []

    def add_page(self, label_key, factory, **kwargs):
        self.pages.append((label_key, factory, kwargs))

    def add_side_panel(self, label_key, factory, **kwargs):
        self.side_panels.append((label_key, factory, kwargs))

    def status(self, message, timeout=3000):
        self.statuses.append(message)


class _Ctx:
    def __init__(self, config):
        self._services = _Services({"config": config})
        self.ui = _Ui()
        self.emitted = []
        self.logger = type("L", (), {"info": staticmethod(lambda *a, **k: None)})()

    def get_service(self, name):
        return self._services.get(name)

    def tr(self, key, /, **params):
        return key

    class _Events:
        def __init__(self, sink):
            self._sink = sink

        def emit(self, name, payload=None):
            self._sink.append(name)

    @property
    def events(self):
        return self._Events(self.emitted)


@pytest.fixture
def plugin(qapp, config_env):
    from core.services.config_manager import ConfigManager
    from plugins.clipboard.plugin import ClipboardPlugin

    manager = ConfigManager()
    ctx = _Ctx(manager)
    instance = ClipboardPlugin()
    instance.activate(ctx)
    yield instance, ctx, manager


def test_activate_registers_side_panel(plugin):
    _instance, ctx, _manager = plugin
    assert ctx.ui.side_panels, "翻译改写插件必须注册右侧抽屉（提示词设置在这里）"
    label_key, factory, kwargs = ctx.ui.side_panels[0]
    assert label_key == "side_label"
    assert kwargs.get("plugin_id") == "clipboard"
    assert callable(factory)


def test_side_panel_saves_prompts(plugin):
    _instance, ctx, manager = plugin
    _label, factory, _kwargs = ctx.ui.side_panels[0]
    panel = factory()
    assert panel is not None

    _instance.translate_prompt_edit.setPlainText("译成{target_lang}：{text}")
    _instance.rewrite_prompt_edit.setPlainText("改写：{text}")
    _instance.save_plugin_settings()

    assert manager.get("translate_prompt") == "译成{target_lang}：{text}"
    assert manager.get("rewrite_prompt") == "改写：{text}"
    assert "settings.saved" in ctx.emitted


def test_side_panel_is_cached(plugin):
    _instance, ctx, _manager = plugin
    _label, factory, _kwargs = ctx.ui.side_panels[0]
    assert factory() is factory(), "抽屉控件应缓存，重复调用返回同一实例"
