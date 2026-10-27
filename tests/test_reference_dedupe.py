# tests/test_reference_dedupe.py
"""清理"无编号且与有编号条目重复"的历史文献（合并到有编号那条，键保留为别名）。

现场：旧版同步本文把文末列表 + 域元数据都入库，Zotero 库 43 条 = 25 有编号 + 18 无编号且重复。
"""
from __future__ import annotations


def _plugin(tmp_path, refs):
    from plugins.reference import plugin as plugin_module
    from plugins.reference.reference_store import ReferenceStore

    plugin = plugin_module.ReferencePlugin.__new__(plugin_module.ReferencePlugin)
    plugin._store = ReferenceStore(directory=tmp_path)
    for ref in refs:
        plugin._store.upsert(ref)
    return plugin


def _ref(key, title, label="", doi="", aliases=()):
    from plugins.reference.reference_store import Reference

    return Reference(ref_key=key, title=title, cite_label=label, doi=doi,
                     aliases=list(aliases))


def test_duplicate_unlabeled_entry_is_merged(tmp_path):
    plugin = _plugin(tmp_path, [
        _ref("bib:1", "基于双孔射流推进的仿生扇贝机器人", label="2"),
        _ref("zotero:906", "基于双孔射流推进的仿生扇贝机器人"),      # 重复，无编号
    ])

    merged, kept = plugin.dedupe_references()

    assert (merged, kept) == (1, 1)
    remaining = plugin._store.all()
    assert len(remaining) == 1, "重复条目应被并掉"
    kept_ref = remaining[0]
    assert kept_ref.ref_key == "bib:1" and kept_ref.cite_label == "2"
    assert "zotero:906" in kept_ref.aliases, "旧键要保留为别名，旧锚点仍能解析"
    assert plugin._store.get("zotero:906") is kept_ref, "按旧键也要能取到同一篇"


def test_genuinely_distinct_rows_are_untouched(tmp_path):
    plugin = _plugin(tmp_path, [
        _ref("bib:1", "有编号的文献", label="1"),
        _ref("zotero:1", "另一篇没编号的文献（列表里没有）"),        # 不是重复，保留
    ])

    merged, kept = plugin.dedupe_references()

    assert merged == 0
    assert len(plugin._store.all()) == 2


def test_doi_match_is_used_for_merging(tmp_path):
    plugin = _plugin(tmp_path, [
        _ref("bib:1", "English Title", label="3", doi="10.1002/aisy.202300299"),
        _ref("doi:10.1002/aisy.202300299", "English Title (short)"),
    ])

    merged, _kept = plugin.dedupe_references()

    assert merged == 1
    assert len(plugin._store.all()) == 1
