# tests/test_reference_document_pinning.py
"""重扫/读编号必须用**前台窗口那份文档**，不能用 `word.ActiveDocument`。

现场（用户实测）：两篇文档同时打开，只有 Word 认为的 ActiveDocument 那篇能弹窗；
另一篇的 `doc_scan.db` 里锚点写成了 **0**（扫描时读的是别人的域），点引用毫无反应。
真实链路复核：EndNote 那篇按其自身文档重扫有 19 个锚点，`lookup_all(53)` 命中编号 1。
"""
from __future__ import annotations


class _Result:
    def __init__(self, text: str):
        self.Text = text
        self.Start = 0
        self.End = 0


class _Code:
    Text = " ADDIN EN.CITE "


class _Field:
    def __init__(self, label: str, start: int):
        from plugins.reference.docx_scan import ScanCitation  # noqa: F401 - 确认可用

        self.Code = _Code()
        self.Result = type("R", (), {"Text": f"[{label}]", "Start": start, "End": start + len(label)})()


class _Fields:
    def __init__(self, items):
        self._items = items

    @property
    def Count(self):
        return len(self._items)

    def __call__(self, index):
        return self._items[index - 1]


class _Document:
    def __init__(self, name: str, labels=()):
        self.Name = name
        self.FullName = f"C:/docs/{name}"
        self.Fields = _Fields([_Field(label, 10 * (i + 1)) for i, label in enumerate(labels)])


class _Word:
    def __init__(self, active):
        self.ActiveDocument = active
        self.Windows = []


def test_rescan_uses_given_document_not_active_document():
    from plugins.reference.doc_index import DocIndex

    active = _Document("另一篇.docx")                  # 没有任何域
    target = _Document("目标.docx", labels=("1", "2"))
    index = DocIndex()

    count = index.rescan(_Word(active), document=target)

    assert count >= 2, f"应按传入文档扫描，实际 {count}"
    assert index.doc_name == "目标.docx"
    assert [anchor.label for anchor in index.lookup_all(20)] == ["2"]


def test_watch_tick_reads_the_foreground_document(monkeypatch):
    """ActiveDocument 停在另一篇时，tick 仍要按前台窗口那篇取文档/选区并发出命中。"""
    from plugins.reference import word_watch

    active = _Document("另一篇.docx")
    target = _Document("目标.docx", labels=("5",))
    window = type("W", (), {"Document": target, "Visible": True, "Hwnd": 1,
                            "Selection": None})()
    word = _Word(active)

    hits: list[dict] = []
    watch = word_watch.WordWatch()
    watch.citationFound.connect(hits.append)
    monkeypatch.setattr(word_watch, "_active_window", lambda _word: (window, "前台窗口"))

    selection = type("S", (), {"Start": 10, "End": 10, "Text": ""})()
    word.Selection = selection                     # 旧写法会读它 → 落在"另一篇"上
    watch._tick(lambda: word)
    watch._tick(lambda: word)

    labels = [item["label"] for payload in hits for item in payload.get("items", [])]
    assert "5" in labels, f"应按前台窗口那篇的域锚点命中编号 5，实际 {labels}"
    assert all(payload["doc_name"] == "目标.docx" for payload in hits)
