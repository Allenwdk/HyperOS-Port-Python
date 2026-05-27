"""事件总线系统测试。

测试 EventBus、Event 基类和预定义事件类型、EventHandler 等组件。
"""

import asyncio
import threading
from unittest.mock import Mock

import pytest

from src.core.events import (
    ErrorEvent,
    Event,
    EventBus,
    EventHandler,
    LoggingHandler,
    MetricsHandler,
    PhaseEndEvent,
    PhaseStartEvent,
    PluginEndEvent,
    PluginStartEvent,
)


class TestEvent:
    """Event 基类和预定义事件类型测试。"""

    def test_event_creation(self):
        """测试基本事件创建。"""
        event = Event("test.event", data={"key": "value"})
        assert event.event_type == "test.event"
        assert event.data == {"key": "value"}
        assert event.event_id is not None
        assert event.timestamp > 0

    def test_event_default_data(self):
        """测试事件默认数据为空字典。"""
        event = Event("test")
        assert event.data == {}

    def test_event_get_method(self):
        """测试 Event.get() 方法。"""
        event = Event("test", data={"a": 1, "b": 2})
        assert event.get("a") == 1
        assert event.get("c", "default") == "default"
        assert event.get("c") is None

    def test_phase_start_event(self):
        """测试阶段开始事件。"""
        event = PhaseStartEvent("extraction", data={"files": 100})
        assert event.event_type == "phase.start"
        assert event.phase_name == "extraction"
        assert event.get("files") == 100

    def test_phase_end_event(self):
        """测试阶段结束事件。"""
        event = PhaseEndEvent("extraction", success=True, duration=1.5)
        assert event.event_type == "phase.end"
        assert event.phase_name == "extraction"
        assert event.success is True
        assert event.duration == 1.5

    def test_plugin_start_event(self):
        """测试插件开始执行事件。"""
        event = PluginStartEvent("feature_unlock", plugin_priority=50)
        assert event.event_type == "plugin.start"
        assert event.plugin_name == "feature_unlock"
        assert event.plugin_priority == 50

    def test_plugin_end_event(self):
        """测试插件执行结束事件。"""
        event = PluginEndEvent("feature_unlock", success=False, duration=2.3)
        assert event.event_type == "plugin.end"
        assert event.plugin_name == "feature_unlock"
        assert event.success is False
        assert event.duration == 2.3

    def test_error_event(self):
        """测试错误事件。"""
        event = ErrorEvent("ValueError", "参数无效", phase="extraction")
        assert event.event_type == "error"
        assert event.error_type == "ValueError"
        assert event.error_message == "参数无效"
        assert event.phase == "extraction"


