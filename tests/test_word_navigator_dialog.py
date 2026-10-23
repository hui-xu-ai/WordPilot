# tests/test_word_navigator_dialog.py
"""P10-B10：导航对话框关闭后，排队中的批处理回调必须立刻退出。

审计 H17：`closeEvent` 只停保存监测并 `CoUninitialize`，但扫描/批处理是用
`QTimer.singleShot` 排队的，窗口关闭后回调仍会触发——在已释放的 COM 公寓上访问
`self._scan_doc`，且中途退出会让 `_scanning` 永远为 True、`ScreenUpdating` 可能停在 False。
"""
from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def dialog_cls():
    from plugins.navigator.word_navigator_dialog import WordNavigatorDialog

    return WordNavigatorDialog


def _bare(dialog_cls):
    """不跑 __init__（要 Word COM + Qt 控件），只注入本用例需要的属性。"""
    dlg = dialog_cls.__new__(dialog_cls)
    dlg._closing = True
    dlg._scan_doc = None          # 守卫缺失时会 AttributeError
    dlg._scan_seq_info = []
    dlg._scan_index = 0
    dlg._scan_total = 0
    dlg._seq_index = 0
    return dlg


def test_scan_batch_noops_after_close(dialog_cls):
    dlg = _bare(dialog_cls)
    dlg._scan_fields_batch()      # 不应抛、不应访问 COM


def test_process_batch_noops_after_close(dialog_cls):
    dlg = _bare(dialog_cls)
    dlg._process_seq_batch()


def test_auto_refresh_noops_after_close(dialog_cls):
    refreshed: list[int] = []
    dlg = _bare(dialog_cls)
    dlg._refresh_captions = lambda *a, **k: refreshed.append(1)
    dlg.isVisible = lambda: True
    dlg._scanning = False
    dlg._auto_refresh()
    assert refreshed == [], "关闭后不应再刷新题注"


def test_teardown_sets_flag_before_com_uninit(dialog_cls):
    """`_teardown` 必须先把 _closing 置 True，再做任何清理。"""
    events: list[str] = []
    dlg = _bare(dialog_cls)
    dlg._closing = False
    dlg._com_ok = False           # 不真的调 CoUninitialize
    dlg._stop_save_monitor = lambda: events.append("stop_monitor")
    dlg._restore_screen_updating = lambda *a: events.append("restore_screen")
    dlg.cache = type("C", (), {"close": staticmethod(lambda: events.append("cache_close"))})()
    dlg.doc = "doc"
    dlg.word = "word"

    dlg._teardown()

    assert dlg._closing is True
    assert events[:3] == ["stop_monitor", "restore_screen", "cache_close"]
    assert dlg.doc is None and dlg.word is None, "关闭后应释放 Word/文档引用"
