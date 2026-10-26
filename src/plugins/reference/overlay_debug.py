# plugins/reference/overlay_debug.py
"""引用命中但没有可显示内容时的诊断/提示。

独立成文件：`plugin.py` 已接近行数阈值，而这里只负责"告诉用户为什么没弹窗"。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def hint_missing_reference(plugin, items: list[dict]) -> None:
    """把 items 的关键信息记进日志，并在界面提示"库里没有这条"。"""
    summary = [
        f"{item.get('kind')}:{item.get('anchor_key')}:label={item.get('label')}"
        for item in (items or [])[:6]
    ]
    logger.info("命中引用但没有可显示卡片（库=%s，items=%s）",
                getattr(plugin, "_active_name", ""), summary)

    label = ""
    for item in items or []:
        label = str(item.get("label") or item.get("anchor_key") or "")
        if label:
            break
    text = plugin.tr("overlay.no_match", label=label or "?")
    page = getattr(plugin, "_page", None)
    if page is not None:
        try:
            page.append_status(text)
        except Exception:                  # noqa: BLE001
            pass
    try:
        plugin.ctx.ui.status(text, 3000)
    except Exception:                      # noqa: BLE001
        pass
