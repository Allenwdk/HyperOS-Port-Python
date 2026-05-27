"""WorkflowContext 协调器的单元测试。

测试覆盖：
1. 与 PortingContext 相同的构造参数和公共 API
2. 子上下文组合（DeviceContext、PackContext、AVBContext）
3. initialize_target 并行分区安装逻辑
4. get_target_prop_file 查找逻辑
5. APK 缓存相关方法
6. 类行数限制
"""

from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _make_mock_rom(tmp_path: Path, name: str) -> MagicMock:
    """构造模拟 RomPackage。"""
    rom = MagicMock()
    rom.extracted_dir = tmp_path / name
    rom.extracted_dir.mkdir(exist_ok=True)
    rom.label = name
    return rom


class TestWorkflowContext构造:
    """测试 WorkflowContext 的构造函数兼容性。"""

    def test_接受与PortingContext相同的参数(self, tmp_path: Path) -> None:
        """WorkflowContext 应接受 stock_rom, port_rom, target_work_dir 三个参数。"""
        from src.core.context.workflow import WorkflowContext

        stock = _make_mock_rom(tmp_path, "stock")
        port = _make_mock_rom(tmp_path, "port")
        target_dir = tmp_path / "target"

        ctx = WorkflowContext(stock, port, target_dir)
        assert ctx.stock is stock
        assert ctx.port is port
        assert ctx.target_dir == target_dir.resolve()

    def test_接受is_official_modify参数(self, tmp_path: Path) -> None:
        """WorkflowContext 应接受可选的 is_official_modify 参数。"""
        from src.core.context.workflow import WorkflowContext

        stock = _make_mock_rom(tmp_path, "stock")
        port = _make_mock_rom(tmp_path, "port")
        target_dir = tmp_path / "target"

        ctx = WorkflowContext(stock, port, target_dir, is_official_modify=True)
        assert ctx.is_official_modify is True

    def test_is_official_modify默认为False(self, tmp_path: Path) -> None:
        """is_official_modify 默认值应为 False。"""
        from src.core.context.workflow import WorkflowContext

        stock = _make_mock_rom(tmp_path, "stock")
        port = _make_mock_rom(tmp_path, "port")

        ctx = WorkflowContext(stock, port, tmp_path / "target")
        assert ctx.is_official_modify is False


class TestWorkflowContext子上下文组合:
    """测试 WorkflowContext 是否正确组合三个子上下文。"""

    def test_包含device_context属性(self, tmp_path: Path) -> None:
        """应有 device 属性，类型为 DeviceContext。"""
        from src.core.context.device import DeviceContext
        from src.core.context.workflow import WorkflowContext

        stock = _make_mock_rom(tmp_path, "stock")
        port = _make_mock_rom(tmp_path, "port")

        ctx = WorkflowContext(stock, port, tmp_path / "target")
        assert hasattr(ctx, "device")
        assert isinstance(ctx.device, DeviceContext)

    def test_包含pack_context属性(self, tmp_path: Path) -> None:
        """应有 pack 属性，类型为 PackContext。"""
        from src.core.context.pack import PackContext
        from src.core.context.workflow import WorkflowContext

        stock = _make_mock_rom(tmp_path, "stock")
        port = _make_mock_rom(tmp_path, "port")

        ctx = WorkflowContext(stock, port, tmp_path / "target")
        assert hasattr(ctx, "pack")
        assert isinstance(ctx.pack, PackContext)

    def test_包含avb_context属性(self, tmp_path: Path) -> None:
        """应有 avb 属性，类型为 AVBContext。"""
        from src.core.context.avb import AVBContext
        from src.core.context.workflow import WorkflowContext

        stock = _make_mock_rom(tmp_path, "stock")
        port = _make_mock_rom(tmp_path, "port")

        ctx = WorkflowContext(stock, port, tmp_path / "target")
        assert hasattr(ctx, "avb")
        assert isinstance(ctx.avb, AVBContext)


