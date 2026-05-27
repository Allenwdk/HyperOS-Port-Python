"""事件定义模块。

提供事件基类和预定义事件类型，用于事件总线系统。
"""

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class Event:
    """事件基类。

    所有事件必须继承此类，包含事件类型标识和关联数据。

    Attributes:
        event_type: 事件类型标识符
        data: 事件携带的数据字典
        source: 事件来源标识（可选）
        timestamp: 事件创建时间戳（自动填充）
        event_id: 事件唯一标识（自动生成）
    """

    event_type: str
    data: Dict[str, Any] = field(default_factory=dict)
    source: Optional[str] = None
    timestamp: float = field(default_factory=time.time)
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def get(self, key: str, default: Any = None) -> Any:
        """从 data 字典中获取值。"""
        return self.data.get(key, default)

    def __post_init__(self):
        """初始化后确保 data 不为空字典。"""
        if self.data is None:
            self.data = {}


@dataclass
class PhaseStartEvent(Event):
    """阶段开始事件。

    当某个执行阶段开始时触发。

    Attributes:
        phase_name: 阶段名称
    """

    phase_name: str = ""

    def __init__(
        self,
        phase_name: str,
        data: Optional[Dict[str, Any]] = None,
        source: Optional[str] = None,
    ):
        super().__init__(
            event_type="phase.start",
            data=data or {},
            source=source,
        )
        self.phase_name = phase_name


@dataclass
class PhaseEndEvent(Event):
    """阶段结束事件。

    当某个执行阶段完成时触发。

    Attributes:
        phase_name: 阶段名称
        success: 阶段是否成功完成
        duration: 阶段执行耗时（秒）
    """

    phase_name: str = ""
    success: bool = True
    duration: float = 0.0

    def __init__(
        self,
        phase_name: str,
        success: bool = True,
        duration: float = 0.0,
        data: Optional[Dict[str, Any]] = None,
        source: Optional[str] = None,
    ):
        super().__init__(
            event_type="phase.end",
            data=data or {},
            source=source,
        )
        self.phase_name = phase_name
        self.success = success
        self.duration = duration


@dataclass
class PluginStartEvent(Event):
    """插件开始执行事件。

    当插件开始执行时触发。

    Attributes:
        plugin_name: 插件名称
        plugin_priority: 插件优先级
    """

    plugin_name: str = ""
    plugin_priority: int = 100

    def __init__(
        self,
        plugin_name: str,
        plugin_priority: int = 100,
        data: Optional[Dict[str, Any]] = None,
        source: Optional[str] = None,
    ):
        super().__init__(
            event_type="plugin.start",
            data=data or {},
            source=source,
        )
        self.plugin_name = plugin_name
        self.plugin_priority = plugin_priority


@dataclass
class PluginEndEvent(Event):
    """插件执行结束事件。

    当插件执行完成时触发。

    Attributes:
        plugin_name: 插件名称
        success: 插件是否执行成功
        duration: 插件执行耗时（秒）
    """

    plugin_name: str = ""
    success: bool = True
    duration: float = 0.0

    def __init__(
        self,
        plugin_name: str,
        success: bool = True,
        duration: float = 0.0,
        data: Optional[Dict[str, Any]] = None,
        source: Optional[str] = None,
    ):
        super().__init__(
            event_type="plugin.end",
            data=data or {},
            source=source,
        )
        self.plugin_name = plugin_name
        self.success = success
        self.duration = duration


@dataclass
class ErrorEvent(Event):
    """错误事件。

    当系统发生错误时触发。

    Attributes:
        error_type: 错误类型名称
        error_message: 错误描述信息
        phase: 发生错误的阶段（可选）
        plugin: 发生错误的插件（可选）
        traceback: 错误堆栈信息（可选）
    """

    error_type: str = ""
    error_message: str = ""
    phase: Optional[str] = None
    plugin: Optional[str] = None
    traceback: Optional[str] = None

    def __init__(
        self,
        error_type: str,
        error_message: str,
        phase: Optional[str] = None,
        plugin: Optional[str] = None,
        traceback_str: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
        source: Optional[str] = None,
    ):
        super().__init__(
            event_type="error",
            data=data or {},
            source=source,
        )
        self.error_type = error_type
        self.error_message = error_message
        self.phase = phase
        self.plugin = plugin
        self.traceback = traceback_str
