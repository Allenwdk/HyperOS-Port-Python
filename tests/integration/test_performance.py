"""性能回归测试。

确保重构后关键操作的性能不下降。
使用计时基准验证 Pipeline 执行、EventBus 事件分发和 Phase 执行的性能。
"""

import threading
import time
from unittest.mock import MagicMock

import pytest

from src.core.events.bus import EventBus
from src.core.events.events import Event, PhaseStartEvent
from src.core.monitoring import Monitor, MetricsCollector
from src.core.workflow.orchestrator import PortingOrchestrator
from src.core.workflow.pipeline import Pipeline

from tests.integration.conftest import RecordingPhase, SlowPhase


# ============================================================
# 性能基准阈值（秒）
# ============================================================

PIPELINE_EMPTY_OVERHEAD = 0.01
PIPELINE_10_PHASES_OVERHEAD = 0.05
EVENTBUS_1000_EVENTS_TIME = 1.0
MONITOR_RECORD_1000_TIME = 1.0
ORCHESTRATOR_4_PHASES_TIME = 0.1


class TestPipelinePerformance:
    """Pipeline 执行性能回归测试。"""

    def test_empty_pipeline_overhead(self):
        """验证空 Pipeline 的执行开销在基准范围内。"""
        pipeline = Pipeline()
        start = time.monotonic()
        for _ in range(100):
            pipeline.run({})
        elapsed = time.monotonic() - start

        assert elapsed < PIPELINE_EMPTY_OVERHEAD * 100

    def test_ten_phase_pipeline_overhead(self):
        """验证 10 阶段 Pipeline 的执行开销在基准范围内。"""
        pipeline = Pipeline()
        for i in range(10):
            pipeline.add_phase(RecordingPhase(f"phase_{i}", []))

        start = time.monotonic()
        result = pipeline.run({})
        elapsed = time.monotonic() - start

        assert result.success is True
        assert elapsed < PIPELINE_10_PHASES_OVERHEAD

    def test_orchestrator_four_phase_performance(self):
        """验证四阶段编排器的执行性能在基准范围内。"""
        phases = [RecordingPhase(f"p{i}", []) for i in range(4)]
        orchestrator = PortingOrchestrator(phases=phases)

        start = time.monotonic()
        result = orchestrator.run({})
        elapsed = time.monotonic() - start

        assert result.success is True
        assert elapsed < ORCHESTRATOR_4_PHASES_TIME


class TestEventBusPerformance:
    """EventBus 事件分发性能回归测试。"""

    def test_publish_1000_events_performance(self):
        """验证发布 1000 个事件的性能在基准范围内。"""
        bus = EventBus()
        received = []
        bus.subscribe("*", lambda e: received.append(e))

        start = time.monotonic()
        for i in range(1000):
            bus.publish(Event(event_type="perf.test", data={"i": i}))
        elapsed = time.monotonic() - start

        assert len(received) == 1000
        assert elapsed < EVENTBUS_1000_EVENTS_TIME

    def test_publish_with_multiple_subscribers_performance(self):
        """验证多订阅者场景下的事件分发性能。"""
        bus = EventBus()
        for _ in range(10):
            bus.subscribe("perf", lambda e: None)

        start = time.monotonic()
        for _ in range(100):
            bus.publish(Event(event_type="perf"))
        elapsed = time.monotonic() - start

        assert elapsed < 0.5

    def test_concurrent_publish_performance(self):
        """验证并发发布事件的性能。"""
        bus = EventBus()
        count = [0]
        lock = threading.Lock()

        def increment(e):
            with lock:
                count[0] += 1

        bus.subscribe("*", increment)

        def publish_batch(n):
            for _ in range(n):
                bus.publish(Event(event_type="concurrent"))

        threads = [threading.Thread(target=publish_batch, args=(100,)) for _ in range(4)]

        start = time.monotonic()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        elapsed = time.monotonic() - start

        assert count[0] == 400
        assert elapsed < 2.0


class TestMonitorPerformance:
    """Monitor 监控系统性能回归测试。"""

    def test_metrics_collector_1000_records_performance(self):
        """验证 MetricsCollector 记录 1000 个指标的性能。"""
        collector = MetricsCollector()

        start = time.monotonic()
        for i in range(1000):
            collector.record(f"metric_{i}", float(i), "units")
        elapsed = time.monotonic() - start

        assert len(collector._metrics) == 1000
        assert elapsed < MONITOR_RECORD_1000_TIME

    def test_monitor_phase_recording_performance(self):
        """验证 Monitor 阶段记录性能。"""
        monitor = Monitor()
        monitor.start()

        start = time.monotonic()
        for i in range(100):
            monitor.report.add_phase_result(f"phase_{i}", True, {"duration": 0.1})
        elapsed = time.monotonic() - start

        monitor.stop()
        assert elapsed < 0.5

    def test_monitor_with_pipeline_performance(self):
        """验证 Monitor 与 Pipeline 集成时的性能影响。"""
        monitor = Monitor()
        monitor.start()

        pipeline = Pipeline(monitor=monitor)
        for i in range(4):
            pipeline.add_phase(RecordingPhase(f"p{i}", []))

        start = time.monotonic()
        result = pipeline.run({})
        elapsed = time.monotonic() - start

        monitor.stop()
        assert result.success is True
        assert elapsed < 0.1


class TestPhaseExecutionPerformance:
    """Phase 执行性能回归测试。"""

    def test_recording_phase_overhead(self):
        """验证 RecordingPhase 的执行开销极小。"""
        log = []
        phase = RecordingPhase("fast", log)

        start = time.monotonic()
        for _ in range(10000):
            phase.execute({})
        elapsed = time.monotonic() - start

        assert len(log) == 10000
        assert elapsed < 1.0

    def test_context_chaining_phase_performance(self):
        """验证上下文传递阶段的性能。"""
        from tests.integration.conftest import ContextChainingPhase

        phase = ContextChainingPhase("chain", "key", "value")
        ctx = {}

        start = time.monotonic()
        for _ in range(10000):
            phase.execute(ctx)
        elapsed = time.monotonic() - start

        assert elapsed < 1.0
