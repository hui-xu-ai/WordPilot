# plugins/reference/bib_import.py
"""点引用时把文末"参考文献"列表补进库（自动路径）。

从 plugin.py 抽出：那段逻辑与本模块的其他界面职责无关，也便于单测。
"""
from __future__ import annotations

import logging
import os

from plugins.reference.reference_store import ReferenceStoreError

logger = logging.getLogger(__name__)


def import_bibliography(plugin, doc_path: str, doc_name: str) -> None:
    """同一文档按 mtime 只做一次；**写失败不记缓存**（R3），下次还能重试。"""
    if not doc_path or not os.path.exists(doc_path):
        return
    try:
        mtime = os.path.getmtime(doc_path)
    except OSError:
        return
    if plugin._bib_cache.get(doc_path) == mtime:
        return
    # R1：自动导入同样先按文档绑定确定目标库，不能从"当前库"推断写入位置
    plugin._ensure_scan_library(doc_path)

    from plugins.reference.docx_scan import scan_docx

    try:
        result = scan_docx(doc_path, doc_name or os.path.basename(doc_path))
    except Exception as exc:                    # noqa: BLE001 - 解析失败不影响弹窗
        logger.warning("解析参考文献列表失败：%s", exc)
        return

    # 候选集必须和「同步本文」（plugin.scan_active_document）一致：文末列表 + **域里带元数据
    # 的引用**。只入文末列表是不够的 —— Zotero/EndNote 域里的条目（`zotero:906`、`doi:...`）
    # 若文末列表没有对应行，就永远进不了库，点引用只得到"命中引用但没有可显示卡片"，
    # 用户必须手动点一次「同步本文」才恢复（用户现场）。
    candidates = list(result.references)
    candidates.extend(citation.reference for citation in result.provider_citations
                      if citation.reference is not None)

    imported = 0
    failed = 0
    for reference in candidates:
        try:
            plugin._store.upsert(reference)     # 保留用户已有备注
            imported += 1
        except ReferenceStoreError as exc:
            failed += 1
            logger.warning("导入参考文献条目失败：%s", exc)
    if failed:
        logger.warning("本次有 %d 条导入失败，未标记为已完成（%s）", failed, doc_path)
        return
    plugin._bib_cache[doc_path] = mtime
    if imported:
        logger.info("从文末参考文献列表补入 %d 条元数据（%s）", imported,
                    doc_name or os.path.basename(doc_path))
