"""plan_switch：把"该不该切仓库"的决策收成纯函数（插件只负责执行与提示）。

输入：
- `document_keys`：当前文档的引用集合；
- `library_snapshots`：{仓库名: 该库所有历史快照的并集}；
- `current_library`：当前激活仓库；
- `bound_library`：该文档路径此前绑定的仓库（用户手动改绑或上次自动判定，优先于相似度）。

输出 `Plan`：
- `stay`：不动；
- `switch`：直接切到 `library`（≥0.9 或已绑定）；
- `prompt`：疑似同源，需要用户确认（0.5–0.9）；
- `new`：与现有仓库几乎无重叠，建议新建仓库。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from plugins.reference.matching import decide, jaccard

KEEP = "stay"
SWITCH = "switch"
PROMPT = "prompt"
NEW = "new"


@dataclass
class Plan:
    action: str
    library: str = ""
    score: float = 0.0
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)


def plan_switch(document_keys: set[str], library_snapshots: dict[str, set[str]],
                current_library: str = "", bound_library: str = "") -> Plan:
    if bound_library:
        if bound_library == current_library:
            return Plan(KEEP, library=bound_library, score=1.0)
        return Plan(SWITCH, library=bound_library, score=1.0)

    if not library_snapshots:
        return Plan(NEW, score=0.0)

    best_library = ""
    best_score = -1.0
    for library, keys in sorted(library_snapshots.items()):
        score = jaccard(document_keys, keys)
        if score > best_score:
            best_library, best_score = library, score

    if best_score <= 0:
        return Plan(NEW, score=0.0)

    decision = decide(best_score)
    added = sorted(document_keys - library_snapshots.get(best_library, set()))
    removed = sorted(library_snapshots.get(best_library, set()) - document_keys)

    if decision == "auto":
        if best_library == current_library:
            return Plan(KEEP, library=best_library, score=best_score, added=added, removed=removed)
        return Plan(SWITCH, library=best_library, score=best_score, added=added, removed=removed)
    if decision == "prompt":
        return Plan(PROMPT, library=best_library, score=best_score, added=added, removed=removed)
    return Plan(NEW, library=best_library, score=best_score, added=added, removed=removed)
