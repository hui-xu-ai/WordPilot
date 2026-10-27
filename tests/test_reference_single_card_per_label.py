# tests/test_reference_single_card_per_label.py
"""同一个编号只能出一张卡（用户现场：Zotero 的 `[1]` 弹了两张一模一样的卡）。

成因：域锚点的 key 是文献自身的键（`zotero:…` / `doi:…`），而"按角标文字补充"分支用的是
`doc:<文档>:cite:<编号>` —— 只按 key 去重，同一个编号就被加了第二遍。
"""
from __future__ import annotations

from plugins.reference.doc_index import DocAnchor


def _watch():
    from plugins.reference.word_watch import WordWatch

    return WordWatch()


def test_label_not_duplicated_when_anchor_already_covers_it(monkeypatch):
    from plugins.reference import word_watch as module

    watch = _watch()
    anchors = [DocAnchor(start=10, end=13, anchor_key="zotero:906", label="1", kind="zotero")]
    monkeypatch.setattr(module, "labels_at_position", lambda *a, **k: ["1"])

    payload = watch._anchor_payload(_FakeWord(), anchors, "论文.docx", position=10)

    labels = [item["label"] for item in payload["items"]]
    assert labels == ["1"], f"同一个编号不该出现两次，实际 {labels}"


def test_extra_label_still_added_when_anchor_only_has_first(monkeypatch):
    """域只给出 5、正文写着 `[5,6]` 时，6 仍要补上（原有能力不能丢）。"""
    from plugins.reference import word_watch as module

    watch = _watch()
    anchors = [DocAnchor(start=10, end=13, anchor_key="zotero:1", label="5", kind="zotero")]
    monkeypatch.setattr(module, "labels_at_position", lambda *a, **k: ["5", "6"])

    payload = watch._anchor_payload(_FakeWord(), anchors, "论文.docx", position=10)

    labels = [item["label"] for item in payload["items"]]
    assert labels == ["5", "6"], f"合并引用应得到两个编号，实际 {labels}"


class _FakeDocument:
    FullName = "C:/docs/论文.docx"


class _FakeWord:
    """`_anchor_payload` 只用到 ActiveDocument / Selection 取路径与屏幕坐标。"""

    ActiveDocument = _FakeDocument()
    Selection = type("S", (), {"Start": 10, "End": 13, "Text": "", "Range": None})()

    class Documents:                            # screen_point 里可能用到
        pass
