"""AVB 配置数据类，从 PortingContext 的 AVB 相关属性中提取。"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("AVBContext")


@dataclass
class AVBContext:
    """AVB（Android Verified Boot）配置上下文。

    封装自定义 AVB 验证链所需的全部配置项，支持从 partition_info.json 加载。
    """

    custom_avb_chain: bool = False
    avb_key_path: Optional[Path] = None
    avb_hash_partitions: List[str] = field(default_factory=list)
    avb_hashtree_partitions: List[str] = field(default_factory=list)
    avb_chain_partitions: List[Dict[str, Any]] = field(default_factory=list)
    avb_strict_partitions: List[str] = field(default_factory=list)
    physical_partition_sizes: Dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_partition_info(cls, path: Path) -> AVBContext:
        """从 partition_info.json 加载 AVB 配置。

        Args:
            path: partition_info.json 文件路径

        Returns:
            AVBContext 实例；文件不存在或解析失败时返回默认空配置
        """
        if not path.exists():
            logger.info("分区信息文件不存在，使用默认 AVB 配置: %s", path)
            return cls()

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("解析分区信息文件失败，使用默认 AVB 配置: %s", exc)
            return cls()

        return cls(
            avb_hash_partitions=data.get("avb_hash_partitions", []),
            avb_hashtree_partitions=data.get("avb_hashtree_partitions", []),
            avb_chain_partitions=data.get("avb_chain_partitions", []),
            avb_strict_partitions=data.get("avb_strict_partitions", []),
            physical_partition_sizes=data.get("physical_partition_sizes", {}),
        )
