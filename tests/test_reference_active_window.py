# tests/test_reference_active_window.py
"""同一个 Word 进程开着两篇文档时，必须按**前台窗口**识别当前文档。

用户现场：两篇 Word 文档同时打开，鼠标切到另一篇后程序仍按旧文档判断 ——
仓库不跟着切、点了引文也不弹。原因：`Application.ActiveDocument` / `Selection`
会停留在旧窗口，按窗口句柄（`Window.Hwnd`）匹配才准。
"""
from __future__ import annotations

import types

from core.services import word_bridge


class _Document:
    def __init__(self, name: str, full_name: str):
        self.Name = name
        self.FullName = full_name
        self.activated = 0

    def Activate(self) -> None:            # noqa: N802 - 模拟 COM
        self.activated += 1


class _Window:
    def __init__(self, hwnd: int, document: _Document):
        self.Hwnd = hwnd
        self.Document = document


class _Word:
    def __init__(self, windows, active_index: int):
        self.Windows = windows
        self._active = windows[active_index]

    @property
    def ActiveDocument(self):              # noqa: N802
        return self._active.Document

    @property
    def ActiveWindow(self):                # noqa: N802
        return self._active

    @property
    def Selection(self):                   # noqa: N802
        return types.SimpleNamespace(Start=4, End=4, Text="[2]")


def test_active_window_matches_foreground_hwnd(monkeypatch):
    old = _Document("EndNote.docx", "C:/docs/EndNote.docx")
    new = _Document("Zotero.docx", "C:/docs/Zotero.docx")
    windows = [_Window(111, old), _Window(222, new)]
    word = _Word(windows, active_index=0)          # Word 自认为还在第一篇

    monkeypatch.setattr(word_bridge, "foreground_hwnd", lambda: 222)

    window = word_bridge.active_window(word)

    assert window is windows[1], "必须按前台窗口句柄匹配，而不是 ActiveWindow"
    assert word_bridge.active_window_document(word) is new


def test_watch_tick_activates_foreground_document(monkeypatch, qapp_unused=None):
    """tick 要按前台窗口取文档，并把 Word 自己的 ActiveDocument 同步过来（否则 Selection 读的是旧窗口）。"""
    from plugins.reference import word_watch

    old = _Document("EndNote.docx", "C:/docs/EndNote.docx")
    new = _Document("Zotero.docx", "C:/docs/Zotero.docx")
    windows = [_Window(111, old), _Window(222, new)]
    word = _Word(windows, active_index=0)

    seen: list[str] = []
    watch = word_watch.WordWatch()
    watch.documentChanged.connect(seen.append)
    monkeypatch.setattr(word_watch, "_active_window", lambda _word: windows[1])

    watch._tick(lambda: word)

    assert seen == ["Zotero.docx"], "应识别为前台窗口那篇文档"
    assert new.activated == 1, "要 Activate 一次让 Word 内部状态跟上（Selection 才读得准）"
    assert watch._last_doc_identity == "C:/docs/Zotero.docx"
