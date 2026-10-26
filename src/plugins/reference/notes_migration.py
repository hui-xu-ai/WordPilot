# plugins/reference/notes_migration.py
"""把旧版"每库一份总库备注"（libraries/<库>/notes.csv）合并进全局总库（C1 迁移）。

旧文件改名成 `notes.csv.migrated` 保留，可人工回滚。
"""
from __future__ import annotations

import csv
import logging

logger = logging.getLogger(__name__)


def migrate_legacy_notes(libraries, master) -> int:
    """返回迁移的条数；任何异常都不应阻塞插件启动。"""
    try:
        libraries_dir = libraries.libraries_dir
    except Exception:                          # noqa: BLE001
        return 0
    if not libraries_dir.is_dir():
        return 0

    total = 0
    for legacy in libraries_dir.glob("*/notes.csv"):
        try:
            rows = legacy.read_text(encoding="utf-8-sig").splitlines()
        except OSError:
            continue
        if not rows:
            continue
        header = rows[0].split(",")
        if "ref_key" not in header or "text" not in header:
            continue
        added = 0
        for row in csv.DictReader(rows[1:]):
            ref_key = (row.get("ref_key") or "").strip()
            text = (row.get("text") or "").strip()
            if not ref_key or not text:
                continue
            if any(note.text.strip() == text for note in master.notes_for(ref_key)):
                continue
            master.add(ref_key, text, label=(row.get("label") or "").strip())
            added += 1
        if added:
            logger.info("已把 %d 条旧总库备注并入全局总库（%s）", added, legacy)
            total += added
        try:
            legacy.replace(legacy.with_suffix(".csv.migrated"))
        except OSError:
            logger.debug("旧 notes.csv 改名失败：%s", legacy, exc_info=True)
    return total
