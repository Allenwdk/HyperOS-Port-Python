"""build.prop 缓存模块

缓存 build.prop 文件路径和属性值，避免重复的 rglob 扫描。
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class BuildPropCache:
    """build.prop 路径和属性缓存。

    替代多处 rglob("build.prop") 调用，提供统一的缓存访问。
    """

    def __init__(self, root: Path) -> None:
        self._root = Path(root).resolve()
        self._prop_paths: Optional[List[Path]] = None
        self._prop_values: Dict[str, str] = {}
        self.cache_hits: int = 0
        self.cache_misses: int = 0

    def get_build_prop_paths(self) -> List[Path]:
        """获取所有 build.prop 路径，首次调用扫描，后续返回缓存。"""
        if self._prop_paths is not None:
            self.cache_hits += 1
            logger.debug(f"build.prop 路径缓存命中, {len(self._prop_paths)} 个文件")
            return self._prop_paths

        self.cache_misses += 1
        self._prop_paths = list(self._root.rglob("build.prop"))
        logger.debug(f"build.prop 路径扫描完成, 找到 {len(self._prop_paths)} 个文件")
        return self._prop_paths

    def get_prop_value(self, key: str) -> Optional[str]:
        """从缓存的 build.prop 中查找属性值，返回第一个匹配。"""
        if key in self._prop_values:
            self.cache_hits += 1
            return self._prop_values[key]

        for prop_path in self.get_build_prop_paths():
            try:
                content = prop_path.read_text(encoding="utf-8", errors="ignore")
                for line in content.splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    if k.strip() == key:
                        value = v.strip()
                        self._prop_values[key] = value
                        return value
            except OSError:
                continue

        self._prop_values[key] = ""
        return None

    def invalidate(self) -> None:
        self._prop_paths = None
        self._prop_values.clear()
        self.cache_hits = 0
        self.cache_misses = 0
        logger.debug("build.prop 缓存已清空")
