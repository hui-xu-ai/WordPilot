# plugins/reference/page_scan.py
"""页面里的"扫描本文"逻辑（从 library_page 拆出，保持单文件在阈值内）。

- 扫描在工作线程里跑（离线解析 docx + 重建 COM 域索引），期间进度条为不确定态；
- 结果写进底部状态栏，并刷新表格。
"""
from __future__ import annotations

import logging

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QWidget

logger = logging.getLogger(__name__)


class ScanMixin:
    """给 `LibraryPage` 提供扫描入口；宿主需提供 `_plugin / progress / scan_btn / footer_label`。"""

    def showEvent(self, event) -> None:        # noqa: N802 - Qt 命名惯例
        super().showEvent(event)
        if not getattr(self, "_auto_scanned", False) and getattr(self, "_plugin", None) is not None:
            self._auto_scanned = True
            self.scan_document(silent=True)

    def scan_document(self, silent: bool = False) -> None:
        """扫描当前 Word 文档（工作线程）：导入参考文献 + 重建点击查表索引。"""
        plugin = getattr(self, "_plugin", None)
        if plugin is None or getattr(self, "_scanning", False):
            return
        self._scanning = True
        self.progress.setVisible(True)
        self.scan_btn.setEnabled(False)
        if not silent:
            self.append_status(self.tr("status.scanning"))

        page = self

        class _Worker(QThread):
            result: dict = {}
            error = ""

            def run(self) -> None:
                try:
                    self.result = page._plugin.scan_active_document()
                except Exception as exc:        # noqa: BLE001 - 展示到状态栏即可
                    self.error = str(exc)

        worker = _Worker(self)
        self._worker = worker

        def _done() -> None:
            page._scanning = False
            page.progress.setVisible(False)
            page.scan_btn.setEnabled(True)
            if worker.error:
                page.append_status(page.tr("status.scan_failed", error=worker.error))
                return
            stats = worker.result or {}
            page.reload()
            page.append_status(page.tr(
                "status.scan_done",
                doc=stats.get("doc_name", ""), bibliography=stats.get("bibliography", 0),
                provider=stats.get("provider", 0), imported=stats.get("imported", 0),
                anchors=stats.get("anchors", 0)))

        worker.finished.connect(_done)
        worker.start()

    def append_status(self, text: str) -> None:
        """底部状态栏保留最近 3 条（监听事件与扫描结果共用）。"""
        label = getattr(self, "footer_label", None)
        if label is None or not isinstance(label, QWidget):
            return
        lines = [line for line in (label.text() or "").splitlines() if line]
        lines.append(text)
        label.setText("\n".join(lines[-3:]))
