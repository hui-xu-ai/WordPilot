# tests/test_prompt_no_hardcoded_copies.py
"""提示词默认文案只能有一处来源（语言包的 `prompt.*.default`），代码里不许留副本。

用户要求（2026-10-27）：改默认提示词时，程序里硬编码的默认也要一起改。
实测发现 `settings.prompt.proof_placeholder` 里留着一份**旧版**校对提示词全文
（没人再引用它，纯留垃圾，且会误导后续维护）。此测试防止再出现同类硬编码副本。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

#: 旧版默认提示词的特征片段（出现在任何"文案"里都说明是过期副本）
LEGACY_SNIPPETS = (
    "中文科技论文校对专家",
    "按{style}风格改写以下文本",
    "将以下文本翻译为{target_lang}，只输出译文",
    "更流畅",
)

#: 允许保留旧文本的地方：迁移清单（用于识别用户 config 里的旧默认）
ALLOW_PATHS = {
    "core/services/prompt_store.py",          # LEGACY_DEFAULTS 迁移清单
}


def _iter_source_files():
    for path in SRC.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        yield path
    for path in (SRC / "assets").rglob("*.json"):
        yield path


@pytest.mark.parametrize("snippet", LEGACY_SNIPPETS)
def test_no_legacy_prompt_text_in_source(snippet):
    offenders = []
    for path in _iter_source_files():
        relative = path.relative_to(SRC).as_posix()
        if relative in ALLOW_PATHS:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if snippet in text:
            offenders.append(relative)
    assert not offenders, f"这些文件里还留着旧版提示词文案（{snippet}）：{offenders}"


def test_i18n_has_no_duplicated_prompt_defaults():
    """`settings.prompt.*_placeholder` 之类不该再复制一份默认提示词全文。"""
    for locale in ("zh_CN", "en_US"):
        data = json.loads((SRC / f"assets/i18n/{locale}.json").read_text(encoding="utf-8"))
        defaults = {value for key, value in data.items() if key.startswith("prompt.")}
        for key, value in data.items():
            if not key.startswith("settings.prompt."):
                continue
            if not isinstance(value, str):
                continue
            assert value not in defaults, f"{locale}/{key} 是默认提示词的重复副本"


def test_default_prompts_are_single_source_of_truth():
    """四个默认提示词都必须存在于语言包的 `prompt.*.default` 里。"""
    from core.services import prompt_store

    data = json.loads((SRC / "assets/i18n/zh_CN.json").read_text(encoding="utf-8"))
    for key in prompt_store.PROMPT_KEYS:
        i18n_key = prompt_store.default_keys(key)
        assert i18n_key in data, f"缺默认文案：{i18n_key}"
        assert re.search(r"\{text\}", data[i18n_key]), f"{i18n_key} 必须含 {{text}} 占位符"
