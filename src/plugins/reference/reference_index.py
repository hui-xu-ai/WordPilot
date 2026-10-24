# plugins/reference/reference_index.py
"""参考文献派生索引（SQLite，可随时删除重建）。

CSV 是唯一事实源；这里只为「文档里 2000+ 条引用也能毫秒级查询」提供加速。
索引与源不一致（指纹变了）时自动重建，因此**永远不会**成为第二个事实源。
"""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:                       # 仅类型引用，避免运行期循环导入
    from plugins.reference.reference_store import Reference, ReferenceStore

logger = logging.getLogger(__name__)

INDEX_FILENAME = "index.db"
_SCHEMA = """
CREATE TABLE IF NOT EXISTS refs (
    ref_key   TEXT PRIMARY KEY,
    title     TEXT,
    authors   TEXT,
    year      TEXT,
    container TEXT,
    doi       TEXT,
    note      TEXT,
    starred   INTEGER,
    payload   TEXT
);
CREATE TABLE IF NOT EXISTS ref_aliases (
    alias   TEXT PRIMARY KEY,
    ref_key TEXT
);
CREATE TABLE IF NOT EXISTS anchors (
    doc_name   TEXT,
    cite_label TEXT,
    anchor_key TEXT,
    PRIMARY KEY (doc_name, cite_label)
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


class ReferenceIndex:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self._path = Path(db_path) if db_path is not None else None
        self._conn: sqlite3.Connection | None = None

    # ---------------- 连接与路径 ----------------
    @property
    def path(self) -> Path:
        if self._path is None:
            from core.services import config_manager

            self._path = Path(config_manager.CONFIG_DIR) / "references" / INDEX_FILENAME
        return self._path

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self.path))
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ---------------- 同步 ----------------
    def _read_meta(self, key: str) -> str | None:
        row = self._connect().execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def _write_meta(self, key: str, value: str) -> None:
        conn = self._connect()
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))
        conn.commit()

    def ensure(self, store: "ReferenceStore", *, force: bool = False) -> bool:
        """源指纹变化（或 force）时重建索引；返回是否重建。"""
        signature = store.source_signature()
        if not force and self._read_meta("signature") == signature:
            return False
        self.rebuild(store)
        return True

    def rebuild(self, store: "ReferenceStore") -> None:
        conn = self._connect()
        with conn:                                  # 单事务：要么全量换新，要么保持旧索引
            conn.execute("DELETE FROM refs")
            conn.execute("DELETE FROM ref_aliases")
            conn.execute("DELETE FROM anchors")
            refs = []
            aliases = []
            for ref in store.all():
                payload = json.dumps(ref.to_row(), ensure_ascii=False)
                refs.append((ref.ref_key, ref.title, ref.authors, ref.year, ref.container,
                             ref.doi, ref.note, 1 if ref.starred else 0, payload))
                aliases.extend((alias, ref.ref_key) for alias in ref.aliases if alias)
            conn.executemany(
                "INSERT OR REPLACE INTO refs"
                " (ref_key, title, authors, year, container, doi, note, starred, payload)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", refs)
            conn.executemany(
                "INSERT OR REPLACE INTO ref_aliases (alias, ref_key) VALUES (?, ?)", aliases)
            conn.executemany(
                "INSERT OR REPLACE INTO anchors (doc_name, cite_label, anchor_key) VALUES (?, ?, ?)",
                [(anchor.doc_name, anchor.cite_label, anchor.anchor_key)
                 for anchor in store.anchors()])
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('signature', ?)",
                         (store.source_signature(),))
        logger.debug("参考文献索引已重建：%d 条", len(refs))

    # ---------------- 查询 ----------------
    def resolve_key(self, key: str) -> str | None:
        conn = self._connect()
        row = conn.execute("SELECT ref_key FROM refs WHERE ref_key = ?", (key,)).fetchone()
        if row:
            return row[0]
        row = conn.execute("SELECT ref_key FROM ref_aliases WHERE alias = ?", (key,)).fetchone()
        return row[0] if row else None

    def get_payload(self, key: str) -> dict | None:
        resolved = self.resolve_key(key)
        if resolved is None:
            return None
        row = self._connect().execute("SELECT payload FROM refs WHERE ref_key = ?",
                                      (resolved,)).fetchone()
        return json.loads(row[0]) if row and row[0] else None

    def anchor_target(self, doc_name: str, cite_label: str) -> str | None:
        row = self._connect().execute(
            "SELECT anchor_key FROM anchors WHERE doc_name = ? AND cite_label = ?",
            (doc_name, cite_label)).fetchone()
        return row[0] if row else None

    def search(self, text: str, limit: int = 200) -> list[dict]:
        """在标题/作者/期刊/备注里做 LIKE 搜索（管理页用）。"""
        needle = f"%{(text or '').strip()}%"
        rows = self._connect().execute(
            "SELECT payload FROM refs"
            " WHERE title LIKE ? OR authors LIKE ? OR container LIKE ? OR note LIKE ?"
            " ORDER BY year DESC, ref_key LIMIT ?",
            (needle, needle, needle, needle, limit)).fetchall()
        return [json.loads(row[0]) for row in rows if row[0]]

    def count(self) -> int:
        row = self._connect().execute("SELECT COUNT(*) FROM refs").fetchone()
        return int(row[0]) if row else 0
