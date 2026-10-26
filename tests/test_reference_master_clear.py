# tests/test_reference_master_clear.py
"""更多菜单里的「删除总库备注」：备份后可回滚，子库备注不受影响。

另外覆盖两条用户反馈：
- 宠物在屏幕右侧时，悬浮卡应贴宠物（service 拿不到坐标要退回窗口几何）；
- 切换 Word 文档后，旧文档的"点 ✕ 不再提示"不能影响新文档。
"""
from __future__ import annotations

import pytest


def test_clear_master_backs_up_and_resets(tmp_path):
    from plugins.reference.notes import NoteStore
    from plugins.reference.plugin import ReferencePlugin
    from plugins.reference.reference_store import ReferenceStore

    master = NoteStore(directory=tmp_path)
    master.add("doi:1", "历史干扰备注")

    plugin = ReferencePlugin.__new__(ReferencePlugin)
    plugin._master_notes = master
    plugin._store = ReferenceStore(directory=tmp_path / "子库")
    plugin.refresh_page = lambda: None

    assert plugin.clear_master_notes() is True

    backups = list(tmp_path.glob("notes.csv.bak-*"))
    assert backups, "删除总库前必须留下备份"
    assert "历史干扰备注" in backups[0].read_text(encoding="utf-8-sig")
    assert master.notes_for("doi:1") == [], "内存里也要清空"


def test_pet_anchor_falls_back_to_window(monkeypatch):
    """PetService 没有坐标时，用宠物窗口几何算贴靠点（否则卡片跑到屏幕另一边）。"""
    from plugins.reference import pet_anchor

    class _Service:
        def anchor_point(self):
            return None

    class _Window:
        def isVisible(self):
            return True

        def frameGeometry(self):
            class _Rect:
                def right(self):
                    return 1800

                def top(self):
                    return 40

            return _Rect()

    class _Host:
        def window(self):
            return _Window()

    monkeypatch.setattr("core.services.pet_service.PetService.instance",
                        staticmethod(lambda: _Service()), raising=False)
    monkeypatch.setattr("core.services.pet_host.PetHost.instance",
                        staticmethod(lambda: _Host()), raising=False)

    assert pet_anchor.pet_anchor_point() == (1808, 40)


def test_switch_document_clears_dismissed(monkeypatch):
    from plugins.reference.plugin import ReferencePlugin

    plugin = ReferencePlugin.__new__(ReferencePlugin)
    plugin._dismissed = {"doc:旧.docx:cite:2"}
    plugin._shown_signature = ("doc:旧.docx:cite:2",)
    plugin._page = None
    plugin._overlay = None

    plugin._on_document_changed("新.docx")

    assert plugin._dismissed == set(), "换文档后不能沿用旧的忽略记录"
    assert plugin._shown_signature == (), "去重签名也要重置"
