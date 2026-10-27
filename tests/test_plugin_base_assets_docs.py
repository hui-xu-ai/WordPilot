# tests/test_asset_convention_docs.py
"""资产约定必须写在插件开发文档与 SDK 里（新插件照约定放数据，清理才不会误删）。"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_plugin_base_docstring_documents_asset_rule():
    import core.sdk.plugin_base as module

    module_doc = module.__doc__ or ""
    assert "config/plugins/<plugin_id>/" in module_doc
    assert "永不清理" in module_doc

    assets_doc = module.PluginBase.assets.__doc__ or ""
    assert "ctx.data_dir" in assets_doc


def test_context_data_dir_documents_protection():
    from core.sdk.context import PluginContext

    doc = PluginContext.data_dir.fget.__doc__ or ""   # type: ignore[attr-defined]

    assert "config/plugins/<id>/" in doc
    assert "资产" in doc


def test_plugin_guide_has_asset_section():
    text = (ROOT / "docs/PLUGIN_GUIDE.md").read_text(encoding="utf-8")

    assert "资产存放约定" in text
    assert "ctx.data_dir" in text
    assert "永不清理" in text


def test_architecture_documents_asset_vs_junk():
    text = (ROOT / "docs/ARCHITECTURE.md").read_text(encoding="utf-8")

    assert "插件资产：整目录永不清理" in text
    assert "housekeeping" in text
