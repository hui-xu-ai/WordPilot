# tests/test_reference_label_fallback.py
"""锚点键失配时按"文中编号"兜底（用户现场：重建仓库后卡片空白）。

重建/换库后条目的 `ref_key` 会变（`zotero:906` → DOI/标题键），`store.get(anchor_key)`
查不到；旧实现只有 `kind == "label_fallback"`（EndNote 空壳域）才按编号兜底，
于是 Zotero 文档重建库后点引用只得到空白卡（诊断行 `有文献=0/1`）。
"""
from __future__ import annotations

import types

import pytest

from plugins.reference.plugin import ReferencePlugin


class _Ref:
    title = "Smart Film Actuators"
    authors = "Zhang Z H"
    year = "2022"
    container = "Small"
    doi = "10.1002/1"
    note = ""
    cite_label = "2"


class _Store:
    def __init__(self, by_label):
        self._by_label = by_label
        self.looked_up: list[str] = []

    def get(self, key):
        self.looked_up.append(key)
        return None                            # 模拟"重建库后键变了"

    def resolve_key(self, key):
        return key

    def find_by_label(self, label):
        return self._by_label if label == "2" else None

    def anchor_for(self, *_args):
        return None


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _make_plugin(store):
    plugin = ReferencePlugin.__new__(ReferencePlugin)
    plugin._store = store
    plugin._page = None
    plugin._overlay = None
    plugin._dismissed = set()
    plugin._show_signature = ()
    plugin._shown_signature = ()
    plugin._last_point = (0, 0)
    plugin._last_pet_rect = None
    plugin._last_items = []
    plugin._bib_cache = {}
    plugin._note_service = None
    plugin._active_name = "zotero"
    plugin.tr = lambda key, **kw: key
    plugin.ctx = types.SimpleNamespace(ui=types.SimpleNamespace(status=lambda *a: None))
    plugin._watch = types.SimpleNamespace(_last_doc_identity="C:/tmp/Zotero.docx")
    return plugin


def test_cite_item_falls_back_to_label(qapp):
    plugin = _make_plugin(_Store(_Ref()))

    entry = plugin._entry_for_item({"anchor_key": "zotero:906", "label": "2",
                                   "doc_name": "Zotero.docx", "kind": "cite"})

    assert entry is not None
    assert entry.title == "Smart Film Actuators", "按编号兜底后应拿到文献，而不是空白卡"


def test_shell_domain_without_match_stays_silent(qapp):
    """空壳域（EndNote shell）库里也确实没有同编号 → 仍然不弹（保持原行为）。"""
    plugin = _make_plugin(_Store(None))

    entry = plugin._entry_for_item({"anchor_key": "endnote_shell:cite:9", "label": "9",
                                    "doc_name": "EndNote.docx", "kind": "label_fallback"})

    assert entry is None
