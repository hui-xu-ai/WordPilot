# tests/test_i18n_language_switch.py
"""切换界面语言后，已构造的页面必须跟着换文案（含提示词默认内容）。

现场（2026-10-27 英文界面截图）：卡片标题/占位符说明/下拉选项/版本标签仍是中文。
根因：这些 QLabel 与下拉项在构造时用 `tr()` 求值一次，`retranslateUi` 里没有重新设置。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CJK = re.compile(r"[\u4e00-\u9fff]")
PLUGINS = ("clipboard", "proofread", "image_toolbox", "navigator", "reference")


@pytest.fixture
def app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_choice_combo_relabels_options(app):
    from core.widgets.choice_combo import ChoiceCombo

    labels = {"opt.auto": "自动识别", "opt.zh": "中文"}
    combo = ChoiceCombo()
    combo.set_choices([("opt.auto", "auto"), ("opt.zh", "中文")],
                      tr=lambda key: labels.get(key, key))
    combo.set_value("中文")

    labels.update({"opt.auto": "Auto detect", "opt.zh": "Chinese"})
    combo.retranslate_options(lambda key: labels.get(key, key))

    assert [combo.itemText(i) for i in range(combo.count())] == ["Auto detect", "Chinese"]
    assert combo.current_value() == "中文", "重译不能改变选中值"


def test_prompt_bar_refreshes_default_text_on_language_switch(app, tmp_path, monkeypatch):
    from core.services import prompt_store
    from core.widgets.prompt_version_bar import PromptVersionBar

    monkeypatch.setattr(prompt_store, "_config_dir", lambda: tmp_path)

    class _Config:
        def __init__(self):
            self.data = {"proof_prompt": ""}          # 用户没自定义 → 跟随默认

        def get(self, key, default=None):
            return self.data.get(key, default)

        def set(self, key, value):
            self.data[key] = value

        def save(self):
            pass

    texts = {"prompt.proof.default": "中文默认 {text}"}
    holder = {"text": "中文默认 {text}"}
    bar = PromptVersionBar("proof_prompt", _Config(), lambda key, **kw: key,
                           read_text=lambda: holder["text"],
                           apply_text=lambda text: holder.__setitem__("text", text),
                           prompt_tr=lambda key: texts.get(key, key))

    texts["prompt.proof.default"] = "English default {text}"
    bar.refresh_language()

    assert holder["text"] == "English default {text}"


def test_custom_prompt_is_not_overwritten_on_language_switch(app, tmp_path, monkeypatch):
    from core.services import prompt_store
    from core.widgets.prompt_version_bar import PromptVersionBar

    monkeypatch.setattr(prompt_store, "_config_dir", lambda: tmp_path)

    class _Config:
        def __init__(self):
            self.data = {"proof_prompt": "我自己的提示词 {text}"}

        def get(self, key, default=None):
            return self.data.get(key, default)

        def set(self, key, value):
            self.data[key] = value

        def save(self):
            pass

    holder = {"text": "我自己的提示词 {text}"}
    bar = PromptVersionBar("proof_prompt", _Config(), lambda key, **kw: key,
                           read_text=lambda: holder["text"],
                           apply_text=lambda text: holder.__setitem__("text", text),
                           prompt_tr=lambda key: "English default {text}")

    bar.refresh_language()

    assert holder["text"] == "我自己的提示词 {text}"


@pytest.mark.parametrize("name", ["app"] + list(PLUGINS))
def test_locales_have_same_keys_and_english_has_no_chinese(name):
    directory = ROOT / "src/assets/i18n" if name == "app" else ROOT / f"src/plugins/{name}/i18n"
    zh = json.loads((directory / "zh_CN.json").read_text(encoding="utf-8"))
    en = json.loads((directory / "en_US.json").read_text(encoding="utf-8"))

    assert set(zh) == set(en), f"{name} 两种语言的 key 不一致：{set(zh) ^ set(en)}"
    leftovers = [key for key, value in en.items()
                 if isinstance(value, str) and CJK.search(value)
                 and key != "language.zh_CN"]     # 语言名称本身要用中文显示
    assert not leftovers, f"{name} 的英文包里仍有中文：{leftovers}"


def test_pages_retranslate_added_labels():
    clip = (ROOT / "src/plugins/clipboard/plugin.py").read_text(encoding="utf-8")
    proof = (ROOT / "src/plugins/proofread/dashboard_page.py").read_text(encoding="utf-8")

    assert "_card_labels" in clip and "placeholder_help" in clip
    assert "retranslate_options" in proof, "校对语言下拉要在切语言后重译"
    assert "refresh_language()" in clip and "refresh_language()" in proof
