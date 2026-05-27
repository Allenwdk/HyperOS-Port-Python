"""事件处理器模块。

提供 EventHandler 抽象基类和内置处理器，用于处理事件总线中的事件。
"""

import logging
from abc import ABC, abstractmethod
from typing import Dict, List, Optional

from src.core.events.events import Event

logger = logging.getLogger(__name__)


class EventHandler(ABC):
    """事件处理器抽象基类。

    所有自定义处理器必须继承此类并实现 handle() 方法。
    支持事件类型过滤和优先级设置。
    """

    def __init__(
        self,
        name: str = "",
        event_types: Optional[List[str]] = None,
        priority: int = 0,
    ):
        """初始化处理器。

        Args:
            name: 处理器名称（用于日志和调试）
            event_types: 关注的事件类型列表，None 表示处理所有事件
            priority: 处理器优先级（数值越小优先级越高）
        """
        self.name = name or self.__class__.__name__
        self.event_types = event_types
        self.priority = priority
        self.enabled = True

    def should_handle(self, event: Event) -> bool:
        """判断是否应该处理该事件。

        根据 event_types 过滤器决定是否处理。

        Args:
            event: 待处理事件

        Returns:
            bool: True 表示应该处理，False 表示跳过
        """
        if not self.enabled:
            return False
        if self.event_types is None:
            return True
        return event.event_type in self.event_types

    def handle(self, event: Event) -> None:
        """处理事件。

        先调用 should_handle 检查过滤条件，然后调用 _process。

        Args:
            event: 待处理事件
        """
        if not self.should_handle(event):
            return
        self._process(event)

    @abstractmethod
    def _process(self, event: Event) -> None:
        """实际处理逻辑（子类必须实现）。

        Args:
            event: 待处理事件
        """
        pass


class LoggingHandler(EventHandler):
    """日志处理器。

    将事件信息输出到日志系统，支持按事件类型设置日志级别。
    """

    # 事件类型到日志级别的映射
    EVENT_LEVEL_MAP: Dict[str, int] = {
        "error": logging.ERROR,
        "phase.start": logging.INFO,
        "phase.end": logging.INFO,
        "plugin.start": logging.INFO,
        "plugin.end": logging.INFO,
    }

    def __init__(
        self,
        name: str = "LoggingHandler",
        event_types: Optional[List[str]] = None,
        priority: int = 0,
        log_level: Optional[int] = None,
    ):
        """初始化日志处理器。

        Args:
            name: 处理器名称
            event_types: 关注的事件类型
            priority: 处理器优先级
            log_level: 指定固定日志级别，None 则按事件类型自动选择
        """
        super().__init__(name=name, event_types=event_types, priority=priority)
        self.log_level = log_level
        self._logger = logging.getLogger(f"EventHandler.{name}")

    def _process(self, event: Event) -> None:
        """输出事件日志。"""
        level = self.log_level or self.EVENT_LEVEL_MAP.get(event.event_type, logging.INFO)

        # 构建日志消息
        msg_parts = [f"[事件] {event.event_type}"]
        if event.source:
            msg_parts.append(f"来源: {event.source}")
        if event.data:
            msg_parts.append(f"数据: {event.data}")

        message = " | ".join(msg_parts)

        # 针对特定事件类型附加额外信息
        if hasattr(event, "phase_name") and event.phase_name:
            message += f" | 阶段: {event.phase_name}"
        if hasattr(event, "plugin_name") and event.plugin_name:
            message += f" | 插件: {event.plugin_name}"
        if hasattr(event, "error_message") and event.error_message:
            message += f" | 错误: {event.error_message}"

        self._logger.log(level, message)


class MetricsHandler(EventHandler):
    """指标收集处理器。

    收集事件发生的计数和耗时信息，可用于性能分析。
    """

    def __init__(
        self,
        name: str = "MetricsHandler",
        event_types: Optional[List[str]] = None,
        priority: int = 0,
    ):
        """初始化指标处理器。

        Args:
            name: 处理器名称
            event_types: 关注的事件类型
            priority: 处理器优先级
        """
        super().__init__(name=name, event_types=event_types, priority=priority)
        self._counters: Dict[str, int] = {}
        self._timers: Dict[str, List[float]] = {}
        self._phase_start_times: Dict[str, float] = {}
        self._plugin_start_times: Dict[str, float] = {}

    def _process(self, event: Event) -> None:
        """收集事件指标。"""
        # 递增事件类型计数器
        self._counters[event.event_type] = self._counters.get(event.event_type, 0) + 1

        # 处理阶段开始/结束事件的耗时统计
        if event.event_type == "phase.start" and hasattr(event, "phase_name"):
            if event.phase_name:
                self._phase_start_times[event.phase_name] = event.timestamp
        elif event.event_type == "phase.end" and hasattr(event, "phase_name"):
            if event.phase_name:
                start_time = self._phase_start_times.pop(event.phase_name, None)
                if start_time is not None:
                    duration = event.timestamp - start_time
                    self._record_timer(f"phase.{event.phase_name}.duration", duration)

        # 处理插件开始/结束事件的耗时统计
        if event.event_type == "plugin.start" and hasattr(event, "plugin_name"):
            if event.plugin_name:
                self._plugin_start_times[event.plugin_name] = event.timestamp
        elif event.event_type == "plugin.end" and hasattr(event, "plugin_name"):
            if event.plugin_name:
                start_time = self._plugin_start_times.pop(event.plugin_name, None)
                if start_time is not None:
                    duration = event.timestamp - start_time
                    self._record_timer(f"plugin.{event.plugin_name}.duration", duration)

    def _record_timer(self, name: str, duration: float) -> None:
        """记录耗时数据。"""
        if name not in self._timers:
            self._timers[name] = []
        self._timers[name].append(duration)

    def get_counter(self, event_type: str) -> int:
        """获取指定事件类型的触发次数。"""
        return self._counters.get(event_type, 0)

    def get_all_counters(self) -> Dict[str, int]:
        """获取所有计数器。"""
        return dict(self._counters)

    def get_timer_stats(self, name: str) -> Dict[str, float]:
        """获取指定计时器的统计信息。"""
        durations = self._timers.get(name, [])
        if not durations:
            return {"count": 0, "total": 0.0, "avg": 0.0, "min": 0.0, "max": 0.0}
        return {
            "count": len(durations),
            "total": sum(durations),
            "avg": sum(durations) / len(durations),
            "min": min(durations),
            "max": max(durations),
        }

    def get_all_timers(self) -> Dict[str, Dict[str, float]]:
        """获取所有计时器统计信息。"""
        return {name: self.get_timer_stats(name) for name in self._timers}

    def clear(self) -> None:
        """清空所有收集的指标。"""
        self._counters.clear()
        self._timers.clear()
        self._phase_start_times.clear()
        self._plugin_start_times.clear()
