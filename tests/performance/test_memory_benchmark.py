"""内存使用测试。

验证缓存系统的内存使用效率，确保缓存不会导致内存泄漏。
"""

import gc
import os
import sys
from pathlib import Path

import pytest

from src.core.monitoring import MetricsCollector
from src.core.performance import (
    BackgroundHasher,
    BuildPropCache,
    IncrementalTracker,
    PathCache,
)

from tests.performance.conftest import MemoryTracker, TimingResult


class TestPathCacheMemoryUsage:
    """PathCache 内存使用测试。"""

    def test_cache_memory_bounded(self, large_test_tree, memory_tracker, report_collector):
        """缓存的内存使用应在合理范围内。"""
        memory_tracker.snapshot("before_cache")

        cache = PathCache(large_test_tree)
        cache.rglob("*.txt")
        cache.rglob("dir_*")
        cache.rglob("*.log")

        memory_tracker.snapshot("after_cache")

        delta = memory_tracker.delta_objects("before_cache", "after_cache")
        report_collector.add(
            TimingResult(
                label="path_cache_memory_objects_delta",
                elapsed=0,
                iterations=1,
                metadata={"objects_delta": delta},
            )
        )

        assert delta < 10000

    def test_invalidate_releases_memory(self, large_test_tree, memory_tracker):
        """缓存失效应释放内存。"""
        cache = PathCache(large_test_tree)
        cache.rglob("*.txt")

        memory_tracker.snapshot("before_invalidate")
        cache.invalidate()
        gc.collect()
        memory_tracker.snapshot("after_invalidate")

        delta = memory_tracker.delta_objects("before_invalidate", "after_invalidate")
        assert delta <= 0 or abs(delta) < 100


class TestIncrementalTrackerMemoryUsage:
    """IncrementalTracker 内存使用测试。"""

    def test_tracker_memory_scales_linearly(self, tmp_path, memory_tracker, report_collector):
        """追踪器内存应随文件数量线性增长。"""
        memory_tracker.snapshot("baseline")

        files = []
        for i in range(500):
            f = tmp_path / f"file_{i}.txt"
            f.write_text(f"content_{i}")
            files.append(f)

        tracker = IncrementalTracker(tmp_path)
        for f in files:
            tracker.is_changed(f)

        memory_tracker.snapshot("after_500")

        more_files = []
        for i in range(500, 1000):
            f = tmp_path / f"file_{i}.txt"
            f.write_text(f"content_{i}")
            more_files.append(f)

        for f in more_files:
            tracker.is_changed(f)

        memory_tracker.snapshot("after_1000")

        delta_first_500 = memory_tracker.delta_objects("baseline", "after_500")
        delta_second_500 = memory_tracker.delta_objects("after_500", "after_1000")

        report_collector.add(
            TimingResult(
                label="tracker_memory_scaling",
                elapsed=0,
                iterations=1000,
                metadata={
                    "first_500_objects": delta_first_500,
                    "second_500_objects": delta_second_500,
                },
            )
        )

        if delta_first_500 > 0 and delta_second_500 > 0:
            ratio = delta_second_500 / delta_first_500
            assert 0.5 < ratio < 2.0, f"内存增长比率 {ratio:.2f} 偏离线性"

    def test_reset_releases_tracker_memory(self, tmp_path, memory_tracker):
        """重置追踪器应释放内存。"""
        files = []
        for i in range(200):
            f = tmp_path / f"file_{i}.txt"
            f.write_text(f"content_{i}")
            files.append(f)

        tracker = IncrementalTracker(tmp_path)
        for f in files:
            tracker.is_changed(f)

        memory_tracker.snapshot("before_reset")
        tracker.reset()
        gc.collect()
        memory_tracker.snapshot("after_reset")

        delta = memory_tracker.delta_objects("before_reset", "after_reset")
        assert delta <= 0 or abs(delta) < 500


class TestBuildPropCacheMemoryUsage:
    """BuildPropCache 内存使用测试。"""

    def test_cache_memory_efficient(self, build_prop_tree, memory_tracker, report_collector):
        """BuildPropCache 应高效使用内存。"""
        memory_tracker.snapshot("before")

        cache = BuildPropCache(build_prop_tree)
        cache.get_build_prop_paths()
        for _ in range(10):
            cache.get_prop_value("ro.product.model")
            cache.get_prop_value("ro.build.version.release")

        memory_tracker.snapshot("after")

        delta = memory_tracker.delta_objects("before", "after")
        report_collector.add(
            TimingResult(
                label="build_prop_cache_memory",
                elapsed=0,
                iterations=1,
                metadata={"objects_delta": delta},
            )
        )

        assert delta < 1000


class TestBackgroundHasherMemoryUsage:
    """BackgroundHasher 内存使用测试。"""

    def test_hasher_releases_resources_on_cancel(self, tmp_path, memory_tracker):
        """取消哈希任务应释放线程池资源。"""
        files = []
        for i in range(20):
            f = tmp_path / f"file_{i}.bin"
            f.write_bytes(b"\x00" * (1024 * 1024))
            files.append(f)

        memory_tracker.snapshot("before_hasher")

        bg_hasher = BackgroundHasher(max_workers=4)
        bg_hasher.submit_files(files)
        bg_hasher.wait_all()

        memory_tracker.snapshot("after_hash")

        bg_hasher.cancel_all()
        gc.collect()

        memory_tracker.snapshot("after_cancel")

        delta_during = memory_tracker.delta_objects("before_hasher", "after_hash")
        delta_after = memory_tracker.delta_objects("after_hash", "after_cancel")

        assert delta_after <= delta_during


class TestMetricsCollectorMemoryUsage:
    """MetricsCollector 内存使用测试。"""

    def test_collector_memory_after_many_records(self, memory_tracker, report_collector):
        """大量记录后内存使用应可控。"""
        memory_tracker.snapshot("before")

        collector = MetricsCollector()
        for i in range(5000):
            collector.record(f"metric_{i}", float(i), "units")

        memory_tracker.snapshot("after_5000")

        delta = memory_tracker.delta_objects("before", "after_5000")
        report_collector.add(
            TimingResult(
                label="metrics_5000_records_memory",
                elapsed=0,
                iterations=5000,
                metadata={"objects_delta": delta},
            )
        )

        assert delta < 20000

    def test_collector_clear_releases_memory(self, memory_tracker):
        """清空收集器应释放内存。"""
        collector = MetricsCollector()
        for i in range(2000):
            collector.record(f"metric_{i}", float(i), "units")

        memory_tracker.snapshot("before_clear")
        collector.clear()
        gc.collect()
        memory_tracker.snapshot("after_clear")

        delta = memory_tracker.delta_objects("before_clear", "after_clear")
        assert delta <= 0 or abs(delta) < 100


class TestMemoryLeakDetection:
    """内存泄漏检测测试。"""

    def test_repeated_cache_create_destroy_no_leak(self, large_test_tree, memory_tracker, report_collector):
        """反复创建和销毁缓存不应导致内存泄漏。"""
        gc.collect()
        memory_tracker.snapshot("initial")

        for _ in range(20):
            cache = PathCache(large_test_tree)
            cache.rglob("*.txt")
            cache.rglob("dir_*")
            del cache

        gc.collect()
        memory_tracker.snapshot("final")

        delta = memory_tracker.delta_objects("initial", "final")
        report_collector.add(
            TimingResult(
                label="cache_create_destroy_leak",
                elapsed=0,
                iterations=20,
                metadata={"objects_delta": delta},
            )
        )

        assert delta < 500, f"疑似内存泄漏：对象增加 {delta}"
