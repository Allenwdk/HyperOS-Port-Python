"""并行化效果测试。

验证 BackgroundHasher 的并发哈希计算和 EventBus 的并发事件发布
相比串行执行的性能提升。
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest

from src.core.events.bus import EventBus
from src.core.events.events import Event
from src.core.performance import BackgroundHasher, FastHasher

from tests.performance.conftest import Timer, TimingResult


class TestBackgroundHasherParallelism:
    """BackgroundHasher 并行哈希计算效果测试。"""

    def test_parallel_hash_faster_than_sequential(self, tmp_path, report_collector):
        """并行哈希应比串行哈希更快。"""
        files = []
        for i in range(20):
            f = tmp_path / f"file_{i}.bin"
            f.write_bytes(b"\x00" * (5 * 1024 * 1024))
            files.append(f)

        hasher = FastHasher()

        with Timer("sequential_hash") as t_seq:
            sequential_results = {}
            for f in files:
                sequential_results[str(f)] = hasher.hash_file(f)

        bg_hasher = BackgroundHasher(max_workers=4)
        with Timer("parallel_hash") as t_par:
            bg_hasher.submit_files(files)
            bg_hasher.wait_all()

        parallel_results = {}
        for f in files:
            h = bg_hasher.get_hash(f)
            if h:
                parallel_results[str(f)] = h

        speedup = t_seq.elapsed / t_par.elapsed if t_par.elapsed > 0 else float("inf")

        report_collector.add(
            TimingResult(
                label="parallel_hash_speedup",
                elapsed=t_par.elapsed,
                iterations=len(files),
                metadata={
                    "sequential_ms": t_seq.elapsed * 1000,
                    "parallel_ms": t_par.elapsed * 1000,
                    "speedup_factor": speedup,
                    "files_count": len(files),
                },
            )
        )

        bg_hasher.cancel_all()
        assert len(parallel_results) == len(files)
        assert speedup > 1.2, f"并行加速比 {speedup:.1f}x 低于 1.2x"

    def test_parallel_hash_correctness(self, tmp_path):
        """并行哈希结果应与串行一致。"""
        files = []
        for i in range(10):
            f = tmp_path / f"file_{i}.txt"
            f.write_text(f"content_{i}" * 1000)
            files.append(f)

        hasher = FastHasher()
        sequential = {str(f): hasher.hash_file(f) for f in files}

        bg_hasher = BackgroundHasher(max_workers=4)
        bg_hasher.submit_files(files)
        bg_hasher.wait_all()

        for f in files:
            parallel_hash = bg_hasher.get_hash(f)
            assert parallel_hash == sequential[str(f)], f"文件 {f.name} 的哈希不一致"

        bg_hasher.cancel_all()

    def test_background_hash_with_many_workers(self, tmp_path, report_collector):
        """多线程并行哈希的扩展性测试。"""
        files = []
        for i in range(30):
            f = tmp_path / f"file_{i}.bin"
            f.write_bytes(b"\x00" * (2 * 1024 * 1024))
            files.append(f)

        worker_counts = [1, 2, 4, 8]
        timings = {}

        for workers in worker_counts:
            bg_hasher = BackgroundHasher(max_workers=workers)
            with Timer(f"hash_{workers}_workers") as t:
                bg_hasher.submit_files(files)
                bg_hasher.wait_all()
            timings[workers] = t.elapsed
            bg_hasher.cancel_all()

        report_collector.add(
            TimingResult(
                label="hash_worker_scaling",
                elapsed=timings[4],
                iterations=len(files),
                metadata={f"workers_{w}_ms": timings[w] * 1000 for w in worker_counts},
            )
        )

        assert timings[4] < timings[1]


class TestEventBusConcurrency:
    """EventBus 并发发布性能测试。"""

    def test_concurrent_publish_correctness(self):
        """并发发布事件应保证计数正确。"""
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
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert count[0] == 400

    def test_concurrent_publish_performance(self, report_collector):
        """并发发布事件的性能测试。"""
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

        with Timer("concurrent_publish") as t:
            threads = [threading.Thread(target=publish_batch, args=(250,)) for _ in range(4)]
            for t_thread in threads:
                t_thread.start()
            for t_thread in threads:
                t_thread.join()

        result = t.result(iterations=1000)
        report_collector.add(result)

        assert count[0] == 1000
        assert result.elapsed < 2.0

    def test_concurrent_subscribe_unsubscribe_safety(self):
        """并发订阅/取消订阅不应导致崩溃。"""
        bus = EventBus()
        errors = []

        def subscriber(e):
            pass

        def subscribe_unsubscribe():
            try:
                for _ in range(100):
                    bus.subscribe("test", subscriber)
                    bus.unsubscribe("test", subscriber)
            except Exception as ex:
                errors.append(ex)

        def publish_events():
            for _ in range(100):
                bus.publish(Event(event_type="test"))

        threads = []
        for _ in range(3):
            threads.append(threading.Thread(target=subscribe_unsubscribe))
        threads.append(threading.Thread(target=publish_events))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0


class TestThreadPoolExecutorBaseline:
    """ThreadPoolExecutor 基线性能测试，用于对比并行化效果。"""

    def test_io_bound_parallel_speedup(self, tmp_path, report_collector):
        """IO 密集型任务的并行加速效果。"""
        files = []
        for i in range(50):
            f = tmp_path / f"file_{i}.bin"
            f.write_bytes(b"\x00" * (1024 * 1024))
            files.append(f)

        def hash_file(f):
            hasher = FastHasher()
            return hasher.hash_file(f)

        with Timer("serial_io") as t_serial:
            for f in files:
                hash_file(f)

        with Timer("parallel_io") as t_parallel:
            with ThreadPoolExecutor(max_workers=4) as executor:
                list(executor.map(hash_file, files))

        speedup = t_serial.elapsed / t_parallel.elapsed if t_parallel.elapsed > 0 else float("inf")

        report_collector.add(
            TimingResult(
                label="io_bound_parallel_speedup",
                elapsed=t_parallel.elapsed,
                iterations=len(files),
                metadata={
                    "serial_ms": t_serial.elapsed * 1000,
                    "parallel_ms": t_parallel.elapsed * 1000,
                    "speedup_factor": speedup,
                },
            )
        )

        assert speedup > 1.5, f"IO 并行加速比 {speedup:.1f}x 低于 1.5x"
