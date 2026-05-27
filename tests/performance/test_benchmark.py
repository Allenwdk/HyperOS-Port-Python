"""关键操作执行时间基准测试。

测量 PathCache、IncrementalTracker、FastHasher、Pipeline、EventBus
和 MetricsCollector 等关键操作的执行时间。
"""

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.core.events.bus import EventBus
from src.core.events.events import Event, PhaseStartEvent
from src.core.monitoring import MetricsCollector
from src.core.performance import (
    BuildPropCache,
    FastHasher,
    IncrementalTracker,
    PathCache,
)
from src.core.workflow.pipeline import Pipeline
from src.core.workflow.orchestrator import PortingOrchestrator

from tests.integration.conftest import RecordingPhase
from tests.performance.conftest import Timer, TimingResult, benchmark


PATHCACHE_RGLOB_SMALL_THRESHOLD = 0.05
PATHCACHE_RGLOB_LARGE_THRESHOLD = 0.5
INCREMENTAL_100_FILES_THRESHOLD = 0.1
HASHER_SMALL_FILE_THRESHOLD = 0.001
PIPELINE_EMPTY_THRESHOLD = 0.01
PIPELINE_10_PHASES_THRESHOLD = 0.05
EVENTBUS_1000_EVENTS_THRESHOLD = 1.0
METRICS_1000_RECORDS_THRESHOLD = 0.5
ORCHESTRATOR_4_PHASES_THRESHOLD = 0.1


class TestPathCacheBenchmark:
    """PathCache 执行时间基准测试。"""

    def test_rglob_small_tree_performance(self, small_test_tree, report_collector):
        """小型目录树的 rglob 应在 50ms 内完成。"""
        cache = PathCache(small_test_tree)

        with Timer("rglob_small") as t:
            for _ in range(10):
                cache.rglob("*.txt")

        result = t.result(iterations=10)
        report_collector.add(result)

        assert result.elapsed < PATHCACHE_RGLOB_SMALL_THRESHOLD * 10

    def test_rglob_large_tree_performance(self, large_test_tree, report_collector):
        """大型目录树的 rglob 应在 500ms 内完成。"""
        cache = PathCache(large_test_tree)

        with Timer("rglob_large") as t:
            cache.rglob("*.txt")

        result = t.result(iterations=1)
        report_collector.add(result)

        assert result.elapsed < PATHCACHE_RGLOB_LARGE_THRESHOLD

    def test_cached_rglob_near_zero_overhead(self, large_test_tree, report_collector):
        """缓存命中时 rglob 开销应接近零。"""
        cache = PathCache(large_test_tree)
        cache.rglob("*.txt")

        result = benchmark(
            lambda: cache.rglob("*.txt"),
            iterations=1000,
            label="cached_rglob",
        )
        report_collector.add(result)

        assert result.per_iteration < 0.01


class TestIncrementalTrackerBenchmark:
    """IncrementalTracker 执行时间基准测试。"""

    def test_100_files_check_performance(self, tmp_path, report_collector):
        """100 个文件的增量检查应在 100ms 内完成。"""
        files = []
        for i in range(100):
            f = tmp_path / f"file_{i}.txt"
            f.write_text(f"content_{i}")
            files.append(f)

        tracker = IncrementalTracker(tmp_path)
        for f in files:
            tracker.is_changed(f)

        with Timer("incremental_100_files") as t:
            for f in files:
                tracker.is_changed(f)

        result = t.result(iterations=100)
        report_collector.add(result)

        assert result.elapsed < INCREMENTAL_100_FILES_THRESHOLD

    def test_1000_files_batch_check_performance(self, tmp_path, report_collector):
        """1000 个文件的批量检查性能测试。"""
        files = []
        for i in range(1000):
            f = tmp_path / f"file_{i}.txt"
            f.write_text(f"content_{i}")
            files.append(f)

        tracker = IncrementalTracker(tmp_path)
        for f in files:
            tracker.is_changed(f)

        with Timer("incremental_1000_files") as t:
            changed = tracker.get_changed_files(files)

        result = t.result(iterations=1000)
        report_collector.add(result)

        assert len(changed) == 0
        assert result.elapsed < 1.0


