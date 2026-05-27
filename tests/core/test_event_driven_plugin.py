"""事件驱动插件系统测试。

测试 EventDrivenPlugin 基类、插件间通信、热插拔、监控指标采集等功能。
"""

import time
from typing import Any, Optional
from unittest.mock import MagicMock, Mock, patch

import pytest

from src.core.events import Event, EventBus, MetricsHandler, PluginEndEvent, PluginStartEvent
from src.core.modifiers.plugin_system import (
    EventDrivenPlugin,
    ModifierPlugin,
    ModifierRegistry,
    PluginManager,
)


# ──────────────────────────────────────────────────────────────────────
# 测试辅助插件
# ──────────────────────────────────────────────────────────────────────


class SimpleEventPlugin(EventDrivenPlugin):
    """简单的事件驱动插件，用于测试。"""

    name = "simple_event_plugin"
    description = "简单事件驱动插件"
    priority = 100

    def _execute_modify(self) -> bool:
        self.emit_event("plugin.custom", {"action": "modify", "plugin": self.name})
        return True


class ListenerPlugin(EventDrivenPlugin):
    """监听其他插件事件的插件。"""

    name = "listener_plugin"
    description = "监听插件"
    priority = 200

    def __init__(self, context: Any, logger: Optional[Any] = None):
        super().__init__(context, logger)
        self.received_events = []
        self.on_event("plugin.custom", self._handle_custom_event)

    def _handle_custom_event(self, event: Event) -> None:
        self.received_events.append(event)

    def _execute_modify(self) -> bool:
        return True


class FailingPlugin(EventDrivenPlugin):
    """会执行失败的插件，用于测试错误处理。"""

    name = "failing_plugin"
    description = "失败插件"
    priority = 50

    def _execute_modify(self) -> bool:
        raise RuntimeError("插件执行失败")


class MetricsCollectingPlugin(EventDrivenPlugin):
    """采集指标的插件。"""

    name = "metrics_plugin"
    description = "指标采集插件"
    priority = 100

    def _execute_modify(self) -> bool:
        time.sleep(0.01)
        return True


# ──────────────────────────────────────────────────────────────────────
# 测试 EventDrivenPlugin 基类
# ──────────────────────────────────────────────────────────────────────


class TestEventDrivenPlugin:
    """EventDrivenPlugin 基类测试。"""

    @pytest.fixture
    def context(self):
        """创建模拟上下文。"""
        ctx = MagicMock()
        ctx.device_config = {}
        return ctx

    @pytest.fixture
    def bus(self):
        """创建 EventBus 实例。"""
        return EventBus()

    def test_event_driven_plugin_inherits_modifier_plugin(self, context):
        """测试 EventDrivenPlugin 继承自 ModifierPlugin。"""
        plugin = SimpleEventPlugin(context)
        assert isinstance(plugin, ModifierPlugin)
        assert isinstance(plugin, EventDrivenPlugin)

    def test_event_driven_plugin_has_event_bus(self, context, bus):
        """测试 EventDrivenPlugin 可以设置 EventBus。"""
        plugin = SimpleEventPlugin(context)
        plugin.set_event_bus(bus)
        assert plugin.event_bus is bus

    def test_event_driven_plugin_default_no_bus(self, context):
        """测试默认情况下 EventDrivenPlugin 没有 EventBus。"""
        plugin = SimpleEventPlugin(context)
        assert plugin.event_bus is None

    def test_event_driven_plugin_subscribe_and_emit(self, context, bus):
        """测试事件订阅和发布功能。"""
        plugin = SimpleEventPlugin(context)
        plugin.set_event_bus(bus)

        received = []
        plugin.on_event("test.event", lambda e: received.append(e))

        # 发布事件
        plugin.emit_event("test.event", {"key": "value"})

        assert len(received) == 1
        assert received[0].data["key"] == "value"

    def test_event_driven_plugin_emit_without_bus(self, context):
        """测试没有 EventBus 时发布事件不报错。"""
        plugin = SimpleEventPlugin(context)
        # 不设置 event_bus，应该安全地忽略
        plugin.emit_event("test.event", {"key": "value"})

    def test_event_driven_plugin_on_event_without_bus(self, context, bus):
        """测试没有 EventBus 时订阅事件会被暂存。"""
        plugin = SimpleEventPlugin(context)
        received = []
        plugin.on_event("test.event", lambda e: received.append(e))

        # 设置 EventBus 后，暂存的订阅应该自动应用
        plugin.set_event_bus(bus)
        plugin.emit_event("test.event", {"key": "value"})
        assert len(received) == 1

    def test_event_driven_plugin_metrics_collection(self, context, bus):
        """测试插件自动采集执行指标。"""
        plugin = MetricsCollectingPlugin(context)
        plugin.set_event_bus(bus)

        # 执行插件
        result = plugin.modify()
        assert result is True

        # 检查指标
        metrics = plugin.get_metrics()
        assert "execution_count" in metrics
        assert metrics["execution_count"] == 1
        assert "total_duration" in metrics
        assert metrics["total_duration"] > 0
        assert "last_execution_time" in metrics
        assert metrics["last_execution_time"] > 0
        assert "success_count" in metrics
        assert metrics["success_count"] == 1
        assert "failure_count" in metrics
        assert metrics["failure_count"] == 0

    def test_event_driven_plugin_metrics_multiple_executions(self, context, bus):
        """测试多次执行的指标累积。"""
        plugin = MetricsCollectingPlugin(context)
        plugin.set_event_bus(bus)

        # 执行多次
        plugin.modify()
        plugin.modify()
        plugin.modify()

        metrics = plugin.get_metrics()
        assert metrics["execution_count"] == 3
        assert metrics["success_count"] == 3
        assert metrics["total_duration"] > 0

    def test_event_driven_plugin_metrics_on_failure(self, context, bus):
        """测试失败时的指标采集。"""
        plugin = FailingPlugin(context)
        plugin.set_event_bus(bus)

        # 执行失败的插件
        with pytest.raises(RuntimeError, match="插件执行失败"):
            plugin.modify()

        metrics = plugin.get_metrics()
        assert metrics["execution_count"] == 1
        assert metrics["failure_count"] == 1
        assert metrics["success_count"] == 0

    def test_event_driven_plugin_resets_metrics(self, context, bus):
        """测试指标重置功能。"""
        plugin = MetricsCollectingPlugin(context)
        plugin.set_event_bus(bus)

        plugin.modify()
        assert plugin.get_metrics()["execution_count"] == 1

        plugin.reset_metrics()
        assert plugin.get_metrics()["execution_count"] == 0
        assert plugin.get_metrics()["total_duration"] == 0.0


