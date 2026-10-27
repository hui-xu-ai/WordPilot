# tests/test_reference_visible_window.py
"""隐藏的 Word 工作文档（图片工具箱的 Image.docx）不能干扰"当前文档"判断。

用户现场：图片工具箱用 `Documents.Open(Visible=False)` 打开 `Image.docx` 且不恢复用户原活动文档，
于是 `ActiveDocument` 指向那篇隐藏文档 → 参考助手识别错文档、点引文不弹窗。
"""
from __future__ import annotations

from core.services import word_bridge


class _Document:
    def __init__(self, name: str, full_name: str):
        self.Name = name
        self.FullName = full_name


class _Window:
    def __init__(self, hwnd: int, document: _Document, visible: bool):
        self.Hwnd = hwnd
        self.Document = document
        self.Visible = visible


class _Word:
    def __init__(self, windows, active_index: int):
        self.Windows = windows
        self._active = windows[active_index]

    @property
    def ActiveWindow(self):                # noqa: N802
        return self._active

    @property
    def ActiveDocument(self):              # noqa: N802
        return self._active.Document


def test_hidden_work_document_is_ignored(monkeypatch):
    hidden = _Window(111, _Document("Image.docx", "C:/cfg/Image.docx"), visible=False)
    user = _Window(222, _Document("论文.docx", "C:/docs/论文.docx"), visible=True)
    word = _Word([hidden, user], active_index=0)      # Word 自认为活动的是隐藏文档

    monkeypatch.setattr(word_bridge, "foreground_hwnd", lambda: 0)
    monkeypatch.setattr(word_bridge, "cursor_hwnd", lambda: 0)

    window, source = word_bridge.active_window_with_source(word=word)

    assert window is user, "必须忽略隐藏的 Image.docx"
    assert source in ("ActiveWindow", "可见窗口兜底")


def test_hidden_only_returns_none(monkeypatch):
    hidden = _Window(111, _Document("Image.docx", "C:/cfg/Image.docx"), visible=False)
    word = _Word([hidden], active_index=0)

    monkeypatch.setattr(word_bridge, "foreground_hwnd", lambda: 0)
    monkeypatch.setattr(word_bridge, "cursor_hwnd", lambda: 0)

    window, source = word_bridge.active_window_with_source(word=word)

    assert window is None, "只剩隐藏窗口时不能把卡片贴到隐藏文档上"
    assert source == "无可见窗口"
