# ignore_cache.py
import hashlib
import json

# from collections import OrderedDict       #   --修改18
from core.services.config_manager import CONFIG_DIR  # 配置文件路径

IGNORE_DB = CONFIG_DIR / "ignore_db.json"
# CACHE_DB = CONFIG_DIR / "cache_db.json"         #       功能冗余   被查找 api_calls.log 替代   --修改18

class IgnoreCacheManager:
    def __init__(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        self.ignore_hashes = self._load_json(IGNORE_DB, [])
        # self.result_cache = OrderedDict()         #--修改18
        # self.cache_max = 300        #  缓存上限      #  --修改18
        # self._load_cache()        # --修改18

    def _load_json(self, path, default):
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except:
                pass
        return default

    def _save_json(self, path, data):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # def _load_cache(self):        #   --修改18
        # if CACHE_DB.exists():
            # try:
                # with open(CACHE_DB, "r", encoding="utf-8") as f:
                    # items = json.load(f)
                    # for k, v in items[:self.cache_max]:
                        # self.result_cache[k] = v
            # except:
                # pass

    # def _save_cache(self):        #   --修改18
        # with open(CACHE_DB, "w", encoding="utf-8") as f:  
            # json.dump(list(self.result_cache.items())[-self.cache_max:], f, ensure_ascii=False)
    def _save_json(self, path, data):        #   --修改18
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

    # def get_cached_result(self, key: str):         #   --修改18     
        # return self.result_cache.get(key)

    # def set_cache_result(self, key: str, corrected: str):          #   --修改18
        # self.result_cache[key] = corrected
        # if len(self.result_cache) > self.cache_max:
            # self.result_cache.popitem(last=False)
        self._save_cache()

    # def clear_cache(self):                 #   --修改18
        # self.result_cache.clear()
        # if CACHE_DB.exists():
            # CACHE_DB.unlink()

    def clear_ignore(self):
        self.ignore_hashes.clear()
        if IGNORE_DB.exists():
            IGNORE_DB.unlink()

    # def clear_all(self):           #   --修改18
        # self.clear_cache()
        # self.clear_ignore()