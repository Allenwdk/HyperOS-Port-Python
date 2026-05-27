from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

_VALID_PACK_TYPES = frozenset({"payload", "super"})
_VALID_FS_TYPES = frozenset({"erofs", "ext4"})

logger = logging.getLogger("DeviceContext")


@dataclass
class DeviceContext:
    """设备相关配置的上下文数据类。

    从 PortingContext 中提取的设备属性集合，负责设备配置的加载、
    验证和便捷属性访问。
    """

    device_config: dict[str, Any] = field(default_factory=dict)
    enable_ksu: bool = False
    enable_custom_avb_chain: bool = False
    avb_key_path: Optional[Path] = None
    is_ab_device: bool = False
    security_patch: str = "Unknown"
    stock_rom_code: str = "unknown"
    port_rom_code: str = "unknown"
    is_port_eu_rom: bool = False
    is_port_global_rom: bool = False
    port_global_region: str = ""
    stock_region: str = ""

    @property
    def pack_type(self) -> str:
        return self.device_config.get("pack", {}).get("type", "payload")

    @property
    def fs_type(self) -> str:
        return self.device_config.get("pack", {}).get("fs_type", "erofs")

    def validate(self) -> bool:
        pack_section = self.device_config.get("pack", {})

        pack_type = pack_section.get("type")
        if pack_type is not None and pack_type not in _VALID_PACK_TYPES:
            logger.error(f"无效的打包类型: {pack_type}，有效值: {_VALID_PACK_TYPES}")
            return False

        fs_type = pack_section.get("fs_type")
        if fs_type is not None and fs_type not in _VALID_FS_TYPES:
            logger.error(f"无效的文件系统类型: {fs_type}，有效值: {_VALID_FS_TYPES}")
            return False

        return True

    @classmethod
    def load_from_config(
        cls,
        config: dict[str, Any],
        *,
        is_ab_device: bool = False,
        security_patch: str = "Unknown",
        stock_rom_code: str = "unknown",
        port_rom_code: str = "unknown",
        is_port_eu_rom: bool = False,
        is_port_global_rom: bool = False,
        port_global_region: str = "",
        stock_region: str = "",
        enable_custom_avb_chain: bool = False,
        avb_key_path: Optional[Path] = None,
    ) -> DeviceContext:
        ksu_config = config.get("ksu", {})
        enable_ksu = ksu_config.get("enable", False)

        return cls(
            device_config=config,
            enable_ksu=enable_ksu,
            enable_custom_avb_chain=enable_custom_avb_chain,
            avb_key_path=avb_key_path,
            is_ab_device=is_ab_device,
            security_patch=security_patch,
            stock_rom_code=stock_rom_code,
            port_rom_code=port_rom_code,
            is_port_eu_rom=is_port_eu_rom,
            is_port_global_rom=is_port_global_rom,
            port_global_region=port_global_region,
            stock_region=stock_region,
        )

    def summary(self) -> str:
        parts = [
            f"打包类型: {self.pack_type}",
            f"文件系统: {self.fs_type}",
            f"KSU: {'启用' if self.enable_ksu else '禁用'}",
            f"AB设备: {'是' if self.is_ab_device else '否'}",
            f"安全补丁: {self.security_patch}",
            f"底包设备码: {self.stock_rom_code}",
            f"移植包设备码: {self.port_rom_code}",
        ]
        if self.is_port_eu_rom:
            parts.append("移植包区域: EU")
        elif self.is_port_global_rom:
            parts.append(f"移植包区域: Global({self.port_global_region})")
        return " | ".join(parts)

    def __repr__(self) -> str:
        return (
            f"DeviceContext("
            f"pack_type={self.pack_type!r}, "
            f"fs_type={self.fs_type!r}, "
            f"enable_ksu={self.enable_ksu!r}, "
            f"is_ab_device={self.is_ab_device!r}, "
            f"stock_rom_code={self.stock_rom_code!r}, "
            f"port_rom_code={self.port_rom_code!r})"
        )
