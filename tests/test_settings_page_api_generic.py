# tests/test_settings_api_generic.py
"""API 设置不再写死 DeepSeek：接口地址可配、模型可自填、文案去品牌。

用户反馈（2026-10-27）：API 这部分当前硬编码了 DeepSeek。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "src/app/settings_page.py"


@pytest.fixture
def app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_endpoint_is_editable_and_saved(app):
    from app.settings_page import SettingsPage
    from core.services.config_manager import ConfigManager

    page = SettingsPage(ConfigManager())
    source = PAGE.read_text(encoding="utf-8")

    assert hasattr(page, "endpoint_edit"), "必须有可编辑的接口地址"
    assert page.endpoint_edit.text().startswith("http")
    assert '"api_endpoint": self.endpoint_edit.text().strip()' in source, "保存时带上接口地址"


def test_test_connection_uses_configured_endpoint(app):
    source = PAGE.read_text(encoding="utf-8")

    # 接口地址由页面传入后台线程（线程内不得访问控件）
    assert "_ConnectionTester(key, model, endpoint)" in source
    assert '"api_endpoint": self._endpoint}' in source


def test_ui_copy_has_no_provider_branding():
    for locale in ("zh_CN", "en_US"):
        data = json.loads((ROOT / f"src/assets/i18n/{locale}.json").read_text(encoding="utf-8"))
        for key in ("settings.api.key_placeholder", "settings.api.model_placeholder",
                    "settings.api.endpoint_placeholder", "api.monitor.overview"):
            assert "DeepSeek" not in data[key], f"{locale}/{key} 仍写死品牌名"


def test_default_endpoint_still_deepseek_but_overridable(config_env):
    from core.services.config_manager import ConfigManager

    manager = ConfigManager()

    assert manager.get("api_endpoint") == "https://api.deepseek.com"
    assert manager.get("model")
