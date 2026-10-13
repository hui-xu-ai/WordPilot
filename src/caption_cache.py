# caption_cache.py
import os
import sqlite3
import time

from config_manager import CONFIG_DIR

DB_PATH = CONFIG_DIR / "word_caption_cache.db"

class CaptionCache:
    def __init__(self):
        self.conn = None
        self._init_db()
        
        
    def _init_db(self):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        self.conn = sqlite3.connect(str(DB_PATH))
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT UNIQUE NOT NULL,
                file_size INTEGER NOT NULL,
                last_modified REAL NOT NULL,
                cache_time REAL NOT NULL,
                table_count INTEGER DEFAULT 0,
                figure_count INTEGER DEFAULT 0
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS captions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                doc_id INTEGER NOT NULL,
                type TEXT NOT NULL,
                text TEXT NOT NULL,
                page TEXT NOT NULL,
                range_start INTEGER NOT NULL,
                range_end INTEGER NOT NULL,
                order_index INTEGER NOT NULL,
                FOREIGN KEY (doc_id) REFERENCES documents(id) ON DELETE CASCADE
            )
        """)
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_captions_doc_order ON captions(doc_id, order_index)")
        self.conn.commit()

    def get_document_fingerprint(self, full_path):
        if not full_path:
            return None
        try:
            stat = os.stat(full_path)
            return (full_path, stat.st_size, stat.st_mtime)
        except OSError:
            return None

    def is_cache_valid(self, fingerprint):
        if not fingerprint:
            return False, None
        path, size, mtime = fingerprint
        cur = self.conn.execute(
            "SELECT id, file_size, last_modified FROM documents WHERE file_path = ?",
            (path,)
        )
        row = cur.fetchone()
        if row and row[1] == size and abs(row[2] - mtime) < 0.001:
            return True, row[0]
        return False, None

    def get_document_cache(self, fingerprint):      # 2026.05.13  初始扫描缓存，载入题注
        """根据指纹获取缓存的题注数据，若无效返回 None"""
        if not fingerprint:
            return None
        valid, doc_id = self.is_cache_valid(fingerprint)
        if valid:
            return self.load_captions(doc_id)
        return None
        
    def load_captions(self, doc_id):
        cur = self.conn.execute(
            "SELECT type, text, page, range_start, range_end FROM captions WHERE doc_id = ? ORDER BY order_index",
            (doc_id,)
        )
        return [{'type': r[0], 'text': r[1], 'page': r[2],
                 'start': r[3], 'end': r[4]} for r in cur.fetchall()]

    def save_document_cache(self, fingerprint, captions):
        if not fingerprint:
            return
        path, size, mtime = fingerprint
        self.conn.execute("DELETE FROM documents WHERE file_path = ?", (path,))
        self.conn.execute("INSERT INTO documents (file_path, file_size, last_modified, cache_time) VALUES (?,?,?,?)",
                          (path, size, mtime, time.time()))
        doc_id = self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        self.conn.executemany(
            "INSERT INTO captions (doc_id, type, text, page, range_start, range_end, order_index) VALUES (?,?,?,?,?,?,?)",
            [(doc_id, c['type'], c['text'], c['page'], c['start'], c['end'], i)
             for i, c in enumerate(captions)]
        )
        self.conn.commit()

    def close(self):
        if self.conn:
            self.conn.close()