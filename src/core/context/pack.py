"""打包上下文数据类，从 PortingContext 提取打包相关属性并封装分区布局构建。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    pass

logger = logging.getLogger("PackContext")

STOCK_PARTITIONS = frozenset({"vendor", "odm", "vendor_dlkm", "odm_dlkm", "system_dlkm"})
PORT_PARTITIONS = frozenset({"system", "system_ext", "product", "mi_ext", "product_dlkm"})
ALL_PARTITIONS = STOCK_PARTITIONS | PORT_PARTITIONS


@dataclass
class PackContext:
    """打包相关配置的上下文数据类。

    封装打包类型、文件系统类型、目标目录路径、KSU/AVB 配置、
    ROM 元信息以及分区布局构建逻辑。
    """

    pack_type: str = "payload"
    fs_type: str = "erofs"
    target_dir: Path = field(default_factory=lambda: Path("/tmp/target"))
    target_config_dir: Path = field(init=False)
    repack_images_dir: Path = field(init=False)
    enable_ksu: bool = False
    enable_custom_avb_chain: bool = False
    avb_key_path: Optional[Path] = None
    stock_rom_code: str = "unknown"
    port_rom_code: str = "unknown"
    target_rom_version: str = ""
    security_patch: str = "Unknown"
    base_android_version: str = "0"
    port_android_version: str = "0"
    is_ab_device: bool = False
    is_port_eu_rom: bool = False
    is_port_global_rom: bool = False
    port_global_region: str = ""
    stock_region: str = ""

    def __post_init__(self) -> None:
        self.target_dir = Path(self.target_dir)
        self.target_config_dir = self.target_dir / "config"
        self.repack_images_dir = self.target_dir / "repack_images"

    def build_partition_layout(self, *, stock: Any, port: Any) -> Dict[str, Any]:
        """构建分区到源 ROM 的映射关系。

        底层分区（vendor/odm 等）来自 stock ROM，
        上层分区（system/product 等）来自 port ROM。

        Args:
            stock: 底包 RomPackage 实例
            port: 移植包 RomPackage 实例

        Returns:
            分区名到 RomPackage 的映射字典
        """
        layout: Dict[str, Any] = {}
        for name in STOCK_PARTITIONS:
            layout[name] = stock
        for name in PORT_PARTITIONS:
            layout[name] = port
        return layout

    @classmethod
    def from_porting_context(
        cls,
        ctx: Any,
        *,
        pack_type: str = "payload",
        fs_type: str = "erofs",
    ) -> PackContext:
        """从 PortingContext 实例提取打包相关属性，构造 PackContext。

        Args:
            ctx: PortingContext 实例
            pack_type: 打包类型（payload 或 super）
            fs_type: 文件系统类型（erofs 或 ext4）

        Returns:
            PackContext 实例
        """
        return cls(
            pack_type=pack_type,
            fs_type=fs_type,
            target_dir=getattr(ctx, "target_dir", Path("/tmp/target")),
            enable_ksu=getattr(ctx, "enable_ksu", False),
            enable_custom_avb_chain=getattr(ctx, "enable_custom_avb_chain", False),
            avb_key_path=getattr(ctx, "avb_key_path", None),
            stock_rom_code=getattr(ctx, "stock_rom_code", "unknown"),
            port_rom_code=getattr(ctx, "port_rom_code", "unknown"),
            target_rom_version=getattr(ctx, "target_rom_version", ""),
            security_patch=getattr(ctx, "security_patch", "Unknown"),
            base_android_version=getattr(ctx, "base_android_version", "0"),
            port_android_version=getattr(ctx, "port_android_version", "0"),
            is_ab_device=getattr(ctx, "is_ab_device", False),
            is_port_eu_rom=getattr(ctx, "is_port_eu_rom", False),
            is_port_global_rom=getattr(ctx, "is_port_global_rom", False),
            port_global_region=getattr(ctx, "port_global_region", ""),
            stock_region=getattr(ctx, "stock_region", ""),
        )
