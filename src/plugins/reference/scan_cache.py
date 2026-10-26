# plugins/reference/scan_cache.py
"""扫描结果缓存：文档没改动就不重复扫描（对齐题录导航助手的"预先识别"做法）。

键 = 文档路径 + 文件指纹（mtime_ns + size）。存两样东西：
- `anchors`：域锚点表（start/end/anchor_key/label/kind）⇒ 启动时直接装载，跳过 COM 遍历；
- `bibliography_done`：文末参考文献列表是否已导入 CSV（同文档不重复导入）。

文档一旦被编辑保存，指纹变化 ⇒ 自动重新扫描，不存在"用了旧数据"的风险。
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from plugins.reference.doc_index import DocAnchor

logger = logging.getLogger(__name__)

DB_NAME = "doc_scan.db"
_SCHEMA = """
CREATE TABLE IF NOT EXISTS doc_scan (
    path              TEXT PRIMARY KEY,
    mtime_ns          INTEGER,
    size              INTEGER,
    doc_name          TEXT,
    anchors           TEXT,
    bibliography_done INTEGER DEFAULT 0
);
"""


def fingerprint(path: str) -> tuple[int, int] | None:
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


class ScanCache:
    """按文档指纹存取扫描结果（SQLite，可随时删除重建）。"""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._path = Path(db_path) if db_path is not None else None
        self._conn: sqlite3.Connection | None = None
        # 连接生命周期跨线程：监听线程、扫描工作线程都会用（Qt 线程池不固定线程）
        self._lock = threading.RLock()

    @property
    def path(self) -> Path:
        if self._path is None:
            from core.services import config_manager

            self._path = Path(config_manager.CONFIG_DIR) / "references" / DB_NAME
        return self._path

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)
        return self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def _row(self, path: str) -> tuple | None:
        with self._lock:
            return self._connect().execute(
                "SELECT mtime_ns, size, doc_name, anchors, bibliography_done"
                " FROM doc_scan WHERE path = ?",
                (path,)).fetchone()

    # ---------------- 锚点 ----------------
    def load_anchors(self, path: str, fp: tuple[int, int] | None) -> list["DocAnchor"] | None:
        """指纹一致才返回缓存的锚点；不一致（或没缓存）返回 None。"""
        if not path or fp is None:
            return None
        row = self._row(path)
        if row is None or (row[0], row[1]) != fp:
            return None
        try:
            from plugins.reference.doc_index import DocAnchor

            return [DocAnchor(**item) for item in json.loads(row[3] or "[]")]
        except Exception as exc:             # noqa: BLE001 - 缓存坏了就当没缓存
            logger.warning("读取锚点缓存失败：%s", exc)
            return None

    def save_anchors(self, path: str, fp: tuple[int, int] | None, doc_name: str,
                     anchors: list["DocAnchor"]) -> None:
        if not path or fp is None:
            return
        payload = json.dumps([asdict(anchor) for anchor in anchors], ensure_ascii=False)
        row = self._row(path)
        # 文件变了（指纹不同）就不能再认为"参考文献列表已导入"（审查 S2）
        still_imported = 1 if row and (row[0], row[1]) == fp and row[4] else 0
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO doc_scan (path, mtime_ns, size, doc_name, anchors, bibliography_done)"
                " VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(path) DO UPDATE SET"
                " mtime_ns=excluded.mtime_ns, size=excluded.size, doc_name=excluded.doc_name,"
                " anchors=excluded.anchors, bibliography_done=excluded.bibliography_done",
                (path, fp[0], fp[1], doc_name, payload, still_imported))

    # ---------------- 参考文献列表是否已导入 ----------------
    def bibliography_done(self, path: str, fp: tuple[int, int] | None) -> bool:
        if not path or fp is None:
            return False
        row = self._row(path)
        return bool(row and (row[0], row[1]) == fp and row[4])

    def mark_bibliography(self, path: str, fp: tuple[int, int] | None, doc_name: str) -> None:
        if not path or fp is None:
            return
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO doc_scan (path, mtime_ns, size, doc_name, anchors, bibliography_done)"
                " VALUES (?, ?, ?, ?, '[]', 1)"
                " ON CONFLICT(path) DO UPDATE SET"
                " mtime_ns=excluded.mtime_ns, size=excluded.size, doc_name=excluded.doc_name,"
                " bibliography_done=1",
                (path, fp[0], fp[1], doc_name))
