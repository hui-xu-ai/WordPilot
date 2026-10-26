# tests/test_reference_watch_bridge_switch.py
"""切文档必须立刻切到该文档绑定的仓库（用户现场：切了文档，仓库没跟着切）。

根因：`WordWatch.documentChanged` 只带**文件名**（`ActiveDocument.Name`），
而绑定表 `doc_bindings()` 按**全路径**存 → 用文件名去查永远查不到，
只有扫描线程里的 `_ensure_scan_library()` 才顺带切一次（看起来要等好几秒）。
"""
from __future__ import annotations

import types

from plugins.reference.watch_bridge import on_document_switched


class _Registry:
    def doc_bindings(self) -> dict:
        return {"C:/tmp/论文.docx": "zotero"}


class _Plugin:
    def __init__(self, identity: str = "C:/tmp/论文.docx") -> None:
        self._watch = types.SimpleNamespace(_last_doc_identity=identity)
        self._libraries = _Registry()
        self._active_name = "EndNote"
        self._page = None
        self._overlay = None
        self._dismissed = {"old"}
        self._shown_signature = ("old",)
        self.switched: list[str] = []

    def switch_library(self, name: str, update_page: bool = True) -> None:
        self.switched.append(name)
        self._active_name = name


def test_switch_uses_full_path_from_watch():
    plugin = _Plugin()

    on_document_switched(plugin, "论文.docx")          # 信号里只有文件名

    assert plugin.switched == ["zotero"], "必须用全路径查到绑定并切库"
    assert plugin._dismissed == set(), "旧文档的'不再提示'不能带到新文档"
    assert plugin._shown_signature == ()


def test_switch_falls_back_to_name_when_identity_missing():
    """拿不到全路径时退回文件名（至少不比改前更差）。"""
    plugin = _Plugin(identity="")
    plugin._libraries = type("_R", (), {"doc_bindings": lambda self: {
        "论文.docx": "EndNote"}})()

    on_document_switched(plugin, "论文.docx")

    assert plugin.switched == []
