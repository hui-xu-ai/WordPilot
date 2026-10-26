# plugins/reference/doc_bindings.py
"""文档 ↔ 文献库的绑定（"这份文档属于哪个库"）。

单独成文件：`plugin.py` 已到行数阈值，而这里是纯数据操作、便于单测。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def bind_doc(libraries, doc_path: str, library: str) -> bool:
    """把 doc_path 关联到 library；空参数或失败返回 False。"""
    if not doc_path or not library:
        return False
    try:
        libraries.bind_document(doc_path, library)
    except Exception as exc:                   # noqa: BLE001
        logger.warning("关联文档失败（%s → %s）：%s", doc_path, library, exc)
        return False
    return True
