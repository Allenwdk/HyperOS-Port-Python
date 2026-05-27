"""工作流阶段抽象基类和具体移植流程 Phase 类。

定义 Phase 标准接口及四个具体的移植流程阶段：
- ExtractionPhase: ROM 解包
- InitializationPhase: 上下文初始化、分区安装
- ModificationPhase: 系统/框架/固件修改
- PackingPhase: 镜像重打包
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class Phase(ABC):
    """工作流阶段抽象基类。

    所有移植流程阶段必须继承此类，实现 execute() 和 rollback() 方法。
    Phase 是 Pipeline 的基本执行单元。

    Attributes:
        name: 阶段唯一标识名
        description: 阶段描述信息
    """

    name: str = field(metadata={"description": "阶段唯一标识名"})
    description: str = field(default="", metadata={"description": "阶段描述信息"})

    @abstractmethod
    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        """执行阶段逻辑。

        Args:
            context: 执行上下文字典，包含阶段间共享数据

        Returns:
            更新后的上下文字典

        Raises:
            Exception: 阶段执行失败时抛出异常
        """

    def rollback(self, context: dict[str, Any]) -> dict[str, Any]:
        """回滚阶段操作。

        当 Pipeline 中后续阶段失败时，此方法被调用以撤销本阶段的修改。
        默认实现直接返回上下文，子类可覆盖以提供实际回滚逻辑。

        Args:
            context: 当前上下文字典

        Returns:
            回滚后的上下文字典
        """
        return context


@dataclass
class ExtractionPhase(Phase):
    """ROM 解压阶段。

    负责解压 Stock ROM 和 Port ROM 的镜像文件。
    对应现有流程中的 Phase 1: Extraction。

    上下文输入:
        stock_rom_path: Stock ROM ZIP 路径
        port_rom_path: Port ROM ZIP 路径
        stock_work_dir: Stock 解压目录
        port_work_dir: Port 解压目录
        is_official_modify: 是否为官改模式
        cache_manager: 缓存管理器（可选）

    上下文输出:
        stock_rom: Stock RomPackage 实例
        port_rom: Port RomPackage 实例
        extraction_done: 解压完成标志
    """

    name: str = "extraction"
    description: str = "解压 ROM 镜像文件"

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        """执行 ROM 解压逻辑。

        创建 Stock 和 Port 的 RomPackage 并解压镜像文件。
        官改模式下 Port ROM 复用 Stock ROM。
        """
        from src.core.rom import RomPackage

        logger.info("开始执行 ROM 解压阶段")

        stock_rom_path = context["stock_rom_path"]
        stock_work_dir = context["stock_work_dir"]
        port_rom_path = context["port_rom_path"]
        port_work_dir = context["port_work_dir"]
        is_official_modify = context.get("is_official_modify", False)
        cache_manager = context.get("cache_manager")

        stock = RomPackage(stock_rom_path, stock_work_dir, label="Stock")
        stock.extract_images()

        if is_official_modify:
            port = stock
        else:
            port = RomPackage(
                port_rom_path, port_work_dir, label="Port", cache_manager=cache_manager
            )
            port.extract_images(["system", "product", "system_ext", "mi_ext"])

        context["stock_rom"] = stock
        context["port_rom"] = port
        context["extraction_done"] = True

        logger.info("ROM 解压阶段完成")
        return context

    def rollback(self, context: dict[str, Any]) -> dict[str, Any]:
        """回滚解压操作（清理已解压文件）。"""
        logger.info("回滚 ROM 解压阶段")
        context.pop("extraction_done", None)
        context.pop("stock_rom", None)
        context.pop("port_rom", None)
        return context


@dataclass
class InitializationPhase(Phase):
    """目标工作区初始化阶段。

    负责创建 PortingContext、初始化目标目录、加载设备配置。
    对应现有流程中的 Phase 2: Initialization。

    上下文输入:
        stock_rom: Stock RomPackage 实例
        port_rom: Port RomPackage 实例
        target_work_dir: 目标工作目录
        is_official_modify: 是否为官改模式
        cache_manager: 缓存管理器（可选）
        eu_bundle: EU 本地化资源路径（可选）

    上下文输出:
        porting_context: PortingContext 实例
        initialized: 初始化完成标志
    """

    name: str = "initialization"
    description: str = "初始化目标工作区和移植上下文"

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        """执行工作区初始化逻辑。

        创建 PortingContext，初始化目标目录，加载设备配置，
        确定打包设置。
        """
        from src.core.config_loader import load_device_config
        from src.core.context import PortingContext
        from src.core.device_auto_config import get_or_create_device_config

        logger.info("开始执行目标工作区初始化阶段")

        stock = context["stock_rom"]
        port = context["port_rom"]
        target_work_dir = context["target_work_dir"]
        is_official_modify = context.get("is_official_modify", False)
        cache_manager = context.get("cache_manager")
        eu_bundle = context.get("eu_bundle")

        ctx = PortingContext(stock, port, target_work_dir, is_official_modify=is_official_modify)
        ctx.cache_manager = cache_manager
        ctx.eu_bundle = eu_bundle
        ctx.initialize_target(clean_existing=True)

        stock_device_code = (
            stock.get_prop("ro.product.name_for_attestation")
            or stock.get_prop("ro.product.vendor.device")
            or "unknown"
        )

        device_config_dir = Path("devices") / stock_device_code
        if not device_config_dir.exists():
            logger.info(
                "未找到设备配置 %s，尝试自动生成...", stock_device_code
            )
        else:
            logger.info("已找到设备配置 %s，确保分区信息完整。", stock_device_code)

        try:
            ctx.device_config = get_or_create_device_config(
                device_code=stock_device_code,
                payload_path=Path(context.get("stock_rom_path", ""))
                if stock.rom_type.name == "PAYLOAD"
                else None,
                stock_props=stock.props,
                logger=logger,
                payload_info=stock.payload_info,
            )
        except Exception as e:
            logger.warning("设备配置初始化失败: %s，回退到通用配置", e)
            ctx.device_config = load_device_config(stock_device_code, logger)

        if cache_manager and ctx.device_config.get("cache", {}).get("partitions", False):
            logger.info("设备配置启用了分区级缓存")
            cache_manager.cache_partitions = True

        context["porting_context"] = ctx
        context["stock_device_code"] = stock_device_code
        context["initialized"] = True

        logger.info("目标工作区初始化阶段完成")
        return context

    def rollback(self, context: dict[str, Any]) -> dict[str, Any]:
        """回滚初始化操作。"""
        logger.info("回滚目标工作区初始化阶段")
        context.pop("initialized", None)
        context.pop("porting_context", None)
        return context


@dataclass
class ModificationPhase(Phase):
    """ROM 修改阶段。

    负责执行系统补丁、框架修改和固件修改等操作。
    对应现有流程中的 Phase 3: Modifications。

    上下文输入:
        porting_context: PortingContext 实例
        phases_to_run: 要执行的修改子阶段列表

    上下文输出:
        modified: 修改完成标志
    """

    name: str = "modification"
    description: str = "执行 ROM 修改操作（系统/框架/固件）"

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        """执行 ROM 修改逻辑。

        按 phases_to_run 列表依次调用 UnifiedModifier、
        FrameworkModifier、FirmwareModifier，最后总调用 RomModifier。
        """
        from src.core.modifiers import (
            FirmwareModifier,
            FrameworkModifier,
            RomModifier,
            UnifiedModifier,
        )

        logger.info("开始执行 ROM 修改阶段")

        ctx = context["porting_context"]
        phases_to_run = context.get("phases_to_run", ["system", "apk", "framework", "firmware"])

        if "system" in phases_to_run or "apk" in phases_to_run:
            logger.info("运行 UnifiedModifier（系统 + APK）...")
            unified_modifier = UnifiedModifier(ctx, enable_apk_mods=("apk" in phases_to_run))
            unified_phases = [p for p in ("system", "apk") if p in phases_to_run]
            if unified_phases and not unified_modifier.run(phases=unified_phases):
                logger.warning("部分修改失败，继续执行...")

        if "framework" in phases_to_run:
            logger.info("运行 FrameworkModifier...")
            FrameworkModifier(ctx).run()

        if "firmware" in phases_to_run:
            logger.info("运行 FirmwareModifier...")
            FirmwareModifier(ctx).run()

        RomModifier(ctx).run_all_modifications()

        context["modified"] = True
        logger.info("ROM 修改阶段完成")
        return context

    def rollback(self, context: dict[str, Any]) -> dict[str, Any]:
        """回滚修改操作。"""
        logger.info("回滚 ROM 修改阶段")
        context.pop("modified", None)
        return context


@dataclass
class PackingPhase(Phase):
    """ROM 重打包阶段。

    负责将修改后的镜像重新打包为可刷入的 ROM 包。
    对应现有流程中的 Phase 4: Repacking。

    上下文输入:
        porting_context: PortingContext 实例
        phases_to_run: 要执行的阶段列表（用于判断是否需要重打包）
        pack_type: 打包类型（payload 或 super）
        fs_type: 文件系统类型（erofs 或 ext4）
        target_work_dir: 目标工作目录

    上下文输出:
        packed: 打包完成标志
    """

    name: str = "repack"
    description: str = "重新打包 ROM 镜像"

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        """执行重打包逻辑。

        调用 Repacker 打包所有分区镜像，然后根据 pack_type
        生成 OTA payload 或 super image。
        """
        from src.core.packer import Repacker

        logger.info("开始执行 ROM 重打包阶段")

        ctx = context["porting_context"]
        phases_to_run = context.get("phases_to_run", [])
        pack_type = context.get("pack_type", "payload")
        fs_type = context.get("fs_type", "erofs")

        default_phases = ["system", "apk", "framework", "firmware"]
        if "repack" not in phases_to_run and phases_to_run != default_phases:
            logger.info("跳过重打包阶段（未包含在执行计划中）")
            return context

        packer = Repacker(ctx)
        packer.pack_all(pack_type=fs_type.upper(), is_rw=(fs_type == "ext4"))

        if pack_type == "super":
            logger.info("生成 Super Image...")
            packer.pack_super_image()
        else:
            logger.info("生成 OTA Payload...")
            packer.pack_ota_payload()

        context["packed"] = True
        logger.info("ROM 重打包阶段完成")
        return context

    def rollback(self, context: dict[str, Any]) -> dict[str, Any]:
        """回滚打包操作。"""
        logger.info("回滚 ROM 重打包阶段")
        context.pop("packed", None)
        return context


# 向后兼容别名
InitPhase = InitializationPhase
ModifyPhase = ModificationPhase
PackPhase = PackingPhase
