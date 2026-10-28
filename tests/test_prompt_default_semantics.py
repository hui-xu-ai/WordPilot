# tests/test_prompt_default_semantics.py
"""用户 2026-10-28 定义的口径：「默认」＝程序内置提示词，按界面语言取，**永不落盘**。

- 默认文案来自文案数据库 `prompt.<场景>.default`（中英各一套）；
- config 只存用户自定义文本（另存为过的版本）；
- 保存路径发现"等于任一语言默认"→ 存空串，界面仍正常显示默认；
- 版本下拉的文字只受界面语言控制（应用级 tr，不是插件命名空间）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


class _Config:
    def __init__(self, language: str = "en_US"):
        self.data = {"ui_language": language}
        self.saved = 0

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value

    def save(self):
        self.saved += 1


@pytest.fixture
def app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _i18n(language: str = "en_US"):
    from core.services.i18n import I18nService

    return I18nService(_Config(language), ROOT / "src/assets/i18n")


def _default(key: str, language: str) -> str:
    data = json.loads((ROOT / f"src/assets/i18n/{language}.json").read_text(encoding="utf-8"))
    from core.services.prompt_store import default_keys
    return data[default_keys(key)]


def test_default_text_is_never_saved():
    from core.services import prompt_store

    i18n = _i18n("en_US")
    en_default = _default("proof_prompt", "en_US")
    zh_default = _default("proof_prompt", "zh_CN")

    assert prompt_store.value_for_save("proof_prompt", en_default, i18n.prompt_tr) == ""
    assert prompt_store.value_for_save("proof_prompt", zh_default, i18n.prompt_tr) == "", \
        "中文默认也不算用户内容（否则英文界面又被中文覆盖）"
    assert prompt_store.value_for_save("proof_prompt", "只改错别字：{text}", i18n.prompt_tr) == \
        "只改错别字：{text}", "真正自定义的文本必须原样保存"


@pytest.mark.parametrize("language", ["zh_CN", "en_US"])
def test_display_follows_ui_language(language):
    from core.services import prompt_store

    i18n = _i18n(language)
    cfg = _Config(language)          # config 为空 = 用默认

    shown = prompt_store.effective_text("proof_prompt", cfg, i18n.prompt_tr)
    assert shown == _default("proof_prompt", language)


def test_saved_default_copy_falls_back_to_ui_language():
    """旧数据里残留"某语言的默认副本"时，界面仍按界面语言显示。"""
    from core.services import prompt_store

    i18n = _i18n("en_US")
    cfg = _Config("en_US")
    cfg.data["proof_prompt"] = _default("proof_prompt", "zh_CN")

    assert prompt_store.effective_text("proof_prompt", cfg, i18n.prompt_tr) == \
        _default("proof_prompt", "en_US")


def test_version_label_uses_ui_language(app):
    """版本下拉文字只看界面语言：应用级 tr 给英文，插件级 tr 也不会串味。"""
    from PySide6.QtWidgets import QApplication  # noqa: F401

    from core.widgets.prompt_version_bar import PromptVersionBar

    en = _i18n("en_US")
    holder = {"text": ""}
    bar = PromptVersionBar(
        "proof_prompt", _Config("en_US"), lambda key, **kw: key,
        read_text=lambda: holder["text"],
        apply_text=lambda text: holder.__setitem__("text", text),
        prompt_tr=en.prompt_tr,
        label_tr=en.tr,
    )

    assert bar._display_version_name("默认") == "Default"
    assert bar._display_version_name("mine-2") == "My version 2"
    assert bar._display_version_name("auto-20261028-091500") == "Auto backup 20261028-091500"


def test_plugins_use_value_for_save():
    """两个插件的保存路径都必须过 `value_for_save`（默认文案不落盘）。"""
    for relative in ("src/plugins/proofread/dashboard_page.py",
                     "src/plugins/clipboard/plugin.py"):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "value_for_save(" in source, f"{relative} 未使用 value_for_save"
