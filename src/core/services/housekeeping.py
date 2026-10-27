# core/services/housekeeping.py
"""存储清理：区分**用户资产**与**运行垃圾**，只清理后者。

用户要求（2026-10-27）：主程序缺少垃圾清理功能，且不能碰用户资产。
分类口径：
- 资产（永不删）：API key、配置文件、`references/libraries/<库>/references.csv`、总库 `notes.csv`、
  文档绑定、`Image.docx`（图片工具箱的工作文档）、`user.qss`；
- 运行垃圾（可清）：`app.log`/`errors.log`/`api_calls.log`、SQLite 的 `-wal`/`-shm` 残留、
  `_trash` 里删除库留下的备份、可重建的缓存库（`doc_scan.db`/`proof_cache.db`/`word_caption_cache.db`）。
"""
from __future__ import annotations

import logging
import os
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

#: 回收站里的删除备份默认保留天数（谨慎组）
TRASH_KEEP_DAYS = 7

#: 可重建的缓存库（删了会自动重建，代价是重扫/重算）
CACHE_DB_NAMES = {"doc_scan.db", "proof_cache.db", "word_caption_cache.db"}

#: 资产白名单：这些名字**永远**不删（防止误伤用户数据）
PROTECTED_NAMES = {
    "references.csv",       # 子库文献本体
    "notes.csv",            # 总库备注
    "doc_bindings.json", "libraries.json",
    "Image.docx", "user.qss",
}


@dataclass
class Group:
    """一类可清理的内容。"""

    key: str
    title: str
    safety: str                     # safe | careful
    detail: str
    files: list[Path] = field(default_factory=list)

    @property
    def size(self) -> int:
        total = 0
        for path in self.files:
            try:
                total += path.stat().st_size
            except OSError:
                continue
        return total

    @property
    def count(self) -> int:
        return len(self.files)


def config_dir() -> Path:
    from core.services.config_manager import CONFIG_DIR

    return Path(CONFIG_DIR)


def _iter_files(root: Path):
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            yield Path(dirpath) / name


def _is_protected(path: Path, root: Path) -> bool:
    if path.name in PROTECTED_NAMES:
        return True
    # 子库本体与总库备注所在目录里的 csv/json 一律视为资产
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    if relative.parts and relative.parts[0] == "references":
        if path.suffix in {".csv", ".json"} and "_trash" not in relative.parts:
            return True
    return False


def inventory(root: Path | None = None, keep_days: int = TRASH_KEEP_DAYS) -> list[Group]:
    """盘点各类内容（不删除任何东西）。"""
    root = Path(root) if root is not None else config_dir()
    groups = {
        "logs": Group("logs", "运行日志", "safe", "app.log / errors.log / api_calls.log"),
        "wal": Group("wal", "数据库残留", "safe", "SQLite 的 -wal / -shm 残留"),
        "trash": Group("trash", "删除库的备份(_trash)", "careful",
                       f"超过 {keep_days} 天的旧备份"),
        "cache": Group("cache", "可重建缓存", "careful", "doc_scan / proof_cache / word_caption 缓存库"),
    }
    cutoff = time.time() - keep_days * 86400
    trash_root = root / "references" / "libraries" / "_trash"

    for path in _iter_files(root):
        if _is_protected(path, root):
            continue
        name, suffix = path.name, path.suffix.lower()
        if suffix == ".log":
            groups["logs"].files.append(path)
        elif suffix in {".db-wal", ".db-shm"}:
            groups["wal"].files.append(path)
        elif name in CACHE_DB_NAMES:
            groups["cache"].files.append(path)
        elif trash_root in path.parents:
            try:
                # keep_days <= 0 表示"连新的备份也清"（界面上是"清空回收站"）
                if keep_days <= 0 or path.stat().st_mtime < cutoff:
                    groups["trash"].files.append(path)
            except OSError:
                continue
    return list(groups.values())


def checkpoint_wal(root: Path | None = None) -> tuple[int, int]:
    """把各数据库的 WAL 落盘并截断（比直接删 -wal 安全）。

    返回 (处理库数, 释放字节)。应用正在运行时也能做 —— SQLite 会自己处理并发。
    """
    root = Path(root) if root is not None else config_dir()
    freed = 0
    handled = 0
    for path in _iter_files(root):
        if path.suffix.lower() != ".db" or _is_protected(path, root):
            continue
        wal = path.with_name(path.name + "-wal")
        try:
            before = wal.stat().st_size if wal.exists() else 0
        except OSError:
            before = 0
        try:
            with sqlite3.connect(str(path), timeout=2) as conn:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            handled += 1
        except Exception as exc:                     # noqa: BLE001 - 单个库失败不影响其它
            logger.debug("WAL 截断失败（%s）：%s", path.name, exc)
            continue
        try:
            after = wal.stat().st_size if wal.exists() else 0
        except OSError:
            after = 0
        freed += max(0, before - after)
    return handled, freed


def clean(keys: list[str], root: Path | None = None,
          keep_days: int = TRASH_KEEP_DAYS) -> dict[str, tuple[int, int]]:
    """按组清理；返回 {组: (删除文件数, 释放字节)}。资产与保护名单一律跳过。"""
    root = Path(root) if root is not None else config_dir()
    wanted = {key for key in keys}
    result: dict[str, tuple[int, int]] = {}
    for group in inventory(root, keep_days):
        if group.key not in wanted:
            continue
        removed = freed = 0
        for path in group.files:
            if _is_protected(path, root):
                continue
            try:
                size = path.stat().st_size
                path.unlink()
            except OSError as exc:                   # 文件被占用等：跳过，不报错中断
                logger.debug("跳过无法删除的文件 %s：%s", path, exc)
                continue
            removed += 1
            freed += size
        result[group.key] = (removed, freed)
        if removed:
            logger.info("存储清理：%s 删除 %d 个文件，释放 %.1f KB",
                        group.title, removed, freed / 1024)
    # 回收站目录清空后顺手删掉空目录（保留 _trash 本身）
    trash_root = root / "references" / "libraries" / "_trash"
    if trash_root.exists() and "trash" in wanted:
        for dirpath, dirnames, filenames in os.walk(trash_root, topdown=False):
            path = Path(dirpath)
            if path == trash_root:
                continue
            try:
                if not filenames and not dirnames:
                    path.rmdir()
            except OSError:
                continue
    return result


def summary(root: Path | None = None, keep_days: int = TRASH_KEEP_DAYS) -> str:
    """一行可读摘要（供界面显示）。"""
    groups = inventory(root, keep_days)
    parts = [f"{group.title} {group.size / 1024:.0f}KB/{group.count}个" for group in groups]
    return " · ".join(parts)
