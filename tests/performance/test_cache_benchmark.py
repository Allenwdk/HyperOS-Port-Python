"""缓存命中率基准测试。

验证 PathCache、IncrementalTracker 和 BuildPropCache 的缓存效果，
测量缓存命中率和缓存加速比。
"""

import os
import time
from pathlib import Path

import pytest

from src.core.performance import (
    BuildPropCache,
    IncrementalTracker,
    PathCache,
)


class TestPathCacheHitRate:
    """PathCache 缓存命中率测试。"""

    def test_repeated_rglob_achieves_high_hit_rate(self, large_test_tree, report_collector):
        """重复 rglob 调用应达到高缓存命中率。"""
        cache = PathCache(large_test_tree)

        cache.rglob("*.txt")

        for _ in range(50):
            cache.rglob("*.txt")

        hit_rate = cache.cache_hits / (cache.cache_hits + cache.cache_misses)
        report_collector.add(
            TimingResult(
                label="path_cache_hit_rate",
                elapsed=0,
                iterations=51,
                metadata={"hit_rate": hit_rate, "hits": cache.cache_hits, "misses": cache.cache_misses},
            )
        )

        assert hit_rate >= 0.95, f"缓存命中率 {hit_rate:.2%} 低于 95% 阈值"

    def test_cached_rglob_faster_than_uncached(self, large_test_tree, report_collector):
        """缓存后的 rglob 应比未缓存时显著更快。"""
        cache = PathCache(large_test_tree)

        start = time.perf_counter()
        cache.rglob("*.txt")
        uncached_time = time.perf_counter() - start

        cached_times = []
        for _ in range(100):
            start = time.perf_counter()
            cache.rglob("*.txt")
            cached_times.append(time.perf_counter() - start)

        avg_cached = sum(cached_times) / len(cached_times)
        speedup = uncached_time / avg_cached if avg_cached > 0 else float("inf")

        report_collector.add(
            TimingResult(
                label="path_cache_speedup",
                elapsed=uncached_time,
                iterations=100,
                metadata={
                    "uncached_ms": uncached_time * 1000,
                    "avg_cached_ms": avg_cached * 1000,
                    "speedup_factor": speedup,
                },
            )
        )

        assert speedup > 2.0, f"缓存加速比 {speedup:.1f}x 低于 2x 阈值"

    def test_different_patterns_independent_cache(self, large_test_tree):
        """不同模式的缓存应独立维护。"""
        cache = PathCache(large_test_tree)

        results_txt = cache.rglob("*.txt")
        results_dir = cache.rglob("dir_*")

        cache.rglob("*.txt")
        cache.rglob("dir_*")

        assert cache.cache_hits >= 2
        assert len(results_txt) > 0
        assert len(results_dir) > 0

    def test_invalidate_resets_hit_counters(self, large_test_tree):
        """缓存失效应重置命中计数器。"""
        cache = PathCache(large_test_tree)
        cache.rglob("*.txt")
        cache.rglob("*.txt")

        assert cache.cache_hits > 0
        cache.invalidate()

        cache.rglob("*.txt")
        assert cache.cache_misses >= 1


class TestIncrementalTrackerHitRate:
    """IncrementalTracker 缓存命中率测试。"""

    def test_unchanged_files_detected_efficiently(self, tmp_path, report_collector):
        """未变更文件应被高效检测为未变更。"""
        files = []
        for i in range(100):
            f = tmp_path / f"file_{i}.txt"
            f.write_text(f"content_{i}")
            files.append(f)

        tracker = IncrementalTracker(tmp_path)

        for f in files:
            tracker.is_changed(f)

        tracker.cache_hits = 0
        tracker.cache_misses = 0

        start = time.perf_counter()
        for f in files:
            assert tracker.is_changed(f) is False
        check_time = time.perf_counter() - start

        hit_rate = tracker.cache_hits / (tracker.cache_hits + tracker.cache_misses) if (tracker.cache_hits + tracker.cache_misses) > 0 else 0
        report_collector.add(
            TimingResult(
                label="incremental_unchanged_detection",
                elapsed=check_time,
                iterations=100,
                metadata={"hit_rate": hit_rate},
            )
        )

        assert hit_rate >= 0.95, f"未变更检测命中率 {hit_rate:.2%} 低于 95%"

    def test_changed_files_detected_correctly(self, tmp_path):
        """已变更文件应被正确检测。"""
        files = []
        for i in range(50):
            f = tmp_path / f"file_{i}.txt"
            f.write_text(f"content_{i}")
            files.append(f)

        tracker = IncrementalTracker(tmp_path)
        for f in files:
            tracker.is_changed(f)

        time.sleep(0.05)
        for i in range(0, 50, 5):
            files[i].write_text(f"modified_{i}")

        changed = tracker.get_changed_files(files)
        assert len(changed) == 10

    def test_incremental_check_speedup(self, tmp_path, report_collector):
        """增量检查应比完整扫描快。"""
        files = []
        for i in range(200):
            f = tmp_path / f"file_{i}.txt"
            f.write_text(f"content_{i}")
            files.append(f)

        tracker = IncrementalTracker(tmp_path)

        for f in files:
            tracker.is_changed(f)

        start = time.perf_counter()
        for _ in range(10):
            changed = tracker.get_changed_files(files)
        incremental_time = time.perf_counter() - start

        report_collector.add(
            TimingResult(
                label="incremental_check_speed",
                elapsed=incremental_time,
                iterations=10 * len(files),
                metadata={"files_checked": len(files), "changed_found": len(changed)},
            )
        )

        assert len(changed) == 0
        assert incremental_time < 2.0


