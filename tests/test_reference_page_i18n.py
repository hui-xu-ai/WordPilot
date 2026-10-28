# tests/test_reference_page_i18n.py
"""参考文献页面的界面文案：切语言后必须全部跟着变，且往返幂等。

现场（用户截图）：
- 英文界面里统计行仍是「共 N 条 · 有备注 N · 孤立锚点 N」；
- 切回中文后详情开关还是「Hide details」；
- 孤立锚点模式的表头根本没进语言包（显示 col.doc_name 这种裸 key）。

这里用**真实语言包** + 替身 ctx（只实现 `tr` / `get_service`）构造 LibraryPage，
不启动 Word、不启动主窗口。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / "src/plugins/reference"
HAN = re.compile(r"[\u4e00-\u9fff]")


class _Config:
    def __init__(self) -> None:
        self.data = {"ui_language": "zh_CN"}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value


class _Ctx:
    """与 `PluginContext.tr` 一致：自动加 `reference.` 前缀。"""

    def __init__(self, i18n) -> None:
        self.i18n = i18n

    def tr(self, key, /, **params):
        return self.i18n.tr(f"reference.{key}", **params)

    def get_service(self, name):
        return self.i18n if name == "i18n" else None


@pytest.fixture
def page(tmp_path):
    from PySide6.QtWidgets import QApplication

    from core.services.i18n import I18nService
    from plugins.reference.library_page import LibraryPage
    from plugins.reference.notes import NoteService, NoteStore
    from plugins.reference.reference_index import ReferenceIndex
    from plugins.reference.reference_store import Reference, ReferenceStore

    app = QApplication.instance() or QApplication([])
    i18n = I18nService(_Config(), i18n_dir=ROOT / "src/assets/i18n")
    i18n.register_namespace("reference", PLUGIN_DIR / "i18n")
    store = ReferenceStore(directory=tmp_path / "LibA")
    store.upsert(Reference(ref_key="doi:1", title="Paper One", authors="Wang D P",
                           year="2020", cite_label="1", note="my note"))
    store.upsert(Reference(ref_key="doi:2", title="Paper Two", cite_label="2"))
    index = ReferenceIndex(db_path=tmp_path / "LibA" / "index.db")
    widget = LibraryPage(_Ctx(i18n), store, index, plugin=None,
                         note_service=NoteService(store, NoteStore(directory=tmp_path)),
                         libraries=["LibA"], active_library="LibA")
    widget.resize(1200, 700)
    widget.show()
    widget.set_current_document("Paper.docx")
    widget.reload()
    yield i18n, widget, store, app
    index.close()
    widget.deleteLater()


def _page_texts(page) -> dict[str, str]:
    """页面顶层的文案快照（表头 / 占位符 / 统计 / 文件标签 / 按钮 / 开关）。"""
    return {
        "headers": "|".join(_headers(page)),
        "search": page.search_edit.placeholderText(),
        "status": page.status_label.text(),
        "doc": page.doc_label.text(),
        "toggle": page.panel_toggle.text(),
        "toggle.tip": page.panel_toggle.toolTip(),
        "new": page.new_lib_btn.text(),
        "delete": page.del_lib_btn.text(),
        "reload": page.refresh_btn.text(),
        "sync": page.scan_btn.text(),
        "more": page.more_btn.text(),
        "filter": page.filter_btn.text(),
    }


def _headers(page) -> list[str]:
    return [page.table.horizontalHeaderItem(column).text()
            for column in range(page.table.columnCount())]


def test_english_has_no_chinese(page):
    i18n, widget, _store, _app = page

    i18n.set_language("en_US")
    widget.retranslateUi()

    dirty = {key: value for key, value in _page_texts(widget).items() if HAN.search(value)}
    assert not dirty, f"英文界面仍有中文：{dirty}"

    assert _headers(widget) == ["No.", "Title", "Authors · Year", "My note"]
    assert widget.search_edit.placeholderText().startswith("Search title")
    assert "entries" in widget.status_label.text()
    assert widget.doc_label.text().startswith("File:")
    assert widget.panel_toggle.text() == "Hide details"


def test_switch_back_to_chinese_restores_everything(page):
    i18n, widget, _store, _app = page

    before = _page_texts(widget)
    i18n.set_language("en_US")
    widget.retranslateUi()
    i18n.set_language("zh_CN")
    widget.retranslateUi()

    assert _page_texts(widget) == before, "zh → en → zh 往返后文案必须与最初一致"


def test_counts_survive_retranslation(page):
    """统计行是"模板 + 变量"：重译必须用同一份数据重算，不能丢计数。"""
    i18n, widget, _store, _app = page

    i18n.set_language("en_US")
    widget.retranslateUi()
    assert widget.status_label.text() == "2 entries · 1 with notes · 0 orphan anchors"

    i18n.set_language("zh_CN")
    widget.retranslateUi()
    assert widget.status_label.text() == "共 2 条 · 有备注 1 · 孤立锚点 0"


def test_orphan_mode_headers_are_translated(page):
    i18n, widget, store, _app = page

    store.bind_anchor("Paper.docx", "5", "zotero:missing")
    widget.set_filter("orphan")
    i18n.set_language("en_US")
    widget.retranslateUi()

    headers = _headers(widget)
    assert not any(HAN.search(text) for text in headers), headers
    assert not any(text.startswith("col.") for text in headers), "表头不能是裸 key"
    assert headers[0] == "Document"


def test_language_switch_event_retranslates_overlay():
    """悬浮窗不在主窗口的页面循环里，必须自己订阅 i18n.language_changed。"""
    source = (PLUGIN_DIR / "plugin.py").read_text(encoding="utf-8")

    assert 'subscribe("i18n.language_changed"' in source
    assert "def retranslateUi" in (PLUGIN_DIR / "overlay.py").read_text(encoding="utf-8")


def test_footer_status_lines_follow_language(page):
    """状态栏历史存 key + 参数：切语言后逐条重画，条数/顺序不变。

    现场（用户截图）：以前存的是渲染好的成品文本 → 切英文再切回中文，
    旧英文句子永远留在底部状态栏里。
    """
    i18n, widget, _store, _app = page

    widget.append_status("status.reloaded")
    widget.append_status("status.library", name="LibA")
    widget.append_status("status.diff", added=2, removed=1)
    zh_lines = widget.footer_label.text().splitlines()
    assert len(zh_lines) == 3, "状态栏保留最近 3 条"

    i18n.set_language("en_US")
    widget.retranslateUi()
    en_lines = widget.footer_label.text().splitlines()
    assert len(en_lines) == 3, "重画不能丢行"
    assert not any(HAN.search(line) for line in en_lines), en_lines
    assert en_lines[0] == "Reloaded"
    assert en_lines[1] == "Library: LibA"
    assert "2" in en_lines[2] and "1" in en_lines[2]

    i18n.set_language("zh_CN")
    widget.retranslateUi()
    assert widget.footer_label.text().splitlines() == zh_lines, "切回中文必须与最初逐字一致"
