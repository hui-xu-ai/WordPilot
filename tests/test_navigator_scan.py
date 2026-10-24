# tests/test_navigator_scan.py
"""导航面板的离线快速路径：不碰 COM 也能刷新列表 + 按章节过滤。

现场问题：每次刷新走 COM 把 GUI 卡住约 2s（40MB 论文样本实测 COM 遍历字段 ~1.3s，
离线解析同样本 ~70ms）。这里用假控件把 `_try_offline_scan` 的接线钉住：
章节填入下拉、两份列表按章节过滤、状态栏给出条数。
"""
from __future__ import annotations

import zipfile

import pytest

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


class _FakeList:
    def __init__(self):
        self.items: list[str] = []

    def clear(self):
        self.items.clear()

    def addItem(self, text):        # noqa: N802 - 模拟 Qt API
        self.items.append(text)

    def count(self):
        return len(self.items)


class _FakeCombo:
    def __init__(self):
        self._data: list[tuple[str, int]] = []
        self._index = 0
        self.enabled = True

    def clear(self):
        self._data.clear()
        self._index = 0

    def addItem(self, text, data):  # noqa: N802
        self._data.append((text, data))

    def setCurrentIndex(self, index):   # noqa: N802
        self._index = index

    def currentData(self):          # noqa: N802
        return self._data[self._index][1] if self._data else None

    def setEnabled(self, value):    # noqa: N802
        self.enabled = value

    def blockSignals(self, _value):  # noqa: N802
        return None

    def texts(self):
        return [text for text, _d in self._data]


class _FakeEdit:
    def __init__(self):
        self.value = ""

    def text(self):
        return self.value


class _FakeLabel:
    def __init__(self):
        self.value = ""

    def setText(self, text):        # noqa: N802
        self.value = text


class _FakePanel:
    """把两个 mixin 直接挂在假面板上，避免起 Qt 应用与 Word。"""

    def __init__(self):
        self.table_list = _FakeList()
        self.figure_list = _FakeList()
        self.chapter_combo = _FakeCombo()
        self.search_edit = _FakeEdit()
        self.status_label = _FakeLabel()
        self._chapters = []
        self._closing = False
        self.table_captions = []
        self.figure_captions = []

    def tr(self, key, /, **params):
        return f"{key}|{params}"


def _panel():
    from plugins.navigator.navigator_jump import JumpMixin
    from plugins.navigator.navigator_scan import ScanMixin

    cls = type("FakePanel", (ScanMixin, JumpMixin, _FakePanel), {})
    return cls()


def _make_docx(path):
    def p(text, style=""):
        style_xml = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
        return f"<w:p>{style_xml}<w:r><w:t>{text}</w:t></w:r></w:p>"

    document = (f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="{W}"><w:body>'
                + p("第1章  绪论", "2")
                + p("图 1.1 装置示意图", "ae")
                + p("第2章  方法", "2")
                + p("图 2.1 结构图", "ae")
                + p("表 2.1 参数表", "ae")
                + "</w:body></w:document>")
    styles = (f'<?xml version="1.0" encoding="UTF-8"?><w:styles xmlns:w="{W}">'
              '<w:style w:styleId="2"><w:name w:val="heading 2"/></w:style>'
              '<w:style w:styleId="ae"><w:name w:val="图表标题"/></w:style></w:styles>')
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document)
        archive.writestr("word/styles.xml", styles)
    return str(path)


@pytest.fixture
def docx(tmp_path):
    return _make_docx(tmp_path / "nav.docx")


def test_offline_scan_fills_lists_and_chapters(docx):
    panel = _panel()

    assert panel._try_offline_scan(docx) is True
    assert panel.chapter_combo.texts() == ["chapter.all|{}", "第1章  绪论", "第2章  方法"]
    assert panel.chapter_combo.enabled is True
    assert panel.figure_list.items == ["图 1.1 装置示意图", "图 2.1 结构图"]
    assert panel.table_list.items == ["表 2.1 参数表"]
    assert "offline_done" in panel.status_label.value


def test_chapter_filter_narrows_both_lists(docx):
    panel = _panel()
    panel._try_offline_scan(docx)

    panel.chapter_combo.setCurrentIndex(1)      # 第1章
    panel.filter_lists()
    assert panel.figure_list.items == ["图 1.1 装置示意图"]
    assert panel.table_list.items == []

    panel.chapter_combo.setCurrentIndex(2)      # 第2章
    panel.filter_lists()
    assert panel.figure_list.items == ["图 2.1 结构图"]
    assert panel.table_list.items == ["表 2.1 参数表"]


def test_chapter_and_keyword_combine(docx):
    panel = _panel()
    panel._try_offline_scan(docx)

    panel.chapter_combo.setCurrentIndex(2)
    panel.search_edit.value = "结构"
    panel.filter_lists()
    assert panel.figure_list.items == ["图 2.1 结构图"]
    assert panel.table_list.items == []


def test_missing_file_falls_back_to_com_path(tmp_path):
    """离线解析失败时返回 False，让调用方继续走 COM。"""
    panel = _panel()
    assert panel._try_offline_scan(str(tmp_path / "不存在的.docx")) is False


def test_empty_result_falls_back(tmp_path):
    """没有可识别题录时也交回 COM（可能用了不常见的样式）。"""
    path = tmp_path / "empty.docx"
    document = f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="{W}"><w:body></w:body></w:document>'
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document)

    panel = _panel()
    assert panel._try_offline_scan(str(path)) is False


# ── 刷新策略：默认离线（不动页码），只有「刷新列表」走 COM ─────────────────
class _FakeCache:
    def get_document_fingerprint(self, _path):
        return None

    def get_document_cache(self, _fingerprint):
        return None


class _PolicyPanel:
    def __init__(self):
        self.calls: list[str] = []
        self._captions_cache_data = []
        self._scanning = False
        self.status_label = _FakeLabel()
        self.cache = _FakeCache()

    def tr(self, key, /, **_params):
        return key


def _policy_panel(offline_ok: bool = True):
    """只替换取文档/离线扫描/全量扫描三处，其余用真实 ScanMixin 逻辑。"""
    from plugins.navigator.navigator_scan import ScanMixin

    cls = type("PolicyPanel", (ScanMixin, _PolicyPanel), {})
    panel = cls()
    panel._get_word_document = lambda: (object(), object(), "C:/fake/doc.docx")

    def _offline(_path):
        panel.calls.append("offline")
        return offline_ok

    panel._try_offline_scan = _offline
    panel._do_full_scan = lambda *_args: panel.calls.append("com")
    return panel


def test_default_refresh_uses_offline_scan_only():
    """打开软件/保存后自动刷新：只走离线解析，不触发 COM（不再每次启动全量扫描）。"""
    panel = _policy_panel()
    panel._refresh_captions(force_com=False)
    assert panel.calls == ["offline"]


def test_refresh_button_triggers_com_full_scan():
    """工具栏「刷新列表」才走 COM 全量扫描并更新页码。"""
    panel = _policy_panel()
    panel._refresh_captions(force_com=True)
    assert panel.calls == ["com"]


def test_offline_failure_falls_back_to_com():
    """离线解析失败时仍要能刷新（先试离线，失败再走 COM）。"""
    panel = _policy_panel(offline_ok=False)
    panel._refresh_captions(force_com=False)
    assert panel.calls == ["offline", "com"]