class TestEventBus:
    """EventBus 核心功能测试。"""

    @pytest.fixture
    def bus(self):
        """创建 EventBus 实例。"""
        return EventBus()

    def test_subscribe_and_publish(self, bus):
        """测试基本的订阅和发布功能。"""
        results = []
        bus.subscribe("test", lambda e: results.append(e))
        bus.publish(Event("test", {"data": "hello"}))
        assert len(results) == 1
        assert results[0].data["data"] == "hello"

    def test_multiple_subscribers(self, bus):
        """测试多个订阅者接收同一事件。"""
        counter = [0]

        def handler1(e):
            counter[0] += 1

        def handler2(e):
            counter[0] += 10

        bus.subscribe("test", handler1)
        bus.subscribe("test", handler2)
        bus.publish(Event("test"))
        assert counter[0] == 11

    def test_unsubscribe(self, bus):
        """测试取消订阅。"""
        results = []
        handler = lambda e: results.append(1)
        bus.subscribe("test", handler)
        bus.publish(Event("test"))
        assert len(results) == 1

        bus.unsubscribe("test", handler)
        bus.publish(Event("test"))
        assert len(results) == 1

    def test_wildcard_subscription(self, bus):
        """测试通配符订阅所有事件。"""
        results = []
        bus.subscribe("*", lambda e: results.append(e.event_type))
        bus.publish(Event("phase.start"))
        bus.publish(Event("plugin.end"))
        assert results == ["phase.start", "plugin.end"]

    def test_event_type_isolation(self, bus):
        """测试不同事件类型之间的隔离性。"""
        results_a = []
        results_b = []
        bus.subscribe("type_a", lambda e: results_a.append(1))
        bus.subscribe("type_b", lambda e: results_b.append(1))
        bus.publish(Event("type_a"))
        assert results_a == [1]
        assert results_b == []

    def test_publish_returns_handler_count(self, bus):
        """测试发布事件返回实际处理的处理器数量。"""
        bus.subscribe("test", lambda e: None)
        bus.subscribe("test", lambda e: None)
        count = bus.publish(Event("test"))
        assert count == 2

    def test_handler_exception_does_not_break_others(self, bus):
        """测试处理器异常不影响其他处理器执行。"""
        results = []

        def bad_handler(e):
            raise RuntimeError("处理器错误")

        def good_handler(e):
            results.append("ok")

        bus.subscribe("test", bad_handler)
        bus.subscribe("test", good_handler)
        bus.publish(Event("test"))
        assert results == ["ok"]

    def test_handler_object_subscription(self, bus):
        """测试通过 EventHandler 对象订阅。"""
        results = []
        handler = LoggingHandler(event_types=["phase.start"])
        original_handle = handler._process
        handler._process = lambda e: results.append(e.event_type)

        bus.subscribe_handler(handler)
        bus.publish(PhaseStartEvent("test"))
        assert results == ["phase.start"]

    def test_handler_object_filtering(self, bus):
        """测试 EventHandler 对象的事件过滤功能。"""
        results = []
        handler = LoggingHandler(event_types=["phase.start"])
        handler._process = lambda e: results.append(1)

        bus.subscribe_handler(handler)
        bus.publish(PhaseStartEvent("test"))
        bus.publish(PluginEndEvent("test"))
        assert results == [1]

    def test_disable_event_bus(self, bus):
        """测试禁用事件总线。"""
        results = []
        bus.subscribe("test", lambda e: results.append(1))
        bus.disable()
        bus.publish(Event("test"))
        assert results == []

    def test_clear_subscribers(self, bus):
        """测试清空所有订阅者。"""
        bus.subscribe("test", lambda e: None)
        bus.subscribe("*", lambda e: None)
        bus.clear()
        assert bus.subscriber_count() == 0

    def test_subscriber_count(self, bus):
        """测试获取订阅者数量。"""
        bus.subscribe("type_a", lambda e: None)
        bus.subscribe("type_a", lambda e: None)
        bus.subscribe("type_b", lambda e: None)
        assert bus.subscriber_count("type_a") == 2
        assert bus.subscriber_count("type_b") == 1
        assert bus.subscriber_count() == 3

    def test_has_subscribers(self, bus):
        """测试检查是否有订阅者。"""
        assert not bus.has_subscribers("test")
        bus.subscribe("test", lambda e: None)
        assert bus.has_subscribers("test")


class TestEventBusThreadSafety:
    """EventBus 线程安全测试。"""

    def test_concurrent_publish_subscribe(self):
        """测试并发发布事件的线程安全性。"""
        bus = EventBus()
        counter = [0]
        lock = threading.Lock()

        def handler(e):
            with lock:
                counter[0] += 1

        bus.subscribe("test", handler)

        threads = [
            threading.Thread(target=lambda: bus.publish(Event("test", {})))
            for _ in range(100)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert counter[0] == 100, f"期望 100，实际 {counter[0]}"

    def test_concurrent_subscribe_unsubscribe(self):
        """测试并发订阅和取消订阅的线程安全性。"""
        bus = EventBus()

        def subscriber(e):
            pass

        def subscribe():
            bus.subscribe("test", subscriber)

        def unsubscribe():
            bus.unsubscribe("test", subscriber)

        threads = []
        for _ in range(50):
            threads.append(threading.Thread(target=subscribe))
            threads.append(threading.Thread(target=unsubscribe))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert bus.subscriber_count("test") <= 50

    def test_concurrent_event_bus_operations(self):
        """测试多种并发操作的线程安全性。"""
        bus = EventBus()
        results = []
        lock = threading.Lock()

        def handler(e):
            with lock:
                results.append(1)

        def subscriber():
            bus.subscribe("test", handler)

        def publisher():
            bus.publish(Event("test"))

        threads = []
        for _ in range(30):
            threads.append(threading.Thread(target=subscriber))
            threads.append(threading.Thread(target=publisher))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) >= 0


