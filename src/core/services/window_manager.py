# core/services/window_manager.py
"""窗口治理服务：所有顶层窗口的唯一出入口。

解决的问题（改造前）：
- 四套置顶机制并存（Qt.ToolTip / Qt.Tool / WindowStaysOnTopHint / win32 HWND_TOPMOST），
  悬浮按钮、建议卡片、导航面板互相抢层级；
- 每个窗口各自实现「单例 / 关闭转隐藏 / 几何记忆」，逻辑分散且不一致；
- 插件自己 new Dialog，卸载时窗口可能残留或访问已销毁对象。

治理约定：
- 插件与外壳**不得**直接设置 WindowStaysOnTopHint，也不得直接调 win32 置顶，
  置顶一律由本服务按 Layer 策略施加；
- 关闭语义（销毁 / 转隐藏）由本服务统一拦截，窗口自身不再写 event.ignore()；
- 几何记忆只在关闭/隐藏时写盘，避免配置高频落盘。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum, IntEnum
from typing import Any, Callable

from PySide6.QtCore import QByteArray, QEvent, QObject, QTimer

try:  # 置顶在非 Windows 环境下静默降级，保证服务可导入可测试
    import win32con
    import win32gui

    _HAS_WIN32 = True
except Exception:  # pragma: no cover
    _HAS_WIN32 = False

logger = logging.getLogger(__name__)


class Layer(IntEnum):
    """窗口层级：决定置顶策略，而非创建时的 Qt flag。"""

    NORMAL = 0    # 普通窗口（设置、监控、图片工具箱）
    TOPMOST = 1   # 主窗口置顶（受 main_on_top 控制）
    PALETTE = 2   # 工具面板（导航助手，用户可勾选置顶）
    OVERLAY = 3   # 桌宠等浮层（恒置顶，不进任务栏）


class Mode(Enum):
    NON_MODAL = "non_modal"   # 每次新建（调用方负责生命周期）
    MODAL = "modal"           # 模态执行，返回后销毁
    SINGLETON = "singleton"   # 同一 id 只保留一个实例


class CloseBehavior(Enum):
    DESTROY = "destroy"       # 关闭即销毁
    HIDE = "hide"             # 关闭转隐藏（常驻窗口）


@dataclass
class _Entry:
    window_id: str
    widget: Any
    owner: str
    layer: Layer
    behavior: CloseBehavior
    remember_geometry: bool
    on_hide: Callable | None = None   # HIDE 语义下隐藏后的回调（如主窗口弹托盘提示）


class _CloseFilter(QObject):
    """把窗口的 Close/Destroy 事件转交 WindowManager 统一裁决。

    以被监听的窗口作为 QObject 父对象：生命周期与窗口绑定，
    这样注销时无需（也不应）在事件派发过程中 removeEventFilter。
    """

    def __init__(self, manager: "WindowManager", window_id: str, widget) -> None:
        super().__init__(widget)
        self._manager = manager
        self._window_id = window_id

    def eventFilter(self, obj, event):  # noqa: N802 - Qt 回调命名
        if event.type() == QEvent.Close:
            return self._manager._on_close(self._window_id, obj)
        if event.type() == QEvent.Destroy:
            self._manager._on_destroyed(self._window_id)
        return False


class WindowManager:
    """顶层窗口的注册表与策略执行者。

    依赖 QApplication 存在，因此在 Kernel.attach_ui() 之后注册，
    而不是在 boot() 里（内核本身保持零 Qt 依赖）。
    """

    def __init__(self, config, kernel=None) -> None:
        self.config = config
        self.kernel = kernel
        self._entries: dict[str, _Entry] = {}
        self._filters: dict[str, _CloseFilter] = {}
        self._force_closing: set[str] = set()
        # 最近一次打开失败的原因（window_id -> 错误文本）：调用方据此给用户看得见的提示，
        # 否则工厂里的异常被这里吞掉后，用户只看到"点了没反应"。
        self._last_errors: dict[str, str] = {}

    # ---------------- 查询 ----------------
    def get(self, window_id: str):
        entry = self._entries.get(window_id)
        if entry is None or not self._alive(entry.widget):
            return None
        return entry.widget

    def is_open(self, window_id: str) -> bool:
        return self.get(window_id) is not None

    def last_error(self, window_id: str) -> str:
        """最近一次打开该窗口失败的原因（空串表示没有记录）。"""
        return self._last_errors.get(window_id, "")

    def ids(self) -> list[str]:
        return [wid for wid in self._entries if self.get(wid) is not None]

    def main_window(self):
        """主窗口引用（经 UiRegistry 提供），不存在时返回 None。"""
        ui = getattr(self.kernel, "ui", None) if self.kernel is not None else None
        getter = getattr(ui, "main_window", None)
        if not callable(getter):
            return None
        try:
            return getter()
        except Exception:
            return None

    # ---------------- 注册 / 打开 ----------------
    def register(self, window_id: str, widget, *, owner: str = "app",
                 layer: Layer = Layer.NORMAL,
                 behavior: CloseBehavior = CloseBehavior.DESTROY,
                 remember_geometry: bool = True,
                 parent=None, on_hide: Callable | None = None):
        """把一个已存在的窗口纳入治理（主窗口用这种方式接入）。"""
        if parent is not None and widget.parent() is None and layer is not Layer.OVERLAY:
            try:
                widget.setParent(parent)
            except Exception:
                logger.debug("设置父窗口失败: %s", window_id, exc_info=True)
        self._entries[window_id] = _Entry(window_id, widget, owner, layer,
                                          behavior, remember_geometry, on_hide)
        # 过滤器以 widget 为父对象，随窗口销毁，无需在事件派发中摘除
        widget.installEventFilter(_CloseFilter(self, window_id, widget))
        if remember_geometry:
            self._restore_geometry(window_id, widget)
        return widget

    def open(self, window_id: str, factory: Callable[[], Any], *, owner: str = "app",
             mode: Mode = Mode.NON_MODAL, layer: Layer = Layer.NORMAL,
             behavior: CloseBehavior = CloseBehavior.DESTROY,
             remember_geometry: bool = True, on_hide: Callable | None = None):
        """打开窗口的统一入口。

        SINGLETON：已存在则激活并返回同一个实例；
        MODAL：exec() 返回后立即销毁并注销（调用方无需关心释放）；
        NON_MODAL：新建、注册、显示。
        """
        existing = self.get(window_id)
        if existing is not None and mode is Mode.SINGLETON:
            self._activate(existing)
            return existing

        try:
            widget = factory()
        except Exception as exc:
            self._last_errors[window_id] = str(exc)
            logger.exception("窗口创建失败: %s", window_id)
            return None
        self._last_errors.pop(window_id, None)
        if widget is None:
            return None

        parent = None if layer is Layer.OVERLAY else self.main_window()
        self.register(window_id, widget, owner=owner, layer=layer,
                      behavior=behavior, remember_geometry=remember_geometry,
                      parent=parent, on_hide=on_hide)

        if mode is Mode.MODAL:
            try:
                if hasattr(widget, "exec"):
                    widget.exec()
                else:
                    widget.show()
            finally:
                self.close(window_id, force=True)
            return None

        widget.show()
        self._apply_layer(window_id)
        self._activate(widget)
        return widget

    # ---------------- 关闭 ----------------
    def close(self, window_id: str, force: bool = False) -> None:
        entry = self._entries.get(window_id)
        if entry is None:
            return
        widget = entry.widget
        if not self._alive(widget):
            self._unregister(window_id)
            return
        if force:
            self._force_closing.add(window_id)
        try:
            widget.close()
        finally:
            self._force_closing.discard(window_id)
        # 强制关闭或 DESTROY 语义：同步注销，不依赖事件循环跑完 singleShot
        if force or entry.behavior is CloseBehavior.DESTROY:
            if self._alive(widget):
                try:
                    widget.deleteLater()
                except Exception:
                    pass
            self._unregister(window_id)

    def close_owner(self, owner: str, force: bool = True) -> None:
        """关闭某个插件（或 app）名下的全部窗口——插件卸载前务必调用。"""
        for window_id in [wid for wid, e in self._entries.items() if e.owner == owner]:
            self.close(window_id, force=force)

    def close_all(self, force: bool = True) -> None:
        for window_id in list(self._entries):
            self.close(window_id, force=force)

    # ---------------- 置顶策略 ----------------
    def set_topmost(self, widget, on: bool) -> None:
        """win32 置顶的唯一出口（不激活窗口，避免抢 Word 输入焦点）。"""
        if not _HAS_WIN32 or not self._alive(widget):
            return
        try:
            hwnd = int(widget.winId())
            flag = win32con.HWND_TOPMOST if on else win32con.HWND_NOTOPMOST
            win32gui.SetWindowPos(hwnd, flag, 0, 0, 0, 0,
                                  win32con.SWP_NOMOVE | win32con.SWP_NOSIZE
                                  | win32con.SWP_NOACTIVATE)
        except Exception:
            logger.debug("设置置顶失败", exc_info=True)

    def set_palette_topmost(self, window_id: str, on: bool) -> None:
        """PALETTE 层窗口的置顶偏好（持久化到配置）。"""
        self.config.set(f"window.{window_id}.topmost", bool(on))
        self._apply_layer(window_id)

    def _wants_topmost(self, entry: _Entry) -> bool:
        if entry.layer is Layer.OVERLAY:
            return True
        if entry.layer is Layer.PALETTE:
            return bool(self.config.get(f"window.{entry.window_id}.topmost", False))
        if entry.layer is Layer.TOPMOST:
            return bool(self.config.get("main_on_top", False))
        return False

    def _apply_layer(self, window_id: str) -> None:
        """show 之后延迟施加置顶：窗口尚未创建原生句柄时直接调用会失败。"""
        entry = self._entries.get(window_id)
        if entry is None:
            return

        def _apply():
            widget = self.get(window_id)
            if widget is None:
                return
            self.set_topmost(widget, self._wants_topmost(entry))

        QTimer.singleShot(50, _apply)

    def refresh_topmost(self, window_id: str) -> None:
        """配置变更后立即重算置顶（主窗口置顶开关用）。"""
        self._apply_layer(window_id)

    # ---------------- 内部：几何记忆 ----------------
    def _restore_geometry(self, window_id: str, widget) -> None:
        data = (self.config.get("geometry") or {}).get(window_id)
        if not data:
            return
        try:
            widget.restoreGeometry(QByteArray.fromBase64(data.encode("ascii")))
        except Exception:
            logger.debug("恢复窗口几何失败: %s", window_id, exc_info=True)

    def save_geometry(self, window_id: str) -> None:
        widget = self.get(window_id)
        entry = self._entries.get(window_id)
        if widget is None or entry is None or not entry.remember_geometry:
            return
        try:
            data = widget.saveGeometry().toBase64().data().decode("ascii")
        except Exception:
            return
        store = dict(self.config.get("geometry") or {})
        if store.get(window_id) == data:
            return
        store[window_id] = data
        self.config.set("geometry", store)

    # ---------------- 内部：事件裁决 ----------------
    def _on_close(self, window_id: str, widget) -> bool:
        """返回 True 表示吞掉关闭事件（转隐藏）。"""
        entry = self._entries.get(window_id)
        if entry is None:
            return False
        if entry.remember_geometry:
            self.save_geometry(window_id)
        if entry.behavior is CloseBehavior.HIDE and window_id not in self._force_closing:
            widget.hide()
            if entry.on_hide is not None:
                try:
                    entry.on_hide()
                except Exception:
                    logger.exception("窗口隐藏回调失败: %s", window_id)
            return True
        # 同步注销：不依赖事件循环，避免窗口已关但登记表仍持有引用
        self._unregister(window_id)
        return False

    def _on_destroyed(self, window_id: str) -> None:
        self._unregister(window_id)

    def _unregister(self, window_id: str) -> None:
        # 只摘登记表；事件过滤器由被监听窗口以父对象身份托管并随之销毁
        self._entries.pop(window_id, None)
        self._filters.pop(window_id, None)

    # ---------------- 内部：工具 ----------------
    @staticmethod
    def _alive(widget) -> bool:
        """判断 Qt 对象的 C++ 侧是否仍然有效。"""
        if widget is None:
            return False
        try:
            widget.objectName()
            return True
        except RuntimeError:
            return False

    @staticmethod
    def _activate(widget) -> None:
        try:
            widget.show()
            widget.raise_()
            widget.activateWindow()
        except RuntimeError:
            pass
