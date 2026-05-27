"""路径缓存模块

提供基于目录 mtime 的 rglob 结果缓存，支持多级缓存（内存→磁盘）。
"""

import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class PathCache:
    """多级路径缓存：内存 → 磁盘。

    缓存 rglob 搜索结果，当目录的 mtime 发生变化时自动清除缓存。
    支持磁盘持久化，跨实例复用缓存结果。
    """

    def __init__(self, root: Path, disk_cache_dir: Optional[Path] = None) -> None:
        self._root = Path(root).resolve()
        self._cache: Dict[str, List[Path]] = {}
        self._dir_mtimes: Dict[str, float] = {}
        self.cache_hits: int = 0
        self.cache_misses: int = 0

        self._disk_cache_dir: Optional[Path] = None
        self._disk_cache_file: Optional[Path] = None
        if disk_cache_dir is not None:
            self._disk_cache_dir = Path(disk_cache_dir).resolve()
            self._disk_cache_dir.mkdir(parents=True, exist_ok=True)
            self._disk_cache_file = self._disk_cache_dir / "path_cache.json"

    def rglob(self, pattern: str) -> List[Path]:
        """执行 rglob 搜索，优先命中内存缓存，其次磁盘缓存。"""
        if self._is_cache_valid(pattern):
            self.cache_hits += 1
            logger.debug(f"路径缓存命中: {pattern}")
            return self._cache[pattern]

        self.cache_misses += 1
        self._invalidate(pattern)
        self._record_dir_mtimes()

        results = list(self._root.rglob(pattern))
        self._cache[pattern] = results
        logger.debug(f"路径缓存填充: {pattern}, 找到 {len(results)} 个结果")
        return results

    def invalidate(self) -> None:
        self._cache.clear()
        self._dir_mtimes.clear()
        logger.debug("路径缓存已清空")

    def save_to_disk(self) -> None:
        """将内存缓存序列化到磁盘，供跨实例复用。"""
        if self._disk_cache_file is None:
            return

        serializable = {}
        for pattern, paths in self._cache.items():
            serializable[pattern] = {
                "paths": [str(p) for p in paths],
                "dir_mtimes": dict(self._dir_mtimes),
            }

        try:
            with open(self._disk_cache_file, "w", encoding="utf-8") as f:
                json.dump(serializable, f)
            logger.debug(f"路径缓存已保存到磁盘: {self._disk_cache_file}")
        except OSError as e:
            logger.warning(f"路径缓存保存失败: {e}")

    def load_from_disk(self) -> bool:
        """从磁盘加载缓存到内存，返回是否加载成功。"""
        if self._disk_cache_file is None or not self._disk_cache_file.exists():
            return False

        try:
            with open(self._disk_cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"路径缓存加载失败: {e}")
            return False

        for pattern, entry in data.items():
            paths = [Path(p) for p in entry.get("paths", [])]
            if not paths:
                continue

            dir_mtimes = entry.get("dir_mtimes", {})
            if self._is_disk_cache_valid(dir_mtimes):
                self._cache[pattern] = paths
                logger.debug(f"从磁盘加载路径缓存: {pattern}, {len(paths)} 个结果")
            else:
                logger.debug(f"磁盘缓存已过期，跳过: {pattern}")

        if self._cache:
            self._record_dir_mtimes()

        return bool(self._cache)

    def _is_cache_valid(self, pattern: str) -> bool:
        if pattern not in self._cache:
            return False

        for dir_path, cached_mtime in self._dir_mtimes.items():
            try:
                current_mtime = os.path.getmtime(dir_path)
                if current_mtime != cached_mtime:
                    logger.debug(f"目录 mtime 变化，缓存失效: {dir_path}")
                    return False
            except OSError:
                return False

        return True

    def _is_disk_cache_valid(self, dir_mtimes: Dict[str, float]) -> bool:
        """检查磁盘缓存中的目录 mtime 是否仍然有效。"""
        for dir_path, cached_mtime in dir_mtimes.items():
            try:
                current_mtime = os.path.getmtime(dir_path)
                if current_mtime != cached_mtime:
                    return False
            except OSError:
                return False
        return True

    def _invalidate(self, pattern: str) -> None:
        self._cache.pop(pattern, None)

    def _record_dir_mtimes(self) -> None:
        for dirpath, _, _ in os.walk(self._root):
            try:
                self._dir_mtimes[dirpath] = os.path.getmtime(dirpath)
            except OSError:
                continue
