"""性能报告生成测试。

汇总所有性能基准测试结果，生成对比报告。
验证报告格式和内容完整性。
"""

import json
import time
from pathlib import Path

import pytest

from src.core.events.bus import EventBus
from src.core.events.events import Event
from src.core.monitoring import MetricsCollector, MonitoringReport
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


class TestPerformanceReportGeneration:
    """性能报告生成和验证测试。"""

    def test_generate_comprehensive_report(self, tmp_path, report_collector):
        """生成包含所有基准测试结果的综合报告。"""
        # 运行各类基准测试并收集结果
        results = {}

        tree = tmp_path / "tree"
        tree.mkdir()
        for i in range(50):
            d = tree / f"dir_{i}"
            d.mkdir()
            (d / f"file.txt").write_text(f"content_{i}")

        cache = PathCache(tree)
        with Timer("path_cache_first") as t:
            cache.rglob("*.txt")
        results["path_cache_first_ms"] = t.elapsed * 1000

        with Timer("path_cache_cached") as t:
            for _ in range(100):
                cache.rglob("*.txt")
        results["path_cache_cached_avg_ms"] = (t.elapsed / 100) * 1000
        results["path_cache_hit_rate"] = cache.cache_hits / (cache.cache_hits + cache.cache_misses)

        files = []
        for i in range(100):
            f = tmp_path / f"inc_{i}.txt"
            f.write_text(f"content_{i}")
            files.append(f)

        tracker = IncrementalTracker(tmp_path)
        for f in files:
            tracker.is_changed(f)

        with Timer("incremental_check") as t:
            for f in files:
                tracker.is_changed(f)
        results["incremental_check_100_files_ms"] = t.elapsed * 1000
        results["incremental_hit_rate"] = tracker.cache_hits / (tracker.cache_hits + tracker.cache_misses)

        hasher = FastHasher()
        small_file = tmp_path / "small.txt"
        small_file.write_bytes(b"x" * 1024)

        with Timer("hasher_small") as t:
            for _ in range(100):
                hasher.hash_file(small_file)
        results["hasher_small_avg_ms"] = (t.elapsed / 100) * 1000

        pipeline = Pipeline()
        for i in range(4):
            pipeline.add_phase(RecordingPhase(f"p{i}", []))

        with Timer("pipeline_4_phases") as t:
            pipeline.run({})
        results["pipeline_4_phases_ms"] = t.elapsed * 1000

        bus = EventBus()
        counter = [0]
        bus.subscribe("*", lambda e: counter.__setitem__(0, counter[0] + 1))

        with Timer("eventbus_1000") as t:
            for i in range(1000):
                bus.publish(Event(event_type="test"))
        results["eventbus_1000_events_ms"] = t.elapsed * 1000

        for label, value in results.items():
            report_collector.add(
                TimingResult(
                    label=label,
                    elapsed=value / 1000 if "ms" in label else value,
                    iterations=1,
                    metadata={"value": value, "unit": "ms" if "ms" in label else "ratio"},
                )
            )

        report = report_collector.generate_report()

        assert "benchmark_results" in report
        assert "summary" in report
        assert report["summary"]["total_benchmarks"] > 0

        report_path = tmp_path / "performance_report.json"
        report_collector.save_report(report_path)
        assert report_path.exists()

        with open(report_path, encoding="utf-8") as f:
            loaded = json.load(f)
        assert len(loaded["benchmark_results"]) > 0

    def test_monitoring_report_integration(self, tmp_path):
        """验证 MonitoringReport 与性能测试的集成。"""
        report = MonitoringReport()

        report.metrics_collector.record("cache.hit_rate", 0.95, "ratio")
        report.metrics_collector.record("cache.speedup", 15.0, "x")
        report.metrics_collector.record("incremental.check_time", 0.05, "s")
        report.metrics_collector.record("hasher.small_file_time", 0.0005, "s")

        report.add_phase_result("cache_benchmark", True, {"duration": 1.5})
        report.add_phase_result("incremental_benchmark", True, {"duration": 0.8})
        report.add_phase_result("hasher_benchmark", True, {"duration": 0.3})

        report.finalize()

        generated = report.generate()

        assert generated["summary"]["phases_completed"] == 3
        assert generated["summary"]["phases_failed"] == 0
        assert generated["summary"]["total_errors"] == 0
        assert generated["duration_seconds"] >= 0

        report_path = tmp_path / "monitoring_report.json"
        report.save(report_path)
        assert report_path.exists()

    def test_report_contains_all_metrics(self, report_collector):
        """报告应包含所有基准测试指标。"""
        report_collector.add(
            TimingResult(label="cache_hit_rate", elapsed=0, iterations=1, metadata={"rate": 0.95})
        )
        report_collector.add(
            TimingResult(label="parallel_speedup", elapsed=2.5, iterations=10, metadata={"speedup": 3.2})
        )
        report_collector.add(
            TimingResult(label="memory_delta", elapsed=0, iterations=1, metadata={"objects": 500})
        )

        report = report_collector.generate_report()

        assert len(report["benchmark_results"]) == 3
        labels = {r["label"] for r in report["benchmark_results"]}
        assert "cache_hit_rate" in labels
        assert "parallel_speedup" in labels
        assert "memory_delta" in labels

    def test_report_json_serializable(self, tmp_path, report_collector):
        """报告应可序列化为 JSON。"""
        report_collector.add(
            TimingResult(label="test", elapsed=1.234, iterations=100, metadata={"key": "value"})
        )

        report_path = tmp_path / "serializable_report.json"
        report_collector.save_report(report_path)

        with open(report_path, encoding="utf-8") as f:
            data = json.load(f)

        assert data["benchmark_results"][0]["label"] == "test"
        assert abs(data["benchmark_results"][0]["elapsed_seconds"] - 1.234) < 0.001


