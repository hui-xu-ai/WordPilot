# core/widgets/api_monitor.py
"""API 监控面板（设置页独立 Tab）：任务清单 + 单次明细 + 每日/每月消耗。

数据全部来自 `core.services.api_logger` 的调用日志，插件页面不再各自统计。
"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QHeaderView, QLabel, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from core.services import api_logger


class ApiMonitorPanel(QWidget):
    """API 调用监控：概览 / 按任务 / 按次 / 按日·月。"""

    def __init__(self, i18n=None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._i18n = i18n
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self.overview = QLabel()
        self.overview.setObjectName("hintLabel")
        self.overview.setWordWrap(True)
        layout.addWidget(self.overview)

        controls = QHBoxLayout()
        self.period_combo = QComboBox()
        self.period_combo.addItem("", "task")      # 文案在 retranslate() 里填
        self.period_combo.addItem("", "call")
        self.period_combo.addItem("", "day")
        self.period_combo.addItem("", "month")
        self.period_combo.currentIndexChanged.connect(self.refresh)
        self.refresh_btn = QPushButton()
        self.refresh_btn.clicked.connect(self.refresh)
        self.clear_btn = QPushButton()
        self.clear_btn.clicked.connect(self._clear)
        controls.addWidget(self.period_combo, 1)
        controls.addWidget(self.refresh_btn)
        controls.addWidget(self.clear_btn)
        layout.addLayout(controls)

        self.table = QTableWidget(0, 1)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        layout.addWidget(self.table, 1)

        self.retranslate()
        self.refresh()

    # ---------------- 文案 ----------------
    def _tr(self, key: str, **kwargs) -> str:
        if self._i18n is None:
            return key
        return self._i18n.tr(key, **kwargs)

    def retranslateUi(self) -> None:        # noqa: N802 - Qt 命名惯例
        """统一入口（Qt 惯例命名）。"""
        self.retranslate()

    def retranslate(self) -> None:
        labels = (("api.monitor.task_view", "task"), ("api.monitor.call_view", "call"),
                  ("api.monitor.day_view", "day"), ("api.monitor.month_view", "month"))
        for index, (key, data) in enumerate(labels):
            self.period_combo.setItemText(index, self._tr(key))
            self.period_combo.setItemData(index, data)
        self.refresh_btn.setText(self._tr("api.monitor.refresh"))
        self.clear_btn.setText(self._tr("api.monitor.clear"))
        self.refresh()

    # ---------------- 数据 ----------------
    def refresh(self) -> None:
        logs = api_logger.get_all_logs()
        today = datetime.now().strftime("%Y-%m-%d")
        month = today[:7]
        day_stats = api_logger.totals(today, logs)
        month_stats = api_logger.totals(month, logs)
        self.overview.setText(self._tr(
            "api.monitor.overview",
            day_calls=day_stats["count"], day_in=day_stats["prompt"],
            day_hit=day_stats["cache_hit"], day_out=day_stats["completion"],
            month_calls=month_stats["count"], month_in=month_stats["prompt"],
            month_hit=month_stats["cache_hit"], month_out=month_stats["completion"],
            day_rate=self._hit_rate(day_stats), month_rate=self._hit_rate(month_stats)))

        mode = self.period_combo.currentData() or "task"
        if mode == "task":
            self._fill_task_table(logs)
        elif mode == "call":
            self._fill_call_table(logs)
        elif mode == "day":
            self._fill_period_table(logs, "day", "api.monitor.col_day")
        else:
            self._fill_period_table(logs, "month", "api.monitor.col_month")

    def _task_label(self, value: str) -> str:
        """任务名显示：映射集中在 core.services.ui_labels（接受 key 与旧中文名）。"""
        from core.services.ui_labels import task_label

        return task_label(value, self._tr)

    @staticmethod
    def _hit_rate(row: dict) -> str:
        """命中率 = 命中缓存 / 输入 tokens；输入为 0 时显示 "-"。"""
        prompt = row.get("prompt") or 0
        if not prompt:
            return "-"
        return f"{(row.get('cache_hit') or 0) / prompt:.0%}"

    def _set_headers(self, headers: list[str], stretch_all: bool = True) -> None:
        self.table.clear()
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setRowCount(0)
        header = self.table.horizontalHeader()
        if stretch_all:
            header.setSectionResizeMode(QHeaderView.Stretch)      # 列宽均分，铺满面板
        else:
            header.setSectionResizeMode(QHeaderView.ResizeToContents)
            header.setSectionResizeMode(len(headers) - 1, QHeaderView.Stretch)

    def _fill_task_table(self, logs) -> None:
        rows = api_logger.task_summary(logs)
        self._set_headers([self._tr("api.monitor.col_plugin"), self._tr("api.monitor.col_task"),
                           self._tr("api.monitor.col_calls"), self._tr("api.monitor.col_in"),
                           self._tr("api.monitor.col_hit"), self._tr("api.monitor.col_rate"),
                           self._tr("api.monitor.col_out"),
                           self._tr("api.monitor.col_total")])
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, value in enumerate((row["plugin"], self._task_label(row["task"]),
                                       row["count"],
                                       row["prompt"], row["cache_hit"], self._hit_rate(row),
                                       row["completion"], row["total"])):
                self.table.setItem(r, c, QTableWidgetItem(str(value)))

    def _fill_call_table(self, logs) -> None:
        recent = list(reversed(logs))[:300]
        self._set_headers([self._tr("api.monitor.col_time"), self._tr("api.monitor.col_task"),
                           self._tr("api.monitor.col_in"), self._tr("api.monitor.col_hit"),
                           self._tr("api.monitor.col_out"), self._tr("api.monitor.col_total"),
                           self._tr("api.monitor.col_latency")])
        self.table.setRowCount(len(recent))
        for r, entry in enumerate(recent):
            tokens = entry["tokens"]
            task = self._task_label(str(entry.get("task") or entry.get("action") or ""))
            label = f"{entry.get('plugin') or '-'} / {task}"
            values = (entry.get("time", ""), label, tokens["prompt"], tokens["cache_hit"],
                      tokens["completion"], tokens["total"],
                      entry.get("latency_ms", ""))
            for c, value in enumerate(values):
                self.table.setItem(r, c, QTableWidgetItem(str(value)))

    def _fill_period_table(self, logs, key: str, header_key: str) -> None:
        rows = api_logger.period_summary(key, logs)
        self._set_headers([self._tr(header_key), self._tr("api.monitor.col_calls"),
                           self._tr("api.monitor.col_in"), self._tr("api.monitor.col_hit"),
                           self._tr("api.monitor.col_rate"), self._tr("api.monitor.col_out"),
                           self._tr("api.monitor.col_total")])
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, value in enumerate((row[key], row["count"], row["prompt"],
                                       row["cache_hit"], self._hit_rate(row),
                                       row["completion"], row["total"])):
                self.table.setItem(r, c, QTableWidgetItem(str(value)))

    def _clear(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        if QMessageBox.question(self, self._tr("api.monitor.clear"),
                                self._tr("api.monitor.clear_confirm")) != QMessageBox.Yes:
            return
        api_logger.clear_api_log()
        self.refresh()
