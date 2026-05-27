"""性能缓存基础设施

提供缓存和增量构建支持：
- PathCache: 基于目录 mtime 的 rglob 结果缓存（支持多级：内存→磁盘）
- IncrementalTracker: 基于 mtime+size 的文件变更检测（支持持久化）
- FastHasher: 大文件分段哈希（头/中/尾各 10MB）
- BackgroundHasher: 后台线程池并发哈希预计算
- BuildPropCache: build.prop 路径和属性缓存
"""

from src.core.performance.build_prop_cache import BuildPropCache
from src.core.performance.cache import PathCache
from src.core.performance.hasher import BackgroundHasher, FastHasher
from src.core.performance.incremental import IncrementalTracker

__all__ = [
    "PathCache",
    "IncrementalTracker",
    "FastHasher",
    "BackgroundHasher",
    "BuildPropCache",
]
