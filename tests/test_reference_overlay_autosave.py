# tests/test_reference_overlay_autosave.py
"""第 1 步：自动保存只响应用户真实输入。

现场 bug：点开引用浮层（什么都没输入）就把页面里的备注覆盖了。
根因是 `_refresh_note_meta()` 程序化 `setPlainText()` 没屏蔽信号 →
触发 textChanged → 800ms 后自动保存。
"""
from __future__ import annotations

import pytest


@pytest.fixture
def overlay(tmp_path):
    from PySide6.QtWidgets import QApplication
    from plugins.reference.notes import NoteService, NoteStore
    from plugins.reference.overlay import ReferenceOverlay
    from plugins.reference.reference_store import Reference, ReferenceStore

    app = QApplication.instance() or QApplication([])
    store = ReferenceStore(directory=tmp_path / "库A")
    store.upsert(Reference(ref_key="doi:1", title="文献", note="页面里写好的备注"))
    widget = ReferenceOverlay(store, tr=lambda key, **kw: key,
                              note_service=NoteService(store, NoteStore(directory=tmp_path)))
    yield widget, store
    widget.deleteLater()


def _entry():
    from plugins.reference.overlay import OverlayEntry

    return OverlayEntry(anchor_key="doi:1", label="1", doc_name="论文.docx", force_edit=True)


def _wait_autosave(app, ms=1200):
    import time

    end = time.time() + ms / 1000
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def test_show_and_wait_writes_nothing(overlay):
    """显示浮层 + 等过自动保存窗口：库里内容必须原样。"""
    from PySide6.QtWidgets import QApplication

    widget, store = overlay
    before = store.get("doi:1").note

    widget.show_entries([_entry()], (10, 10))
    QApplication.processEvents()
    _wait_autosave(QApplication.instance())

    assert store.get("doi:1").note == before, "没输入就不该写库"


def test_refresh_notes_does_not_write(overlay):
    """刷新（程序化补字）同样不能写库 —— 这是覆盖 GUI 备注的直接路径。"""
    from PySide6.QtWidgets import QApplication

    widget, store = overlay
    widget.show_entries([_entry()], (10, 10))
    QApplication.processEvents()
    card = widget._cards[0]
    card.note_edit.blockSignals(True)
    card.note_edit.clear()                    # 模拟"界面上暂时是空的"
    card.note_edit.blockSignals(False)
    card._dirty = False

    card._refresh_note_meta()                 # 程序化补回内容
    _wait_autosave(QApplication.instance())

    assert store.get("doi:1").note == "页面里写好的备注"


def test_typing_then_flush_writes(overlay):
    """真实输入仍然要能保存（不能把功能一起关掉）。"""
    from PySide6.QtWidgets import QApplication

    widget, store = overlay
    widget.show_entries([_entry()], (10, 10))
    QApplication.processEvents()

    card = widget._cards[0]
    card.note_edit.setPlainText("我改的备注")
    assert card._dirty is True
    card.flush()

    assert store.get("doi:1").note == "我改的备注"
    assert card._dirty is False, "写入后应清脏"


def test_flush_after_rebuild_keeps_untouched_note(overlay):
    from PySide6.QtWidgets import QApplication

    widget, store = overlay
    widget.show_entries([_entry()], (10, 10))
    QApplication.processEvents()

    widget._rebuild([_entry()])               # 切引用/重建
    _wait_autosave(QApplication.instance())

    assert store.get("doi:1").note == "页面里写好的备注"
