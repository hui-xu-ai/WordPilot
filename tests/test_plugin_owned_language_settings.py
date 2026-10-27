# tests/test_plugin_owned_language_settings.py
"""语言相关配置归各自插件所有，不塞进主程序设置页。

用户要求（2026-10-27）：校对语言属于智能纠错插件、翻译目标语言属于翻译改写插件，
不应出现在主程序的 API/外观设置里（那是核心配置，不是插件配置）。
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_main_settings_page_has_no_plugin_language_settings():
    text = (ROOT / "src/app/settings_page.py").read_text(encoding="utf-8")

    assert "proof_language" not in text, "校对语言不该出现在主程序设置页"
    assert "translate_language" not in text, "翻译目标语言不该出现在主程序设置页"


def test_proofread_plugin_owns_proof_language():
    text = (ROOT / "src/plugins/proofread/dashboard_page.py").read_text(encoding="utf-8")
    assert "proof_language_edit" in text
    assert '"proof_language": _value_for(self.proof_language_edit)' in text


def test_clipboard_plugin_owns_translate_language():
    text = (ROOT / "src/plugins/clipboard/plugin.py").read_text(encoding="utf-8")
    assert "translate_language_edit" in text
    assert '"translate_language": _language_value(self.translate_language_edit)' in text


def test_plugin_i18n_has_language_labels():
    clip = json.loads((ROOT / "src/plugins/clipboard/i18n/zh_CN.json").read_text(encoding="utf-8"))
    proof = json.loads((ROOT / "src/plugins/proofread/i18n/zh_CN.json").read_text(encoding="utf-8"))

    assert clip["settings.prompt.translatelang"] == "翻译目标语言"
    assert proof["settings.prompt.prooflang"] == "校对语言"


def test_correct_hint_has_no_developer_note():
    """纠错提示词下的说明只讲占位符，不再写"与某插件解耦"这类开发注释。"""
    clip = json.loads((ROOT / "src/plugins/clipboard/i18n/zh_CN.json").read_text(encoding="utf-8"))
    hint = clip["settings.prompt.correct_hint"]

    assert "{text}" in hint
    for noise in ("完全独立", "解耦", "单独保存"):
        assert noise not in hint, f"说明里还留着开发注释：{noise}"
