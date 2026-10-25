# tests/test_dashboard_page.py
"""智能纠错插件抽屉里的设置（从主设置页搬来的那批）。

原先在 `tests/test_settings_page.py` 里测的透明度范围/夹取，随控件一起迁到这里：
抽屉里的滑块是唯一写入 `suggestion_opacity` 的地方，范围 20–100 的约束不能丢。
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


class _Ctx:
    """最小插件上下文：DashboardPage 只用 services / events。"""

    def __init__(self, config):
        self._services = _Services({"config": config})
        self.emitted: list[str] = []

    def get_service(self, name):
        return self._services.get(name)

    def tr(self, key, /, **params):
        """假翻译：直接回 key，便于断言文案来源。"""
        return key

    @property
    def events(self):
        emitted = self.emitted

        class _Events:
            @staticmethod
            def emit(name, payload=None):
                emitted.append(name)

        return _Events()


class _FakePlugin:
    """DashboardPage 只用到这几样：ctx、开关状态、启停回调。"""

    def __init__(self, config):
        self.ctx = _Ctx(config)
        self.enabled = True

    def is_enabled(self):
        return self.enabled

    def subscribe(self, *_args, **_kwargs):
        return lambda: None

    def set_enabled(self, on):
        self.enabled = bool(on)

    def toggle(self, *_args):
        self.enabled = not self.enabled

    def apply_correction(self, *_args, **_kwargs):
        return None

    def _plugin_tools(self):
        return []


@pytest.fixture
def page(qapp, config_env):
    from core.services.config_manager import ConfigManager
    from plugins.proofread.dashboard_page import DashboardPage

    manager = ConfigManager()
    plugin = _FakePlugin(manager)
    widget = DashboardPage(plugin=plugin)
    widget.build_secondary_panel()
    yield widget, manager, plugin
    widget.deleteLater()


def test_opacity_slider_has_reachable_full_range(page):
    widget, _manager, _plugin = page
    assert widget.opacity_slider.minimum() == 20, "下限要防止 0% 完全不可见"
    assert widget.opacity_slider.maximum() == 100, "上限必须是 100%，否则 100% 不可达"


def test_save_clamps_opacity_and_broadcasts(page):
    widget, manager, plugin = page
    widget.opacity_slider.setValue(100)
    widget.save_plugin_settings()
    assert manager.get("suggestion_opacity") == 1.0
    widget.opacity_slider.setValue(20)
    widget.save_plugin_settings()
    assert manager.get("suggestion_opacity") == 0.2
    assert plugin.ctx.emitted.count("settings.saved") == 2, "保存成功必须广播 settings.saved"


def test_save_writes_proof_prompt(page):
    widget, manager, _plugin = page
    widget.proof_prompt_edit.setPlainText("只改错别字：{text}")
    widget.save_plugin_settings()
    assert manager.get("proof_prompt") == "只改错别字：{text}"

def test_proofread_drawer_has_no_pet_controls(page):
    """宠物外观/显隐归内核外观页，proofread 抽屉里不能再有宠物尺寸、皮肤或显隐开关。"""
    widget = page[0]
    assert not hasattr(widget, "pet_size_slider")
    assert not hasattr(widget, "pet_skin_combo")
    assert not hasattr(widget, "show_floating_check")