class TestWorkflowContext公共API兼容:
    """测试 WorkflowContext 暴露与 PortingContext 相同的公共属性。"""

    def test_暴露stock_rom_dir属性(self, tmp_path: Path) -> None:
        from src.core.context.workflow import WorkflowContext

        stock = _make_mock_rom(tmp_path, "stock")
        port = _make_mock_rom(tmp_path, "port")

        ctx = WorkflowContext(stock, port, tmp_path / "target")
        assert ctx.stock_rom_dir == stock.extracted_dir

    def test_暴露target_config_dir属性(self, tmp_path: Path) -> None:
        from src.core.context.workflow import WorkflowContext

        stock = _make_mock_rom(tmp_path, "stock")
        port = _make_mock_rom(tmp_path, "port")
        target_dir = tmp_path / "target"

        ctx = WorkflowContext(stock, port, target_dir)
        assert ctx.target_config_dir == target_dir.resolve() / "config"

    def test_暴露repack_images_dir属性(self, tmp_path: Path) -> None:
        from src.core.context.workflow import WorkflowContext

        stock = _make_mock_rom(tmp_path, "stock")
        port = _make_mock_rom(tmp_path, "port")
        target_dir = tmp_path / "target"

        ctx = WorkflowContext(stock, port, target_dir)
        assert ctx.repack_images_dir == target_dir.resolve() / "repack_images"

    def test_暴露logger属性(self, tmp_path: Path) -> None:
        import logging
        from src.core.context.workflow import WorkflowContext

        stock = _make_mock_rom(tmp_path, "stock")
        port = _make_mock_rom(tmp_path, "port")

        ctx = WorkflowContext(stock, port, tmp_path / "target")
        assert isinstance(ctx.logger, logging.Logger)

    def test_暴露shell属性(self, tmp_path: Path) -> None:
        from src.core.context.workflow import WorkflowContext

        stock = _make_mock_rom(tmp_path, "stock")
        port = _make_mock_rom(tmp_path, "port")

        ctx = WorkflowContext(stock, port, tmp_path / "target")
        assert ctx.shell is not None

    def test_暴露tools和platform_bin_dir(self, tmp_path: Path) -> None:
        from src.core.context.workflow import WorkflowContext

        stock = _make_mock_rom(tmp_path, "stock")
        port = _make_mock_rom(tmp_path, "port")

        ctx = WorkflowContext(stock, port, tmp_path / "target")
        assert hasattr(ctx, "tools")
        assert hasattr(ctx, "platform_bin_dir")

    def test_暴露默认设备属性(self, tmp_path: Path) -> None:
        from src.core.context.workflow import WorkflowContext

        stock = _make_mock_rom(tmp_path, "stock")
        port = _make_mock_rom(tmp_path, "port")

        ctx = WorkflowContext(stock, port, tmp_path / "target")
        assert ctx.enable_ksu is False
        assert ctx.enable_custom_avb_chain is False
        assert ctx.avb_key_path is None
        assert ctx.is_ab_device is False
        assert ctx.stock_rom_code == "unknown"
        assert ctx.port_rom_code == "unknown"

    def test_暴露ROM版本属性(self, tmp_path: Path) -> None:
        from src.core.context.workflow import WorkflowContext

        stock = _make_mock_rom(tmp_path, "stock")
        port = _make_mock_rom(tmp_path, "port")

        ctx = WorkflowContext(stock, port, tmp_path / "target")
        assert ctx.base_android_version == "0"
        assert ctx.port_android_version == "0"
        assert ctx.base_android_sdk == "0"
        assert ctx.port_android_sdk == "0"
        assert ctx.target_rom_version == ""


class TestWorkflowContextInitializeTarget:
    """测试 initialize_target 的并行分区安装逻辑。"""

    def test_保留已有目录当clean_existing为False(self, tmp_path: Path) -> None:
        """clean_existing=False 时应保留已有的 target 目录内容。"""
        from src.core.context.workflow import WorkflowContext

        stock = _make_mock_rom(tmp_path, "stock")
        port = _make_mock_rom(tmp_path, "port")
        target_dir = tmp_path / "target"
        sentinel = target_dir / "keep.txt"
        target_dir.mkdir()
        sentinel.write_text("preserve")

        ctx = WorkflowContext(stock, port, target_dir)

        with (
            patch.object(ctx, "_install_partition"),
            patch.object(ctx, "_copy_firmware_images"),
            patch.object(ctx, "get_rom_info"),
        ):
            ctx.initialize_target(clean_existing=False)

        assert sentinel.exists()

    def test_清理已有目录当clean_existing为True(self, tmp_path: Path) -> None:
        """clean_existing=True 时应删除已有的 target 目录内容。"""
        from src.core.context.workflow import WorkflowContext

        stock = _make_mock_rom(tmp_path, "stock")
        port = _make_mock_rom(tmp_path, "port")
        target_dir = tmp_path / "target"
        sentinel = target_dir / "delete.txt"
        target_dir.mkdir()
        sentinel.write_text("remove")

        ctx = WorkflowContext(stock, port, target_dir)

        with (
            patch.object(ctx, "_install_partition"),
            patch.object(ctx, "_copy_firmware_images"),
            patch.object(ctx, "get_rom_info"),
        ):
            ctx.initialize_target(clean_existing=True)

        assert not sentinel.exists()

    def test_调用get_rom_info(self, tmp_path: Path) -> None:
        """initialize_target 应调用 get_rom_info。"""
        from src.core.context.workflow import WorkflowContext

        stock = _make_mock_rom(tmp_path, "stock")
        port = _make_mock_rom(tmp_path, "port")

        ctx = WorkflowContext(stock, port, tmp_path / "target")

        with (
            patch.object(ctx, "_install_partition"),
            patch.object(ctx, "_copy_firmware_images"),
            patch.object(ctx, "get_rom_info") as mock_info,
        ):
            ctx.initialize_target()

        mock_info.assert_called_once()


