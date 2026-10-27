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

#: 核心自己的资产（插件资产由插件用 `register_asset_source` 声明，见 `PluginBase.assets`）
PROTECTED_NAMES = {
    "user.qss",
}

#: 插件资产来源登记表：key（一般用 plugin_id）→ 返回资产路径的可调用对象
_ASSET_SOURCES: dict[str, object] = {}


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


def register_asset_source(key: str, source) -> None:
    """登记一份**用户资产**清单（由插件声明，清理时永不删除）。

    key 用插件 id，卸载/禁用插件时 `unregister_asset_source(key)` 撤销。
    `source` 是不带参数的函数，每次盘点时实时调用 —— 所以插件可以返回"当前所有库的
    references.csv"这类随状态变化的路径。
    """
    if not key or not callable(source):
        return
    _ASSET_SOURCES[key] = source
    logger.debug("登记资产来源：%s", key)


def unregister_asset_source(key: str) -> None:
    _ASSET_SOURCES.pop(key, None)


def asset_sources() -> list[str]:
    """当前已登记的资产来源（供界面/诊断查看）。"""
    return sorted(_ASSET_SOURCES)


def declared_assets(root: Path | None = None) -> set[Path]:
    """把各插件声明的资产展开成绝对路径集合（文件或目录）。"""
    root = Path(root) if root is not None else config_dir()
    assets: set[Path] = set()
    for key, source in list(_ASSET_SOURCES.items()):
        try:
            items = source() or ()
        except Exception as exc:                     # noqa: BLE001 - 插件给的东西出错不能影响清理
            logger.warning("读取资产清单失败（%s）：%s", key, exc)
            continue
        for item in items:
            if not item:
                continue
            path = Path(item)
            if not path.is_absolute():
                path = root / path
            assets.add(path)
    return assets


def _is_protected(path: Path, root: Path) -> bool:
    if path.name in PROTECTED_NAMES:
        return True
    # 插件声明的资产：文件本身或**目录下的所有内容**
    for asset in declared_assets(root):
        if path == asset or asset in path.parents:
            return True
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    # 保险线：references/ 下（非 _trash）的 csv/json 一律当资产 —— 即使某个插件忘了声明，
    # 也不会把用户的文献库/总库备注删掉（参考助手也会自己声明，见 ReferencePlugin.assets）。
    if relative.parts and relative.parts[0] == "references":
        if path.suffix in {".csv", ".json"} and "_trash" not in relative.parts:
            return True
    return False

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


def plugin_assets(root: Path | None = None) -> list[Path]:
    """`config/plugins/<插件>/` —— 插件的**资产文件夹**，整体永不清理。

    用户要求：资产按插件分文件夹管理，清理时按文件夹区分，避免误删或漏删。
    """
    base = (Path(root) if root is not None else config_dir()) / "plugins"
    if not base.exists():
        return []
    return [path for path in base.iterdir() if path.is_dir()]


def inventory(root: Path | None = None, keep_days: int = 0) -> list[Group]:
    """盘点各类内容（不删除任何东西）。`keep_days=0`（默认）表示回收站也全算进来。"""
    root = Path(root) if root is not None else config_dir()
    groups = {
        "logs": Group("logs", "运行日志", "safe", "app.log / errors.log / api_calls.log"),
        "wal": Group("wal", "数据库残留", "safe", "SQLite 的 -wal / -shm 残留"),
        "trash": Group("trash", "删除库的备份(_trash)", "careful",
                       "清空回收站会删掉所有已删仓库的备份" if keep_days <= 0
                       else f"超过 {keep_days} 天的旧备份"),
        "cache": Group("cache", "可重建缓存", "careful", "doc_scan / proof_cache / word_caption 缓存库"),
        "temp": Group("temp", "临时文件", "safe", "Word 锁文件 ~$*.docx 等"),
    }
    cutoff = time.time() - keep_days * 86400
    trash_root = root / "references" / "libraries" / "_trash"

    for path in _iter_files(root):
        if _is_protected(path, root):
            continue
        if any(asset in path.parents for asset in plugin_assets(root)):
            continue                       # 插件资产文件夹里的东西一律不清
        name, suffix = path.name, path.suffix.lower()
        # **回收区优先**：`_trash` 里已经是"删掉的库"，不管是什么后缀都归 trash ——
        # 否则 index.db-wal/-shm 会先被 wal 规则收走，"清空回收站"永远碰不到它们
        # （用户现场：点了清空，_trash 里还剩 6 个 -wal/-shm）。
        if trash_root in path.parents:
            try:
                # keep_days <= 0 表示"连新的备份也清"（界面上是"清空回收站"）
                if keep_days <= 0 or path.stat().st_mtime < cutoff:
                    groups["trash"].files.append(path)
            except OSError:
                continue
            continue
        if suffix == ".log":
            groups["logs"].files.append(path)
        elif suffix in {".db-wal", ".db-shm"}:
            # **只收孤儿**：属于在用数据库的 -wal/-shm 删掉会破坏正在使用它的连接，
            # 那部分空间靠 `checkpoint_wal()` 截断释放。
            base = path.with_name(path.name.rsplit("-", 1)[0])
            if not base.exists():
                groups["wal"].files.append(path)
        elif name in CACHE_DB_NAMES:
            groups["cache"].files.append(path)
        elif name.startswith("~$"):
            groups["temp"].files.append(path)          # Word 打开文档时留的锁文件
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