# ──────────────────────────────────────────────────────────────────────
# 测试插件间通信
# ──────────────────────────────────────────────────────────────────────


class TestInterPluginCommunication:
    """插件间通信测试。"""

    @pytest.fixture
    def context(self):
        ctx = MagicMock()
        ctx.device_config = {}
        return ctx

    @pytest.fixture
    def bus(self):
        return EventBus()

    def test_two_plugins_communicate_via_events(self, context, bus):
        """测试两个插件通过事件总线通信。"""
        emitter = SimpleEventPlugin(context)
        listener = ListenerPlugin(context)

        emitter.set_event_bus(bus)
        listener.set_event_bus(bus)

        # 执行 emitter 插件，它会发布事件
        emitter.modify()

        # listener 应该接收到事件
        assert len(listener.received_events) == 1
        assert listener.received_events[0].data["action"] == "modify"
        assert listener.received_events[0].data["plugin"] == "simple_event_plugin"

    def test_multiple_listeners_receive_same_event(self, context, bus):
        """测试多个监听者接收同一事件。"""
        emitter = SimpleEventPlugin(context)
        listener1 = ListenerPlugin(context)
        listener2 = ListenerPlugin(context)

        emitter.set_event_bus(bus)
        listener1.set_event_bus(bus)
        listener2.set_event_bus(bus)

        emitter.modify()

        assert len(listener1.received_events) == 1
        assert len(listener2.received_events) == 1

    def test_event_data_isolation_between_plugins(self, context, bus):
        """测试事件数据在插件间的隔离性。"""
        plugin_a = SimpleEventPlugin(context)
        plugin_b = SimpleEventPlugin(context)

        plugin_a.set_event_bus(bus)
        plugin_b.set_event_bus(bus)

        received_a = []
        received_b = []

        plugin_a.on_event("target.event", lambda e: received_a.append(e))
        plugin_b.on_event("target.event", lambda e: received_b.append(e))

        # plugin_a 发布事件
        plugin_a.emit_event("target.event", {"from": "a"})

        # 两者都应该收到
        assert len(received_a) == 1
        assert len(received_b) == 1
        assert received_a[0].data["from"] == "a"


# ──────────────────────────────────────────────────────────────────────
# 测试热插拔
# ──────────────────────────────────────────────────────────────────────


