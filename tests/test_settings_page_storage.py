# tests/test_settings_storage.py
"""设置页的「存储管理」区块：能统计、能清理，且不碰用户资产。"""
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


def test_storage_card_reports_groups(tmp_path, monkeypatch, page):
    from core.services import housekeeping

    (tmp_path / "app.log").write_text("x" * 500, encoding="utf-8")
    monkeypatch.setattr(housekeeping, "config_dir", lambda: tmp_path)

    page._refresh_storage()

    # 测试环境没有装翻译器，`tr()` 原样返回 key —— 断言按 key 计行数即可
    text = page.storage_value.text()
    assert text.count("settings.storage.item") == len(housekeeping.inventory()), text


def test_clean_storage_removes_logs_and_keeps_assets(tmp_path, monkeypatch, page):
    from core.services import housekeeping

    (tmp_path / "app.log").write_text("x" * 100, encoding="utf-8")
    (tmp_path / "Image.docx").write_text("asset", encoding="utf-8")
    (tmp_path / "references").mkdir()
    (tmp_path / "references" / "notes.csv").write_text("note_id\n", encoding="utf-8")
    monkeypatch.setattr(housekeeping, "config_dir", lambda: tmp_path)

    page._clean_storage(["logs", "wal"])

    assert not (tmp_path / "app.log").exists(), "日志应被清掉"
    assert (tmp_path / "Image.docx").exists(), "资产不能被清掉"
    assert (tmp_path / "references" / "notes.csv").exists()
    assert page.storage_result.text() == "settings.storage.done", page.storage_result.text()
