# tests/test_prompt_store_stale_defaults.py
"""config 里遗留的"旧版内置默认"要被清掉，用户自定义必须保留。

现场：`src/config/MWindow_config.json` 里存着 `按{style}风格改写以下文本，只输出改写后的内容：`
（早期"保存"把当时的默认文案写进了 config），config 优先级高 → 新版默认提示词永远不生效。
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def temp_store(tmp_path, monkeypatch):
    from core.services import prompt_store

    monkeypatch.setattr(prompt_store, "_config_dir", lambda: tmp_path)
    yield


class _Config:
    def __init__(self, data=None):
        self.data = dict(data or {})
        self.saved = 0

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value

    def save(self):
        self.saved += 1


def test_stale_builtin_defaults_are_cleared():
    from core.services import prompt_store

    config = _Config({
        "rewrite_prompt": "按{style}风格改写以下文本，只输出改写后的内容：\n{text}",
        "translate_prompt": "将以下文本翻译为{target_lang}，只输出译文：\n{text}",
        "proof_prompt": ("你是中文科技论文校对专家。修正错别字、语病、标点、专业词汇拼写。"
                         "校对所有单位的空格：数字与℃、%、°之间不加空格（例：2℃、5%、9°）；"
                         "数字与其他单位（如m）之间加半角空格（例：10 m）。"),
    })

    cleared = prompt_store.migrate_stale_defaults(config)

    assert cleared == 3
    assert all((config.data[key] or "") == "" for key in
               ("rewrite_prompt", "translate_prompt", "proof_prompt"))
    assert config.saved == 1


def test_user_customised_prompt_is_kept():
    from core.services import prompt_store

    mine = "我的改写规则：先拆长句，再加一个短句收尾。只输出正文：\n{text}"
    config = _Config({"rewrite_prompt": mine})

    cleared = prompt_store.migrate_stale_defaults(config)

    assert cleared == 0
    assert config.data["rewrite_prompt"] == mine
    assert config.saved == 0


def test_whitespace_differences_still_match():
    """旧文案在保存时可能被折叠空白 —— 归一化后比较仍要判定为"旧默认"。"""
    from core.services import prompt_store

    config = _Config({"translate_prompt":
                      "将以下文本翻译为 {target_lang}，只输出译文：   \n\n  {text}"})

    assert prompt_store.migrate_stale_defaults(config) == 1
    assert config.data["translate_prompt"] == ""


def test_rewrite_default_mentions_ai_reduction():
    """改写默认提示词必须覆盖降 AI 率的核心要求（长短句、去套路、保技术边界）。"""
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    data = json.loads((root / "src/assets/i18n/zh_CN.json").read_text(encoding="utf-8"))
    text = data["prompt.rewrite.default"]

    for keyword in ("降低 AI 生成痕迹", "句长", "排比", "技术内容", "{strength}"):
        assert keyword in text, f"改写默认提示词缺少：{keyword}"
