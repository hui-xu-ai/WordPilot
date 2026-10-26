# plugins/reference/watch_bridge.py
"""监听线程（QThread）→ GUI 线程的信号中转。

为什么需要：`WordWatch` 是 QThread，而插件不是 QObject。把 `citationFound`
直接接到普通 Python 方法时是**直连**，槽在工作线程执行 —— 那里创建悬浮窗、
更新 QLabel 都不安全，表现为"点引用没反应、当前文件一直显示 —"。
"""
from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal, Slot

logger = logging.getLogger(__name__)


class WatchRelay(QObject):
    """线程间中转：工作线程发来的信号在这里重新以 GUI 线程的队列形式发出。"""

    citationFound = Signal(object)
    citationCleared = Signal()
    statusChanged = Signal(str)
    documentChanged = Signal(str)

    @Slot(object)
    def on_citation(self, payload) -> None:
        self.citationFound.emit(payload)

    @Slot()
    def on_cleared(self) -> None:
        self.citationCleared.emit()

    @Slot(str)
    def on_status(self, text: str) -> None:
        self.statusChanged.emit(text)

    @Slot(str)
    def on_document(self, name: str) -> None:
        self.documentChanged.emit(name)


def connect_watch(plugin, watch) -> None:
    """把 watch 的四个信号接到 relay，再由 relay 接到插件（GUI 线程）。"""
    if watch is None:
        return
    relay = getattr(plugin, "_watch_relay", None)
    if relay is None:
        relay = WatchRelay()
        plugin._watch_relay = relay
        relay.documentChanged.connect(plugin._on_document_changed)
        relay.citationFound.connect(plugin._on_citation_found)
        relay.citationCleared.connect(plugin._on_citation_cleared)
        relay.statusChanged.connect(plugin._on_watch_status)
    for source, sink in (("citationFound", relay.on_citation),
                         ("citationCleared", relay.on_cleared),
                         ("statusChanged", relay.on_status),
                         ("documentChanged", relay.on_document)):
        signal = getattr(watch, source, None)
        if signal is not None:
            signal.connect(sink)


def apply_document_name(plugin, name: str) -> None:
    page = getattr(plugin, "_page", None)
    setter = getattr(page, "set_current_document", None)
    if callable(setter):
        try:
            setter(name)
        except Exception:                      # noqa: BLE001 - 页面可能已销毁
            logger.debug("更新当前文件显示失败", exc_info=True)


def on_document_switched(plugin, name: str) -> None:
    """换文档 = 换上下文：切到该文档的绑定库、更新"当前文件"、清掉旧上下文。

    - 只更新文件名是不够的：库名不跟随，用户看到"文件名对了、仓库没对"，
      而且点引用时用的还是旧库的数据（于是"点了没弹窗"）；
    - 清 dismissed / signature：旧文档里"点了不再提示"的记录不能影响新文档。
    """
    switch_to_bound_library(plugin, name)
    apply_document_name(plugin, name)
    plugin._dismissed = set()
    plugin._shown_signature = ()
    overlay = getattr(plugin, "_overlay", None)
    if overlay is not None:
        try:
            overlay.hide_overlay()
        except Exception:                      # noqa: BLE001
            logger.debug("隐藏旧悬浮卡失败", exc_info=True)


def switch_to_bound_library(plugin, doc_identity: str) -> None:
    """文档已有绑定时，把当前库切到它的绑定库（GUI 线程执行）。"""
    if not doc_identity:
        return
    try:
        bound = plugin._libraries.doc_bindings().get(doc_identity, "")
        if bound and bound != getattr(plugin, "_active_name", ""):
            plugin.switch_library(bound)          # 允许更新页面：这里在 GUI 线程
            logger.info("按文档绑定切换仓库：%s → %s", doc_identity, bound)
    except Exception:                          # noqa: BLE001 - 切换失败不影响弹窗
        logger.debug("按绑定切换仓库失败", exc_info=True)


def push_current_document(plugin) -> None:
    """页面刚建好时先显示当前文档（监听只在"切换"时发信号）。"""
    watch = getattr(plugin, "_watch", None)
    name = str(getattr(watch, "_last_doc_name", "") or "")
    if name:
        apply_document_name(plugin, name)
