"""事件总线模块。

提供发布/订阅模式的事件分发系统，支持同步和异步处理，线程安全。
"""

import logging
import threading
from typing import Any, Callable, Dict, List, Optional

from src.core.events.events import Event
from src.core.events.handlers import EventHandler

logger = logging.getLogger(__name__)

# 事件处理函数类型
EventHandlerFunc = Callable[[Event], None]
AsyncEventHandlerFunc = Callable[[Event], Any]


class EventBus:
    """事件总线。

    支持发布/订阅模式的事件分发系统。提供线程安全的事件注册、
    分发和取消订阅功能。支持同步和异步事件处理。

    使用示例::

        bus = EventBus()

        # 同步订阅
        bus.subscribe("phase.start", lambda e: print(f"阶段开始: {e.phase_name}"))

        # 使用处理器对象订阅
        handler = LoggingHandler()
        bus.subscribe_handler(handler)

        # 发布事件
        bus.publish(PhaseStartEvent("extraction"))
    """

    def __init__(self):
        """初始化事件总线。"""
        # 事件类型 -> 处理函数列表（使用有序字典维护顺序）
        self._subscribers: Dict[str, List[EventHandlerFunc]] = {}
        # 异步事件类型 -> 异步处理函数列表
        self._async_subscribers: Dict[str, List[AsyncEventHandlerFunc]] = {}
        # 通配符订阅（处理所有事件）
        self._wildcard_subscribers: List[EventHandlerFunc] = []
        # 异步通配符订阅
        self._async_wildcard_subscribers: List[AsyncEventHandlerFunc] = []
        # 通过 EventHandler 对象订阅
        self._handler_subscribers: List[EventHandler] = []
        # 线程锁
        self._lock = threading.Lock()
        # 是否启用事件分发
        self._enabled = True

    def subscribe(
        self,
        event_type: str,
        handler: EventHandlerFunc,
    ) -> None:
        """订阅指定类型的事件。

        Args:
            event_type: 事件类型（支持通配符 "*" 订阅所有事件）
            handler: 事件处理函数

        Raises:
            ValueError: 当 handler 不是可调用对象时
        """
        if not callable(handler):
            raise ValueError("事件处理函数必须是可调用对象")

        with self._lock:
            if event_type == "*":
                self._wildcard_subscribers.append(handler)
                logger.debug(f"注册通配符订阅: {handler}")
            else:
                if event_type not in self._subscribers:
                    self._subscribers[event_type] = []
                self._subscribers[event_type].append(handler)
                logger.debug(f"注册事件订阅: {event_type} -> {handler}")

    def subscribe_async(
        self,
        event_type: str,
        handler: AsyncEventHandlerFunc,
    ) -> None:
        """订阅指定类型的异步事件。

        Args:
            event_type: 事件类型（支持通配符 "*" 订阅所有事件）
            handler: 异步事件处理函数

        Raises:
            ValueError: 当 handler 不是可调用对象时
        """
        if not callable(handler):
            raise ValueError("异步事件处理函数必须是可调用对象")

        with self._lock:
            if event_type == "*":
                self._async_wildcard_subscribers.append(handler)
                logger.debug(f"注册异步通配符订阅: {handler}")
            else:
                if event_type not in self._async_subscribers:
                    self._async_subscribers[event_type] = []
                self._async_subscribers[event_type].append(handler)
                logger.debug(f"注册异步事件订阅: {event_type} -> {handler}")

    def subscribe_handler(self, handler: EventHandler) -> None:
        """通过 EventHandler 对象订阅事件。

        EventHandler 会根据其配置自动过滤关注的事件类型。

        Args:
            handler: EventHandler 实例
        """
        with self._lock:
            self._handler_subscribers.append(handler)
            logger.debug(f"注册事件处理器: {handler.name}")

    def unsubscribe(self, event_type: str, handler: EventHandlerFunc) -> bool:
        """取消订阅指定事件。

        Args:
            event_type: 事件类型
            handler: 要取消的处理函数

        Returns:
            bool: 是否成功取消订阅
        """
        with self._lock:
            if event_type == "*":
                if handler in self._wildcard_subscribers:
                    self._wildcard_subscribers.remove(handler)
                    logger.debug(f"取消通配符订阅: {handler}")
                    return True
            else:
                handlers = self._subscribers.get(event_type, [])
                if handler in handlers:
                    handlers.remove(handler)
                    logger.debug(f"取消事件订阅: {event_type} -> {handler}")
                    return True
        return False

    def unsubscribe_handler(self, handler: EventHandler) -> bool:
        """取消 EventHandler 对象的订阅。

        Args:
            handler: 要取消的 EventHandler 实例

        Returns:
            bool: 是否成功取消订阅
        """
        with self._lock:
            if handler in self._handler_subscribers:
                self._handler_subscribers.remove(handler)
                logger.debug(f"取消事件处理器: {handler.name}")
                return True
        return False

    def publish(self, event: Event) -> int:
        """发布事件（同步分发）。

        按以下顺序调用所有匹配的处理器：
        1. 通配符订阅
        2. 指定事件类型订阅
        3. EventHandler 对象

        Args:
            event: 要发布的事件

        Returns:
            int: 实际处理该事件的处理器数量
        """
        if not self._enabled:
            logger.debug("事件总线已禁用，忽略事件发布")
            return 0

        count = 0

        with self._lock:
            # 复制订阅列表，避免在分发过程中修改
            wildcard = list(self._wildcard_subscribers)
            type_handlers = list(self._subscribers.get(event.event_type, []))
            handlers_snapshot = list(self._handler_subscribers)

        # 分发通配符订阅
        for handler_func in wildcard:
            try:
                handler_func(event)
                count += 1
            except Exception as e:
                logger.error(f"通配符处理器执行出错: {e}")

        # 分发指定类型订阅
        for handler_func in type_handlers:
            try:
                handler_func(event)
                count += 1
            except Exception as e:
                logger.error(f"事件处理器执行出错 [{event.event_type}]: {e}")

        # 分发给 EventHandler 对象
        for handler in handlers_snapshot:
            try:
                if handler.should_handle(event):
                    handler.handle(event)
                    count += 1
            except Exception as e:
                logger.error(f"EventHandler {handler.name} 执行出错: {e}")

        return count

    async def publish_async(self, event: Event) -> int:
        """发布事件（异步分发）。

        异步调用所有匹配的处理器（同步和异步）。

        Args:
            event: 要发布的事件

        Returns:
            int: 实际处理该事件的处理器数量
        """
        if not self._enabled:
            return 0

        count = 0

        with self._lock:
            wildcard = list(self._wildcard_subscribers)
            async_wildcard = list(self._async_wildcard_subscribers)
            type_handlers = list(self._subscribers.get(event.event_type, []))
            async_type_handlers = list(self._async_subscribers.get(event.event_type, []))
            handlers_snapshot = list(self._handler_subscribers)

        # 异步分发通配符同步处理器
        for handler_func in wildcard:
            try:
                handler_func(event)
                count += 1
            except Exception as e:
                logger.error(f"同步通配符处理器执行出错: {e}")

        # 异步分发通配符异步处理器
        for handler_func in async_wildcard:
            try:
                await handler_func(event)
                count += 1
            except Exception as e:
                logger.error(f"异步通配符处理器执行出错: {e}")

        # 异步分发指定类型同步处理器
        for handler_func in type_handlers:
            try:
                handler_func(event)
                count += 1
            except Exception as e:
                logger.error(f"同步事件处理器执行出错 [{event.event_type}]: {e}")

        # 异步分发指定类型异步处理器
        for handler_func in async_type_handlers:
            try:
                await handler_func(event)
                count += 1
            except Exception as e:
                logger.error(f"异步事件处理器执行出错 [{event.event_type}]: {e}")

        # 异步分发给 EventHandler 对象
        for handler in handlers_snapshot:
            try:
                if handler.should_handle(event):
                    handler.handle(event)
                    count += 1
            except Exception as e:
                logger.error(f"EventHandler {handler.name} 执行出错: {e}")

        return count

    def clear(self) -> None:
        """清空所有订阅。"""
        with self._lock:
            self._subscribers.clear()
            self._async_subscribers.clear()
            self._wildcard_subscribers.clear()
            self._async_wildcard_subscribers.clear()
            self._handler_subscribers.clear()
            logger.debug("事件总线已清空所有订阅")

    def enable(self) -> None:
        """启用事件总线。"""
        self._enabled = True

    def disable(self) -> None:
        """禁用事件总线。"""
        self._enabled = False

    @property
    def is_enabled(self) -> bool:
        """检查事件总线是否启用。"""
        return self._enabled

    def subscriber_count(self, event_type: Optional[str] = None) -> int:
        """获取订阅者数量。

        Args:
            event_type: 指定事件类型时返回该类型的订阅数，None 返回总数

        Returns:
            int: 订阅者数量
        """
        with self._lock:
            if event_type is not None:
                return len(self._subscribers.get(event_type, []))
            total = sum(len(handlers) for handlers in self._subscribers.values())
            total += len(self._wildcard_subscribers)
            total += len(self._handler_subscribers)
            return total

    def has_subscribers(self, event_type: Optional[str] = None) -> bool:
        """检查是否有订阅者。

        Args:
            event_type: 指定事件类型时检查该类型是否有订阅

        Returns:
            bool: 是否有订阅者
        """
        return self.subscriber_count(event_type) > 0
