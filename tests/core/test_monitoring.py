"""监控系统增强基类的单元测试。

测试覆盖：
- MonitoredComponent 基类的监控能力
- MonitoredModifier 对修改器执行指标的自动采集
- MonitoredPhase 对阶段执行指标的自动采集
- MonitoringMiddleware 可插入监控中间件
- EventBus 事件发布集成
"""

import time
from unittest.mock import Mock, MagicMock

import pytest

from src.core.monitoring import (
    MetricsCollector,
    MonitoredComponent,
    MonitoredModifier,
    MonitoredPhase,
    MonitoringMiddleware,
    EventBus,
)


class TestMonitoredComponent:
    """MonitoredComponent 基类测试。"""

    def test_basic_tracking(self):
        """测试基本的执行跟踪能力。"""
        collector = MetricsCollector()

        class TestComponent(MonitoredComponent):
            def __init__(self):
                super().__init__(collector=collector, name="test")
            def execute(self):
                with self.track_execution():
                    return "done"

        comp = TestComponent()
        result = comp.execute()
        assert result == "done"
        # 验证指标已记录
        metrics = collector.get_metrics()
        assert any("test" in m.name for m in metrics)

    def test_collector_integration(self):
        """测试与 MetricsCollector 的集成。"""
        collector = MetricsCollector()

        class TestComponent(MonitoredComponent):
            def __init__(self):
                super().__init__(collector=collector, name="my_comp")
            def run(self):
                with self.track_execution():
                    collector.increment("runs")
                    return 42

        comp = TestComponent()
        result = comp.run()
        assert result == 42
        assert collector.get_counter("runs") == 1.0

    def test_track_execution_records_duration(self):
        """测试 track_execution 记录执行耗时。"""
        collector = MetricsCollector()

        class TestComponent(MonitoredComponent):
            def __init__(self):
                super().__init__(collector=collector, name="dur_test")
            def work(self):
                with self.track_execution():
                    time.sleep(0.05)
                    return True

        comp = TestComponent()
        assert comp.work() is True
        metrics = collector.get_metrics()
        duration_metrics = [m for m in metrics if "duration" in m.name]
        assert len(duration_metrics) >= 1
        assert duration_metrics[0].value >= 0.04


class TestMonitoredModifier:
    """MonitoredModifier 修改器监控测试。"""

    def test_wraps_modifier_execution(self):
        """测试包装修改器并自动采集执行指标。"""
        collector = MetricsCollector()

        class FakeModifier:
            def __init__(self):
                self.name = "fake_mod"
            def run(self):
                return True

        modifier = MonitoredModifier(
            wrapped=FakeModifier(),
            collector=collector,
            name="fake_mod",
        )
        result = modifier.run()
        assert result is True
        metrics = collector.get_metrics()
        assert any("fake_mod" in m.name for m in metrics)

    def test_tracks_success_failure(self):
        """测试成功/失败指标采集。"""
        collector = MetricsCollector()

        class FailModifier:
            def __init__(self):
                self.name = "fail_mod"
            def run(self):
                raise RuntimeError("执行失败")

        modifier = MonitoredModifier(
            wrapped=FailModifier(),
            collector=collector,
            name="fail_mod",
        )
        with pytest.raises(RuntimeError):
            modifier.run()
        assert collector.get_counter("modifier.fail_mod.failures") == 1.0

    def test_tracks_modified_files_count(self):
        """测试修改文件数指标采集。"""
        collector = MetricsCollector()

        class FileModifier:
            def __init__(self):
                self.name = "file_mod"
            def run(self):
                return True

        modifier = MonitoredModifier(
            wrapped=FileModifier(),
            collector=collector,
            name="file_mod",
        )
        modifier.record_files_modified(5)
        assert collector.get_counter("modifier.file_mod.files_modified") == 5.0


