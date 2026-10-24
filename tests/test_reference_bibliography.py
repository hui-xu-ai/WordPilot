# tests/test_reference_bibliography.py
"""文末"参考文献"列表解析：EndNote 引用域是空壳时，靠它补元数据。

样例取自用户真实截图（GB/T 7714 风格）：
    [1] Wang D P, Zhao S F, Li L L, et al. All-Flexible Artificial Reflex Arc Based on
        Threshold-Switching Memristor[J]. Advanced Functional Materials, 2022, 32(21).
"""
from __future__ import annotations

import pytest

from plugins.reference.bibliography import (find_heading_index, parse_bibliography,
                                            parse_entry)

SAMPLE = [
    "参考文献",
    "[1] Wang D P, Zhao S F, Li L L, et al. All-Flexible Artificial Reflex Arc Based on "
    "Threshold-Switching Memristor[J]. Advanced Functional Materials, 2022, 32(21).",
    "[2] 李明, 张华. 离子液体驱动器的制备与表征[J]. 中国科学, 2019, 49(3): 123-130.",
    "[3] Smith J. A book about robots[M]. Berlin: Springer, 2001.",
]


def test_parse_single_entry_journal():
    reference = parse_entry(
        "[1] Wang D P, Zhao S F, Li L L, et al. All-Flexible Artificial Reflex Arc Based on "
        "Threshold-Switching Memristor[J]. Advanced Functional Materials, 2022, 32(21).",
        "1", "论文.docx")
    assert reference is not None
    assert reference.ref_key.startswith("bib:")
    assert reference.title.startswith("All-Flexible Artificial Reflex Arc")
    assert "Wang D P" in reference.authors
    assert reference.container == "Advanced Functional Materials"
    assert reference.year == "2022"
    assert "doc:论文.docx:cite:1" in reference.aliases


def test_parse_chinese_entry():
    reference = parse_entry("[2] 李明, 张华. 离子液体驱动器的制备与表征[J]. 中国科学, 2019, 49(3): 123-130.",
                            "2", "论文.docx")
    assert reference is not None
    assert reference.title == "离子液体驱动器的制备与表征"
    assert reference.container == "中国科学"
    assert reference.year == "2019"


def test_same_title_across_documents_shares_identity():
    """不同论文引用同一篇文献 → 身份键相同（备注可复用），各自保留自己的文档别名。"""
    first = parse_entry("[1] Wang D P. Memristor arc[J]. Nature, 2022, 1(1).", "1", "论文A.docx")
    second = parse_entry("[7] Wang D P. Memristor arc[J]. Nature, 2022, 1(1).", "7", "论文B.docx")
    assert first is not None and second is not None
    assert first.ref_key == second.ref_key
    assert "doc:论文A.docx:cite:1" in first.aliases
    assert "doc:论文B.docx:cite:7" in second.aliases


def test_parse_bibliography_joins_continuation_lines():
    references = parse_bibliography([
        "参考文献",
        "[1] Wang D P, Zhao S F. All-Flexible Artificial Reflex Arc Based on",
        "Threshold-Switching Memristor[J]. Advanced Functional Materials, 2022, 32(21).",
        "[2] 李明. 离子液体驱动器[J]. 中国科学, 2019.",
    ], "论文.docx")

    assert [ref.provider_id for ref in references] == ["1", "2"]
    assert references[0].title.startswith("All-Flexible")
    assert references[0].container == "Advanced Functional Materials"
    assert references[1].title == "离子液体驱动器"


def test_parse_bibliography_ignores_non_entries():
    references = parse_bibliography(["参考文献", "这一段不是条目，只是说明文字。"], "论文.docx")
    assert references == []


def test_find_heading_index():
    assert find_heading_index(SAMPLE) == 1
    assert find_heading_index(["正文", "References", "[1] x"]) == 2
    assert find_heading_index(["正文", "没有标题"]) is None


def test_empty_entry_returns_none():
    assert parse_entry("[]", "1", "论文.docx") is None
    assert parse_entry("[1] 短", "1", "论文.docx") is None
