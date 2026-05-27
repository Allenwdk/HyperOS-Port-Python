"""SuperImageBuilder 模块的单元测试。"""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.core.packing.super import PartitionLayout, SuperImageBuilder


class TestPartitionLayout:
    """PartitionLayout 数据类测试。"""

    def test_default_construction(self) -> None:
        """默认构造应产生空分区列表和零大小。"""
        layout = PartitionLayout()
        assert layout.partitions == []
        assert layout.super_size == 0
        assert layout.is_ab is False
        assert layout.group_name == "qti_dynamic_partitions"

    def test_construction_with_values(self) -> None:
        """指定值构造应正确存储。"""
        layout = PartitionLayout(
            partitions=["system", "vendor", "product"],
            super_size=9663676416,
            is_ab=True,
            group_name="my_group",
        )
        assert layout.partitions == ["system", "vendor", "product"]
        assert layout.super_size == 9663676416
        assert layout.is_ab is True
        assert layout.group_name == "my_group"

    def test_total_partition_size(self) -> None:
        """total_partition_size 应返回所有分区大小之和。"""
        layout = PartitionLayout(
            partitions=["system", "vendor"],
            partition_sizes={"system": 4096, "vendor": 8192},
        )
        assert layout.total_partition_size == 12288

    def test_total_partition_size_empty(self) -> None:
        """空分区列表的总大小应为 0。"""
        layout = PartitionLayout()
        assert layout.total_partition_size == 0

    def test_metadata_slots_ab(self) -> None:
        """V-AB 设备的 metadata_slots 应为 3。"""
        layout = PartitionLayout(is_ab=True)
        assert layout.metadata_slots == 3

    def test_metadata_slots_a_only(self) -> None:
        """A-only 设备的 metadata_slots 应为 2。"""
        layout = PartitionLayout(is_ab=False)
        assert layout.metadata_slots == 2


