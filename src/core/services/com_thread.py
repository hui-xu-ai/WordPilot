# core/services/com_thread.py
"""COM apartment 辅助：在非 GUI 线程（QThread）里附接 Word 前必须先初始化。

审查 S8：扫描线程直接 `get_word_app()` 报 `-2147221008 (CoInitialize has not been called)`。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def com_init() -> bool:
    """在工作线程里初始化 COM；返回是否由本次调用完成初始化。"""
    try:
        import pythoncom
    except Exception:                      # noqa: BLE001 - 非 Windows/未装 pywin32
        return False
    try:
        pythoncom.CoInitialize()
        return True
    except Exception:                      # noqa: BLE001 - 已初始化会抛错，忽略即可
        logger.debug("CoInitialize 失败或已初始化", exc_info=True)
        return False


def com_uninit(inited: bool) -> None:
    """与 com_init 配对；只有自己初始化成功时才 CoUninitialize。"""
    if not inited:
        return
    try:
        import pythoncom

        pythoncom.CoUninitialize()
    except Exception:                      # noqa: BLE001
        logger.debug("CoUninitialize 失败", exc_info=True)