class TestWorkflowContextGetTargetPropFile:
    """测试 get_target_prop_file 方法。"""

    def test_找到根目录下的build_prop(self, tmp_path: Path) -> None:
        from src.core.context.workflow import WorkflowContext

        stock = _make_mock_rom(tmp_path, "stock")
        port = _make_mock_rom(tmp_path, "port")
        target_dir = tmp_path / "target"

        ctx = WorkflowContext(stock, port, target_dir)
        ctx.target_dir.mkdir(parents=True, exist_ok=True)
        part_dir = ctx.target_dir / "system"
        part_dir.mkdir()
        prop_file = part_dir / "build.prop"
        prop_file.write_text("ro.build.type=user")

        result = ctx.get_target_prop_file("system")
        assert result == prop_file

    def test_找到system子目录下的build_prop(self, tmp_path: Path) -> None:
        from src.core.context.workflow import WorkflowContext

        stock = _make_mock_rom(tmp_path, "stock")
        port = _make_mock_rom(tmp_path, "port")
        target_dir = tmp_path / "target"

        ctx = WorkflowContext(stock, port, target_dir)
        ctx.target_dir.mkdir(parents=True, exist_ok=True)
        part_dir = ctx.target_dir / "system" / "system"
        part_dir.mkdir(parents=True)
        prop_file = part_dir / "build.prop"
        prop_file.write_text("ro.build.type=user")

        result = ctx.get_target_prop_file("system")
        assert result == prop_file

    def test_分区不存在时返回None(self, tmp_path: Path) -> None:
        from src.core.context.workflow import WorkflowContext

        stock = _make_mock_rom(tmp_path, "stock")
        port = _make_mock_rom(tmp_path, "port")

        ctx = WorkflowContext(stock, port, tmp_path / "target")
        ctx.target_dir.mkdir(parents=True, exist_ok=True)

        result = ctx.get_target_prop_file("nonexistent")
        assert result is None


class TestWorkflowContextAPK缓存:
    """测试 APK 缓存相关方法的委托行为。"""

    def test_find_apk_by_name委托给syncer(self, tmp_path: Path) -> None:
        from src.core.context.workflow import WorkflowContext

        stock = _make_mock_rom(tmp_path, "stock")
        port = _make_mock_rom(tmp_path, "port")

        ctx = WorkflowContext(stock, port, tmp_path / "target")
        mock_result = tmp_path / "found.apk"
        ctx.syncer = MagicMock()
        ctx.syncer.find_apk_by_name.return_value = mock_result

        result = ctx.find_apk_by_name("Settings.apk")
        ctx.syncer.find_apk_by_name.assert_called_once_with("Settings.apk", ctx.target_dir)
        assert result == mock_result

    def test_find_apk_by_package委托给syncer(self, tmp_path: Path) -> None:
        from src.core.context.workflow import WorkflowContext

        stock = _make_mock_rom(tmp_path, "stock")
        port = _make_mock_rom(tmp_path, "port")

        ctx = WorkflowContext(stock, port, tmp_path / "target")
        mock_result = tmp_path / "found.apk"
        ctx.syncer = MagicMock()
        ctx.syncer.find_apk_by_package.return_value = mock_result

        result = ctx.find_apk_by_package("com.android.settings")
        ctx.syncer.find_apk_by_package.assert_called_once_with(
            "com.android.settings", ctx.target_dir
        )
        assert result == mock_result

    def test_clear_apk_caches清空syncer缓存(self, tmp_path: Path) -> None:
        from src.core.context.workflow import WorkflowContext

        stock = _make_mock_rom(tmp_path, "stock")
        port = _make_mock_rom(tmp_path, "port")

        ctx = WorkflowContext(stock, port, tmp_path / "target")
        ctx.syncer = MagicMock()

        ctx.clear_apk_caches()
        ctx.syncer._rom_caches.clear.assert_called_once()
        ctx.syncer._package_caches.clear.assert_called_once()


class TestWorkflowContext行数限制:
    """测试 WorkflowContext 类不超过 200 行。"""

    def test_类行数低于200行(self) -> None:
        from src.core.context.workflow import WorkflowContext

        source = inspect.getsource(WorkflowContext)
        line_count = len(source.splitlines())
        assert line_count < 200, f"WorkflowContext 类有 {line_count} 行，超过 200 行限制"