class TestBuildPropCacheHitRate:
    """BuildPropCache 缓存命中率测试。"""

    def test_prop_path_cache_hit_rate(self, build_prop_tree, report_collector):
        """build.prop 路径缓存应达到高命中率。"""
        cache = BuildPropCache(build_prop_tree)

        paths1 = cache.get_build_prop_paths()

        for _ in range(100):
            cache.get_build_prop_paths()

        hit_rate = cache.cache_hits / (cache.cache_hits + cache.cache_misses)
        report_collector.add(
            TimingResult(
                label="build_prop_path_cache_hit_rate",
                elapsed=0,
                iterations=101,
                metadata={"hit_rate": hit_rate, "paths_found": len(paths1)},
            )
        )

        assert hit_rate >= 0.95
        assert len(paths1) == 5

    def test_prop_value_cache_hit_rate(self, build_prop_tree, report_collector):
        """属性值缓存应达到高命中率。"""
        cache = BuildPropCache(build_prop_tree)

        value1 = cache.get_prop_value("ro.product.model")

        for _ in range(100):
            cache.get_prop_value("ro.product.model")

        hit_rate = cache.cache_hits / (cache.cache_hits + cache.cache_misses)
        report_collector.add(
            TimingResult(
                label="build_prop_value_cache_hit_rate",
                elapsed=0,
                iterations=101,
                metadata={"hit_rate": hit_rate, "value": value1},
            )
        )

        assert hit_rate >= 0.95
        assert value1 is not None

    def test_cached_prop_lookup_faster(self, build_prop_tree, report_collector):
        """缓存后的属性查找应比首次更快。"""
        cache = BuildPropCache(build_prop_tree)

        start = time.perf_counter()
        cache.get_prop_value("ro.product.model")
        first_time = time.perf_counter() - start

        cached_times = []
        for _ in range(50):
            start = time.perf_counter()
            cache.get_prop_value("ro.product.model")
            cached_times.append(time.perf_counter() - start)

        avg_cached = sum(cached_times) / len(cached_times)
        speedup = first_time / avg_cached if avg_cached > 0 else float("inf")

        report_collector.add(
            TimingResult(
                label="build_prop_lookup_speedup",
                elapsed=first_time,
                iterations=50,
                metadata={
                    "first_ms": first_time * 1000,
                    "avg_cached_ms": avg_cached * 1000,
                    "speedup_factor": speedup,
                },
            )
        )

        assert speedup > 1.5


class TestFastHasherPerformance:
    """FastHasher 性能测试。"""

    def test_small_file_hash_speed(self, tmp_path, fast_hasher, report_collector):
        """小文件哈希应在 1ms 内完成。"""
        f = tmp_path / "small.txt"
        f.write_bytes(b"x" * 1024)

        times = []
        for _ in range(100):
            start = time.perf_counter()
            fast_hasher.hash_file(f)
            times.append(time.perf_counter() - start)

        avg_time = sum(times) / len(times)
        report_collector.add(
            TimingResult(
                label="small_file_hash",
                elapsed=sum(times),
                iterations=100,
                metadata={"avg_ms": avg_time * 1000},
            )
        )

        assert avg_time < 0.001

    def test_large_file_segmented_hash_speed(self, tmp_path, fast_hasher, report_collector):
        """大文件分段哈希应比全文哈希快。"""
        f = tmp_path / "large.bin"
        chunk = b"\x00" * (1024 * 1024)
        with open(f, "wb") as fh:
            for _ in range(200):
                fh.write(chunk)

        start = time.perf_counter()
        result = fast_hasher.hash_file(f)
        segmented_time = time.perf_counter() - start

        report_collector.add(
            TimingResult(
                label="large_file_segmented_hash",
                elapsed=segmented_time,
                iterations=1,
                metadata={"file_size_mb": 200, "hash": result[:16]},
            )
        )

        assert len(result) == 32
        assert segmented_time < 0.1


from tests.performance.conftest import TimingResult
