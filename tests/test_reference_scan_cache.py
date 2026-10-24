# tests/test_reference_scan_cache.py
"""扫描缓存：文档没改动就不重复扫描（对齐题录导航助手的"预先识别"）。

- 指纹（mtime_ns + size）一致才复用锚点；
- 文档一改动，缓存立即失效（绝不拿旧数据糊弄）；
- 文末列表"已导入"标记同样按指纹判断。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from plugins.reference.doc_index import DocAnchor
from plugins.reference.scan_cache import ScanCache, fingerprint


@pytest.fixture
def cache(tmp_path: Path):
    instance = ScanCache(db_path=tmp_path / "doc_scan.db")
    yield instance
    instance.close()


@pytest.fixture
def docx(tmp_path: Path) -> Path:
    path = tmp_path / "论文.docx"
    path.write_bytes(b"fake docx")
    return path


def test_anchors_roundtrip(cache, docx):
    fp = fingerprint(str(docx))
    anchors = [DocAnchor(10, 14, "doc:论文.docx:cite:1", "1", "endnote_shell"),
               DocAnchor(20, 24, "doc:论文.docx:cite:2", "2", "endnote_shell")]
    cache.save_anchors(str(docx), fp, "论文.docx", anchors)

    loaded = cache.load_anchors(str(docx), fp)
    assert loaded is not None
    assert [(a.anchor_key, a.label) for a in loaded] == [
        ("doc:论文.docx:cite:1", "1"), ("doc:论文.docx:cite:2", "2")]


def test_cache_invalidated_when_document_changes(cache, docx):
    fp = fingerprint(str(docx))
    cache.save_anchors(str(docx), fp, "论文.docx", [DocAnchor(1, 2, "k", "1", "x")])

    docx.write_bytes(b"fake docx with more content")     # 用户编辑并保存
    new_fp = fingerprint(str(docx))
    assert new_fp != fp
    assert cache.load_anchors(str(docx), new_fp) is None, "文档改动后必须重新扫描"


def test_missing_cache_returns_none(cache, docx):
    assert cache.load_anchors(str(docx), fingerprint(str(docx))) is None
    assert cache.load_anchors("", None) is None


def test_bibliography_flag_per_fingerprint(cache, docx):
    fp = fingerprint(str(docx))
    assert cache.bibliography_done(str(docx), fp) is False

    cache.mark_bibliography(str(docx), fp, "论文.docx")
    assert cache.bibliography_done(str(docx), fp) is True

    docx.write_bytes(b"changed")
    assert cache.bibliography_done(str(docx), fingerprint(str(docx))) is False


def test_saving_anchors_keeps_bibliography_flag(cache, docx):
    fp = fingerprint(str(docx))
    cache.mark_bibliography(str(docx), fp, "论文.docx")
    cache.save_anchors(str(docx), fp, "论文.docx", [DocAnchor(1, 2, "k", "1", "x")])
    assert cache.bibliography_done(str(docx), fp) is True, "存锚点不能把已导入标记冲掉"


def test_corrupted_anchor_json_is_ignored(cache, docx):
    fp = fingerprint(str(docx))
    cache.save_anchors(str(docx), fp, "论文.docx", [])
    conn = cache._connect()
    with conn:
        conn.execute("UPDATE doc_scan SET anchors = 'not json' WHERE path = ?", (str(docx),))
    assert cache.load_anchors(str(docx), fp) is None, "缓存损坏按未缓存处理，不崩"
