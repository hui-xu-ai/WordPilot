# tests/test_word_image_processor.py
"""P10-B9：图片替换必须"先插新、后删旧"，失败能整体撤销。

审计 H16：`_replace_shape` 先 `shape.Delete()` 再 `AddPicture`，中途失败时原图已从
用户文档删除（`process_selected` 只把异常转成一句提示）——等于真丢图。
"""
from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def processor_cls():
    from plugins.image_toolbox.word_image_processor import WordImageProcessor

    return WordImageProcessor


class FakeShape:
    def __init__(self, log: list[str]):
        self.log = log
        self.Left = 0
        self.Top = 0
        self.Width = 0
        self.Height = 0
        self.LockAspectRatio = False

    def Delete(self):
        self.log.append("Delete(旧图)")


class FakeInlineShapes:
    def __init__(self, log, fail: bool):
        self.log = log
        self._fail = fail

    def AddPicture(self, **_kwargs):
        self.log.append("AddPicture(新图)")
        if self._fail:
            raise RuntimeError("AddPicture 失败")
        return FakeShape(self.log)


class FakeUndoRecord:
    def __init__(self, log):
        self.log = log

    def StartCustomRecord(self, name):
        self.log.append("StartCustomRecord")

    def EndCustomRecord(self):
        self.log.append("EndCustomRecord")


class FakeDoc:
    def __init__(self, log, fail):
        self.log = log
        self.InlineShapes = FakeInlineShapes(log, fail)
        self.Shapes = self.InlineShapes

    def Range(self, start, end=None):
        return ("range", start, end)


class FakeWord:
    def __init__(self, log, fail=False, undo=True):
        self.log = log
        self.ScreenUpdating = True
        self.Options = type("O", (), {"PictureCompression": True, "DoNotCompressImages": False})()
        self.ActiveDocument = FakeDoc(log, fail)
        self.UndoRecord = FakeUndoRecord(log) if undo else None

    def ScreenRefresh(self):
        self.log.append("ScreenRefresh")

    def Undo(self):
        self.log.append("Undo")


def _make(processor_cls, word):
    proc = processor_cls.__new__(processor_cls)
    proc.word_app = word
    return proc


INFO = {"range_start": 10, "range_end": 20, "anchor_start": 10,
        "left": 1, "top": 2, "width": 100, "height": 50}


def test_new_picture_inserted_before_old_deleted(processor_cls):
    log: list[str] = []
    word = FakeWord(log)
    proc = _make(processor_cls, word)
    old = FakeShape(log)

    proc._replace_shape(old, True, INFO, "new.png", "width")

    assert log.index("AddPicture(新图)") < log.index("Delete(旧图)"), \
        f"必须先插新图再删旧图，实际顺序 {log}"
    assert log[0] == "StartCustomRecord"
    assert log.index("EndCustomRecord") > log.index("Delete(旧图)"), \
        f"自定义撤销记录应覆盖到删旧图之后，实际顺序 {log}"


def test_failure_rolls_back_and_keeps_old_picture(processor_cls):
    log: list[str] = []
    word = FakeWord(log, fail=True)
    proc = _make(processor_cls, word)
    old = FakeShape(log)

    with pytest.raises(RuntimeError):
        proc._replace_shape(old, True, INFO, "new.png", "width")

    assert "Delete(旧图)" not in log, "失败路径绝不能删除原图"
    assert "Undo" in log, "失败必须整体撤销这次自定义记录"
    assert word.ScreenUpdating is True, "ScreenUpdating 必须复位"


def test_works_without_undo_record_support(processor_cls):
    """老版本 Word 或已结束的记录：没有 UndoRecord 时仍要先插后删。"""
    log: list[str] = []
    word = FakeWord(log, undo=False)
    proc = _make(processor_cls, word)

    proc._replace_shape(FakeShape(log), True, INFO, "new.png", "width")
    assert log.index("AddPicture(新图)") < log.index("Delete(旧图)")
