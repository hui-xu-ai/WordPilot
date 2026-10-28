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
    status_key = "overlay.no_match"
    status_params = {"label": label or "?"}
    text = plugin.tr(status_key, **status_params)
    page = getattr(plugin, "_page", None)
    if page is not None:
        try:
            page.append_status(status_key, **status_params)   # 存 key，切语言可重画
        except Exception:                  # noqa: BLE001
            pass
    try:
        plugin.ctx.ui.status(text, 3000)
    except Exception:                      # noqa: BLE001
        pass
