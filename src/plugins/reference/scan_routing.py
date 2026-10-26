# plugins/reference/scan_routing.py
"""扫描前的仓库路由：决定这份文档该进哪个库、要不要为它新建一个库。

抽成独立模块的原因：`plugin.py` 已接近行数阈值，而这段是纯决策逻辑，
只依赖注册表与快照（便于单测）。
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from plugins.reference.switch_plan import NEW, PROMPT, SWITCH, plan_switch

logger = logging.getLogger(__name__)

_BAD_CHARS = re.compile(r'[\\/:*?"<>|]+')


def suggest_library_name(existing: list[str], doc_name: str) -> str:
    """用文档名当新仓库名（去扩展名与非法字符，重名自动加序号）。"""
    stem = Path(doc_name).stem if doc_name else "未命名文档"
    stem = _BAD_CHARS.sub("_", stem).strip() or "未命名文档"
    name = stem
    index = 2
    while name in existing:
        name = f"{stem} {index}"
        index += 1
    return name


def route_before_import(plugin, doc_path: str, doc_name: str,
                        keys: set[str], stats: dict) -> bool:
    """入库前把库切到"这份文档该去的仓库"，避免多个文档合并进同一个库。

    - 已绑定 → 切绑定库；
    - 与其它库高度相似 → 直接切；
    - 与任何现有库都不相似（含"只有当前库"）→ 按文档名新建一个库再导入；
    - 中等相似（要用户确认）→ **一条都不写**，把 plan 交给界面（R2）。

    返回 True 表示可以入库，False 表示应等用户确认。
    """
    bound = ""
    try:
        bound = plugin._libraries.doc_bindings().get(doc_path, "")
    except Exception:                          # noqa: BLE001 - 绑定读失败不阻塞扫描
        logger.debug("读取文档绑定失败", exc_info=True)

    plan = plan_switch(keys, plugin._library_snapshots(),
                       current_library=plugin._libraries.active_name(),
                       bound_library=bound)
    stats["switch"] = plan.action

    if plan.action == SWITCH and plan.library and plan.library != getattr(plugin, "_active_name", None):
        plugin.switch_library(plan.library)
        plugin._libraries.bind_document(doc_path, plan.library)
        stats["switch_text"] = plugin.tr("switch.auto", library=plan.library,
                                         percent=int(plan.score * 100))
        return True

    if plan.action == NEW:
        new_name = suggest_library_name(plugin._libraries.names(), doc_name)
        try:
            plugin._libraries.create(new_name)
            plugin.switch_library(new_name)
            plugin._libraries.bind_document(doc_path, new_name)
            stats["switch_text"] = plugin.tr("switch.created", name=new_name)
            return True
        except ValueError as exc:
            logger.warning("自动建库失败（%s）：%s", new_name, exc)
            return False

    if plan.action == PROMPT:
        # 相似度只是线索：确认前一条都不写，避免"先混进当前库再问用户"
        stats["plan"] = plan
        stats["pending"] = True
        return False

    return True
