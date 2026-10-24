# tests/test_reference_overlay.py
"""悬浮窗：定位纯函数 + 多篇堆叠 + 备注自动保存 + ✕ 关闭。

定位函数不碰 Qt，可精确断言"翻边/夹边"；窗口部分用离屏 Qt，只验证结构与保存行为。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from plugins.reference.overlay import OverlayEntry, ReferenceOverlay
from plugins.reference.overlay_geometry import place_near
from plugins.reference.reference_store import Reference, ReferenceStore

AREA = (0, 0, 1920, 1040)


# ---------------- 纯函数定位 ----------------
def test_place_below_right_of_anchor():
    x, y = place_near(100, 100, 300, 200, AREA)
    assert (x, y) == (108, 108)


def test_flip_left_when_not_enough_room_on_right():
    x, _y = place_near(1800, 100, 300, 200, AREA)
    assert x + 300 <= 1920 and x < 1800, "右边放不下必须翻到左侧"


def test_flip_above_when_not_enough_room_below():
    _x, y = place_near(100, 1000, 300, 200, AREA)
    assert y + 200 <= 1040 and y < 1000, "下边放不下必须翻到上方"


def test_clamped_into_workarea_when_anchor_near_corner():
    x, y = place_near(1910, 1030, 400, 300, AREA)
    assert 0 <= x <= 1920 - 400
    assert 0 <= y <= 1040 - 300


def test_respects_secondary_monitor_offset():
    area = (-1920, 0, 0, 1080)
    x, y = place_near(-300, 200, 300, 200, area)
    assert -1920 <= x and x + 300 <= 0
    assert 0 <= y <= 1080 - 200


# ---------------- 窗口 ----------------
@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def overlay(qapp, tmp_path: Path):
    store = ReferenceStore(directory=tmp_path)
    widget = ReferenceOverlay(store, lambda key, **params: key)
    yield widget, store
    widget.deleteLater()


def _entry(key: str, label: str, note: str = "") -> OverlayEntry:
    return OverlayEntry(anchor_key=key, label=label, title=f"文献 {label}", meta="Nature · 2004",
                        note=note)


def test_multiple_citations_stack_in_order(overlay):
    widget, _store = overlay
    widget.show_entries([_entry("doi:10.1/a", "1"), _entry("zotero:2", "2"),
                         _entry("zotero:3", "3")], (300, 200))

    assert len(widget._cards) == 3, "[1-3] 必须按编号从上往下各一张卡"
    assert [card.entry.label for card in widget._cards] == ["1", "2", "3"]
    assert widget.isVisible()


def test_caps_visible_entries(overlay):
    widget, _store = overlay
    widget.show_entries([_entry(f"zotero:{i}", str(i)) for i in range(1, 13)], (300, 200))
    assert len(widget._cards) == 8, "超过 8 条只显示前 8 条（其余去主页看）"


def test_note_edit_autosaves_to_store(overlay):
    widget, store = overlay
    store.upsert(Reference(ref_key="zotero:1", title="文献", note="旧备注"))
    widget.show_entries([_entry("zotero:1", "1", "旧备注")], (300, 200))

    card = widget._cards[0]
    card.note_edit.setPlainText("我在悬浮窗里改的备注")
    assert card._timer.isActive(), "编辑后应启动 800 ms 自动保存定时器"
    card.flush()

    assert ReferenceStore(directory=store.directory).get("zotero:1").note == "我在悬浮窗里改的备注"


def test_close_button_hides_and_reports_key(overlay):
    widget, _store = overlay
    widget.show_entries([_entry("zotero:1", "1")], (300, 200))
    seen: list[str] = []
    widget.dismissed.connect(seen.append)

    widget._on_close_clicked()

    assert widget.isVisible() is False
    assert seen == ["zotero:1"], "关闭要上报被关闭的锚点（本次不再自动弹）"


def test_hide_flushes_pending_note(overlay):
    widget, store = overlay
    store.upsert(Reference(ref_key="zotero:1", title="文献"))
    widget.show_entries([_entry("zotero:1", "1")], (300, 200))
    widget._cards[0].note_edit.setPlainText("光标离开时要把这次编辑落下")
    widget.hide_overlay()

    assert ReferenceStore(directory=store.directory).get("zotero:1").note.startswith("光标离开时")


def test_empty_entries_hides_window(overlay):
    widget, _store = overlay
    widget.show_entries([], (300, 200))
    assert widget.isVisible() is False

def test_unbound_entry_offers_bind_and_create(overlay):
    """EndNote 空壳域：没有元数据/备注 → 给「绑定已有文献 / 新建文献」两个动作。"""
    widget, _store = overlay
    entry = OverlayEntry(anchor_key="doc:论文.docx:cite:3", label="3", doc_name="论文.docx")
    widget.show_entries([entry], (300, 200))

    card = widget._cards[0]
    assert entry.unbound is True
    assert not hasattr(card, "note_edit"), "未绑定时先不给编辑框，先让用户绑/建"
    assert card.bind_btn.text() == "overlay.bind"
    assert card.create_btn.text() == "overlay.create"

    seen: list[tuple[str, str]] = []
    widget.bindRequested.connect(lambda key, doc: seen.append((key, doc)))
    card.bind_btn.click()
    assert seen == [("doc:论文.docx:cite:3", "论文.docx")]


def test_force_edit_entry_shows_note_editor(overlay):
    widget, _store = overlay
    entry = OverlayEntry(anchor_key="doc:论文.docx:cite:3", label="3", doc_name="论文.docx",
                         force_edit=True)
    widget.show_entries([entry], (300, 200))
    assert hasattr(widget._cards[0], "note_edit"), "已建/已绑的条目要能直接写备注"

def test_note_box_is_two_lines_high(overlay):
    """备注框固定两行高（用户预设最多两行）。"""
    widget, _store = overlay
    widget.show_entries([_entry("zotero:1", "1")], (300, 200))
    assert widget._cards[0].note_edit.height() == 46


def test_close_button_shows_cross_glyph(overlay):
    widget, _store = overlay
    widget.show_entries([_entry("zotero:1", "1")], (300, 200))
    assert widget.close_btn.text() == "❌"


def test_single_entry_height_is_compact(overlay):
    """单条文献不留大空白：高度随内容，多条才封顶。"""
    widget, _store = overlay
    widget.show_entries([_entry("zotero:1", "1")], (300, 200))
    single = widget.height()

    widget.show_entries([_entry(f"zotero:{i}", str(i)) for i in range(1, 5)], (300, 200))
    multi = widget.height()

    assert single < multi, "单条应比多条矮"
    assert single <= 200, f"单条高度不该留大空白，实际 {single}"
    assert multi <= 520
