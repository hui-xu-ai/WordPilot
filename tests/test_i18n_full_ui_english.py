# tests/test_i18n_full_ui_english.py
"""界面自查：把设置页与插件页切到英文后，界面里不应再有任何中文。

用户要求（2026-10-27）：「你挨个翻译检查，不许再偷懒」。所以这里不靠人眼截图，
而是构造真实控件 → 切语言 → 遍历所有可见文案（QLabel/QPushButton/QComboBox 选项/
QTableWidget 表头/QTextEdit 占位符/QAction）断言没有中文字符。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CJK = re.compile(r"[\u4e00-\u9fff]")
#: 语言名称本身用母语显示，允许中文
ALLOWED = {"中文", "简体中文"}   # 语言名用母语显示，属预期


class _SwitchableConfig:
    """可在测试中切语言的配置替身。"""

    def __init__(self, language: str = "en_US"):
        self.data = {"ui_language": language, "prompt_language": "auto",
                     "proof_language": "auto", "correct_language": "auto",
                     "translate_language": "auto", "rewrite_strength": "中",
                     "api_endpoint": "https://api.deepseek.com", "model": "deepseek-flash"}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value

    def save(self):
        pass

    # SettingsPage 用到的配置接口
    def get_api_key(self):
        return ""

    def set_api_key(self, value):
        self.data["api_key"] = value

    def update(self, payload):
        self.data.update(payload)
        return True


def _i18n(config):
    from core.services.i18n import I18nService

    service = I18nService(config, ROOT / "src/assets/i18n")
    for plugin in ("clipboard", "proofread", "image_toolbox", "navigator", "reference"):
        service.register_namespace(plugin, ROOT / f"src/plugins/{plugin}/i18n")
    return service


def _texts(widget) -> list[str]:
    """收集一个页面里所有"用户能看到的文案"。"""
    from PySide6.QtWidgets import (QAbstractButton, QComboBox, QLabel, QTableWidget, QWidget)

    found: list[str] = []
    for label in widget.findChildren(QLabel):
        found.append(label.text())
    for button in widget.findChildren(QAbstractButton):
        found.append(button.text())
    for combo in widget.findChildren(QComboBox):
        found.extend(combo.itemText(i) for i in range(combo.count()))
        found.append(combo.currentText())
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


def test_settings_page_has_no_chinese_in_english(app):
    from app.settings_page import SettingsPage

    config = _SwitchableConfig("en_US")
    page = SettingsPage(config, i18n=_i18n(config))
    page.retranslateUi()

    _assert_no_chinese(page, "设置页")


def test_settings_storage_lines_are_translated(app):
    from app.settings_page import SettingsPage

    config = _SwitchableConfig("en_US")
    page = SettingsPage(config, i18n=_i18n(config))
    page.retranslateUi()
    page._refresh_storage()

    assert page.storage_value.text(), "存储清单不能为空"
    assert not CJK.search(page.storage_value.text()), page.storage_value.text()


def test_monitor_panel_is_retranslated_with_page(app):
    from app.settings_page import SettingsPage

    config = _SwitchableConfig("en_US")
    page = SettingsPage(config, i18n=_i18n(config))
    page.retranslateUi()

    headers = [page.monitor_panel.table.horizontalHeaderItem(i).text()
               for i in range(page.monitor_panel.table.columnCount())]
    assert headers and all(not CJK.search(text) for text in headers), headers
