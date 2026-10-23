# ignore_cache.py
"""忽略句子库：以 sha256 哈希记录用户选择忽略的句子。

P10-B3：写入改原子（tmp + fsync + replace），读取失败先备份再回退空表——
历史实现"截断写 + print 后返回空表"，一次损坏或写中断就会把用户忽略库永久清空。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path

from core.services.config_manager import CONFIG_DIR  # 配置文件路径

logger = logging.getLogger(__name__)

IGNORE_DB = CONFIG_DIR / "ignore_db.json"


class IgnoreCacheManager:
    def __init__(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        self.ignore_hashes = self._load_json(IGNORE_DB, [])

    def _load_json(self, path, default):
        if not path.exists():
            return default
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                raise ValueError(f"顶层应为数组，实际 {type(data).__name__}")
            return data
        except Exception as exc:  # noqa: BLE001  解析/编码/结构都可能出错，统一进备份分支
            backup = self._backup_corrupt(path)
            logger.warning("忽略库读取失败（已备份到 %s）: %s", backup, exc)
            return default

    @staticmethod
    def _backup_corrupt(path: Path) -> str:
        """把损坏文件改名留档，避免下一次写入把现场覆盖掉。"""
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = path.with_name(f"{path.name}.bad-{stamp}")
        try:
            path.replace(target)
            return str(target)
        except OSError:
            logger.exception("忽略库备份失败: %s", path)
            return ""

    def _save_json(self, path: Path, data) -> None:
        """原子写：同目录临时文件 → fsync → os.replace（中途崩溃不会留下半截 JSON）。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_name, path)
        except Exception:
            logger.exception("忽略库写入失败: %s", path)
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

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
            try:
                IGNORE_DB.unlink()
            except OSError:
                logger.exception("忽略库删除失败: %s", IGNORE_DB)
