# plugins/reference/page_scan.py
"""页面里的"扫描本文"逻辑（从 library_page 拆出，保持单文件在阈值内）。

- 扫描在工作线程里跑（离线解析 docx + 重建 COM 域索引），期间进度条为不确定态；
- 结果写进底部状态栏，并刷新表格。
"""
from __future__ import annotations

import logging

from core.services.com_thread import com_init, com_uninit
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
                # S8：QThread 是新的 OS 线程，附接 Word COM 前必须初始化 apartment，
                # 否则报 -2147221008 (CoInitialize has not been called)。
                inited = com_init()
                try:
                    self.result = page._plugin.scan_active_document()
                except Exception as exc:        # noqa: BLE001 - 展示到状态栏即可
                    self.error = str(exc)
                finally:
                    com_uninit(inited)

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
            # R6：后台线程只切了数据层（update_page=False），页面控件必须由这里
            # （GUI 线程）重绑，否则后台直接操作 QComboBox/表格会出界面异常。
            plugin = getattr(page, "_plugin", None)
            if plugin is not None and stats.get("switch") in ("switch", "new"):
                store = getattr(plugin, "_store", None)
                if store is not None and store is not getattr(page, "_store", None):
                    try:
                        page.rebind(store, plugin._index, plugin._note_service,
                                    getattr(plugin, "_active_name", ""))
                    except Exception:          # noqa: BLE001 - 重绑失败只影响展示
                        page.append_status(page.tr("status.scan_failed",
                                                   error="rebind failed"))
            page.reload()
            if stats.get("switch_text"):
                page.append_status(str(stats["switch_text"]).replace("{library}", "")
                                   .replace("{percent}", ""))
            if "diff_added" in stats or "diff_removed" in stats:
                page.set_doc_diff(set(stats.get("diff_added") or []),
                                  set(stats.get("diff_removed") or []))
            plan = stats.get("plan")
            if plan is not None:
                page.handle_switch_plan(plan)
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

    def handle_switch_plan(self, plan) -> None:
        """扫描判定需要用户决策时弹确认框（只能在 GUI 线程调用）。"""
        from plugins.reference.switch_dialog import SwitchDialog

        dialog = SwitchDialog(plan, tr=self.tr, existing=self._libraries or [], parent=self)
        dialog.exec()
        if dialog.choice == "switch" and getattr(plan, "library", "") and self._on_switch_library:
            self._on_switch_library(plan.library)
            text = self.tr("switch.auto", library=plan.library,
                           percent=int(plan.score * 100))
            self.append_status(str(text).replace("{library}", plan.library)
                               .replace("{percent}", str(int(plan.score * 100))))
        elif dialog.choice == "new" and dialog.new_name and self._on_create_library:
            self._on_create_library(dialog.new_name)
            text = self.tr("switch.created", name=dialog.new_name)
            self.append_status(str(text).replace("{name}", dialog.new_name))
        else:
            self.append_status(self.tr("switch.stay"))


    def set_doc_diff(self, added, removed) -> None:
        """扫描后把"本次新增/已移除"喂给页面（chips 高亮可用 + 状态栏计数）。"""
        self._diff_added = set(added or ())
        self._diff_removed = set(removed or ())
        for name in ("added", "removed"):
            button = getattr(self, "filter_buttons", {}).get(name)
            if button is not None:
                button.setVisible(True)
                button.setToolTip(self.tr("filter.diff_tip"))
        text = self.tr("status.diff", added=len(self._diff_added),
                       removed=len(self._diff_removed))
        self.append_status(str(text).replace("{added}", str(len(self._diff_added)))
                           .replace("{removed}", str(len(self._diff_removed))))
