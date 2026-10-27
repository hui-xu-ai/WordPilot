# tests/test_prompt_prefix_cache.py
"""前缀缓存友好的提示词结构：`{text}` 放末尾、system 稳定、变量不破碎前缀。

依据 DeepSeek 官方文档：上下文硬盘缓存自动生效，但**只有从第 0 个 token 起完全相同的前缀**
才算命中，且以 64 tokens 为存储单元（<64 不缓存）。所以要做的是"前缀稳定"，不是"实现缓存"。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SYSTEM_KEYS = {
    "prompt.proof.default": "prompt.proof.system",
    "prompt.correct.default": "prompt.correct.system",
    "prompt.translate.default": "prompt.translate.system",
    "prompt.rewrite.default": "prompt.rewrite.system",
}


def _i18n(locale: str = "zh_CN") -> dict:
    return json.loads((ROOT / f"src/assets/i18n/{locale}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("key", sorted(SYSTEM_KEYS))
def test_text_placeholder_is_at_the_end(key):
    """`{text}` 必须放在提示词末尾 —— 放在中间会让可变内容夹在稳定前缀里，缓存永远不命中。"""
    data = _i18n()
    template = data[key]
    position = template.find("{text}")

    assert position > 0, f"{key} 缺少 {{text}} 占位符"
    tail = template[position + len("{text}"):]
    assert len(tail.strip()) <= 12, f"{key} 的 {{text}} 后面还有 {len(tail)} 字符内容"


@pytest.mark.parametrize("key", sorted(SYSTEM_KEYS))
def test_system_message_is_stable_and_short(key):
    data = _i18n()
    system = data[SYSTEM_KEYS[key]]

    assert system.strip(), "system 不能为空"
    assert len(system) < 400, "system 应保持简短稳定，避免每次请求都变"


def test_prefix_is_long_enough_to_be_cacheable():
    """官方口径：以 64 tokens 为存储单元，<64 不缓存。中文约 1 token/字。"""
    data = _i18n()
    for key, system_key in SYSTEM_KEYS.items():
        prefix = data[system_key] + data[key].split("{text}")[0]
        assert len(prefix) >= 64, f"{key} 的稳定前缀只有 {len(prefix)} 字，可能不足 64 tokens"


def test_no_variable_content_before_prompt():
    """请求结构必须是 system → user(模板+文本)，中间不能插时间戳之类的变量。"""
    source = (ROOT / "src/core/services/api_client.py").read_text(encoding="utf-8")

    assert '"role": "system"' in source
    assert '"role": "user"' in source
    for marker in ("datetime.now()", "time.time()", "uuid"):
        for chunk_start in ("messages = [", "messages: list"):
            for match in [index for index in range(len(source))
                          if source.startswith(chunk_start, index)]:
                body = source[match:match + 600]
                assert marker not in body, f"请求前缀里出现变量 {marker}"


def test_monitor_shows_hit_rate():
    from core.widgets.api_monitor import ApiMonitorPanel

    assert ApiMonitorPanel._hit_rate({"prompt": 100, "cache_hit": 60}) == "60%"
    assert ApiMonitorPanel._hit_rate({"prompt": 0, "cache_hit": 0}) == "-"
