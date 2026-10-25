# tests/test_reference_library_page.py
"""参考文献助手主页面（重设计后）：默认全库表格 + 右栏只读/编辑 + 筛选 chips。

页面只跟 `ReferenceStore` + `ReferenceIndex` 打交道，测试用临时目录 + 假 ctx，
不需要 Word、不碰真实 `config/`。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from plugins.reference.reference_index import ReferenceIndex
from plugins.reference.reference_store import Reference, ReferenceStore

_DICT = {
    "page_label": "参考文献助手",
    "search.placeholder": "搜索…",
    "filter.all": "全部", "filter.note": "有备注", "filter.star": "星标",
    "filter.meta": "缺元数据", "filter.orphan": "孤立",
    "action.new": "新建", "action.import": "导入", "action.export": "导出",
    "action.open_dir": "数据目录", "action.save": "保存", "action.delete": "删除",
    "action.edit": "编辑", "action.cancel": "取消", "action.reload": "刷新",
    "action.open_csv": "打开CSV", "action.more": "更多",
    "action.bind_orphan": "绑定", "field.key": "身份键", "field.note": "备注",
    "panel.summary": "文献库", "panel.detail": "文献详情", "panel.new": "新建文献",
    "panel.orphan_title": "孤立锚点", "panel.no_note": "（还没有备注）",
    "panel.orphan_hint": "孤立提示", "panel.bind_hint": "先选文献",
    "status.need_title": "标题或备注至少填一项", "status.saved": "已保存",
    "status.deleted": "已删除", "new.hint": "（新条目）",
    "status.counts": "共 {total} 条 · 有备注 {with_note} · 孤立锚点 {orphans}",
    "status.reloaded": "已刷新",
    "status.library": "当前仓库：{name}",
    "status.diff": "本次同步：新增 {added} 条 · 已移除 {removed} 条（备注仍保留）",
    "filter.added": "本次新增", "filter.removed": "已移除", "filter.diff_tip": "与上次扫描的差异",
    "overlay.note_child": "子库备注", "overlay.note_inherit": "跟随总库",
    "overlay.note_auto": "来自总库（唯一）", "overlay.note_none": "总库有多条，请挑选",
    "overlay.master_count": "总库 · {count} 条",
}


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


class _Ctx:
    def tr(self, key, /, **params):
        text = _DICT.get(key, key)
        try:
            return text.format(**params)
        except (KeyError, IndexError):
            return text


@pytest.fixture
def page(qapp, tmp_path: Path):
    from plugins.reference.library_page import LibraryPage

    store = ReferenceStore(directory=tmp_path)
    index = ReferenceIndex(db_path=tmp_path / "index.db")
    widget = LibraryPage(_Ctx(), store, index)
    yield widget, store, index
    index.close()
    widget.deleteLater()


def _add(page_tuple, store, key, label, title, note="", starred=False):
    store.upsert(Reference(ref_key=key, cite_label=label, title=title, note=note,
                           starred=starred, authors="Tittensor DP", year="2004",
                           container="Nature", doi="10.1038/x"))
    page_tuple[0].reload()


def test_empty_library_shows_counts(page):
    widget, _store, _index = page
    assert widget.table.rowCount() == 0
    assert "共 0 条" in widget.status_label.text()


def test_table_is_the_default_view(page):
    """默认主体就是全库表格（用户要求），不是空面板。"""
    widget, store, _index = page
    _add(page, store, "zotero:1", "1", "第一条")
    _add(page, store, "zotero:2", "2", "第二条")

    assert widget.table.rowCount() == 2
    assert widget.table.columnCount() == 8
    assert widget.panel_stack.currentIndex() == 0


def test_selecting_row_shows_readonly_detail(page):
    widget, store, _index = page
    _add(page, store, "zotero:934", "1", "Global biodiversity", note="核心参考")

    widget.table.selectRow(0)

    assert widget.read_labels["title"].text() == "Global biodiversity"
    assert widget.read_labels["doi"].text() == "10.1038/x"
    assert widget.read_note.text() == "核心参考"
    assert widget.edit_btn.isVisibleTo(widget)
    assert widget.panel_stack.currentIndex() == 0, "选中后应停在只读视图"


def test_edit_switches_to_form_and_saves(page):
    widget, store, _index = page
    _add(page, store, "zotero:934", "1", "原标题")
    widget.table.selectRow(0)

    widget.start_edit()
    assert widget.panel_stack.currentIndex() == 1
    widget.title_edit.setText("改过的标题")
    widget.save_current()

    ref = ReferenceStore(directory=store.directory).get("zotero:934")
    assert ref is not None and ref.title == "改过的标题"
    assert widget.table.rowCount() == 1, "原地更新，不能新增一条"


def test_filter_chips(page):
    widget, store, _index = page
    _add(page, store, "zotero:1", "1", "有备注的", note="备注")
    _add(page, store, "zotero:2", "2", "没备注的")
    _add(page, store, "zotero:3", "3", "星标的", starred=True)

    def visible():
        return [row for row in range(widget.table.rowCount()) if not widget.table.isRowHidden(row)]

    widget.set_filter("note")
    assert len(visible()) == 1
    widget.set_filter("star")
    assert len(visible()) == 1
    widget.set_filter("all")
    assert len(visible()) == 3


def test_orphan_filter_lists_anchors_and_binds(page):
    widget, store, _index = page
    _add(page, store, "zotero:934", "1", "目标文献")
    store.bind_anchor("论文.docx", "5", "zotero:missing")

    widget.set_filter("orphan")
    assert widget.table.rowCount() == 1
    widget.table.selectRow(0)
    assert widget.bind_btn.isVisibleTo(widget)

    widget.bind_orphan("zotero:934")

    anchor = ReferenceStore(directory=store.directory).anchor_for("论文.docx", "5")
    assert anchor is not None and anchor.anchor_key == "zotero:934"


def test_new_reference_via_edit_form(page):
    widget, store, _index = page
    widget.start_new()
    widget.label_edit.setText("9")
    widget.title_edit.setText("新文献")
    widget.note_edit.setPlainText("新备注")
    widget.save_current()

    reloaded = ReferenceStore(directory=store.directory)
    saved = reloaded.find_by_label("9")
    assert saved is not None and saved.note == "新备注"
    assert widget.table.rowCount() == 1


def test_save_requires_title_or_note(page):
    widget, store, _index = page
    widget.start_new()
    widget.save_current()
    assert widget.table.rowCount() == 0
    assert widget.status_label.text() == "标题或备注至少填一项"
    assert store.all() == []


def test_delete_removes_row(page):
    widget, store, _index = page
    _add(page, store, "zotero:1", "1", "要删的")
    widget.table.selectRow(0)
    widget.delete_current()

    assert widget.table.rowCount() == 0
    assert ReferenceStore(directory=store.directory).get("zotero:1") is None


def test_translations_are_resolved_not_raw_keys(page):
    """回归：界面里不能再出现 `reference.xxx` 键名。"""
    widget, _store, _index = page
    assert widget.search_edit.placeholderText() == "搜索…"
    assert widget.filter_buttons["all"].text() == "全部"
    assert widget.open_csv_btn.text() == "打开CSV"
    assert "reference." not in widget.panel_title.text()


def test_panel_width_is_400(page):
    widget, _store, _index = page
    panel = widget.panel_stack.parentWidget()
    assert panel.minimumWidth() == 400 and panel.maximumWidth() == 400, "用户指定右栏 400px"

def test_status_bar_keeps_last_three_lines(page):
    widget, _store, _index = page
    for index in range(5):
        widget.append_status(f"事件 {index}")
    lines = widget.footer_label.text().splitlines()
    assert len(lines) == 3
    assert lines[-1] == "事件 4"


def test_scan_controls_start_idle(page):
    """扫描按钮默认可用、进度条默认隐藏（用户要看见"扫描"入口与进度）。"""
    widget, _store, _index = page
    assert widget.scan_btn.isEnabled()
    assert widget.progress.isVisible() is False
    assert widget.progress.minimum() == 0 and widget.progress.maximum() == 0


def test_right_panel_labels_are_translated(page):
    """右栏字段名不能再显示 cite_label/title 这种英文键。"""
    widget, _store, _index = page
    labels = [label.text() for label in widget.read_labels.values()]
    assert all("cite_label" not in text for text in labels)
    assert widget.read_labels["title"].text() == ""

def test_refresh_button_is_visible(page):
    """刷新按钮要直接露在工具栏（之前藏在 ⋯ 菜单里，用户点不到）。"""
    widget, _store, _index = page
    assert widget.refresh_btn.text() != ""
    assert widget.refresh_btn.isVisibleTo(widget)
    widget.append_status("x")
    widget.reload_now()
    assert widget.footer_label.text().endswith("已刷新")


def test_auto_refresh_picks_up_external_csv_edits(page, tmp_path):
    """外部（Excel/悬浮窗）改了 CSV，页面不重启也能看到。"""
    widget, store, _index = page
    store.upsert(Reference(ref_key="zotero:1", cite_label="1", title="旧标题"))
    widget.reload()

    store.upsert(Reference(ref_key="zotero:2", cite_label="2", title="外部新增"))
    widget._auto_sync()

    assert widget.table.rowCount() == 2, "同进程写入后页面应自动刷新"
    assert "有备注" in widget.status_label.text()
