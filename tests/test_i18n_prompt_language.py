# tests/test_i18n_prompt_language.py
"""提示词语言默认跟随界面语言（auto），高级用户仍可在配置里指定 en_US。

用户疑问（2026-10-27）：「你为什么设计一个这个，它的作用是什么？对应插件里面难道没有这个设置吗？」
答：插件里的是**文本语言**（校对语言/翻译目标/纠错语言），这里是**提示词本身用哪种语言书写**，
两者不是一回事；但它与界面语言几乎总是一致，因此不再单独暴露开关。
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


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


def test_explicit_prompt_language_wins():
    from core.services.i18n import I18nService

    service = I18nService(_Config({"ui_language": "zh_CN", "prompt_language": "en_US"}),
                          ROOT / "src/assets/i18n")

    assert service.prompt_language == "en_US", "英文界面中文界都可单独指定提示词语言"


def test_default_is_auto(config_env):
    from core.services.config_manager import ConfigManager

    assert ConfigManager().get("prompt_language") == "auto"


@pytest.mark.parametrize("locale", ["zh_CN", "en_US"])
def test_prompt_language_key_not_in_ui_copy(locale):
    import json

    data = json.loads((ROOT / f"src/assets/i18n/{locale}.json").read_text(encoding="utf-8"))

    assert "settings.prompt.language" not in data, "界面上不再有该选项，文案也应删掉"
