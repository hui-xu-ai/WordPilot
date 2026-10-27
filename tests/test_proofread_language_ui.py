# tests/test_proofread_language_ui.py
"""校对语言下拉：要有可见标签、auto 要显示成可读文案、提示词卡片里不再有「保存」按钮。

用户反馈（2026-10-27 截图）：
1. 下拉只显示 `auto`，没有标签，用户不知道什么意思；
2. 校对提示词卡片里还留着一个「保存」按钮（应改为自动保存）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "src/plugins/proofread/dashboard_page.py"


def test_language_label_and_readable_options_in_source():
    text = PAGE.read_text(encoding="utf-8")

    assert 'self.proof_language_label.setText(self.tr("settings.prompt.prooflang"))' in text
    assert 'settings.prompt.prooflang_auto' in text, "auto 必须用可读文案显示"
    assert "_display_for(" in text and "_value_for(" in text, "显示文案↔配置值要互相映射"


def test_prompt_card_has_no_save_button():
    text = PAGE.read_text(encoding="utf-8")

    assert "save_prompt_btn" not in text, "提示词卡片里的保存按钮应已删除"
    assert "_prompt_save_timer" in text, "改成输入后自动保存"


@pytest.mark.parametrize("locale,expected", [("zh_CN", "自动识别"), ("en_US", "Auto detect")])
def test_auto_option_is_readable(locale, expected):
    data = json.loads((ROOT / f"src/plugins/proofread/i18n/{locale}.json").read_text(encoding="utf-8"))

    assert data["settings.prompt.prooflang_auto"] == expected
    assert data["settings.prompt.prooflang"] in ("校对语言", "Proofreading language")


def test_translate_auto_option_is_readable():
    data = json.loads((ROOT / "src/plugins/clipboard/i18n/zh_CN.json").read_text(encoding="utf-8"))

    assert "自动" in data["settings.prompt.translatelang_auto"]
    assert data["settings.prompt.translatelang"].startswith("翻译目标语言")
