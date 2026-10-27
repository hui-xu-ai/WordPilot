# tests/test_reference_dismiss_scope.py
"""点 ✕ 只压制**那一组**引用：换成另一个引用必须还能弹（用户现场：关一次之后换引用也没反应）。

现场日志（app.log 08:52:12~16）：`命中引用但没有可显示卡片（库=EndNote，items=cite:5/6）`
每 0.3s 刷一次 —— 卡片明明在 08:52:03 显示过，说明是被 `_dismissed` 永久压制了。
"""
from __future__ import annotations

import types

import pytest

from plugins.reference.plugin import ReferencePlugin


class _Ref:
    title = "Functional Fibers"
    authors = "Xiong J Q"
    year = "2021"
    container = "Advanced Materials"
    doi = ""
    note = ""
    cite_label = "5"


class _Store:
    def get(self, _key):
        return _Ref()

    def resolve_key(self, key):
        return f"bib:{key}"

    def find_by_label(self, _label):
        return _Ref()

    def anchor_for(self, *_args):
        return None

    def upsert(self, reference, **_kw):
        return reference

    def reload_if_changed(self):
        return False


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _plugin():
    plugin = ReferencePlugin.__new__(ReferencePlugin)
    plugin._store = _Store()
    plugin._page = None
    plugin._overlay = None
    plugin._dismissed = set()
    plugin._dismissed_signature = ()
    plugin._shown_signature = ()
    plugin._last_items = []
    plugin._last_point = (0, 0)
    plugin._last_pet_rect = None
    plugin._bib_cache = {}
    plugin._note_service = None
    plugin._active_name = "EndNote"
    plugin.tr = lambda key, **kw: key
    plugin.ctx = types.SimpleNamespace(ui=types.SimpleNamespace(status=lambda *a: None))
    plugin._watch = types.SimpleNamespace(_last_doc_identity="C:/tmp/EndNote.docx")
    plugin._pet_anchor_point = lambda: None
    plugin._pet_preferred = staticmethod(lambda: True)
    return plugin


def _item(label: str) -> dict:
    return {"anchor_key": f"doc:EndNote文献论文测试.docx:cite:{label}", "label": label,
            "doc_name": "EndNote文献论文测试.docx", "kind": "endnote_shell", "reference": None}


def test_dismiss_only_suppresses_same_citation_group(qapp):
    plugin = _plugin()
    plugin._last_items = [_item("5"), _item("6")]      # ✕ 关掉的是这一组
    plugin._on_overlay_dismissed("doc:EndNote文献论文测试.docx:cite:5")

    assert plugin._entry_for_item(_item("5")) is None, "刚点过 ✕ 的那一组要继续压制"

    payload = {"items": [_item("7"), _item("8")], "point": (10, 10),
               "doc_path": "C:/tmp/EndNote.docx", "doc_name": "EndNote文献论文测试.docx"}
    plugin._on_citation_found(payload)

    assert plugin._dismissed == set(), "换了一组引用就该解除压制"
    assert plugin._entry_for_item(_item("7")) is not None, "换引用必须还能弹"
