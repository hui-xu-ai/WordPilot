# tests/test_api_monitor.py
"""API 监控：任务 token 清单、按次明细、每日/每月统计；智能纠错只看 proof。"""
from __future__ import annotations

import json

import pytest


class _I18n:
    """极简 i18n 替身：把 key 与参数都渲染出来，便于断言内容。"""

    def tr(self, key: str, **params) -> str:
        return key + ("" if not params else " " + " ".join(str(v) for v in params.values()))

    def prompt_tr(self, key: str, **params) -> str:
        return key


@pytest.fixture
def app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def log_file(tmp_path, monkeypatch):
    from core.services import api_logger

    path = tmp_path / "api_calls.log"
    monkeypatch.setattr(api_logger, "LOG_FILE", path)
    return path


def _write(path, entries):
    path.write_text("\n".join(json.dumps(entry, ensure_ascii=False) for entry in entries) + "\n",
                    encoding="utf-8")


def test_tokens_keep_cache_hit(log_file):
    from core.services import api_logger

    api_logger.log_api_call("proof", "输入", "输出",
                            {"prompt_tokens": 100, "completion_tokens": 20,
                             "prompt_cache_hit_tokens": 60,
                             "prompt_cache_miss_tokens": 40},
                            True, latency_ms=123)

    entry = json.loads(log_file.read_text(encoding="utf-8").strip())
    assert entry["plugin"] == "proofread" and entry["task"] == "逐句校对"
    assert entry["tokens"]["cache_hit"] == 60
    assert entry["tokens"]["total"] == 120


def test_task_summary_groups_by_plugin_and_task(log_file):
    from core.services import api_logger

    api_logger.log_api_call("proof", "a", "b", {"prompt_tokens": 10, "completion_tokens": 5}, True)
    api_logger.log_api_call("translate", "c", "d", {"prompt_tokens": 30, "completion_tokens": 9},
                            True, plugin="clipboard", task="翻译")

    rows = {row["task"]: row for row in api_logger.task_summary()}

    assert rows["逐句校对"]["total"] == 15
    assert rows["翻译"]["plugin"] == "clipboard"
    assert rows["翻译"]["count"] == 1


def test_day_and_month_summary(log_file):
    from core.services import api_logger

    _write(log_file, [
        {"time": "2026-10-01 | 10:00:00", "action": "proof",
         "tokens": {"prompt_tokens": 10, "completion_tokens": 5}},
        {"time": "2026-10-01 | 11:00:00", "action": "translate",
         "tokens": {"prompt_tokens": 20, "completion_tokens": 10}},
        {"time": "2026-10-02 | 09:00:00", "action": "rewrite",
         "tokens": {"prompt_tokens": 5, "completion_tokens": 5}},
    ])

    days = {row["day"]: row for row in api_logger.period_summary("day")}
    months = api_logger.period_summary("month")

    assert days["2026-10-01"]["total"] == 45 and days["2026-10-01"]["count"] == 2
    assert days["2026-10-02"]["total"] == 10
    assert months[0]["month"] == "2026-10" and months[0]["count"] == 3
    assert api_logger.totals("2026-10-01")["prompt"] == 30


def test_monitor_panel_renders_task_table(log_file, app):
    from core.services import api_logger
    from core.widgets.api_monitor import ApiMonitorPanel

    api_logger.log_api_call("proof", "a", "b",
                            {"prompt_tokens": 10, "completion_tokens": 5}, True)

    panel = ApiMonitorPanel(i18n=_I18n())

    assert panel.table.rowCount() >= 1
    assert "api.monitor.overview" in panel.overview.text(), panel.overview.text()
    panel.period_combo.setCurrentIndex(1)          # 按次明细
    assert panel.table.rowCount() >= 1
    panel.period_combo.setCurrentIndex(2)          # 按天
    assert panel.table.rowCount() >= 1
    panel.period_combo.setCurrentIndex(3)          # 按月
    assert panel.table.rowCount() >= 1


def test_proofread_monitor_only_shows_proof(log_file, app):
    from core.services import api_logger
    from core.widgets.monitor_panel import MonitorPanel

    api_logger.log_api_call("proof", "校对输入", "校对输出",
                            {"prompt_tokens": 10, "completion_tokens": 5}, True)
    api_logger.log_api_call("translate", "翻译输入", "翻译输出",
                            {"prompt_tokens": 99, "completion_tokens": 99}, True)

    panel = MonitorPanel(i18n=_I18n(), actions=("proof",))

    assert "校对输入" in panel.last_edit.toPlainText()
    assert "翻译输入" not in panel.history_edit.toPlainText()