class TestMonitoredPhase:
    """MonitoredPhase 阶段监控测试。"""

    def test_tracks_phase_execution(self):
        """测试阶段执行指标自动采集。"""
        collector = MetricsCollector()
        phase = MonitoredPhase("extraction", collector=collector)

        with phase.execute():
            time.sleep(0.02)

        metrics = collector.get_metrics()
        phase_metrics = [m for m in metrics if "extraction" in m.name]
        assert len(phase_metrics) >= 1

    def test_records_phase_success(self):
        """测试阶段成功指标。"""
        collector = MetricsCollector()
        phase = MonitoredPhase("patching", collector=collector)

        with phase.execute():
            pass

        assert collector.get_counter("phase.patching.success") == 1.0

    def test_records_phase_failure(self):
        """测试阶段失败指标。"""
        collector = MetricsCollector()
        phase = MonitoredPhase("failing_phase", collector=collector)

        with pytest.raises(ValueError):
            with phase.execute():
                raise ValueError("阶段失败")

        assert collector.get_counter("phase.failing_phase.failures") == 1.0


class TestMonitoringMiddleware:
    """MonitoringMiddleware 中间件测试。"""

    def test_custom_metric_collection(self):
        """测试自定义指标采集中间件。"""
        collector = MetricsCollector()
        middleware = MonitoringMiddleware(collector=collector)

        @middleware.on_execution
        def on_exec(name, duration, success):
            collector.record("custom.duration", duration, phase=name)

        # 模拟执行回调
        middleware.notify_execution("test_op", 1.5, True)
        metrics = collector.get_metrics("custom.duration")
        assert len(metrics) == 1
        assert metrics[0].value == 1.5

    def test_multiple_middleware_hooks(self):
        """测试多个中间件钩子。"""
        collector = MetricsCollector()
        middleware = MonitoringMiddleware(collector=collector)

        calls = []

        @middleware.on_execution
        def hook_a(name, duration, success):
            calls.append("a")

        @middleware.on_execution
        def hook_b(name, duration, success):
            calls.append("b")

        middleware.notify_execution("op", 0.1, True)
        assert calls == ["a", "b"]


class TestEventBus:
    """EventBus 事件总线测试。"""

    def test_publish_subscribe(self):
        """测试发布/订阅模式。"""
        bus = EventBus()
        received = []

        def handler(event_type, data):
            received.append((event_type, data))

        bus.subscribe("test_event", handler)
        bus.publish("test_event", {"msg": "hello"})

        assert len(received) == 1
        assert received[0] == ("test_event", {"msg": "hello"})

    def test_multiple_subscribers(self):
        """测试多个订阅者。"""
        bus = EventBus()
        results_a = []
        results_b = []

        bus.subscribe("evt", lambda t, d: results_a.append(d))
        bus.subscribe("evt", lambda t, d: results_b.append(d))

        bus.publish("evt", {"val": 1})
        assert results_a == [{"val": 1}]
        assert results_b == [{"val": 1}]

    def test_unsubscribe(self):
        """测试取消订阅。"""
        bus = EventBus()
        received = []

        def handler(event_type, data):
            received.append(data)

        bus.subscribe("evt", handler)
        bus.unsubscribe("evt", handler)
        bus.publish("evt", {"val": 2})

        assert received == []

    def test_monitoring_event_integration(self):
        """测试监控事件自动发布到事件总线。"""
        bus = EventBus()
        collector = MetricsCollector()
        events_received = []

        def on_monitor_event(event_type, data):
            events_received.append((event_type, data))

        bus.subscribe("monitor.execution", on_monitor_event)

        class TestComp(MonitoredComponent):
            def __init__(self):
                super().__init__(collector=collector, name="evt_test", event_bus=bus)
            def run(self):
                with self.track_execution():
                    return "ok"

        comp = TestComp()
        result = comp.run()
        assert result == "ok"
        # 验证事件已发布
        assert len(events_received) >= 1
        assert events_received[0][0] == "monitor.execution"
