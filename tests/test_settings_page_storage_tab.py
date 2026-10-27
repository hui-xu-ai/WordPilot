"""验证存储页是独立 Tab：关于页里不再有存储控件，导航多一项。"""
from __future__ import annotations

import pytest


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


def test_storage_is_its_own_tab(page):
    from app.settings_page import _NAV_KEYS

    assert _NAV_KEYS == ("settings.tab.api", "settings.tab.appearance",
                         "settings.tab.storage", "settings.tab.about")
    assert page.nav_list.count() == 4
    assert page.stack.count() == 4

    page.nav_list.setCurrentRow(2)                 # 存储
    storage_page = page.stack.currentWidget()
    assert storage_page.findChild(type(page.storage_value), "hintLabel") is not None
    assert storage_page.isAncestorOf(page.storage_value)


def test_about_page_no_longer_holds_storage(page):
    page.nav_list.setCurrentRow(3)                 # 关于
    about_page = page.stack.currentWidget()

    assert not about_page.isAncestorOf(page.storage_value), "存储不该再放在关于页里"
    assert not about_page.isAncestorOf(page.storage_logs_btn)
