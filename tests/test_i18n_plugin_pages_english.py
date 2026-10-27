# tests/test_i18n_plugin_pages_english.py
"""插件页自查：切到英文后界面不应残留中文（真实构造控件，不靠人眼截图）。

用户要求（2026-10-27）：「挨个翻译检查，不许再偷懒」。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CJK = re.compile(r"[\u4e00-\u9fff]")
ALLOWED = {"中文", "简体中文"}          # 语言名用母语显示


class _Config:
    def __init__(self):
        self.data = {"ui_language": "en_US", "prompt_language": "auto",
                     "proof_language": "auto", "correct_language": "auto",
                     "translate_language": "auto", "rewrite_strength": "中",
                     "api_endpoint": "https://api.deepseek.com", "model": "deepseek-flash"}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value

    def save(self):
        pass


class _Ctx:
    """插件页需要的最小上下文：i18n 走真实语言包（en_US）。"""

    def __init__(self):
        from core.services.i18n import I18nService

        self.config = _Config()
        self.i18n = I18nService(self.config, ROOT / "src/assets/i18n")
        for plugin in ("clipboard", "proofread", "image_toolbox", "navigator", "reference"):
            self.i18n.register_namespace(plugin, ROOT / f"src/plugins/{plugin}/i18n")
        self.events = _Events()

    def tr(self, key, /, **params):
        return self.i18n.tr(key, **params)

    def get_service(self, name):
        return {"i18n": self.i18n, "config": self.config}.get(name)


class _Events:
    def emit(self, *_args, **_kwargs):
        pass

    def on(self, *_args, **_kwargs):
        pass


def _texts(widget) -> list[str]:
    from PySide6.QtWidgets import (QAbstractButton, QComboBox, QLabel, QTableWidget)

    found: list[str] = []
    for label in widget.findChildren(QLabel):
        found.append(label.text())
    for button in widget.findChildren(QAbstractButton):
        found.append(button.text())
    for combo in widget.findChildren(QComboBox):
        found.extend(combo.itemText(i) for i in range(combo.count()))
    for table in widget.findChildren(QTableWidget):
        for column in range(table.columnCount()):
            header = table.horizontalHeaderItem(column)
            if header is not None:
                found.append(header.text())
    return [text for text in found if text]


def _assert_no_chinese(widget, where: str) -> None:
    offenders = [text for text in _texts(widget)
                 if CJK.search(text) and text.strip() not in ALLOWED]
    assert not offenders, f"{where} 切到英文后仍有中文：{offenders[:8]}"


@pytest.fixture
def app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_clipboard_page_english(app):
    from plugins.clipboard.plugin import ClipboardWidget

    widget = ClipboardWidget(_Ctx())
    widget.retranslateUi()

    _assert_no_chinese(widget, "翻译改写页")


class _Plugin:
    """DashboardPage 只需要 plugin.ctx + 少量属性。"""

    def __init__(self, ctx):
        self.ctx = ctx
        self.floating_btn = None

    # DashboardPage 会用到的插件接口（替身保持最小）
    def is_enabled(self):
        return False

    def set_enabled(self, value):
        return None

    def subscribe(self, *_args, **_kwargs):
        return None

    def get_settings_payload(self):
        return {}

    def save_plugin_settings(self):
        return True
        return True


def test_proofread_page_english(app):
    from plugins.proofread.dashboard_page import DashboardPage

    ctx = _Ctx()
    page = DashboardPage(_Plugin(ctx))
    page.retranslateUi()

    _assert_no_chinese(page, "智能纠错页")