class TestHotPlug:
    """热插拔测试。"""

    @pytest.fixture
    def context(self):
        ctx = MagicMock()
        ctx.device_config = {}
        return ctx

    @pytest.fixture
    def bus(self):
        return EventBus()

    def test_hot_load_plugin_at_runtime(self, context, bus):
        """测试运行时动态加载插件。"""
        manager = PluginManager(context)
        manager.set_event_bus(bus)

        # 初始没有插件
        assert manager.get_plugin("simple_event_plugin") is None

        # 动态加载插件
        manager.register(SimpleEventPlugin)
        assert manager.get_plugin("simple_event_plugin") is not None

    def test_hot_unload_plugin_at_runtime(self, context, bus):
        """测试运行时动态卸载插件。"""
        manager = PluginManager(context)
        manager.set_event_bus(bus)

        manager.register(SimpleEventPlugin)
        assert manager.has_plugin("simple_event_plugin")

        # 动态卸载
        result = manager.unregister("simple_event_plugin")
        assert result is True
        assert not manager.has_plugin("simple_event_plugin")

    def test_hot_reload_plugin(self, context, bus):
        """测试运行时重新加载插件。"""
        manager = PluginManager(context)
        manager.set_event_bus(bus)

        manager.register(SimpleEventPlugin)
        plugin1 = manager.get_plugin("simple_event_plugin")

        # 卸载后重新加载
        manager.unregister("simple_event_plugin")
        manager.register(SimpleEventPlugin)
        plugin2 = manager.get_plugin("simple_event_plugin")

        # 应该是新实例
        assert plugin1 is not plugin2

    def test_unload_nonexistent_plugin(self, context, bus):
        """测试卸载不存在的插件。"""
        manager = PluginManager(context)
        manager.set_event_bus(bus)

        result = manager.unregister("nonexistent")
        assert result is False

    def test_hot_loaded_plugin_has_event_bus(self, context, bus):
        """测试热加载的插件自动绑定 EventBus。"""
        manager = PluginManager(context)
        manager.set_event_bus(bus)

        manager.register(SimpleEventPlugin)
        plugin = manager.get_plugin("simple_event_plugin")

        assert plugin.event_bus is bus


# ──────────────────────────────────────────────────────────────────────
# 测试 ModifierRegistry 增强
# ──────────────────────────────────────────────────────────────────────


class TestModifierRegistryEnhanced:
    """ModifierRegistry 增强功能测试。"""

    @pytest.fixture(autouse=True)
    def _clear_registry(self):
        """每个测试前清空注册表。"""
        ModifierRegistry._registry.clear()
        yield
        ModifierRegistry._registry.clear()

    def test_register_event_driven_plugin(self):
        """测试注册事件驱动插件。"""

        @ModifierRegistry.register
        class TestEDP(EventDrivenPlugin):
            name = "test_edp"

            def modify(self):
                return True

        assert "test_edp" in ModifierRegistry.list_all()

    def test_discover_plugins(self):
        """测试插件发现功能。"""

        @ModifierRegistry.register
        class PluginA(EventDrivenPlugin):
            name = "plugin_a"
            tags = ["system", "feature"]

            def modify(self):
                return True

        @ModifierRegistry.register
        class PluginB(EventDrivenPlugin):
            name = "plugin_b"
            tags = ["system", "config"]

            def modify(self):
                return True

        # 按标签发现
        discovered = ModifierRegistry.discover(tags=["system"])
        assert len(discovered) == 2

        discovered_feature = ModifierRegistry.discover(tags=["feature"])
        assert len(discovered_feature) == 1
        assert discovered_feature[0].name == "plugin_a"

    def test_discover_plugins_by_type(self):
        """测试按类型发现插件。"""

        @ModifierRegistry.register
        class OldPlugin(ModifierPlugin):
            name = "old_plugin"

            def modify(self):
                return True

        @ModifierRegistry.register
        class NewPlugin(EventDrivenPlugin):
            name = "new_plugin"

            def modify(self):
                return True

        # 只发现 EventDrivenPlugin 类型
        discovered = ModifierRegistry.discover(plugin_type=EventDrivenPlugin)
        assert len(discovered) == 1
        assert discovered[0].name == "new_plugin"

    def test_auto_register_to_manager(self):
        """测试自动注册到管理器。"""

        @ModifierRegistry.register
        class AutoPlugin(EventDrivenPlugin):
            name = "auto_plugin"

            def modify(self):
                return True

        context = MagicMock()
        context.device_config = {}
        manager = PluginManager(context)
        bus = EventBus()
        manager.set_event_bus(bus)

        ModifierRegistry.auto_register(manager)
        assert manager.has_plugin("auto_plugin")


