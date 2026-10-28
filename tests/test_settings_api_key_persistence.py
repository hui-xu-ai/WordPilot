# tests/test_settings_api_key_persistence.py
"""API Key 持久化：保存其它设置**绝不能**把已存密钥清空（用户现场）。

现场（2026-10-28）：用户填了密钥，智能纠错仍报
`HTTP 401: Authentication Fails (auth header format should be Bearer sk-...)`；
查 `config/MWindow_config.json` 发现 `api_key` 是空串。
根因：`save_settings()` 每次都把文本框内容当密钥传下去，而文本框在"未修改 / 解密失败"
时是空的 —— 于是任何一次保存（改皮肤、改语言…）都会抹掉密钥。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
KEY = "sk-test-abcdefghijklmnop0123456789"


@pytest.fixture
def app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    """把配置与密钥文件都指到临时目录，避免碰真实配置。

    `.key` 在仓库里是被 gitignore 的（CI 上没有），所以这里**临时生成**一把 Fernet 密钥，
    不能依赖本机文件。
    """
    from cryptography.fernet import Fernet

    from core.services import config_manager as cm

    key_file = tmp_path / ".key"
    if not key_file.exists():
        key_file.write_bytes(Fernet.generate_key())
    monkeypatch.setattr(cm, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cm, "CONFIG_FILE", tmp_path / "MWindow_config.json")
    monkeypatch.setattr(cm, "KEY_FILE", key_file)
    return tmp_path


def _stored(config_dir: Path) -> str:
    raw = json.loads((config_dir / "MWindow_config.json").read_text(encoding="utf-8"))
    return raw.get("api_key", "")


def test_saving_other_settings_keeps_existing_key(config_dir):
    from core.services.config_manager import ConfigManager

    config = ConfigManager()
    config.update({}, api_key=KEY)
    cipher = _stored(config_dir)
    assert cipher and cipher.startswith("gAAAAA"), "密钥应加密保存"

    # 只改主题/语言，不传 api_key
    config.update({"theme": "dark", "ui_language": "en_US"})

    assert _stored(config_dir) == cipher, "无关设置保存后密钥密文必须原样保留"
    assert config.get_api_key() == KEY


def test_settings_page_does_not_wipe_key_when_untouched(app, config_dir):
    """设置页保存（文本框未改）= 不写 api_key。"""
    from app.settings_page import SettingsPage
    from core.services.config_manager import ConfigManager
    from core.services.i18n import I18nService

    config = ConfigManager()
    config.update({}, api_key=KEY)
    cipher = _stored(config_dir)

    page = SettingsPage(config, i18n=I18nService(config, ROOT / "src/assets/i18n"))
    assert page.api_key_edit.text() == KEY, "打开设置页应回填已存密钥"
    page.save_settings()

    assert _stored(config_dir) == cipher, "未修改密钥时保存不得覆盖"


def test_settings_page_writes_key_when_changed(app, config_dir):
    from app.settings_page import SettingsPage
    from core.services.config_manager import ConfigManager
    from core.services.i18n import I18nService

    config = ConfigManager()
    page = SettingsPage(config, i18n=I18nService(config, ROOT / "src/assets/i18n"))
    page.api_key_edit.setText(KEY)
    page.save_settings()

    assert config.get_api_key() == KEY
    assert _stored(config_dir).startswith("gAAAAA")


def test_explicit_clear_is_possible(app, config_dir):
    from app.settings_page import SettingsPage
    from core.services.config_manager import ConfigManager
    from core.services.i18n import I18nService

    config = ConfigManager()
    config.update({}, api_key=KEY)
    page = SettingsPage(config, i18n=I18nService(config, ROOT / "src/assets/i18n"))
    page.api_key_edit.setText("")          # 用户主动清空
    page.save_settings()

    assert _stored(config_dir) == ""
    assert config.get_api_key() == ""


def test_update_without_key_argument_is_noop_for_key(config_dir):
    from core.services.config_manager import ConfigManager

    config = ConfigManager()
    config.update({}, api_key=KEY)
    cipher = _stored(config_dir)

    config.update({"model": "deepseek-flash"})
    assert _stored(config_dir) == cipher
