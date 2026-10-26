# plugins/proofread/context_window.py
"""上下文字窗与防抖间隔：把设置里的 context_before/after、debounce_ms 变成可用值。

抽出来单独放：`word_monitor.py` 已经接近文件行数阈值，而这些工具与线程无关。
"""
from __future__ import annotations

import hashlib


def debate_seconds(config) -> float:
    """debounce_ms → 秒（<=0 或非法值回退 200ms，上限 2s）。"""
    try:
        ms = int(config.get("debounce_ms", 200))
    except (TypeError, ValueError):
        ms = 200
    if ms <= 0:
        ms = 200
    return max(0.05, min(2.0, ms / 1000.0))


def surrounding_text(sentence_range, config) -> str:
    """按 context_before/context_after（字符数）取目标句前后的原文。"""
    try:
        before = max(0, int(config.get("context_before", 0) or 0))
        after = max(0, int(config.get("context_after", 0) or 0))
    except (TypeError, ValueError):
        return ""
    if not before and not after:
        return ""
    try:
        document = sentence_range.Document
        start = max(0, int(sentence_range.Start) - before)
        end = int(sentence_range.End) + after
        text = str(document.Range(start, end).Text or "")
    except Exception:                      # noqa: BLE001 - 取不到上下文不影响校对
        return ""
    return text.strip()


def context_cache_key(body_text: str, context_text: str) -> str:
    """上下文不同时不要复用同一份缓存结果。"""
    if not context_text:
        return body_text
    digest = hashlib.sha1(context_text.encode("utf-8")).hexdigest()[:8]
    return f"{body_text}#ctx{digest}"
