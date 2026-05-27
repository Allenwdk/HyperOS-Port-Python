"""事件总线系统。

提供发布/订阅模式的事件驱动基础设施，支持：
- 线程安全的事件分发
- 同步和异步事件处理
- 事件类型过滤
- 内置日志和指标处理器
- 预定义事件类型（阶段、插件、错误）

使用示例::

    from src.core.events import EventBus, Event, PhaseStartEvent

    # 创建事件总线
    bus = EventBus()

    # 订阅事件
    def on_phase_start(event):
        print(f"阶段开始: {event.phase_name}")

    bus.subscribe("phase.start", on_phase_start)

    # 发布事件
    bus.publish(PhaseStartEvent("extraction"))
"""

from src.core.events.bus import EventBus
from src.core.events.events import (
    ErrorEvent,
    Event,
    PhaseEndEvent,
    PhaseStartEvent,
    PluginEndEvent,
    PluginStartEvent,
)
from src.core.events.handlers import EventHandler, LoggingHandler, MetricsHandler

__all__ = [
    "EventBus",
    "Event",
    "PhaseStartEvent",
    "PhaseEndEvent",
    "PluginStartEvent",
    "PluginEndEvent",
    "ErrorEvent",
    "EventHandler",
    "LoggingHandler",
    "MetricsHandler",
]