class TestFastHasherBenchmark:
    """FastHasher 执行时间基准测试。"""

    def test_small_file_hash_under_1ms(self, tmp_path, fast_hasher, report_collector):
        """小文件哈希应在 1ms 内完成。"""
        f = tmp_path / "small.txt"
        f.write_bytes(b"x" * 1024)

        result = benchmark(
            lambda: fast_hasher.hash_file(f),
            iterations=100,
            label="small_file_hash",
        )
        report_collector.add(result)

        assert result.per_iteration < HASHER_SMALL_FILE_THRESHOLD

    def test_medium_file_hash_performance(self, tmp_path, fast_hasher, report_collector):
        """中等文件（10MB）哈希性能测试。"""
        f = tmp_path / "medium.bin"
        f.write_bytes(b"\x00" * (10 * 1024 * 1024))

        with Timer("medium_file_hash") as t:
            fast_hasher.hash_file(f)

        result = t.result(iterations=1)
        report_collector.add(result)

        assert result.elapsed < 0.1


class TestPipelineBenchmark:
    """Pipeline 执行时间基准测试。"""

    def test_empty_pipeline_overhead(self, report_collector):
        """空 Pipeline 执行开销应在 10ms 内。"""
        pipeline = Pipeline()

        result = benchmark(
            lambda: pipeline.run({}),
            iterations=100,
            label="empty_pipeline",
        )
        report_collector.add(result)

        assert result.per_iteration < PIPELINE_EMPTY_THRESHOLD

    def test_ten_phase_pipeline_performance(self, report_collector):
        """10 阶段 Pipeline 执行应在 50ms 内。"""
        pipeline = Pipeline()
        for i in range(10):
            pipeline.add_phase(RecordingPhase(f"phase_{i}", []))

        with Timer("10_phase_pipeline") as t:
            result = pipeline.run({})

        timing = t.result(iterations=10)
        report_collector.add(timing)

        assert result.success is True
        assert timing.elapsed < PIPELINE_10_PHASES_THRESHOLD


class TestEventBusBenchmark:
    """EventBus 事件分发性能基准测试。"""

    def test_1000_events_performance(self, report_collector):
        """发布 1000 个事件应在 1s 内完成。"""
        bus = EventBus()
        received = []
        bus.subscribe("*", lambda e: received.append(e))

        with Timer("eventbus_1000_events") as t:
            for i in range(1000):
                bus.publish(Event(event_type="perf.test", data={"i": i}))

        result = t.result(iterations=1000)
        report_collector.add(result)

        assert len(received) == 1000
        assert result.elapsed < EVENTBUS_1000_EVENTS_THRESHOLD

    def test_multi_subscriber_performance(self, report_collector):
        """多订阅者场景的事件分发性能测试。"""
        bus = EventBus()
        for _ in range(10):
            bus.subscribe("perf", lambda e: None)

        with Timer("eventbus_multi_subscriber") as t:
            for _ in range(100):
                bus.publish(Event(event_type="perf"))

        result = t.result(iterations=100)
        report_collector.add(result)

        assert result.elapsed < 0.5


class TestMetricsCollectorBenchmark:
    """MetricsCollector 记录性能基准测试。"""

    def test_1000_records_performance(self, report_collector):
        """记录 1000 个指标应在 500ms 内完成。"""
        collector = MetricsCollector()

        with Timer("metrics_1000_records") as t:
            for i in range(1000):
                collector.record(f"metric_{i}", float(i), "units")

        result = t.result(iterations=1000)
        report_collector.add(result)

        assert len(collector._metrics) == 1000
        assert result.elapsed < METRICS_1000_RECORDS_THRESHOLD


class TestOrchestratorBenchmark:
    """PortingOrchestrator 执行性能基准测试。"""

    def test_four_phase_orchestrator_performance(self, report_collector):
        """四阶段编排器执行应在 100ms 内。"""
        phases = [RecordingPhase(f"p{i}", []) for i in range(4)]
        orchestrator = PortingOrchestrator(phases=phases)

        with Timer("orchestrator_4_phases") as t:
            result = orchestrator.run({})

        timing = t.result(iterations=4)
        report_collector.add(timing)

        assert result.success is True
        assert timing.elapsed < ORCHESTRATOR_4_PHASES_THRESHOLD
