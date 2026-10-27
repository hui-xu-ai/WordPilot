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

    from plugins.reference.docx_scan import merge_provider_citations, scan_docx

    try:
        result = scan_docx(doc_path, doc_name or os.path.basename(doc_path))
    except Exception as exc:                    # noqa: BLE001 - 解析失败不影响弹窗
        logger.warning("解析参考文献列表失败：%s", exc)
        return

    # 与「同步本文」用同一条规则（见 docx_scan.merge_provider_citations）：
    # 文末列表为准，域元数据只补全同一篇，多出来的域引用不入库（避免库里堆无编号重复条目）。
    candidates, _filled, _ignored = merge_provider_citations(result)

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
    if imported == 0:
        # 什么都没进库就不能记"已完成"：否则这份文档以后再也不重试（旧待办 R3 的同类问题）。
        logger.info("本次没有可导入条目，未标记为已完成（%s）", doc_path)
        return
    plugin._bib_cache[doc_path] = mtime
    if imported:
        logger.info("从文末参考文献列表补入 %d 条元数据（%s）", imported,
                    doc_name or os.path.basename(doc_path))
