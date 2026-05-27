"""性能缓存基础设施测试"""

import hashlib
import time
from pathlib import Path

import pytest

from src.core.performance import (
    BuildPropCache,
    FastHasher,
    IncrementalTracker,
    PathCache,
)


class TestPathCache:
    def test_rglob_returns_results(self, tmp_path: Path) -> None:
        subdir = tmp_path / "a" / "b"
        subdir.mkdir(parents=True)
        (subdir / "test.txt").write_text("hello")

        cache = PathCache(tmp_path)
        results = cache.rglob("test.txt")
        assert len(results) == 1
        assert results[0].name == "test.txt"

    def test_rglob_cache_hit(self, tmp_path: Path) -> None:
        subdir = tmp_path / "a" / "b"
        subdir.mkdir(parents=True)
        (subdir / "test.txt").write_text("hello")

        cache = PathCache(tmp_path)
        result1 = cache.rglob("test.txt")
        result2 = cache.rglob("test.txt")
        assert result1 == result2
        assert cache.cache_hits >= 1

    def test_rglob_invalidated_on_directory_mtime_change(self, tmp_path: Path) -> None:
        subdir = tmp_path / "a"
        subdir.mkdir()
        (subdir / "test.txt").write_text("hello")

        cache = PathCache(tmp_path)
        result1 = cache.rglob("test.txt")
        assert len(result1) == 1

        new_subdir = tmp_path / "a" / "newdir"
        new_subdir.mkdir()
        time.sleep(0.05)
        (new_subdir / "test.txt").write_text("world")

        cache.invalidate()
        result2 = cache.rglob("test.txt")
        assert len(result2) == 2

    def test_invalidate_clears_cache(self, tmp_path: Path) -> None:
        (tmp_path / "file.txt").write_text("content")
        cache = PathCache(tmp_path)
        cache.rglob("file.txt")
        cache.invalidate()
        results = cache.rglob("file.txt")
        assert len(results) == 1


class TestIncrementalTracker:
    def test_first_check_is_changed(self, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_text("hello")
        tracker = IncrementalTracker(tmp_path)
        assert tracker.is_changed(f) is True

    def test_second_check_is_not_changed(self, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_text("hello")
        tracker = IncrementalTracker(tmp_path)
        assert tracker.is_changed(f) is True
        assert tracker.is_changed(f) is False

    def test_modified_file_is_changed(self, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_text("hello")
        tracker = IncrementalTracker(tmp_path)
        assert tracker.is_changed(f) is True
        assert tracker.is_changed(f) is False

        time.sleep(0.1)
        f.write_text("world")
        assert tracker.is_changed(f) is True

    def test_get_changed_files(self, tmp_path: Path) -> None:
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("aaa")
        f2.write_text("bbb")

        tracker = IncrementalTracker(tmp_path)
        tracker.is_changed(f1)
        tracker.is_changed(f2)

        time.sleep(0.1)
        f1.write_text("changed")

        changed = tracker.get_changed_files([f1, f2])
        assert f1 in changed
        assert f2 not in changed


class TestFastHasher:
    def test_hash_small_file(self, tmp_path: Path) -> None:
        f = tmp_path / "small.txt"
        content = b"hello world"
        f.write_bytes(content)

        hasher = FastHasher()
        result = hasher.hash_file(f)
        expected = hashlib.md5(content).hexdigest()
        assert result == expected

    def test_hash_large_file_segments(self, tmp_path: Path) -> None:
        f = tmp_path / "large.bin"
        chunk_size = 10 * 1024 * 1024
        total_size = chunk_size * 10 + 1024

        with open(f, "wb") as fh:
            fh.write(b"\x00" * chunk_size)
            fh.write(b"\x01" * (total_size - 2 * chunk_size))
            fh.write(b"\x02" * chunk_size)

        hasher = FastHasher()
        result = hasher.hash_file(f)

        assert len(result) == 32
        assert all(c in "0123456789abcdef" for c in result)

    def test_hash_returns_consistent_results(self, tmp_path: Path) -> None:
        f = tmp_path / "consistent.txt"
        f.write_bytes(b"consistent content for hashing")

        hasher = FastHasher()
        h1 = hasher.hash_file(f)
        h2 = hasher.hash_file(f)
        assert h1 == h2

    def test_hash_file_not_found(self, tmp_path: Path) -> None:
        f = tmp_path / "nonexistent.txt"
        hasher = FastHasher()
        with pytest.raises(FileNotFoundError):
            hasher.hash_file(f)

    def test_hash_string(self) -> None:
        hasher = FastHasher()
        result = hasher.hash_string("test content")
        assert len(result) == 32
        expected = hashlib.md5(b"test content").hexdigest()
        assert result == expected


class TestBuildPropCache:
    def test_cache_build_prop_paths(self, tmp_path: Path) -> None:
        for partition in ["system", "product", "vendor"]:
            prop_dir = tmp_path / partition / "etc"
            prop_dir.mkdir(parents=True)
            (prop_dir / "build.prop").write_text(f"ro.product.model=test_{partition}\n")

        cache = BuildPropCache(tmp_path)
        paths = cache.get_build_prop_paths()
        assert len(paths) == 3

    def test_cache_avoids_rglob(self, tmp_path: Path) -> None:
        (tmp_path / "system" / "etc").mkdir(parents=True)
        (tmp_path / "system" / "etc" / "build.prop").write_text("ro.test=1\n")

        cache = BuildPropCache(tmp_path)
        paths1 = cache.get_build_prop_paths()
        paths2 = cache.get_build_prop_paths()
        assert paths1 == paths2
        assert cache.cache_hits >= 1

    def test_get_prop_value(self, tmp_path: Path) -> None:
        (tmp_path / "system" / "etc").mkdir(parents=True)
        (tmp_path / "system" / "etc" / "build.prop").write_text(
            "ro.product.model=TestDevice\nro.build.version.release=15\n"
        )

        cache = BuildPropCache(tmp_path)
        assert cache.get_prop_value("ro.product.model") == "TestDevice"
        assert cache.get_prop_value("ro.build.version.release") == "15"

    def test_invalidate_resets_cache(self, tmp_path: Path) -> None:
        (tmp_path / "system" / "etc").mkdir(parents=True)
        (tmp_path / "system" / "etc" / "build.prop").write_text("ro.test=1\n")

        cache = BuildPropCache(tmp_path)
        cache.get_build_prop_paths()
        cache.invalidate()
        paths = cache.get_build_prop_paths()
        assert len(paths) == 1


class TestModuleImports:
    def test_import_from_performance(self) -> None:
        from src.core.performance import (
            BuildPropCache,
            FastHasher,
            IncrementalTracker,
            PathCache,
        )

        assert PathCache is not None
        assert IncrementalTracker is not None
        assert FastHasher is not None
        assert BuildPropCache is not None