def _make_ctx(**overrides: object) -> SimpleNamespace:
    """构造最小化的移植上下文对象。"""
    defaults: dict = {
        "target_dir": Path("/tmp/target"),
        "stock_rom_code": "FUXI",
        "device_config": {},
        "is_ab_device": False,
        "repack_images_dir": Path("/tmp/repack"),
        "target_rom_version": "1.0.0",
        "security_patch": "2024-01-01",
        "base_android_version": "15",
        "port_android_version": "15",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestSuperImageBuilderInit:
    """SuperImageBuilder 初始化测试。"""

    def test_basic_init(self) -> None:
        """基本初始化应创建有效对象。"""
        builder = SuperImageBuilder(_make_ctx())
        assert builder.ctx.stock_rom_code == "FUXI"
        assert builder.logger is not None

    def test_inherits_monitored_component(self) -> None:
        """应继承 MonitoredComponent 以获得监控能力。"""
        from src.core.monitoring import MonitoredComponent

        builder = SuperImageBuilder(_make_ctx())
        assert isinstance(builder, MonitoredComponent)


class TestSuperImageBuilderGetSuperSize:
    """_get_super_size 测试。"""

    def test_from_device_config(self) -> None:
        """应优先从 device_config 读取 super_size。"""
        ctx = _make_ctx(device_config={"pack": {"super_size": 12345678}})
        builder = SuperImageBuilder(ctx)
        assert builder._get_super_size() == 12345678

    def test_from_builtin_device_map(self) -> None:
        """当 device_config 无 super_size 时，应从内置设备映射查找。"""
        ctx = _make_ctx(stock_rom_code="FUXI", device_config={})
        builder = SuperImageBuilder(ctx)
        assert builder._get_super_size() == 9663676416

    def test_default_fallback(self) -> None:
        """未知设备代码应返回默认 super 大小。"""
        ctx = _make_ctx(stock_rom_code="UNKNOWN_DEVICE", device_config={})
        builder = SuperImageBuilder(ctx)
        assert builder._get_super_size() == 9126805504


class TestSuperImageBuilderGetPartitionList:
    """_get_partition_list 测试。"""

    def test_from_device_config(self) -> None:
        """应优先从 device_config 读取分区列表。"""
        expected = ["system", "vendor"]
        ctx = _make_ctx(device_config={"pack": {"partitions": expected}})
        builder = SuperImageBuilder(ctx)
        assert builder._get_partition_list() == expected

    def test_default_partition_list(self) -> None:
        """无配置时应返回默认分区列表。"""
        ctx = _make_ctx(device_config={})
        builder = SuperImageBuilder(ctx)
        result = builder._get_partition_list()
        assert "system" in result
        assert "vendor" in result
        assert "product" in result


class TestSuperImageBuilderBuildLpmakeArgs:
    """_build_lpmake_args 测试。"""

    def test_a_only_args(self) -> None:
        """A-only 设备应包含 metadata-slots 2 和 -F 标志。"""
        ctx = _make_ctx(is_ab_device=False)
        builder = SuperImageBuilder(ctx)
        layout = PartitionLayout(
            partitions=["system", "vendor"],
            partition_sizes={"system": 1000, "vendor": 500},
            super_size=9663676416,
            is_ab=False,
        )
        args = builder._build_lpmake_args(layout, Path("/tmp/lpmake"), Path("/tmp/super.img"))
        assert "--metadata-slots" in args
        assert "2" in args
        assert "-F" in args
        assert "--virtual-ab" not in args

    def test_vab_args(self) -> None:
        """V-AB 设备应包含 virtual-ab 和 metadata-slots 3。"""
        ctx = _make_ctx(is_ab_device=True)
        builder = SuperImageBuilder(ctx)
        layout = PartitionLayout(
            partitions=["system", "vendor"],
            partition_sizes={"system": 1000, "vendor": 500},
            super_size=9663676416,
            is_ab=True,
        )
        args = builder._build_lpmake_args(layout, Path("/tmp/lpmake"), Path("/tmp/super.img"))
        assert "--virtual-ab" in args
        assert "--metadata-slots" in args
        assert "3" in args

    def test_partition_images_included(self, tmp_path: Path) -> None:
        """分区镜像路径应出现在参数中。"""
        ctx = _make_ctx(is_ab_device=False, target_dir=tmp_path)
        builder = SuperImageBuilder(ctx)
        layout = PartitionLayout(
            partitions=["system", "vendor"],
            partition_sizes={"system": 1000, "vendor": 500},
            super_size=9663676416,
            is_ab=False,
        )
        for part in layout.partitions:
            (tmp_path / f"{part}.img").write_bytes(b"\x00" * layout.partition_sizes[part])
        args = builder._build_lpmake_args(layout, Path("/tmp/lpmake"), tmp_path / "super.img")
        assert "--partition" in args
        assert "--image" in args


class TestSuperImageBuilderLayout:
    """_detect_layout 测试。"""

    def test_detect_layout_a_only(self) -> None:
        """A-only 设备应生成正确布局。"""
        ctx = _make_ctx(
            is_ab_device=False,
            device_config={"pack": {"partitions": ["system", "vendor"], "super_size": 10000}},
        )
        builder = SuperImageBuilder(ctx)
        layout = builder._detect_layout()
        assert layout.is_ab is False
        assert layout.super_size == 10000
        assert layout.partitions == ["system", "vendor"]
        assert layout.metadata_slots == 2

    def test_detect_layout_vab(self) -> None:
        """V-AB 设备应生成正确布局。"""
        ctx = _make_ctx(
            is_ab_device=True,
            device_config={"pack": {"partitions": ["system"]}},
        )
        builder = SuperImageBuilder(ctx)
        layout = builder._detect_layout()
        assert layout.is_ab is True
        assert layout.metadata_slots == 3


class TestScriptPatching:
    """脚本修补方法测试。"""

    def test_process_script_placeholders(self, tmp_path: Path) -> None:
        """占位符替换应替换 device_code、baseversion、portversion。"""
        ctx = _make_ctx(
            stock_rom_code="fuxi",
            base_android_version="15",
            target_rom_version="2.0.0",
        )
        builder = SuperImageBuilder(ctx)
        script = tmp_path / "flash.bat"
        script.write_text(
            "echo device_code\nset ver=baseversion\nset port=portversion\n",
            encoding="utf-8",
        )
        builder._process_script_placeholders(script)
        content = script.read_text(encoding="utf-8")
        assert "fuxi" in content
        assert "15" in content
        assert "2.0.0" in content

    def test_patch_script_for_a_only(self, tmp_path: Path) -> None:
        """A-only 补丁应移除 _a/_b 后缀。"""
        builder = SuperImageBuilder(_make_ctx())
        script = tmp_path / "flash.sh"
        script.write_text(
            "fastboot flash system_a system.img\nfastboot flash system_b system.img\n",
            encoding="utf-8",
        )
        builder._patch_script_for_a_only(script)
        content = script.read_text(encoding="utf-8")
        assert "system_a" not in content
        assert "system_b" not in content
        assert "system" in content
