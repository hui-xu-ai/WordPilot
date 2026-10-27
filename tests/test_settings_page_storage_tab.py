# tests/test_settings_page_storage_tab.py
"""设置页分页结构：API / 提示词 / 外观 / API 监控 / 存储 / 关于，各占一个 Tab。"""
from __future__ import annotations

import pytest

NAV_KEYS = ("settings.tab.api", "settings.tab.prompts", "settings.tab.appearance",
            "settings.tab.monitor", "settings.tab.storage", "settings.tab.about")


@pytest.fixture
def app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def page(app):
    from app.settings_page import SettingsPage
    from core.services.config_manager import ConfigManager

    widget = SettingsPage(ConfigManager())
    yield widget
    widget.deleteLater()


def test_tabs_are_one_per_section(page):
    from app.settings_page import _NAV_KEYS

    assert _NAV_KEYS == NAV_KEYS
    assert page.nav_list.count() == len(NAV_KEYS)
    assert page.stack.count() == len(NAV_KEYS)


def test_prompt_language_lives_in_prompts_tab_not_api(page):
    prompts_page = page.stack.widget(1)
    api_page = page.stack.widget(0)

    assert prompts_page.isAncestorOf(page.prompt_language_combo), "提示词语言应在「提示词」页"
    assert not api_page.isAncestorOf(page.prompt_language_combo), "不该混在 API 页里"


def test_storage_is_its_own_tab(page):
    page.nav_list.setCurrentRow(4)                 # 存储

    assert page.stack.currentWidget().isAncestorOf(page.storage_value)


def test_monitor_is_its_own_tab(page):
    page.nav_list.setCurrentRow(3)                 # API 监控

    monitor_page = page.stack.currentWidget()
    assert monitor_page.isAncestorOf(page.monitor_panel)
    assert not monitor_page.isAncestorOf(page.storage_value)


def test_about_page_no_longer_holds_storage(page):
    page.nav_list.setCurrentRow(5)                 # 关于
    about_page = page.stack.currentWidget()

    assert not about_page.isAncestorOf(page.storage_value), "存储不该再放在关于页里"
    assert not about_page.isAncestorOf(page.storage_logs_btn)
