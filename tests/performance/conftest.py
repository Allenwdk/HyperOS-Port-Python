"""性能测试共享夹具。

提供计时器、内存追踪器和测试数据生成器等基础设施。
"""

import gc
import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

from src.core.events.bus import EventBus
from src.core.events.events import Event
from src.core.monitoring import MetricsCollector
from src.core.performance import (
    BuildPropCache,
    FastHasher,
    IncrementalTracker,
    PathCache,
)


@dataclass
class TimingResult:
    """计时结果容器。"""
    label: str
    elapsed: float
    iterations: int = 1
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def per_iteration(self) -> float:
        return self.elapsed / self.iterations if self.iterations > 0 else 0.0


class Timer:
    """高精度计时器，支持多轮迭代取平均值。"""

    def __init__(self, label: str = ""):
        self._label = label
        self._start: float = 0.0
        self._elapsed: float = 0.0

    def __enter__(self):
        gc.collect()
        self._start = time.perf_counter()
        return self

    def __exit__(self, *args):
        self._elapsed = time.perf_counter() - self._start

    @property
    def elapsed(self) -> float:
        return self._elapsed

    def result(self, iterations: int = 1, **metadata) -> TimingResult:
        return TimingResult(
            label=self._label,
            elapsed=self._elapsed,
            iterations=iterations,
            metadata=metadata,
        )


def benchmark(func: Callable, iterations: int = 100, label: str = "") -> TimingResult:
    """执行函数多次并返回平均耗时。"""
    gc.collect()
    start = time.perf_counter()
    for _ in range(iterations):
        func()
    elapsed = time.perf_counter() - start
    return TimingResult(label=label or func.__name__, elapsed=elapsed, iterations=iterations)


@dataclass
class MemorySnapshot:
    """内存快照。"""
    label: str
    rss_bytes: int
    objects_count: int


class MemoryTracker:
    """内存使用追踪器。"""

    def __init__(self):
        self._snapshots: List[MemorySnapshot] = []

    def snapshot(self, label: str = "") -> MemorySnapshot:
        gc.collect()
        try:
            import psutil
            process = psutil.Process(os.getpid())
            rss = process.memory_info().rss
        except ImportError:
            rss = 0

        snap = MemorySnapshot(
            label=label,
            rss_bytes=rss,
            objects_count=len(gc.get_objects()),
        )
        self._snapshots.append(snap)
        return snap

    @property
    def snapshots(self) -> List[MemorySnapshot]:
        return list(self._snapshots)

    def delta_rss(self, label_from: str, label_to: str) -> int:
        from_snap = next((s for s in self._snapshots if s.label == label_from), None)
        to_snap = next((s for s in self._snapshots if s.label == label_to), None)
        if from_snap and to_snap:
            return to_snap.rss_bytes - from_snap.rss_bytes
        return 0

    def delta_objects(self, label_from: str, label_to: str) -> int:
        from_snap = next((s for s in self._snapshots if s.label == label_from), None)
        to_snap = next((s for s in self._snapshots if s.label == label_to), None)
        if from_snap and to_snap:
            return to_snap.objects_count - from_snap.objects_count
        return 0


def create_test_tree(root: Path, depth: int = 3, width: int = 5, file_size: int = 1024):
    """在指定目录下创建测试文件树。

    Args:
        root: 根目录
        depth: 目录深度
        width: 每层目录的子目录/文件数量
        file_size: 每个文件的大小（字节）
    """
    def _build(current: Path, remaining_depth: int):
        if remaining_depth <= 0:
            for i in range(width):
                f = current / f"file_{i}.txt"
                f.write_bytes(os.urandom(file_size))
            return

        for i in range(width):
            subdir = current / f"dir_{i}"
            subdir.mkdir(exist_ok=True)
            f = current / f"file_{i}.txt"
            f.write_bytes(os.urandom(file_size))
            _build(subdir, remaining_depth - 1)

    _build(root, depth)


def create_build_prop_files(root: Path, partitions: List[str]):
    """创建包含 build.prop 的分区目录结构。"""
    for partition in partitions:
        prop_dir = root / partition / "etc"
        prop_dir.mkdir(parents=True, exist_ok=True)
        content = "\n".join([
            f"ro.product.model=TestDevice_{partition}",
            f"ro.build.version.release=15",
            f"ro.product.device={partition}_device",
            f"ro.build.display.id=HyperOS 3.0.100.0_{partition}",
        ])
        (prop_dir / "build.prop").write_text(content)


@pytest.fixture
def timer():
    """提供 Timer 实例。"""
    return Timer


@pytest.fixture
def memory_tracker():
    """提供 MemoryTracker 实例。"""
    return MemoryTracker()


@pytest.fixture
def large_test_tree(tmp_path):
    """创建大型测试文件树（3 层深度，每层 5 个子项）。"""
    create_test_tree(tmp_path, depth=3, width=5, file_size=1024)
    return tmp_path


@pytest.fixture
def small_test_tree(tmp_path):
    """创建小型测试文件树（1 层深度，3 个子项）。"""
    create_test_tree(tmp_path, depth=1, width=3, file_size=512)
    return tmp_path


@pytest.fixture
def build_prop_tree(tmp_path):
    """创建包含 build.prop 的分区目录。"""
    partitions = ["system", "product", "vendor", "system_ext", "odm"]
    create_build_prop_files(tmp_path, partitions)
    return tmp_path


@pytest.fixture
def path_cache_large(large_test_tree):
    """提供基于大型测试树的 PathCache 实例。"""
    return PathCache(large_test_tree)


@pytest.fixture
def path_cache_small(small_test_tree):
    """提供基于小型测试树的 PathCache 实例。"""
    return PathCache(small_test_tree)


@pytest.fixture
def incremental_tracker(tmp_path):
    """提供 IncrementalTracker 实例。"""
    return IncrementalTracker(tmp_path)


@pytest.fixture
def fast_hasher():
    """提供 FastHasher 实例。"""
    return FastHasher()


@pytest.fixture
def metrics_collector():
    """提供 MetricsCollector 实例。"""
    return MetricsCollector()


@pytest.fixture
def event_bus():
    """提供 EventBus 实例。"""
    return EventBus()


@pytest.fixture
def performance_results():
    """收集性能测试结果的容器。"""
    results: List[TimingResult] = []
    return results


@pytest.fixture
def report_collector(performance_results):
    """提供性能报告收集器，测试结束后生成报告。"""
    class ReportCollector:
        def __init__(self):
            self.results = performance_results

        def add(self, result: TimingResult):
            self.results.append(result)

        def generate_report(self) -> Dict[str, Any]:
            return {
                "benchmark_results": [
                    {
                        "label": r.label,
                        "elapsed_seconds": r.elapsed,
                        "iterations": r.iterations,
                        "per_iteration_ms": r.per_iteration * 1000,
                        "metadata": r.metadata,
                    }
                    for r in self.results
                ],
                "summary": {
                    "total_benchmarks": len(self.results),
                    "total_time_seconds": sum(r.elapsed for r in self.results),
                },
            }

        def save_report(self, path: Path):
            path.parent.mkdir(parents=True, exist_ok=True)
            report = self.generate_report()
            with open(path, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2, ensure_ascii=False)

    return ReportCollector()
