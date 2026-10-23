# tests/test_ignore_cache.py
"""P10-B3：忽略库原子写 + 损坏文件备份。

历史实现「截断写 + 读取失败 print 一下返回空表」：一次写中断或文件损坏，
下一次 `add_ignore` 就会把用户忽略记录全部清空（且打包后 print 不可见）。
"""
from __future__ import annotations

import json

import pytest

from core.services import ignore_cache as ig_mod


@pytest.fixture
def ignore_db(tmp_path, monkeypatch):
    db = tmp_path / "ignore_db.json"
    monkeypatch.setattr(ig_mod, "IGNORE_DB", db)
    return db


def test_ignore_add_persists_atomically(ignore_db):
    manager = ig_mod.IgnoreCacheManager()
    manager.add_ignore("这句话我要忽略。")
    assert manager.is_ignored("这句话我要忽略。")
    assert json.loads(ignore_db.read_text(encoding="utf-8")), "写入后应可解析"
    assert not [p.name for p in ignore_db.parent.iterdir() if p.name.endswith(".tmp")]


def test_corrupt_ignore_db_is_backed_up_and_not_cleared(ignore_db):
    ignore_db.write_text("[坏的", encoding="utf-8")
    manager = ig_mod.IgnoreCacheManager()
    assert manager.ignore_hashes == []
    backups = list(ignore_db.parent.glob("ignore_db.json.bad-*"))
    assert backups, "损坏的忽略库必须先备份"
    assert backups[0].read_text(encoding="utf-8") == "[坏的"


def test_ignore_remove_persists(ignore_db):
    manager = ig_mod.IgnoreCacheManager()
    manager.add_ignore("要删掉的句子")
    manager.remove_ignore("要删掉的句子")
    assert not manager.is_ignored("要删掉的句子")
    assert json.loads(ignore_db.read_text(encoding="utf-8")) == []
