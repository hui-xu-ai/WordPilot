# tests/test_i18n_prompt_language.py
"""显示语言只有界面语言：`prompt_language` 配置键已废弃。

用户 2026-10-28 明确：界面上所有文字（含内置默认提示词）只看界面语言；
校对语言 / 翻译目标 / 纠错语言只用于判断待处理文本的语言，与显示无关。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CJK = re.compile(r"[\u4e00-\u9fff]")


class _Config:
    def __init__(self, data):
        self.data = data

    def get(self, key, default=None):
        return self.data.get(key, default)


def test_auto_follows_ui_language():
    from core.services.i18n import I18nService

    service = I18nService(_Config({"ui_language": "en_US", "prompt_language": "auto"}),
                          ROOT / "src/assets/i18n")

    assert service.prompt_language == "en_US"


def test_prompt_language_config_key_is_ignored():
    """`prompt_language` 配置键已废弃：显示语言只有界面语言一个标准。"""
    from core.services.i18n import I18nService

    class _Config:
        def __init__(self, ui, prompt):
            self.data = {"ui_language": ui, "prompt_language": prompt}

        def get(self, key, default=None):
            return self.data.get(key, default)

        def set(self, key, value):
            self.data[key] = value

    service = I18nService(_Config("zh_CN", "en_US"), ROOT / "src/assets/i18n")

    assert service.prompt_language == "zh_CN", "旧配置里的 prompt_language 不该再影响显示"
    assert CJK.search(service.prompt_tr("prompt.proof.default")), "中文界面就该是中文默认提示词"

    service = I18nService(_Config("en_US", "zh_CN"), ROOT / "src/assets/i18n")
    assert service.prompt_language == "en_US"
    assert not CJK.search(service.prompt_tr("prompt.proof.default")), "英文界面就该是英文默认提示词"


def test_no_prompt_language_setting(config_env):
    """配置键已废弃：不再预置 `prompt_language`（旧配置里有也忽略）。"""
    from core.services.config_manager import ConfigManager

    assert ConfigManager().get("prompt_language", "MISSING") in ("MISSING", "auto")


@pytest.mark.parametrize("locale", ["zh_CN", "en_US"])
def test_prompt_language_key_not_in_ui_copy(locale):
    import json

    data = json.loads((ROOT / f"src/assets/i18n/{locale}.json").read_text(encoding="utf-8"))

    assert "settings.prompt.language" not in data, "界面上不再有该选项，文案也应删掉"
