# tests/test_housekeeping.py
"""存储清理：只动运行垃圾，**永不**碰用户资产。

用户要求（2026-10-27）：主程序要能区分资产与垃圾（日志/运行垃圾/插件垃圾），不能误删。
"""
from __future__ import annotations

import os
import sqlite3
import time

import pytest


@pytest.fixture
def fake_config(tmp_path):
    (tmp_path / "app.log").write_text("log" * 300, encoding="utf-8")
    (tmp_path / "errors.log").write_text("err", encoding="utf-8")
    (tmp_path / "api_calls.log").write_text("api", encoding="utf-8")
    (tmp_path / "proof_cache.db").write_text("cache", encoding="utf-8")
    (tmp_path / "proof_cache.db-wal").write_text("w" * 4000, encoding="utf-8")
    (tmp_path / "word_caption_cache.db").write_text("cache", encoding="utf-8")
    (tmp_path / ".key").write_text("secret", encoding="utf-8")
    (tmp_path / "Image.docx").write_text("img", encoding="utf-8")
    (tmp_path / "MWindow_config.json").write_text("{}", encoding="utf-8")
    (tmp_path / "user.qss").write_text("qss", encoding="utf-8")
    root = tmp_path / "references"
    (root / "libraries" / "库A").mkdir(parents=True)
    (root / "libraries" / "库A" / "references.csv").write_text("ref_key\n", encoding="utf-8")
    (root / "libraries" / "库A" / "doc_scan.db").write_text("scan", encoding="utf-8")
    (root / "libraries" / "库A" / "index.db").write_text("index", encoding="utf-8")
    (root / "notes.csv").write_text("note_id\n", encoding="utf-8")
    (root / "doc_bindings.json").write_text("{}", encoding="utf-8")
    return tmp_path


def test_inventory_classifies_junk_and_keeps_assets(fake_config):
    from core.services import housekeeping

    groups = {group.key: group for group in housekeeping.inventory(fake_config)}

    names = lambda key: sorted(path.name for path in groups[key].files)  # noqa: E731
    assert names("logs") == ["api_calls.log", "app.log", "errors.log"]
    assert "proof_cache.db-wal" in names("wal")
    assert names("cache") == ["doc_scan.db", "proof_cache.db", "word_caption_cache.db"]

    everything = [path.name for group in groups.values() for path in group.files]
    for protected in ("references.csv", "notes.csv", "doc_bindings.json", "doc_scan.db"):
        if protected == "doc_scan.db":
            continue                     # 缓存库本身可清，但不能连带 references.csv
        assert protected not in everything, f"{protected} 是用户资产，不能出现在清理清单里"


def test_clean_removes_junk_only(fake_config):
    from core.services import housekeeping

    result = housekeeping.clean(["logs", "wal", "cache"], root=fake_config)

    assert result["logs"][0] == 3
    assert sum(count for count, _ in result.values()) >= 6
    left = sorted(path.name for path in fake_config.rglob("*") if path.is_file())
    assert "references.csv" in left and "notes.csv" in left
    assert ".key" in left and "Image.docx" in left and "MWindow_config.json" in left
    assert "app.log" not in left and "proof_cache.db-wal" not in left


def test_trash_keeps_recent_backups_but_can_be_emptied(fake_config):
    from core.services import housekeeping

    trash = fake_config / "references" / "libraries" / "_trash"
    old = trash / "旧库-1" / "index.db"
    new = trash / "旧库-2" / "index.db"
    for path in (old, new):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    past = time.time() - 30 * 86400
    os.utime(old, (past, past))

    default = housekeeping.clean(["trash"], root=fake_config)
    assert default["trash"][0] == 1, "默认只清超过 7 天的备份"
    assert new.exists(), "新的备份要留着（可回滚）"

    everything = housekeeping.clean(["trash"], root=fake_config, keep_days=0)
    assert everything["trash"][0] == 1
    assert not new.exists(), "keep_days=0 表示清空回收站"


def test_checkpoint_wal_shrinks_wal(tmp_path):
    from core.services import housekeeping

    db = tmp_path / "proof_cache.db"
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t (x TEXT)")
    conn.executemany("INSERT INTO t VALUES (?)", [("y" * 200,) for _ in range(200)])
    conn.commit()
    wal = tmp_path / "proof_cache.db-wal"
    before = wal.stat().st_size if wal.exists() else 0
    conn.close()

    handled, freed = housekeeping.checkpoint_wal(root=tmp_path)

    assert handled == 1
    after = wal.stat().st_size if wal.exists() else 0
    assert after <= before, "截断后 -wal 不应变大"
    assert freed >= 0