class TestPerformanceThresholds:
    """性能阈值验证测试。"""

    def test_all_operations_within_thresholds(self, tmp_path, report_collector):
        """所有关键操作应在阈值内完成。"""
        thresholds = {
            "path_cache_rglob": 0.5,
            "incremental_check_100": 0.1,
            "hasher_small": 0.001,
            "pipeline_4_phases": 0.1,
            "eventbus_1000": 1.0,
            "metrics_1000": 0.5,
        }

        actuals = {}

        tree = tmp_path / "tree"
        tree.mkdir()
        for i in range(20):
            (tree / f"f_{i}.txt").write_text("x")
        cache = PathCache(tree)
        with Timer() as t:
            cache.rglob("*.txt")
        actuals["path_cache_rglob"] = t.elapsed

        files = []
        for i in range(100):
            f = tmp_path / f"inc_{i}.txt"
            f.write_text(f"c_{i}")
            files.append(f)
        tracker = IncrementalTracker(tmp_path)
        for f in files:
            tracker.is_changed(f)
        with Timer() as t:
            for f in files:
                tracker.is_changed(f)
        actuals["incremental_check_100"] = t.elapsed

        hasher = FastHasher()
        sf = tmp_path / "small.txt"
        sf.write_bytes(b"x" * 1024)
        with Timer() as t:
            hasher.hash_file(sf)
        actuals["hasher_small"] = t.elapsed

        pipeline = Pipeline()
        for i in range(4):
            pipeline.add_phase(RecordingPhase(f"p{i}", []))
        with Timer() as t:
            pipeline.run({})
        actuals["pipeline_4_phases"] = t.elapsed

        bus = EventBus()
        bus.subscribe("*", lambda e: None)
        with Timer() as t:
            for i in range(1000):
                bus.publish(Event(event_type="test"))
        actuals["eventbus_1000"] = t.elapsed

        collector = MetricsCollector()
        with Timer() as t:
            for i in range(1000):
                collector.record(f"m_{i}", float(i))
        actuals["metrics_1000"] = t.elapsed

        for key in thresholds:
            report_collector.add(
                TimingResult(
                    label=key,
                    elapsed=actuals.get(key, 0),
                    iterations=1,
                    metadata={
                        "threshold_ms": thresholds[key] * 1000,
                        "actual_ms": actuals.get(key, 0) * 1000,
                    },
                )
            )

        for key, threshold in thresholds.items():
            assert actuals[key] < threshold, (
                f"{key} 耗时 {actuals[key]*1000:.2f}ms 超过阈值 {threshold*1000:.2f}ms"
            )
