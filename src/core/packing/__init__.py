"""打包子系统模块。

从原 packer.py 拆分出的独立打包功能模块，包含 AVB、OTA、super 等子模块。
"""

from src.core.packing.avb import AVBManager
from src.core.packing.constants import (
    AOSP_AVB_PARTITIONS,
    AVB_DEFAULT_ALGORITHM,
    DEVICE_SIZE_MAP,
    SUPER_SIZE_DEFAULT,
)
from src.core.packing.ota import OTAPacker
from src.core.packing.repacker import Repacker
from src.core.packing.super import PartitionLayout, SuperImageBuilder

__all__ = [
    "AVB_DEFAULT_ALGORITHM",
    "AVBManager",
    "AOSP_AVB_PARTITIONS",
    "DEVICE_SIZE_MAP",
    "OTAPacker",
    "PartitionLayout",
    "Repacker",
    "SUPER_SIZE_DEFAULT",
    "SuperImageBuilder",
]
