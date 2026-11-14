# tests/test_hotkey_conflict.py
"""快捷键冲突检测（离线单测）：组合键归一化/合法性、Word 绑定判定、三种状态。

背景：热键用 `keyboard` 全局钩子注册且不拦截按键，按键会同时进 Word。
Word 的 `Ctrl+Shift+E` 是「修订模式开关」——用户按"切换校对"时文档会静默进修订模式。
这里用假 Word 对象覆盖各分支，真实链路由 `work/scratch/probe_word_keyconflict.py` 验证。
"""
from __future__ import annotations

import pytest

from core.services import hotkey_conflict as hc


class _Binding:
    def __init__(self, command="", key_string=""):
        self.Command = command
        self.KeyString = key_string


class _FakeWord:
    """假 Word：记录 BuildKeyCode 收到的参数，按预置表返回 FindKey 结果。"""

    def __init__(self, binding=None, code=1234, params_keyword="KeyCode"):
        self.binding = binding
        self.code = code
        self.built: list[tuple] = []
        self._params_keyword = params_keyword

    def BuildKeyCode(self, *args):          # noqa: N802
        self.built.append(args)
        return self.code

    def FindKey(self, **kwargs):            # noqa: N802
        return self.binding


# ---------------- 组合键归一化 ----------------
@pytest.mark.parametrize(("raw", "expected"), [
    ("Ctrl+Shift+E", "ctrl+shift+e"),
    ("ctrl + alt + shift + e", "ctrl+alt+shift+e"),
    ("Shift+Ctrl+Alt+E", "ctrl+alt+shift+e"),
    ("Control+E", "ctrl+e"),
    ("", ""),
])
def test_normalize_combo(raw, expected):
    assert hc.normalize_combo(raw) == expected


@pytest.mark.parametrize(("raw", "expected"), [
    ("ctrl+alt+shift+e", (["ctrl", "alt", "shift"], "e")),
    ("Ctrl+Shift+F9", (["ctrl", "shift"], "f9")),
    ("alt+1", (["alt"], "1")),
    ("ctrl+", None),
    ("e", ([], "e")),            # 合法解析，但 is_valid_combo 会拒绝（无修饰键）
    ("ctrl+哆", None),
])
def test_parse_combo(raw, expected):
    assert hc.parse_combo(raw) == expected


@pytest.mark.parametrize(("raw", "expected"), [
    ("ctrl+alt+shift+e", True),
    ("ctrl+shift+z", True),
    ("alt+f9", True),
    ("shift+e", False),          # 只有 Shift：会抢掉正常输入
    ("e", False),
    ("", False),
    ("ctrl+", False),
])
def test_is_valid_combo(raw, expected):
    assert hc.is_valid_combo(raw) is expected


# ---------------- Word 绑定 ----------------
def test_word_binding_passes_word_keycodes():
    """修饰键必须用 Word 的 WdKey 常量（Shift=256、Ctrl=512、Alt=1024）。"""
    word = _FakeWord(binding=_Binding("ToolsRevisionMarksToggle", "Ctrl+Shift+E"))

    binding = hc.word_binding("ctrl+shift+e", word=word)

    assert binding is not None and binding.command == "ToolsRevisionMarksToggle"
    assert word.built == [(hc.WD_KEY_CONTROL, hc.WD_KEY_SHIFT, ord("E"))]


def test_assess_conflict_when_word_binds_command():
    word = _FakeWord(binding=_Binding("ToolsRevisionMarksToggle", "Ctrl+Shift+E"))

    verdict = hc.assess("ctrl+shift+e", word=word)

    assert verdict["status"] == hc.STATUS_CONFLICT
    assert verdict["command"] == "ToolsRevisionMarksToggle"
    assert verdict["human"] == "修订模式开关", "界面上要给人话，而不是 VBA 命令名"


def test_assess_free_when_command_empty():
    word = _FakeWord(binding=_Binding("", "Alt+Ctrl+Shift+E"))

    verdict = hc.assess("ctrl+alt+shift+e", word=word)

    assert verdict["status"] == hc.STATUS_FREE


def test_assess_conflict_for_symbol_insertion():
    """插入 € 这类符号也算副作用（会写进文档），不能当"安全"。"""
    word = _FakeWord(binding=_Binding("€", "Alt+Ctrl+E"))

    verdict = hc.assess("ctrl+alt+e", word=word)

    assert verdict["status"] == hc.STATUS_CONFLICT
    assert verdict["human"] == ""          # 没有中文名就用命令原文
    assert verdict["command"] == "€"


def test_assess_unknown_when_word_not_running(monkeypatch):
    import win32com.client

    def _no_word(*_args, **_kwargs):
        raise OSError("Word 未运行")

    monkeypatch.setattr(win32com.client, "GetActiveObject", _no_word)

    verdict = hc.assess("ctrl+alt+shift+e")

    assert verdict["status"] == hc.STATUS_UNKNOWN
    assert "Word" in verdict["detail"]


def test_assess_unknown_for_invalid_combo():
    verdict = hc.assess("ctrl+哆", word=_FakeWord(binding=_Binding()))

    assert verdict["status"] == hc.STATUS_UNKNOWN


def test_word_binding_survives_com_errors():
    class _Broken:
        def BuildKeyCode(self, *_args):     # noqa: N802
            raise RuntimeError("COM 挂了")

    assert hc.word_binding("ctrl+shift+e", word=_Broken()) is None


def test_word_binding_returns_none_when_no_binding():
    assert hc.word_binding("ctrl+alt+shift+e", word=_FakeWord(binding=None)) is None
