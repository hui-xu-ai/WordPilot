# tests/test_i18n_side_panel_english.py
"""侧边抽屉（插件页的「次要界面」）也要英文：提示词说明、语言下拉、卡片标题。

用户多次截图指出：英文界面里「占位符：…」说明与「自动识别」仍是中文。
这些控件位于侧边抽屉里（`_build_secondary_panel()`），只有打开抽屉时才构造，
所以之前的页面级巡检测不到。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CJK = re.compile(r"[\u4e00-\u9fff]")
ALLOWED = {"中文", "简体中文"}


@pytest.fixture
def app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def ctx():
    from core.services.config_manager import ConfigManager
    from core.services.i18n import I18nService

    class _Config(ConfigManager):
        def __init__(self):
            super().__init__()
            self.set("ui_language", "en_US")

    class _Ctx:
        """真实 PluginContext 的替身：`tr()` 会加 `<plugin_id>.` 前缀。"""

        def __init__(self, plugin_id: str = "proofread"):
            self.config = _Config()
            self.plugin_id = plugin_id
            self.i18n = I18nService(self.config, ROOT / "src/assets/i18n")
            for plugin in ("clipboard", "proofread"):
                self.i18n.register_namespace(plugin, ROOT / f"src/plugins/{plugin}/i18n")
            self.events = type("E", (), {"emit": staticmethod(lambda *a, **k: None)})()

        def tr(self, key, /, **params):
            if "." not in key.split(".")[0] or not key.startswith(f"{self.plugin_id}."):
                key = f"{self.plugin_id}.{key}"
            return self.i18n.tr(key, **params)

        def get_service(self, name):
            return {"i18n": self.i18n, "config": self.config}.get(name)

    return _Ctx()


class _Plugin:
    def __init__(self, ctx):
        self.ctx = ctx
        self.floating_btn = None

    def is_enabled(self):
        return False

    def set_enabled(self, value):
        return None

    def subscribe(self, *args, **kwargs):
        return None

    def get_settings_payload(self):
        return {}


def _texts(widget) -> list[str]:
    from PySide6.QtWidgets import QAbstractButton, QComboBox, QLabel

    found: list[str] = [label.text() for label in widget.findChildren(QLabel)]
    found += [button.text() for button in widget.findChildren(QAbstractButton)]
    for combo in widget.findChildren(QComboBox):
        found += [combo.itemText(i) for i in range(combo.count())]
    return [text for text in found if text]


def _assert_no_chinese(widget, where: str) -> None:
    offenders = [text for text in _texts(widget)
                 if CJK.search(text) and text.strip() not in ALLOWED]
    assert not offenders, f"{where} 仍有中文：{offenders[:8]}"


def test_proofread_side_panel_english(app, ctx):
    from plugins.proofread.dashboard_page import DashboardPage

    page = DashboardPage(_Plugin(ctx))
    panel = page.build_secondary_panel()
    assert panel is not None, "侧边抽屉必须能构造出来"
    page.retranslateUi()

    _assert_no_chinese(panel, "智能纠错侧边抽屉")


def test_clipboard_side_panel_english(app, ctx):
    from plugins.clipboard.plugin import ClipboardWidget

    ctx.plugin_id = "clipboard"
    widget = ClipboardWidget(ctx)
    panel = widget._build_side_panel() if hasattr(widget, "_build_side_panel") else None
    if panel is None:
        pytest.skip("该插件没有单独的侧边抽屉")
    widget.retranslateUi()

    _assert_no_chinese(panel, "翻译改写侧边抽屉")
