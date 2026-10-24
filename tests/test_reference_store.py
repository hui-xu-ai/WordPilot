# tests/test_reference_store.py
"""参考文献数据层：CSV 是唯一事实源，写盘要安全，外部替换要能被感知。

对应计划 P1 的验收点：
- 中文/逗号/引号/换行的往返；BOM 输入能读；
- 原子写：写失败不破坏原文件；
- 备份轮转只保留最近 N 份；
- 用户用自己维护的 CSV 覆盖 → 自动重载，且程序**不会**把旧内容写回去；
- 缺列补默认、用户自加列原样保留；
- 别名归一（同一篇文献多把钥匙）；锚点绑定幂等。
"""
from __future__ import annotations

import csv
import os
from pathlib import Path

import pytest

from plugins.reference.reference_store import (
    BACKUP_KEEP,
    REF_COLUMNS,
    Anchor,
    Reference,
    ReferenceStore,
    ReferenceStoreError,
)


@pytest.fixture
def store(tmp_path: Path):
    return ReferenceStore(directory=tmp_path)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def test_roundtrip_preserves_tricky_text(store):
    ref = Reference(
        ref_key="zotero:934",
        title='Global biodiversity, "Nature" 模式',
        authors="Tittensor DP; Mora C",
        note="第一行\n第二行，含逗号, 和引号\"x\"",
        tags=["核心", "待核实"],
        aliases=["doi:10.1038/nature02593"],
    )
    store.upsert(ref)

    reloaded = ReferenceStore(directory=store.directory)
    got = reloaded.get("zotero:934")
    assert got is not None
    assert got.title == ref.title
    assert got.note == ref.note
    assert got.tags == ["核心", "待核实"]
    assert reloaded.get("doi:10.1038/nature02593") is got, "别名必须能解析到同一条"


def test_reads_bom_and_missing_columns(store, tmp_path):
    path = tmp_path / "references.csv"
    path.write_text("\ufeffref_key,title,my_own_column\nzotero:1,只填了标题,自定义值\n",
                    encoding="utf-8")
    store.load(force=True)

    ref = store.get("zotero:1")
    assert ref is not None and ref.title == "只填了标题"
    assert ref.authors == "" and ref.starred is False, "缺列要补默认值"
    assert ref.extra.get("my_own_column") == "自定义值"

    store.set_note("zotero:1", note="补个备注")
    rows = _read_csv(path)
    assert "my_own_column" in rows[0], "用户自加列不能被吞掉"
    assert rows[0]["my_own_column"] == "自定义值"


def test_atomic_write_keeps_original_when_replace_fails(store, monkeypatch):
    store.upsert(Reference(ref_key="zotero:1", title="原始标题"))
    before = store.refs_path.read_bytes()

    def boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(ReferenceStoreError):
        store.upsert(Reference(ref_key="zotero:1", title="新标题"))

    assert store.refs_path.read_bytes() == before, "写失败时原文件必须原样保留"
    assert not list(store.directory.glob("*.tmp")), "失败后不能留下临时文件"


def test_backup_rotation_keeps_latest_only(store):
    store.upsert(Reference(ref_key="zotero:1", title="v0"))
    for index in range(BACKUP_KEEP + 3):
        store.upsert(Reference(ref_key="zotero:1", title=f"v{index + 1}"))

    backups = sorted(store.directory.glob("references.csv.bak-*"))
    assert len(backups) == BACKUP_KEEP, f"备份应只保留 {BACKUP_KEEP} 份，实际 {len(backups)}"


def test_external_replacement_is_detected_and_not_overwritten(store, tmp_path):
    store.upsert(Reference(ref_key="zotero:1", title="我的旧数据"))
    original = store.refs_path.read_bytes()

    # 用户拿自己维护的文件整份覆盖
    store.refs_path.write_text(
        "\ufeffref_key,title,note\nzotero:1,用户手改的标题,用户写的备注\n",
        encoding="utf-8")
    assert store.reload_if_changed() is True

    ref = store.get("zotero:1")
    assert ref is not None and ref.title == "用户手改的标题"
    assert ref.note == "用户写的备注"
    assert store.refs_path.read_bytes() != original

    # 只读不写：不能把内存里的旧值刷回去
    reference_bytes = store.refs_path.read_bytes()
    store.reload_if_changed()
    assert store.refs_path.read_bytes() == reference_bytes