# ──────────────────────────────────────────────────────────────────────
# 测试 PluginManager EventBus 集成
# ──────────────────────────────────────────────────────────────────────


class TestPluginManagerEventBusIntegration:
    """PluginManager EventBus 集成测试。"""

    @pytest.fixture
    def context(self):
        ctx = MagicMock()
        ctx.device_config = {}
        return ctx

    def test_plugin_manager_set_event_bus(self, context):
        """测试 PluginManager 设置 EventBus。"""
        manager = PluginManager(context)
        bus = EventBus()
        manager.set_event_bus(bus)
        assert manager.event_bus is bus

    def test_plugin_manager_default_no_bus(self, context):
        """测试默认情况下 PluginManager 没有 EventBus。"""
        manager = PluginManager(context)
        assert manager.event_bus is None

    def test_registered_plugin_gets_event_bus(self, context):
        """测试注册的插件自动获得 EventBus。"""
        manager = PluginManager(context)
        bus = EventBus()
        manager.set_event_bus(bus)

        manager.register(SimpleEventPlugin)
        plugin = manager.get_plugin("simple_event_plugin")

        assert plugin.event_bus is bus

    def test_execute_publishes_plugin_events(self, context):
        """测试执行插件时发布插件事件。"""
        manager = PluginManager(context, enable_transactions=False)
        bus = EventBus()
        manager.set_event_bus(bus)

        received_events = []
        bus.subscribe("plugin.start", lambda e: received_events.append(("start", e)))
        bus.subscribe("plugin.end", lambda e: received_events.append(("end", e)))

        manager.register(SimpleEventPlugin)
        manager.execute()

        # 应该收到 plugin.start 和 plugin.end 事件
        start_events = [e for t, e in received_events if t == "start"]
        end_events = [e for t, e in received_events if t == "end"]

        assert len(start_events) == 1
        assert start_events[0].plugin_name == "simple_event_plugin"
        assert len(end_events) == 1
        assert end_events[0].plugin_name == "simple_event_plugin"
        assert end_events[0].success is True

    def test_execute_failure_publishes_error_event(self, context):
        """测试插件失败时发布错误事件。"""
        manager = PluginManager(context, enable_transactions=False)
        bus = EventBus()
        manager.set_event_bus(bus)

        received_events = []
        bus.subscribe("plugin.end", lambda e: received_events.append(e))

        manager.register(FailingPlugin)
        results = manager.execute()

        # 执行应该返回失败
        assert results.get("failing_plugin") is False

        # 应该收到 plugin.end 事件且 success=False
        assert len(received_events) == 1
        assert received_events[0].success is False


# ──────────────────────────────────────────────────────────────────────
# 集成测试
# ──────────────────────────────────────────────────────────────────────


class TestEventDrivenPluginIntegration:
    """EventDrivenPlugin 集成测试。"""

    @pytest.fixture
    def context(self):
        ctx = MagicMock()
        ctx.device_config = {}
        return ctx

    def test_full_lifecycle_with_event_bus(self, context):
        """测试完整的事件驱动插件生命周期。"""
        bus = EventBus()
        metrics_handler = MetricsHandler()
        bus.subscribe_handler(metrics_handler)

        manager = PluginManager(context, enable_transactions=False)
        manager.set_event_bus(bus)

        # 注册插件
        manager.register(SimpleEventPlugin)
        manager.register(ListenerPlugin)

        # 执行
        results = manager.execute()

        # 验证执行结果
        assert results.get("simple_event_plugin") is True
        assert results.get("listener_plugin") is True

        # 验证事件传播
        assert metrics_handler.get_counter("plugin.start") == 2
        assert metrics_handler.get_counter("plugin.end") == 2

    def test_plugin_metrics_in_execution_report(self, context):
        """测试插件指标在执行报告中体现。"""
        bus = EventBus()
        manager = PluginManager(context, enable_transactions=False)
        manager.set_event_bus(bus)

        manager.register(MetricsCollectingPlugin)
        manager.execute()

        report = manager.get_execution_report()
        assert report["total"] == 1
        assert report["succeeded"] == 1
        assert report["failed"] == 0

    def test_custom_event_during_execution(self, context):
        """测试执行过程中发布自定义事件。"""
        bus = EventBus()
        manager = PluginManager(context, enable_transactions=False)
        manager.set_event_bus(bus)

        custom_events = []
        bus.subscribe("plugin.custom", lambda e: custom_events.append(e))

        manager.register(SimpleEventPlugin)
        manager.execute()

        # 验证自定义事件被发布
        assert len(custom_events) == 1
        assert custom_events[0].data["action"] == "modify"
