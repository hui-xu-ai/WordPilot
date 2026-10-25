# plugins/reference/matching.py
"""按"引用快照"判断该用哪个文献仓库（纯函数 + 极简快照存储）。

思路（用户认可的方案）：
- 每个文档扫描后得到一个**引用集合**（`cite_key` 的集合：DOI / 标题哈希 / 脚注哈希）；
- 新文档与每个仓库里的**历史快照**做 Jaccard 相似度，取最高分；
- 三档决策：≥0.9 自动切换；0.5–0.9 提示用户确认；<0.5 视为新文档（建议新建仓库）；
- 只用快照比对（不是跟整库比对），避免"新论文恰好引用了大库里的少数文献"被误判。
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

AUTO_THRESHOLD = 0.9
PROMPT_THRESHOLD = 0.5
SNAPSHOT_FILE = "snapshots.json"
MAX_SNAPSHOTS = 40          # 每个仓库保留最近 N 个文档快照


@dataclass
class Match:
    """一次匹配结果。"""

    library: str
    score: float
    added: list[str]
    removed: list[str]
    decision: str              # "auto" | "prompt" | "new"

    @property
    def kept_count(self) -> int:
        return len(set(self.added) & set(self.removed)) if False else 0


def cite_key_set(references) -> set[str]:
    """把扫描结果收敛成身份集合（忽略编号，保证改名/重编号不影响判断）。"""
    keys: set[str] = set()
    for reference in references:
        key = getattr(reference, "ref_key", "") or ""
        if key:
            keys.add(key.lower())
    return keys


def jaccard(left: set[str], right: set[str]) -> float:
    """交集/并集：对"新论文引用同一批文献"天然不敏感（比分包含度稳）。"""
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def decide(score: float, auto: float = AUTO_THRESHOLD,
           prompt: float = PROMPT_THRESHOLD) -> str:
    if score >= auto:
        return "auto"
    if score >= prompt:
        return "prompt"
    return "new"


def diff(previous: set[str], current: set[str]) -> tuple[list[str], list[str]]:
    """对比两次快照：返回 (新增, 移除)。"""
    return sorted(current - previous), sorted(previous - current)


def best_match(document_keys: set[str],
               snapshots: dict[str, set[str]]) -> tuple[str, float, list[str], list[str]] | None:
    """在 {仓库名: 快照集合} 里找最相似的一个（同分按名字稳定排序）。"""
    best: tuple[str, float, list[str], list[str]] | None = None
    for library, keys in sorted(snapshots.items()):
        score = jaccard(document_keys, keys)
        if best is None or score > best[1]:
            added, removed = diff(keys, document_keys)
            best = (library, score, added, removed)
    return best


def match_library(document_keys: set[str], snapshots: dict[str, set[str]]) -> Match | None:
    best = best_match(document_keys, snapshots)
    if best is None:
        return None
    library, score, added, removed = best
    return Match(library=library, score=score, added=added, removed=removed,
                 decision=decide(score))


class SnapshotStore:
    """每个仓库一份 `snapshots.json`：{文档路径: {"keys": [...], "updated": 时间戳}}。"""

    def __init__(self, directory: str | Path) -> None:
        self._dir = Path(directory)

    @property
    def path(self) -> Path:
        return self._dir / SNAPSHOT_FILE

    def load(self) -> dict[str, dict]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception as exc:                 # noqa: BLE001 - 坏文件按空处理
            logger.warning("读取快照失败：%s", exc)
            return {}

    def save(self, data: dict[str, dict]) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        if len(data) > MAX_SNAPSHOTS:
            keep = sorted(data.items(), key=lambda item: item[1].get("updated", 0),
                          reverse=True)[:MAX_SNAPSHOTS]
            data = dict(keep)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def record(self, document_path: str, keys: set[str]) -> None:
        data = self.load()
        data[document_path] = {"keys": sorted(keys), "updated": time.time()}
        self.save(data)

    def library_snapshot(self) -> set[str]:
        """某个仓库里所有文档快照的并集（作为"这个库覆盖了哪些文献"）。"""
        merged: set[str] = set()
        for entry in self.load().values():
            merged.update(entry.get("keys", []))
        return merged
