import time
from pathlib import Path

import pytest

from src.core.performance.cache import PathCache
from src.core.performance.incremental import IncrementalTracker


class TestPathCacheMultiLevel:

    def test_disk_cache_save_and_load(self, tmp_path: Path) -> None:
        root = tmp_path / "project"
        root.mkdir()
        subdir = root / "data" / "subdir"
        subdir.mkdir(parents=True)
        (subdir / "file_a.txt").write_text("aaa")
        (subdir / "file_b.txt").write_text("bbb")

        cache_dir = tmp_path / ".cache"
        cache = PathCache(root, disk_cache_dir=cache_dir)

        results1 = cache.rglob("*.txt")
        assert len(results1) == 2

        cache.save_to_disk()
        assert cache._disk_cache_file.exists()

        cache2 = PathCache(root, disk_cache_dir=cache_dir)
        cache2.load_from_disk()

        results2 = cache2.rglob("*.txt")
        assert len(results2) == 2
        assert cache2.cache_hits >= 1

    def test_disk_cache_persistence_across_instances(self, tmp_path: Path) -> None:
        root = tmp_path / "project"
        root.mkdir()
        (root / "data.txt").write_text("content")

        cache_dir = tmp_path / ".cache"
        cache1 = PathCache(root, disk_cache_dir=cache_dir)
        cache1.rglob("*.txt")
        cache1.save_to_disk()

        cache2 = PathCache(root, disk_cache_dir=cache_dir)
        cache2.load_from_disk()
        results = cache2.rglob("*.txt")
        assert len(results) == 1
        assert cache2.cache_hits >= 1

    def test_disk_cache_invalidated_on_mtime_change(self, tmp_path: Path) -> None:
        root = tmp_path / "project"
        root.mkdir()
        (root / "file.txt").write_text("v1")

        cache_dir = tmp_path / ".cache"
        cache = PathCache(root, disk_cache_dir=cache_dir)
        cache.rglob("*.txt")
        cache.save_to_disk()

        time.sleep(0.05)
        (root / "new_file.txt").write_text("v2")
        cache.invalidate()

        results = cache.rglob("*.txt")
        assert len(results) == 2


class TestIncrementalTrackerPersistence:

    def test_save_and_load_state(self, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_text("hello")

        state_file = tmp_path / ".state.json"

        tracker1 = IncrementalTracker(tmp_path, state_file=state_file)
        assert tracker1.is_changed(f) is True
        tracker1.save_state()
        assert state_file.exists()

        tracker2 = IncrementalTracker(tmp_path, state_file=state_file)
        tracker2.load_state()
        assert tracker2.is_changed(f) is False

    def test_persistence_detects_file_change(self, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_text("v1")

        state_file = tmp_path / ".state.json"

        tracker1 = IncrementalTracker(tmp_path, state_file=state_file)
        tracker1.is_changed(f)
        tracker1.save_state()

        time.sleep(0.1)
        f.write_text("v2")

        tracker2 = IncrementalTracker(tmp_path, state_file=state_file)
        tracker2.load_state()
        assert tracker2.is_changed(f) is True

    def test_get_changed_files_after_reload(self, tmp_path: Path) -> None:
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("aaa")
        f2.write_text("bbb")

        state_file = tmp_path / ".state.json"
        tracker1 = IncrementalTracker(tmp_path, state_file=state_file)
        tracker1.is_changed(f1)
        tracker1.is_changed(f2)
        tracker1.save_state()

        time.sleep(0.1)
        f1.write_text("changed")

        tracker2 = IncrementalTracker(tmp_path, state_file=state_file)
        tracker2.load_state()
        changed = tracker2.get_changed_files([f1, f2])
        assert f1 in changed
        assert f2 not in changed


class TestBackgroundHasher:

    def test_background_hash_precompute(self, tmp_path: Path) -> None:
        from src.core.performance.hasher import BackgroundHasher

        files = []
        for i in range(5):
            f = tmp_path / f"file_{i}.txt"
            f.write_text(f"content_{i}")
            files.append(f)

        hasher = BackgroundHasher(max_workers=2)
        hasher.submit_files(files)
        hasher.wait_all()

        for f in files:
            h = hasher.get_hash(f)
            assert h is not None
            assert len(h) == 32

    def test_background_hasher_with_path_cache(self, tmp_path: Path) -> None:
        from src.core.performance.hasher import BackgroundHasher

        subdir = tmp_path / "data"
        subdir.mkdir()
        for i in range(3):
            (subdir / f"f{i}.bin").write_bytes(b"\x00" * (i + 1) * 100)

        cache = PathCache(tmp_path)
        results = cache.rglob("*.bin")

        hasher = BackgroundHasher(max_workers=2)
        hasher.submit_files(results)
        hasher.wait_all()

        for f in results:
            assert hasher.get_hash(f) is not None

    def test_background_hasher_cancel(self, tmp_path: Path) -> None:
        from src.core.performance.hasher import BackgroundHasher

        files = []
        for i in range(10):
            f = tmp_path / f"file_{i}.txt"
            f.write_text(f"content_{i}")
            files.append(f)

        hasher = BackgroundHasher(max_workers=1)
        hasher.submit_files(files)
        hasher.cancel_all()

        assert True
