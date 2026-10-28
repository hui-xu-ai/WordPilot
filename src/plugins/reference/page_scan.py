# plugins/reference/page_scan.py
"""页面里的"扫描本文"逻辑（从 library_page 拆出，保持单文件在阈值内）。

- 扫描在工作线程里跑（离线解析 docx + 重建 COM 域索引），期间进度条为不确定态；
- 结果写进底部状态栏，并刷新表格。
"""
from __future__ import annotations

import logging

from core.services.com_thread import com_init, com_uninit
from core.services.i18n import message
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QWidget

logger = logging.getLogger(__name__)

#: 底部状态栏保留的行数
_STATUS_LINES = 3
#: 状态 key 的前缀标记：带它的是**应用级**语言包（src/assets/i18n）里的 key，
#: 不在插件语言包里，渲染时走 `core.services.i18n.message()`（同样按当前语言查）
_APP_KEY = "app:"


class ScanMixin:
    """给 `LibraryPage` 提供扫描入口。

    宿主需提供 `_plugin / progress / scan_btn / footer_label` 与状态栏历史
    `_status_history`（存 key + 参数，见 `append_status`）。
    """

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
            self.append_status("status.scanning")

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
                page.append_status("status.scan_failed", error=worker.error)
                return
            stats = worker.result or {}
            if stats.get("failed"):            # R3：写失败要明确提示，且允许重试
                page.append_status("status.scan_failed",
                                   error=stats.get("error") or "write failed")
            # R6：后台线程只切了数据层（update_page=False），页面控件必须由这里
            # （GUI 线程）重绑，否则后台直接操作 QComboBox/表格会出界面异常。
            plugin = getattr(page, "_plugin", None)
            if plugin is not None:
                # 路由可能在后台切了库（update_page=False），页面必须跟上；
                # 不能只看 stats["switch"] —— 这里漏判时表格会停在旧库（显示 0 条）。
                store = getattr(plugin, "_store", None)
                if store is not None and store is not getattr(page, "_store", None):
                    try:
                        page.rebind(store, plugin._index, plugin._note_service,
                                    getattr(plugin, "_active_name", ""))
                    except Exception:          # noqa: BLE001 - 重绑失败只影响展示
                        page.append_status("status.scan_failed", error="rebind failed")
            page.reload()
            switch_status = stats.get("switch_status")
            if switch_status:                  # 后台给的 key + 参数：切语言能重画
                page.append_status(switch_status[0], **switch_status[1])
            elif stats.get("switch_text"):     # 旧数据只有成品文本 → 只能原样显示
                page.append_status_text(str(stats["switch_text"]).replace("{library}", "")
                                        .replace("{percent}", ""))
            if "diff_added" in stats or "diff_removed" in stats:
                page.set_doc_diff(set(stats.get("diff_added") or []),
                                  set(stats.get("diff_removed") or []))
            plan = stats.get("plan")
            if plan is not None:
                page.handle_switch_plan(plan,
                                        doc_path=stats.get("doc_path", ""),
                                        suggested=stats.get("suggested", ""))
            page.append_status("status.scan_done",
                               doc=stats.get("doc_name", ""),
                               bibliography=stats.get("bibliography", 0),
                               provider=stats.get("provider", 0),
                               imported=stats.get("imported", 0),
                               anchors=stats.get("anchors", 0))

        worker.finished.connect(_done)
        worker.start()

    # ---------------- 底部状态栏（存 key + 参数，切语言可重画） ----------------
    def append_status(self, key: str, **params) -> None:
        """追加一条状态：**只存 key + 参数**，渲染交给 `_render_status()`。

        过去存的是渲染好的成品文本：界面切到英文再切回中文，旧英文永远留在栏里
        （用户现场截图）。存 key 才能按当前语言逐条重画。
        """
        self._push_status(key, params)

    def append_status_text(self, text: str) -> None:
        """追加一条**成品文本**（key 无法追溯时的兼容入口，切语言**不会**重画）。

        只给"后台把已渲染好的字符串塞进 stats"这类来源用；能用 key 表达的一律走
        `append_status()`，否则这行会卡在旧语言。
        """
        self._push_status(None, {"text": str(text)})

    def _push_status(self, key: str | None, params: dict) -> None:
        history = self._status_history
        history.append((key, dict(params)))
        del history[:-_STATUS_LINES]      # 只留最近 3 条（条数、顺序不变）
        self._render_status()

    def _render_status(self) -> None:
        """按**当前语言**把历史逐条重画（语言切换后由 `retranslateUi` 调）。"""
        label = getattr(self, "footer_label", None)
        if label is None or not isinstance(label, QWidget):
            return
        label.setText("\n".join(self._status_text(key, params)
                                for key, params in self._status_history))

    def _status_text(self, key: str | None, params: dict) -> str:
        if key is None:                   # 成品文本：原样显示
            return str(params.get("text", ""))
        if key.startswith(_APP_KEY):      # 应用级语言包（library.error.* 等）
            return message(key[len(_APP_KEY):], **params)
        return self.tr(key, **params)

    def handle_switch_plan(self, plan, doc_path: str = "", suggested: str = "") -> None:
        """扫描判定"这份文档归属不明"时弹窗，由**用户**决定，软件不自动建库。"""
        from plugins.reference.switch_dialog import SwitchDialog

        dialog = SwitchDialog(plan, tr=self.tr, existing=self._library_names(), parent=self)
        if suggested:
            dialog.name_edit.setText(suggested)     # 只做预填，不替用户建
            dialog.name_edit.setVisible(True)
        dialog.exec()

        plugin = getattr(self, "_plugin", None)
        bundle = doc_path and plugin is not None

        if dialog.choice == "switch" and getattr(plan, "library", "") and self._on_switch_library:
            if bundle:
                plugin.bind_document(doc_path, plan.library)
            self._on_switch_library(plan.library)
            self.append_status("switch.auto", library=plan.library,
                               percent=int(getattr(plan, "score", 0.0) * 100))
            self._rescan_after_choice()
        elif dialog.choice == "new" and dialog.new_name and self._on_create_library:
            created = self._on_create_library(dialog.new_name)
            if created is False:            # 名字不合法：必须提示，不能静默
                # `library.error.invalid_name` 在**应用级**语言包里 → 加 `app:` 标记
                self.append_status(_APP_KEY + "library.error.invalid_name",
                                   name=dialog.new_name)
                return
            if bundle:
                plugin.bind_document(doc_path, dialog.new_name)
            self.append_status("switch.created", name=dialog.new_name)
            self._rescan_after_choice()
        else:
            # "保持当前不动" = 用**当前库**装这份文档（用户点的就是这个意思）。
            # 之前的实现只是什么都不做，于是空白库永远同步不进去。
            current = getattr(plugin, "_active_name", "") or ""
            if bundle and current:
                plugin.bind_document(doc_path, current)
                self.append_status("switch.into_current", name=current)
                self._rescan_after_choice()
            else:
                self.append_status("switch.stay")

    def _rescan_after_choice(self) -> None:
        """用户做出选择后重新扫描一次：此时绑定已存在，会直接入库。"""
        scan = getattr(self, "scan_document", None)
        if callable(scan):
            scan(silent=True)

    def set_doc_diff(self, added, removed) -> None:
        """扫描后把"本次新增/已移除"喂给页面（chips 高亮可用 + 状态栏计数）。"""
        self._diff_added = set(added or ())
        self._diff_removed = set(removed or ())
        for name in ("added", "removed"):
            button = getattr(self, "filter_buttons", {}).get(name)
            if button is not None:
                button.setVisible(True)
                button.setToolTip(self.tr("filter.diff_tip"))
        self.append_status("status.diff", added=len(self._diff_added),
                           removed=len(self._diff_removed))
