# core/services/proof_cache.py
"""校对结果缓存（SQLite）。

独立于 api_calls.log：
- 日志只负责「记录」，缓存只负责「命中」，清空日志不再连带清缓存；
- 缓存键统一为「送入校对逻辑的正文文本」（body_text），
  proof_sentence 与剪贴板校对两条路径共享同一份缓存，不再重复调 API；
- O(1) 主键查询，替代原来每次线性扫描日志文件末尾 N 行的做法。
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime

from core.services.config_manager import CONFIG_DIR

DB_FILE = CONFIG_DIR / "proof_cache.db"


class ProofCache:
    """线程安全的校对缓存。key 为校对正文文本，value 为 AI 返回的校对结果。"""

    def __init__(self, db_path=DB_FILE) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS proof_cache (
                   key        TEXT PRIMARY KEY,
                   output     TEXT NOT NULL,
                   model      TEXT DEFAULT '',
                   created_at TEXT NOT NULL
               )"""
        )
        self._conn.commit()

    def get(self, key: str) -> str | None:
        """命中返回校对结果，未命中返回 None。"""
        if not key:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT output FROM proof_cache WHERE key = ?", (key,)
            ).fetchone()
        return row[0] if row else None

    def put(self, key: str, output: str, model: str = "") -> None:
        """写入/覆盖一条缓存。空结果不缓存（失败响应不值得复用）。"""
        if not key or not output:
            return
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO proof_cache (key, output, model, created_at)"
                " VALUES (?, ?, ?, ?)",
                (key, output, model, datetime.now().isoformat(timespec="seconds")),
            )
            self._conn.commit()

    def clear(self) -> int:
        """清空缓存，返回删除条数。"""
        with self._lock:
            count = self._conn.execute("SELECT COUNT(*) FROM proof_cache").fetchone()[0]
            self._conn.execute("DELETE FROM proof_cache")
            self._conn.commit()
        return count

    def count(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM proof_cache").fetchone()[0]

    def close(self) -> None:
        with self._lock:
            self._conn.close()


_default_cache: ProofCache | None = None
_default_lock = threading.Lock()


def get_default_cache() -> ProofCache:
    """进程级共享缓存实例（内核服务与 api_client / 插件共用同一份连接）。"""
    global _default_cache
    with _default_lock:
        if _default_cache is None:
            _default_cache = ProofCache()
        return _default_cache
