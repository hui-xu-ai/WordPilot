# tests/test_reference_matching.py
"""按引用快照判断该用哪个仓库：Jaccard + 三档决策 + 快照存储。

关键场景（用户提过的）：
- 同一文档增删几篇 → 仍应判为同一仓库（auto）；
- 复制文档改几篇 → auto；
- 毫不相关的文档 → new；
- 新论文恰好引用某大库里的少数文献 → 不应被误判（Jaccard 低）。
"""
from __future__ import annotations

import pytest

from plugins.reference.matching import (AUTO_THRESHOLD, PROMPT_THRESHOLD, SnapshotStore,
                                        best_match, cite_key_set, decide, diff, jaccard,
                                        match_library)


def _refs(*keys: str):
    return [type("R", (), {"ref_key": key})() for key in keys]


def test_cite_key_set_ignores_labels_and_case():
    keys = cite_key_set(_refs("doi:10.1/A", "doc:x:cite:3", ""))
    assert keys == {"doi:10.1/a", "doc:x:cite:3"}


def test_jaccard_basics():
    assert jaccard({"a", "b"}, {"a", "b"}) == 1.0
    assert jaccard({"a", "b"}, {"b", "c"}) == pytest.approx(1 / 3)
    assert jaccard(set(), {"a"}) == 0.0


def test_same_document_with_edits_stays_auto():
    previous = {f"doi:{i}" for i in range(20)}
    current = set(previous) | {"doi:new1", "doi:new2"}       # 加了两篇
    score = jaccard(current, previous)
    assert score >= AUTO_THRESHOLD
    assert decide(score) == "auto"


def test_document_with_a_few_removed_is_auto():
    previous = {f"doi:{i}" for i in range(20)}
    current = set(list(previous)[:18])
    assert decide(jaccard(current, previous)) == "auto"


def test_unrelated_document_is_new():
    assert decide(jaccard({"doi:a", "doi:b"}, {"doi:c", "doi:d"})) == "new"


def test_new_paper_citing_few_items_from_a_big_library_is_not_matched():
    """新论文只引了大库里的 3 篇：包含度会误判，Jaccard 不会。"""
    big_library = {f"doi:{i}" for i in range(50)} | {"doi:x", "doi:y", "doi:z"}
    new_paper = {"doi:x", "doi:y", "doi:z"}
    assert decide(jaccard(new_paper, big_library)) == "new"


def test_middle_zone_prompts_user():
    previous = {f"doi:{i}" for i in range(10)}
    current = {f"doi:{i}" for i in range(7)} | {f"doi:new{i}" for i in range(3)}
    score = jaccard(current, previous)
    assert PROMPT_THRESHOLD <= score < AUTO_THRESHOLD
    assert decide(score) == "prompt"


def test_diff_reports_added_and_removed():
    added, removed = diff({"a", "b", "c"}, {"b", "c", "d"})
    assert added == ["d"] and removed == ["a"]


def test_best_match_picks_highest_and_reports_diff():
    snapshots = {
        "论文A": {f"doi:{i}" for i in range(10)},
        "论文B": {"doi:zz", "doi:yy"},
    }
    current = {f"doi:{i}" for i in range(10)} | {"doi:new"}   # 只加一篇：10/11 ≈ 0.91
    match = match_library(current, snapshots)
    assert match is not None
    assert match.library == "论文A"
    assert match.decision == "auto"
    assert match.added == ["doi:new"]
    assert match.removed == []


def test_best_match_empty_snapshots():
    assert best_match({"doi:a"}, {}) is None
    assert match_library({"doi:a"}, {}) is None


def test_snapshot_store_roundtrip_and_union(tmp_path):
    store = SnapshotStore(tmp_path)
    store.record("C:/a.docx", {"doi:1", "doi:2"})
    store.record("C:/b.docx", {"doi:2", "doi:3"})

    assert SnapshotStore(tmp_path).load()["C:/a.docx"]["keys"] == ["doi:1", "doi:2"]
    assert store.library_snapshot() == {"doi:1", "doi:2", "doi:3"}


def test_snapshot_store_trims_old_entries(tmp_path):
    from plugins.reference.matching import MAX_SNAPSHOTS

    store = SnapshotStore(tmp_path)
    data = {f"doc{i}": {"keys": [], "updated": i} for i in range(MAX_SNAPSHOTS + 10)}
    store.save(data)
    saved = store.load()
    assert len(saved) == MAX_SNAPSHOTS
    assert f"doc{MAX_SNAPSHOTS + 9}" in saved, "保留最近更新的"


def test_snapshot_store_survives_corrupt_file(tmp_path):
    (tmp_path / "snapshots.json").write_text("not json", encoding="utf-8")
    assert SnapshotStore(tmp_path).load() == {}
