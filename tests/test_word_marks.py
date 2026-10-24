# tests/test_word_marks.py
"""清除校对标记的性能路径：按**格式**一次性 Find 替换，不再逐字符走 COM。

旧实现 `for i in range(len(range.Characters))`：每个字符至少 3 次跨进程 COM 调用，
一句话 60 字就是近 200 次（几百毫秒）——用户点「确认」时的卡顿主要来自这里。
这里用假 Word 对象断言：① 走 Find 路径时**不**逐字符；② Find 失败时退回逐字符（功能不退化）。
"""
from __future__ import annotations

import pytest


class _Font:
    def __init__(self):
        self.Underline = 0
        self.UnderlineColor = 0


class _Replacement:
    def __init__(self):
        self.Font = _Font()

    def ClearFormatting(self):
        pass


class _Find:
    def __init__(self, *, fail: bool = False):
        self.Font = _Font()
        self.Replacement = _Replacement()
        self.calls = 0
        self.fail = fail

    def ClearFormatting(self):
        pass

    def Execute(self, *args, **kwargs):
        self.calls += 1
        if self.fail:
            raise RuntimeError("find unsupported")
        return True


class _CharacterAccessor:
    """记录逐字符访问次数：走 Find 路径时它必须是 0。"""

    def __init__(self, length: int):
        self.length = length
        self.touched = 0

    def __len__(self):
        return self.length

    def __call__(self, index):
        self.touched += 1
        char = type("Char", (), {})()
        char.Font = _Font()
        return char


class _Range:
    def __init__(self, length: int, *, find_fails: bool = False):
        self.Characters = _CharacterAccessor(length)
        self.Find = _Find(fail=find_fails)


class _Doc:
    def __init__(self, rng):
        self._rng = rng

    def Range(self, start, end):
        return self._rng


class _Word:
    def __init__(self, rng):
        self.ActiveDocument = _Doc(rng)


@pytest.fixture
def patch_word(monkeypatch):
    import plugins.proofread.word_marks as wm

    def _install(rng):
        monkeypatch.setattr(wm, "get_word_app", lambda: _Word(rng))
        return rng

    return _install


def test_clear_marks_uses_format_find_not_per_character(patch_word):
    from plugins.proofread.word_marks import clear_error_marks

    rng = patch_word(_Range(length=60))
    clear_error_marks(0, 60)

    assert rng.Find.calls == 1, "应当用一次格式 Find 清掉全部标记"
    assert rng.Characters.touched == 0, f"不应逐字符访问 COM，实际访问 {rng.Characters.touched} 次"
    assert rng.Find.Font.Underline != rng.Find.Replacement.Font.Underline, "Find 应匹配波浪线并替换为无下划线"


def test_clear_marks_falls_back_to_per_character(patch_word):
    from plugins.proofread.word_marks import clear_error_marks

    rng = patch_word(_Range(length=3, find_fails=True))
    clear_error_marks(0, 3)

    assert rng.Characters.touched >= 1, "Find 不可用时必须退回逐字符，保证标记仍能清掉"