def test_upsert_merges_without_clobbering_note(store):
    store.upsert(Reference(ref_key="zotero:1", title="标题"))
    store.set_note("zotero:1", note="我写的备注")

    store.upsert(Reference(ref_key="zotero:1", year="2004", doi="10.1/x"))
    ref = store.get("zotero:1")
    assert ref is not None
    assert ref.note == "我写的备注", "再次导入元数据不能清掉备注"
    assert ref.year == "2004" and ref.doi == "10.1/x"

    store.upsert(Reference(ref_key="zotero:1", note="显式覆盖"), overwrite_note=True)
    assert store.get("zotero:1").note == "显式覆盖"


def test_starred_variants_are_parsed(store, tmp_path):
    path = tmp_path / "references.csv"
    path.write_text(
        "\ufeffref_key,starred\nzotero:1,1\nzotero:2,true\nzotero:3,0\nzotero:4,★\n",
        encoding="utf-8")
    store.load(force=True)
    assert [store.get(f"zotero:{i}").starred for i in (1, 2, 3, 4)] == [True, True, False, True]


def test_delete_removes_alias_index(store):
    store.upsert(Reference(ref_key="zotero:1", aliases=["doi:10.1/x"]))
    assert store.delete("doi:10.1/x") is True
    assert store.get("zotero:1") is None
    assert store.get("doi:10.1/x") is None, "删除后别名不能还指向旧条目"


def test_anchor_binding_is_idempotent(store):
    store.bind_anchor("论文.docx", "1", "zotero:934", first_para="第一段")
    store.bind_anchor("论文.docx", "2", "zotero:935")
    store.bind_anchor("论文.docx", "1", "zotero:934", first_para="第一段")

    anchors = store.anchors("论文.docx")
    assert len(anchors) == 2, "同一 (文档, 编号) 只保留一条"
    assert store.anchor_for("论文.docx", "1").anchor_key == "zotero:934"

    reloaded = ReferenceStore(directory=store.directory)
    assert len(reloaded.anchors("论文.docx")) == 2
    assert reloaded.unbind_anchor("论文.docx", "2") is True
    assert len(reloaded.anchors("论文.docx")) == 1


def test_anchor_survives_document_copy(store):
    """文档另存为新版本：doc_name 变了但 anchor_key（文献身份）不变，备注跟着身份走。"""
    store.upsert(Reference(ref_key="zotero:934", title="某文献", note="我的备注"))
    store.bind_anchor("论文v1.docx", "1", "zotero:934")
    store.bind_anchor("论文v2.docx", "1", "zotero:934")

    for doc in ("论文v1.docx", "论文v2.docx"):
        anchor = store.anchor_for(doc, "1")
        assert store.get(anchor.anchor_key).note == "我的备注"


def test_source_signature_changes_after_write(store):
    signature_before = store.source_signature()
    store.upsert(Reference(ref_key="zotero:1", title="x"))
    assert store.source_signature() != signature_before


def test_unknown_key_returns_none(store):
    assert store.get("zotero:does-not-exist") is None
    assert store.get("") is None
    assert store.resolve_key(None) is None


def test_anchor_rows_without_key_are_ignored(store, tmp_path):
    (tmp_path / "anchors.csv").write_text(
        "\ufeffdoc_name,cite_label,anchor_key\n论文.docx,1,\n论文.docx,2,zotero:1\n",
        encoding="utf-8")
    store.load(force=True)
    assert [a.cite_label for a in store.anchors()] == ["2"]


def test_reference_columns_are_stable_contract(store):
    """列名是给用户看/手改的契约，改名会破坏已存在的 CSV，必须有测试兜住。"""
    assert REF_COLUMNS[0] == "ref_key"
    assert "note" in REF_COLUMNS and "aliases" in REF_COLUMNS
    assert Anchor.__dataclass_fields__.keys() >= {"doc_name", "cite_label", "anchor_key"}
