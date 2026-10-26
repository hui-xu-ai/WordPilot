# tests/test_reference_stale_payload.py
"""切文档瞬间的"过期命中"必须丢弃（用户现场：切库后卡片空白、库被串数据）。

现场日志（app.log 22:42:32）：
    已切换到文献仓库：zotero
    从文末参考文献列表补入 43 条（EndNote文献论文测试.docx）   ← A 文档的条目写进 B 库
    显示引用卡片：1 张 … 库=zotero 有文献=0/1                  ← A 的锚点在 B 库里查不到 → 空白卡
"""
from __future__ import annotations

import types

import pytest

from plugins.reference.plugin import ReferencePlugin


@pytest.fixture(scope="module")
def qapp():
    """建悬浮卡需要 QApplication；没有它 Qt 会直接把进程打断（不是断言失败）。"""
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


class _Store:
    def __init__(self):
        self.reloads = 0

    def reload_if_changed(self):
        self.reloads += 1

    def get(self, _key):
        return None

    def resolve_key(self, key):
        return key

    def anchor_for(self, *_args):
        return None

    def find_by_label(self, _label):
        return None


def _make_plugin(current_doc: str):
    plugin = ReferencePlugin.__new__(ReferencePlugin)
    plugin._store = _Store()
    plugin._page = None
    plugin._overlay = None
    plugin._dismissed = set()
    plugin._shown_signature = ()
    plugin._last_point = (0, 0)
    plugin._last_pet_rect = None
    plugin._last_items = []
    plugin._bib_cache = {}
    plugin._note_service = None
    plugin._active_name = "zotero"
    plugin.tr = lambda key, **kw: key
    plugin.ctx = types.SimpleNamespace(ui=types.SimpleNamespace(status=lambda *a: None))
    plugin._watch = types.SimpleNamespace(_last_doc_identity=current_doc)
    plugin.imported: list[str] = []
    plugin._import_bibliography = lambda doc_path, doc_name: plugin.imported.append(doc_path)
    return plugin


def test_payload_from_previous_document_is_dropped(qapp):
    plugin = _make_plugin("C:/tmp/Zotero.docx")

    plugin._on_citation_found({
        "items": [{"anchor_key": "doc:EndNote.docx:cite:2", "label": "2",
                   "doc_name": "EndNote.docx"}],
        "point": (200, 200),
        "doc_path": "C:/tmp/EndNote.docx",
        "doc_name": "EndNote.docx",
    })

    assert plugin.imported == [], "过期命中不能触发入库（会把 A 的条目写进 B 库）"
    assert plugin._store.reloads == 0, "过期命中不该再去读当前库"
    assert plugin._overlay is None, "过期命中不该画卡片"


def test_payload_for_current_document_still_processed(qapp):
    plugin = _make_plugin("C:/tmp/Zotero.docx")

    plugin._on_citation_found({
        "items": [{"anchor_key": "zotero:906", "label": "2", "doc_name": "Zotero.docx"}],
        "point": (200, 200),
        "doc_path": "C:/tmp/Zotero.docx",
        "doc_name": "Zotero.docx",
    })

    assert plugin.imported == ["C:/tmp/Zotero.docx"], "当前文档的命中要照常处理"


def test_identity_by_filename_is_not_stale(qapp):
    """`WordWatch` 读 FullName 失败时会退化成**文件名**，不能因此丢掉当前文档的命中。

    实测（app.log 00:23:03）：点 Zotero 的引用被"丢弃过期命中"拦掉 → ②③④ 全不出现。
    """
    plugin = _make_plugin("Zotero文献论文测试.docx")     # 只有文件名（退化情形）

    plugin._on_citation_found({
        "items": [{"anchor_key": "zotero:906", "label": "2", "doc_name": "Zotero.docx"}],
        "point": (200, 200),
        "doc_path": "D:/papers/Zotero文献论文测试.docx",   # payload 带全路径
        "doc_name": "Zotero文献论文测试.docx",
    })

    assert plugin.imported, "同一篇文档（全路径 vs 文件名）不能被当成过期命中"
