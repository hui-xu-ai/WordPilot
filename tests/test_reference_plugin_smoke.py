# tests/test_reference_plugin_smoke.py
"""冒烟：插件的关键回调必须真的能被调用（防"少装饰器/少 self"这类致命小错）。

教训（用户现场）：`_pet_preferred()` 丢了 `@staticmethod`，
`self._pet_preferred()` 变成"多传一个 self"→ `TypeError`，
而它在 `_on_citation_found()` 的第一行——于是**所有引用点击都不弹窗**，
pytest 全绿（没有用例真正调用过这条路径）。
"""
from __future__ import annotations

import pytest


def _plugin():
    from plugins.reference.plugin import ReferencePlugin

    plugin = ReferencePlugin.__new__(ReferencePlugin)
    plugin.ctx = None
    plugin._page = None
    plugin._overlay = None
    plugin._dismissed = set()
    plugin._shown_signature = ()
    plugin._store = None
    plugin.tr = lambda key, **kw: key
    return plugin


def test_pet_helpers_callable():
    plugin = _plugin()
    assert plugin._pet_preferred() is True
    plugin._pet_anchor_point()            # 不能抛（没有宠物时返回 None）


def test_citation_handler_runs_without_type_error():
    """没有库/没有条目时也应安静返回，不能抛 TypeError。"""
    plugin = _plugin()
    plugin._on_citation_found({"items": [], "doc_name": "论文.docx"})
    plugin._on_citation_found({})
