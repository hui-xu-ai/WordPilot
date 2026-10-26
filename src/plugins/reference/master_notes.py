# plugins/reference/master_notes.py
"""总库备注的维护动作（目前只有"删除总库"）。

单独成文件：`plugin.py` 已接近行数阈值，而这里只是文件与内存的清理。
"""
from __future__ import annotations

import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def clear_master(plugin) -> bool:
    """备份 notes.csv 后清空总库；返回是否成功。子库自己的备注不受影响。"""
    store = getattr(plugin, "_master_notes", None)
    if store is None:
        return False
    try:
        path = store.path
        if path.exists():
            backup = path.with_name(f"{path.name}.bak-{datetime.now():%Y%m%d-%H%M%S}")
            path.replace(backup)
            logger.info("总库备注已备份：%s", backup)
        store.reset()
    except Exception as exc:                   # noqa: BLE001 - 清理失败要能看见
        logger.warning("清理总库失败：%s", exc)
        return False

    plugin.refresh_page()
    overlay = getattr(plugin, "_overlay", None)
    if overlay is not None:
        overlay.refresh_notes()
    return True
