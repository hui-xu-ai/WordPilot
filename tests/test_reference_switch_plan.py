# tests/test_reference_switch_plan.py
"""切库决策：已绑定优先、自动切、提示、新库、留在原地。"""
from __future__ import annotations

from plugins.reference.switch_plan import KEEP, NEW, PROMPT, SWITCH, plan_switch


def _keys(count: int, prefix: str = "doi") -> set[str]:
    return {f"{prefix}:{i}" for i in range(count)}


def test_bound_document_switches_without_similarity():
    """用户手动改绑过 / 上次判定过 → 直接按绑定走，不算相似度（秒切）。"""
    plan = plan_switch(_keys(5), {"论文A": _keys(50, "x")},
                       current_library="默认库", bound_library="论文A")
    assert plan.action == SWITCH and plan.library == "论文A" and plan.score == 1.0


def test_bound_to_current_library_stays():
    plan = plan_switch(_keys(5), {}, current_library="论文A", bound_library="论文A")
    assert plan.action == KEEP


def test_high_similarity_switches_automatically():
    library = _keys(30) | {"doi:extra"}
    plan = plan_switch(_keys(30), {"论文A": library}, current_library="默认库")
    assert plan.action == SWITCH
    assert plan.library == "论文A"
    assert plan.score > 0.9
    assert "doi:extra" in plan.removed
    assert plan.added == []


def test_same_library_high_similarity_keeps():
    library = _keys(30)
    plan = plan_switch(_keys(30), {"论文A": library}, current_library="论文A")
    assert plan.action == KEEP


def test_middle_similarity_prompts():
    library = _keys(10)
    document = _keys(7) | {"doi:new1", "doi:new2", "doi:new3"}
    plan = plan_switch(document, {"论文A": library}, current_library="默认库")
    assert plan.action == PROMPT
    assert plan.library == "论文A"
    assert 0.5 <= plan.score < 0.9
    assert "doi:new1" in plan.added


def test_unrelated_document_suggests_new_library():
    plan = plan_switch({"doi:a", "doi:b"}, {"论文A": {"doi:x", "doi:y"}})
    assert plan.action == NEW


def test_no_libraries_means_new():
    assert plan_switch({"doi:a"}, {}) .action == NEW


def test_multiple_libraries_pick_best():
    snapshots = {"小库": {"doi:1", "doi:2"}, "大库": _keys(20)}
    document = _keys(20) | {"doi:1", "doi:2"}
    plan = plan_switch(document, snapshots)
    assert plan.library == "大库"
    assert plan.action == SWITCH
