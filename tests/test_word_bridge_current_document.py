# tests/test_word_bridge_current_document.py
"""`current_document()` 是"用户正在看哪篇"的唯一出口。

2026-11-13 现场：`word.Windows` 里有一个**取就抛 COM 错的幽灵条目**，`visible_windows()`
用列表推导遍历时整条中断返回 `[]`，前台窗口机制随之失效，于是退回 `ActiveDocument`
—— 而图片工具箱隐藏打开的 `Image.docx` 正好占着它：题注导航扫出"共 0 个题注"、
参考助手按 Image.docx 建了 0 个锚点的索引（app.log 实测）。
"""
from __future__ import annotations

from core.services import word_bridge


class _Document:
    def __init__(self, name: str, full_name: str):
        self.Name = name
        self.FullName = full_name
        self.activated = 0

    def Activate(self):                 # noqa: N802 - Word COM 命名
        self.activated += 1


class _Window:
    def __init__(self, hwnd: int, document: _Document, visible: bool = True):
        self.Hwnd = hwnd
        self.Document = document
        self.Visible = visible


class _WindowsCollection:
    """仿 Word COM 窗口集合：1-based 下标 + `Count`，可指定"取就抛错"的幽灵下标。"""

    def __init__(self, items, broken=()):
        self._items = list(items)
        self._broken = set(broken)

    @property
    def Count(self):                    # noqa: N802 - Word COM 命名
        return len(self._items)

    def __call__(self, index: int):
        if index in self._broken:
            raise RuntimeError("幽灵窗口：取这一项就抛 COM 错")
        return self._items[index - 1]


class _Word:
    def __init__(self, windows, active_index: int, broken=()):
        self.Windows = _WindowsCollection(windows, broken=broken)
        self._active = windows[active_index]

    @property
    def ActiveWindow(self):             # noqa: N802
        return self._active

    @property
    def ActiveDocument(self):           # noqa: N802
        return self._active.Document


def _quiet(monkeypatch):
    """不依赖真实前台窗口/鼠标；清空工作文档登记，避免用例之间串味。"""
    monkeypatch.setattr(word_bridge, "foreground_hwnd", lambda: 0)
    monkeypatch.setattr(word_bridge, "cursor_hwnd", lambda: 0)
    monkeypatch.setattr(word_bridge, "_work_documents", set())


def test_phantom_window_does_not_kill_the_window_list(monkeypatch):
    _quiet(monkeypatch)
    user = _Window(222, _Document("论文.docx", "C:/docs/论文.docx"))
    phantom = _Window(999, _Document("幽灵.docx", "C:/docs/幽灵.docx"))
    word = _Word([user, phantom], active_index=0, broken=(2,))

    assert word_bridge.visible_windows(word) == [user], \
        "幽灵条目只该跳过自己，不能把整份可见窗口列表打空"
    document, _source = word_bridge.current_document_with_source(word=word)
    assert document is user.Document


def test_phantom_window_keeps_foreground_matching(monkeypatch):
    """幽灵条目在场时，前台窗口匹配仍要生效（这正是"认错文档"的直接成因）。"""
    _quiet(monkeypatch)
    old = _Window(111, _Document("旧.docx", "C:/docs/旧.docx"))
    new = _Window(222, _Document("新.docx", "C:/docs/新.docx"))
    word = _Word([old, new], active_index=0, broken=(3,))
    monkeypatch.setattr(word_bridge, "foreground_hwnd", lambda: 222)

    document, source = word_bridge.current_document_with_source(word=word)

    assert document is new.Document
    assert source == "前台窗口"


def test_work_document_is_never_the_current_document(monkeypatch):
    """ActiveDocument 停在 Image.docx 时，必须仍指向用户那篇。"""
    _quiet(monkeypatch)
    image = _Window(111, _Document("Image.docx", "C:/cfg/Image.docx"), visible=False)
    paper = _Window(222, _Document("模板.docx", "C:/docs/模板.docx"))
    word = _Word([image, paper], active_index=0)       # Word 自认为活动的是隐藏工作文档
    word_bridge.register_work_document("C:/cfg/Image.docx")

    document, _source = word_bridge.current_document_with_source(word=word)

    assert document is paper.Document, "工作文档不能当当前文档"


def test_only_work_document_yields_none(monkeypatch):
    """只剩工作文档时返回 None：宁可不出卡，也不按工作文档扫一遍。"""
    _quiet(monkeypatch)
    image = _Window(111, _Document("Image.docx", "C:/cfg/Image.docx"))
    word = _Word([image], active_index=0)
    word_bridge.register_work_document("C:/cfg/Image.docx")

    document, source = word_bridge.current_document_with_source(word=word)

    assert document is None
    assert source == "仅工作文档"


def test_registered_work_document_excluded_even_when_window_visible(monkeypatch):
    """`Window.Visible` 会骗人（Word 进程整体不可见时用户文档也读成 False），登记才是准的。"""
    _quiet(monkeypatch)
    image = _Window(111, _Document("Image.docx", "C:/cfg/Image.docx"), visible=True)
    paper = _Window(222, _Document("模板.docx", "C:/docs/模板.docx"), visible=True)
    word = _Word([image, paper], active_index=1)
    word_bridge.register_work_document(r"C:\cfg\Image.docx")   # 大小写/反斜杠都要能匹配

    assert word_bridge.visible_windows(word) == [paper]