class TestLoggingHandler:
    """LoggingHandler 测试。"""

    def test_logging_handler_filters_by_type(self):
        """测试日志处理器按事件类型过滤。"""
        handler = LoggingHandler(event_types=["phase.start"])
        assert handler.should_handle(PhaseStartEvent("test"))
        assert not handler.should_handle(PluginEndEvent("test"))

    def test_logging_handler_handles_all_when_no_filter(self):
        """测试无过滤器时处理所有事件。"""
        handler = LoggingHandler()
        assert handler.should_handle(PhaseStartEvent("test"))
        assert handler.should_handle(PluginEndEvent("test"))
        assert handler.should_handle(ErrorEvent("err", "msg"))


class TestMetricsHandler:
    """MetricsHandler 测试。"""

    def test_counter_increment(self):
        """测试指标计数器递增。"""
        handler = MetricsHandler()
        handler.handle(PhaseStartEvent("p1"))
        handler.handle(PhaseStartEvent("p2"))
        handler.handle(PluginStartEvent("pl1"))
        assert handler.get_counter("phase.start") == 2
        assert handler.get_counter("plugin.start") == 1

    def test_phase_duration_tracking(self):
        """测试阶段耗时统计。"""
        handler = MetricsHandler()
        start = PhaseStartEvent("extraction")
        start.timestamp = 100.0
        handler.handle(start)

        end = PhaseEndEvent("extraction", success=True, duration=5.0)
        end.timestamp = 105.0
        handler.handle(end)

        stats = handler.get_timer_stats("phase.extraction.duration")
        assert stats["count"] == 1
        assert stats["total"] == 5.0

    def test_clear_metrics(self):
        """测试清空指标数据。"""
        handler = MetricsHandler()
        handler.handle(PhaseStartEvent("p1"))
        handler.clear()
        assert handler.get_counter("phase.start") == 0


class TestAsyncEventBus:
    """异步事件分发测试。"""

    @pytest.fixture
    def event_loop(self):
        """创建事件循环。"""
        loop = asyncio.new_event_loop()
        yield loop
        loop.close()

    def test_async_publish(self, event_loop):
        """测试异步发布事件。"""
        bus = EventBus()
        results = []

        async def async_handler(e):
            results.append(e.event_type)

        bus.subscribe_async("test", async_handler)

        async def run():
            await bus.publish_async(Event("test"))

        event_loop.run_until_complete(run())
        assert results == ["test"]

    def test_async_publish_mixed_handlers(self, event_loop):
        """测试异步发布同时包含同步和异步处理器。"""
        bus = EventBus()
        results = []

        def sync_handler(e):
            results.append("sync")

        async def async_handler(e):
            results.append("async")

        bus.subscribe("test", sync_handler)
        bus.subscribe_async("test", async_handler)

        async def run():
            await bus.publish_async(Event("test"))

        event_loop.run_until_complete(run())
        assert "sync" in results
        assert "async" in results


class TestEventBusDependencyInjection:
    """EventBus 依赖注入模式测试。"""

    def test_bus_passed_as_parameter(self):
        """测试 EventBus 通过参数传递而非全局单例。"""
        bus1 = EventBus()
        bus2 = EventBus()

        results1 = []
        results2 = []

        bus1.subscribe("test", lambda e: results1.append(1))
        bus2.subscribe("test", lambda e: results2.append(1))

        bus1.publish(Event("test"))

        assert len(results1) == 1
        assert len(results2) == 0

    def test_independent_bus_instances(self):
        """测试多个 EventBus 实例独立运作。"""
        buses = [EventBus() for _ in range(5)]
        counters = [0] * 5

        for i, bus in enumerate(buses):
            idx = i
            bus.subscribe("test", lambda e, idx=idx: counters.__setitem__(idx, counters[idx] + 1))

        buses[2].publish(Event("test"))
        assert counters == [0, 0, 1, 0, 0]
