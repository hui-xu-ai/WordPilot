# core/event_bus.py
"""事件总线（纯 Python，零 Qt 依赖）。

前端（app）与后端（core + plugins）之间唯一的通信方式：
后端只负责 emit，前端与其它插件按需 subscribe。

这样做的收益：后端不依赖 PySide6，可以脱离界面单独运行/测试；
新增功能时外壳不必改动一行代码，只需订阅它关心的事件。

事件命名约定：<插件或模块>.<动作>，例如
    "proofread.toggled"    payload: bool   校对开关状态变化
    "ai.client_changed"    payload: DeepSeekClient  API 客户端重建
    "kernel.shutdown"      payload: None   内核即将关闭
"""
import logging
from collections import defaultdict
from typing import Any, Callable

logger = logging.getLogger(__name__)


class EventBus:
    """极简发布/订阅总线，单个订阅者抛异常不影响其它订阅者。"""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Callable]] = defaultdict(list)
        self._any_handlers: list[Callable] = []

    def subscribe(self, event: str, handler: Callable) -> Callable:
        """订阅指定事件，返回用于退订的闭包。"""
        self._handlers[event].append(handler)

        def _unsubscribe() -> None:
            self.unsubscribe(event, handler)

        return _unsubscribe

    def subscribe_all(self, handler: Callable) -> Callable:
        """订阅所有事件（用于日志/调试）。"""
        self._any_handlers.append(handler)

        def _unsubscribe() -> None:
            self.unsubscribe_all(handler)

        return _unsubscribe

    def unsubscribe(self, event: str, handler: Callable) -> None:
        try:
            self._handlers[event].remove(handler)
        except (ValueError, KeyError):
            pass

    def unsubscribe_all(self, handler: Callable) -> None:
        try:
            self._any_handlers.remove(handler)
        except ValueError:
            pass

    def emit(self, event: str, payload: Any = None) -> None:
        """发布事件；处理器签名为 handler(event_name, payload)。"""
        for handler in list(self._handlers.get(event, ())) + list(self._any_handlers):
            try:
                handler(event, payload)
            except Exception:
                logger.exception("事件 [%s] 的处理器执行失败", event)