def _remove(path: Path, truncate_ok: bool) -> tuple[bool, int]:
    """删一个文件：能删就删；删不掉但允许截断（日志）就清空内容。返回 (是否处理, 释放字节)。"""
    try:
        size = path.stat().st_size
    except OSError:
        return False, 0
    try:
        path.unlink()
        return True, size
    except OSError as exc:                           # 被占用的文件（WinError 32）
        if not truncate_ok:
            logger.debug("跳过被占用的文件 %s：%s", path, exc)
            return False, 0
    try:
        with path.open("w", encoding="utf-8"):
            pass                                     # 截断：持有它的日志处理器会继续往空文件写
    except OSError as exc:
        logger.debug("截断失败 %s：%s", path, exc)
        return False, 0
    return True, size


#: 允许"截断代替删除"的组（日志被 logging 处理器占着时删不掉，但能清空）
TRUNCATABLE = {"logs"}


def clean(keys: list[str], root: Path | None = None, keep_days: int = 0,
          allow_truncate: bool = True) -> dict[str, tuple[int, int, int]]:
    """按组清理；返回 {组: (处理文件数, 释放字节, 被占用跳过数)}。资产与保护名单一律跳过。"""
    root = Path(root) if root is not None else config_dir()
    wanted = {key for key in keys}
    result: dict[str, tuple[int, int, int]] = {}
    skipped_all: dict[str, int] = {}
    for group in inventory(root, keep_days):
        if group.key not in wanted:
            continue
        removed = freed = skipped = 0
        trash_cutoff = None
        if group.key == "trash" and keep_days > 0:
            trash_cutoff = time.time() - keep_days * 86400
        for path in group.files:
            if _is_protected(path, root):
                continue
            if trash_cutoff is not None:
                try:
                    if path.stat().st_mtime >= trash_cutoff:
                        continue                     # 还新的备份先留着（可回滚）
                except OSError:
                    continue
            ok, size = _remove(path, truncate_ok=allow_truncate and group.key in TRUNCATABLE)
            if ok:
                removed += 1
                freed += size
            else:
                skipped += 1
        result[group.key] = (removed, freed, skipped)
        if skipped:
            skipped_all[group.key] = skipped
        if removed:
            logger.info("存储清理：%s 处理 %d 个文件，释放 %.1f KB（跳过 %d）",
                        group.title, removed, freed / 1024, skipped)
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


#: 待清理标记：被本进程占用、只能下次启动（DB 尚未打开时）再清的分组
PENDING_FILE = ".cleanup_pending.json"


def mark_pending(keys: list[str], root: Path | None = None) -> None:
    """把"这次删不掉"的分组记下来，下次启动时再清。"""
    import json

    root = Path(root) if root is not None else config_dir()
    path = root / PENDING_FILE
    pending: set[str] = set()
    try:
        if path.exists():
            pending = set(json.loads(path.read_text(encoding="utf-8")).get("groups") or [])
    except Exception:                    # noqa: BLE001 - 坏文件忽略
        pending = set()
    pending.update(keys)
    try:
        root.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"groups": sorted(pending)}, ensure_ascii=False),
                        encoding="utf-8")
        logger.info("已安排下次启动清理：%s", ", ".join(sorted(pending)))
    except OSError as exc:
        logger.warning("写入清理标记失败：%s", exc)


def run_pending(root: Path | None = None) -> dict[str, tuple[int, int, int]]:
    """启动时执行上次排队的清理（此时数据库还没打开，能真正删掉缓存库/-wal）。"""
    import json

    root = Path(root) if root is not None else config_dir()
    path = root / PENDING_FILE
    if not path.exists():
        return {}
    try:
        keys = list(json.loads(path.read_text(encoding="utf-8")).get("groups") or [])
    except Exception:                    # noqa: BLE001
        keys = []
    result = clean(keys, root=root, keep_days=0) if keys else {}
    try:
        path.unlink()
    except OSError:
        pass
    return result


def summary(root: Path | None = None, keep_days: int = 0) -> str:
    """一行可读摘要（供界面显示）。"""
    groups = inventory(root, keep_days)
    parts = [f"{group.title} {group.size / 1024:.0f}KB/{group.count}个" for group in groups]
    return " · ".join(parts)
