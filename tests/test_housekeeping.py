# tests/test_housekeeping.py
"""存储清理：只动运行垃圾，**永不**碰用户资产；被占用的文件要能截断或排队。

用户要求（2026-10-27）：主程序要能区分资产与垃圾，且**实际能清掉**（现场：点了一堆按钮没反应，
因为 app.log 被 logging 处理器占着删不掉、界面又按 7 天把 _trash 算成 0 个）。
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
    (tmp_path / "~$Image.docx").write_text("lock", encoding="utf-8")
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
    assert names("wal") == [], "在用数据库的 -wal 不能删（靠 checkpoint 截断）"
    assert "~$Image.docx" in names("temp")
    assert names("cache") == ["doc_scan.db", "proof_cache.db", "word_caption_cache.db"]

    everything = [path.name for group in groups.values() for path in group.files]
    for protected in ("references.csv", "notes.csv", "doc_bindings.json"):
        assert protected not in everything, f"{protected} 是用户资产，不能出现在清理清单里"


def test_clean_removes_junk_only(fake_config):
    from core.services import housekeeping

    result = housekeeping.clean(["logs", "wal", "cache", "temp"], root=fake_config)

    assert result["logs"][0] == 3
    left = sorted(path.name for path in fake_config.rglob("*") if path.is_file())
    assert "references.csv" in left and "notes.csv" in left
    assert ".key" in left and "Image.docx" in left and "MWindow_config.json" in left
    assert "app.log" not in left, "日志要清掉"
    assert (fake_config / "proof_cache.db-wal").exists(), "在用库的 -wal 交给 checkpoint 处理"
    assert "~$Image.docx" not in left, "Word 锁文件属于临时垃圾"


def test_busy_log_is_truncated_not_skipped(fake_config):
    """日志被 logging 处理器占着时删不掉 —— 必须**截断**，否则用户点了按钮毫无反应。"""
    from core.services import housekeeping

    log = fake_config / "app.log"
    held = log.open("a", encoding="utf-8")           # 模拟 Handler 持有
    try:
        held.write("running")
        held.flush()
        result = housekeeping.clean(["logs"], root=fake_config)
        assert result["logs"][1] > 0, "应当报告释放的字节数"
        assert log.exists() and log.stat().st_size == 0, "内容应被清空"
    finally:
        held.close()


def test_busy_cache_is_queued_for_next_start(fake_config):
    """运行中的缓存库删不掉 → 记录 pending，下次启动（DB 未打开）时真正删掉。"""
    from core.services import housekeeping

    db = fake_config / "proof_cache.db"
    held = db.open("a", encoding="utf-8")
    try:
        held.write("busy")
        held.flush()
        result = housekeeping.clean(["cache"], root=fake_config)
        assert result["cache"][2] >= 1, "应报告被占用数量"
        assert db.exists()
        housekeeping.mark_pending(["cache"], root=fake_config)
    finally:
        held.close()

    done = housekeeping.run_pending(root=fake_config)

    assert done["cache"][0] >= 1
    assert not db.exists(), "下次启动应真正删掉"
    assert not (fake_config / housekeeping.PENDING_FILE).exists(), "标记文件用完即删"


def test_trash_shows_all_and_empties(fake_config):
    from core.services import housekeeping

    trash = fake_config / "references" / "libraries" / "_trash"
    old = trash / "旧库-1" / "index.db"
    new = trash / "旧库-2" / "index.db"
    for path in (old, new):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    past = time.time() - 30 * 86400
    os.utime(old, (past, past))

    shown = {group.key: group for group in housekeeping.inventory(fake_config)}["trash"]
    assert shown.count == 2, "界面要显示全部备份（否则显示 0 个、按钮点了没反应）"

    recent_only = housekeeping.clean(["trash"], root=fake_config, keep_days=7)
    assert recent_only["trash"][0] == 1, "默认清理只动超过 7 天的备份"
    assert new.exists()

    everything = housekeeping.clean(["trash"], root=fake_config, keep_days=0)
    assert everything["trash"][0] == 1
    assert not new.exists(), "keep_days=0 = 清空回收站"


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
    assert after <= before
    assert freed >= 0
