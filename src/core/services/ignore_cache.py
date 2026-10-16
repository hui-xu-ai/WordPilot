# ignore_cache.py
"""忽略句子库：以 sha256 哈希记录用户选择忽略的句子。"""
import hashlib
import json

from core.services.config_manager import CONFIG_DIR  # 配置文件路径

IGNORE_DB = CONFIG_DIR / "ignore_db.json"


class IgnoreCacheManager:
    def __init__(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        self.ignore_hashes = self._load_json(IGNORE_DB, [])

    def _load_json(self, path, default):
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"读取忽略库失败: {e}")
        return default

    def _save_json(self, path, data):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def is_ignored(self, sentence: str) -> bool:
        h = hashlib.sha256(sentence.encode()).hexdigest()
        return h in self.ignore_hashes

    def add_ignore(self, sentence: str):
        h = hashlib.sha256(sentence.encode()).hexdigest()
        if h not in self.ignore_hashes:
            self.ignore_hashes.append(h)
            self._save_json(IGNORE_DB, self.ignore_hashes)

    def remove_ignore(self, sentence: str):
        h = hashlib.sha256(sentence.encode()).hexdigest()
        if h in self.ignore_hashes:
            self.ignore_hashes.remove(h)
            self._save_json(IGNORE_DB, self.ignore_hashes)

    def clear_ignore(self):
        self.ignore_hashes.clear()
        if IGNORE_DB.exists():
            IGNORE_DB.unlink()
