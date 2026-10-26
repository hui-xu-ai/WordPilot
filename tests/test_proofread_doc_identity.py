# tests/test_proofread_doc_identity.py
"""G1/G2：写回与标记都必须绑定"生成建议时的那份文档"。

审查复现：
- G1 在 A 取得建议、切到 B 后点桌宠"应用"，B 被改成 A 的建议；
  即使补传原文，子串放行仍会把"新增前缀 + 原句"整体替换；
- G2 A 等待结果时切到 B，A 的旧坐标把红色波浪线画到 B 上。
"""
from __future__ import annotations

import pytest


class _FakeDoc:
    def __init__(self, full_name: str, text: str):
        self.FullName = full_name
        self.text = text
        self.written: list[str] = []

    def Range(self, start, end):            # noqa: N802 - 模拟 COM
        doc = self

        class _Range:
            Start, End = start, end

            @property
            def Text(self):
                return doc.text[start:end]

            @Text.setter
            def Text(self, value):
                doc.written.append(value)

            def Duplicate(self):
                return _Range()

        return _Range()


class _FakeWord:
    def __init__(self, doc):
        self.ActiveDocument = doc
        self.Selection = type("S", (), {"Start": 0, "End": 0})()
        self.ScreenUpdating = True


class _Tokenized:
    def __init__(self, sentence_range):
        self.range = sentence_range

    def get_blocks(self):
        return [{"type": "text"}]

    pure_text = ""


def _plugin(monkeypatch, doc):
    from plugins.proofread import writeback as wb
    from plugins.proofread.plugin import ProofreadPlugin

    plugin = ProofreadPlugin.__new__(ProofreadPlugin)
    statuses: list[str] = []
    warnings: list[str] = []
    plugin._status = lambda text: statuses.append(text)
    plugin.tr = lambda key, **kw: key
    plugin._warn_stale_suggestion = lambda: warnings.append("stale")
    plugin._notify_manual_edit = lambda: None

    monkeypatch.setattr(wb, "get_word_app", lambda: _FakeWord(doc))
    monkeypatch.setattr(wb, "TokenizedSentence", _Tokenized)
    monkeypatch.setattr(wb, "clear_error_marks", lambda *a, **k: None)
    monkeypatch.setattr(wb, "QMessageBox", type("Q", (), {
        "warning": staticmethod(lambda *a, **k: warnings.append("warning")),
        "information": staticmethod(lambda *a, **k: None),
    }))
    return plugin, statuses, warnings


def test_refuses_when_document_switched(monkeypatch):
    """G1：当前文档不是生成建议时的文档 → 一条都不写。"""
    doc = _FakeDoc("C:/b/另一篇.docx", "完全不同的当前内容。")
    plugin, statuses, warnings = _plugin(monkeypatch, doc)

    ok = plugin.apply_correction("建议修改后的内容。", 0, 10,
                                 original_text="完全不同的当前内容。",
                                 doc_identity="C:/a/原文.docx")

    assert ok is False
    assert doc.written == [], "切文档后绝不能写"
    assert warnings, "应提示建议已过期"


def test_refuses_when_text_gained_a_prefix(monkeypatch):
    """G1：原句被加了前缀（不是等价）→ 拒绝，不能整体替换丢掉前缀。"""
    doc = _FakeDoc("C:/a/原文.docx", "新增前缀，这是原来的句子。")
    plugin, _statuses, warnings = _plugin(monkeypatch, doc)

    ok = plugin.apply_correction("这是修改后的句子。", 0, 14,
                                 original_text="这是原来的句子。",
                                 doc_identity="C:/a/原文.docx")

    assert ok is False
    assert doc.written == [], "不等的原文不能写回"
    assert warnings


def test_applies_when_document_and_text_match(monkeypatch):
    doc = _FakeDoc("C:/a/原文.docx", "这是原来的句子。")
    plugin, _statuses, warnings = _plugin(monkeypatch, doc)

    ok = plugin.apply_correction("这是修改后的句子。", 0, 8,
                                 original_text="这是原来的句子。",
                                 doc_identity="C:/a/原文.docx")

    assert ok is True
    assert doc.written, "等价且同一文档时应正常写回"
    assert not warnings
