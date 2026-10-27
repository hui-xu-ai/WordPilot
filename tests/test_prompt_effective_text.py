# tests/test_prompt_effective_text.py
"""编辑框显示"生效文本"：config 为空时必须显示系统默认，而不是空白。

现场（用户截图）：校对提示词 / 纠错提示词编辑框都是空的 —— 因为只读了 config，
而这两项 config 里是空串，实际发给模型的却是语言包默认文案。
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def temp_store(tmp_path, monkeypatch):
    from core.services import prompt_store

    monkeypatch.setattr(prompt_store, "_config_dir", lambda: tmp_path)
    yield tmp_path


class _Config:
    def __init__(self, data=None):
        self.data = data or {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value

    def save(self):
        pass


DEFAULTS = {
    "prompt.proof.default": "你是{lang}校对专家：{text}",
    "prompt.correct.default": "整段纠错：{text}",
    "prompt.translate.default": "翻译：{text}",
    "prompt.rewrite.default": "改写：{text}",
}


def _prompt_tr(key):
    return DEFAULTS.get(key, key)


def test_effective_text_falls_back_to_default():
    from core.services import prompt_store

    assert prompt_store.effective_text("proof_prompt", _Config(), _prompt_tr) == \
        "你是{lang}校对专家：{text}"


def test_effective_text_prefers_saved_config():
    from core.services import prompt_store

    config = _Config({"correct_prompt": "我自己的纠错：{text}"})

    assert prompt_store.effective_text("correct_prompt", config, _prompt_tr) == \
        "我自己的纠错：{text}"


def test_default_text_rejects_raw_key():
    from core.services import prompt_store

    assert prompt_store.default_text("proof_prompt", lambda key: key) == ""


def test_default_version_is_always_available(temp_store):
    from core.services import prompt_store

    # 已有别的版本（例如自动备份）时，仍要保证「默认」存在
    prompt_store.save_version("proof_prompt", "自动-20260101-000000", "旧文本", activate=True)

    name = prompt_store.ensure_default_version("proof_prompt", _prompt_tr)

    assert name == prompt_store.DEFAULT_VERSION
    names = [item["name"] for item in prompt_store.versions("proof_prompt")]
    assert names == ["自动-20260101-000000", "默认"], names
    assert prompt_store.read("proof_prompt", "默认") == "你是{lang}校对专家：{text}"
    assert prompt_store.active_name("proof_prompt") == "自动-20260101-000000", "不能改当前版本"


def test_default_prompt_texts_have_no_markdown():
    """默认提示词与说明文案里不该出现 markdown 星号（用户截图里显示成字面量）。"""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for locale in ("zh_CN", "en_US"):
        import json

        data = json.loads((root / f"src/assets/i18n/{locale}.json").read_text(encoding="utf-8"))
        for key, value in data.items():
            if key.startswith("prompt.") and isinstance(value, str):
                assert "**" not in value, f"{locale}/{key} 含 markdown 星号"
