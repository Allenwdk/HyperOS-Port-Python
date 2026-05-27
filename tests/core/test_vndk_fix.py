"""VNDK Fix 插件测试。

测试 VNDK 修复插件的各项功能：
- VNDK APEX 修复
- VINTF manifest 修复
- 文件递归查找
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.core.modifiers.plugins.vndk_fix import VNDKFixPlugin


# ──────────────────────────────────────────────────────────────────────
# 测试辅助工具
# ──────────────────────────────────────────────────────────────────────


def _create_mock_context(tmp_path: Path) -> MagicMock:
    """创建 VNDKFix 插件所需的模拟上下文。"""
    ctx = MagicMock()
    ctx.target_dir = tmp_path / "target"
    ctx.target_dir.mkdir(parents=True, exist_ok=True)
    ctx.stock = MagicMock()
    ctx.stock.extracted_dir = tmp_path / "stock"
    ctx.stock.extracted_dir.mkdir(parents=True, exist_ok=True)
    ctx.device_config = {}
    return ctx


# ──────────────────────────────────────────────────────────────────────
# 测试 modify
# ──────────────────────────────────────────────────────────────────────


class TestVNDKFixModify:
    """modify 方法测试。"""

    def test_modify_returns_true(self, tmp_path):
        """测试 modify 始终返回 True。"""
        ctx = _create_mock_context(tmp_path)
        ctx.stock.get_prop.return_value = None
        plugin = VNDKFixPlugin(ctx)

        result = plugin.modify()
        assert result is True


# ──────────────────────────────────────────────────────────────────────
# 测试 _fix_vndk_apex
# ──────────────────────────────────────────────────────────────────────


class TestVNDKFixApex:
    """VNDK APEX 修复测试。"""

    def test_fix_vndk_apex_copies_from_stock(self, tmp_path):
        """测试从 stock 复制缺失的 VNDK APEX。"""
        ctx = _create_mock_context(tmp_path)
        ctx.stock.get_prop.return_value = "34"

        # 在 stock 中创建 APEX 文件
        stock_apex_dir = ctx.stock.extracted_dir / "system_ext" / "apex"
        stock_apex_dir.mkdir(parents=True, exist_ok=True)
        apex_file = stock_apex_dir / "com.android.vndk.v34.apex"
        apex_file.write_bytes(b"fake apex content")

        # 在 target 中创建目标目录但不包含 APEX
        target_apex_dir = ctx.target_dir / "system_ext" / "apex"
        target_apex_dir.mkdir(parents=True, exist_ok=True)

        plugin = VNDKFixPlugin(ctx)
        plugin._fix_vndk_apex()

        target_file = target_apex_dir / "com.android.vndk.v34.apex"
        assert target_file.exists()
        assert target_file.read_bytes() == b"fake apex content"

    def test_fix_vndk_apex_no_version(self, tmp_path):
        """测试无法获取 VNDK 版本时不执行操作。"""
        ctx = _create_mock_context(tmp_path)
        ctx.stock.get_prop.return_value = None

        # vendor 目录下无 .prop 文件
        vendor_dir = ctx.stock.extracted_dir / "vendor"
        vendor_dir.mkdir(parents=True, exist_ok=True)

        plugin = VNDKFixPlugin(ctx)
        plugin._fix_vndk_apex()

        # 什么都不应发生
        target_apex_dir = ctx.target_dir / "system_ext" / "apex"
        if target_apex_dir.exists():
            assert len(list(target_apex_dir.iterdir())) == 0

    def test_fix_vndk_apex_already_exists(self, tmp_path):
        """测试目标 APEX 已存在时不覆盖。"""
        ctx = _create_mock_context(tmp_path)
        ctx.stock.get_prop.return_value = "34"

        # stock 中有 APEX
        stock_apex_dir = ctx.stock.extracted_dir / "system_ext" / "apex"
        stock_apex_dir.mkdir(parents=True, exist_ok=True)
        apex_file = stock_apex_dir / "com.android.vndk.v34.apex"
        apex_file.write_bytes(b"stock content")

        # target 中已有同名 APEX
        target_apex_dir = ctx.target_dir / "system_ext" / "apex"
        target_apex_dir.mkdir(parents=True, exist_ok=True)
        target_file = target_apex_dir / "com.android.vndk.v34.apex"
        target_file.write_bytes(b"existing content")

        plugin = VNDKFixPlugin(ctx)
        plugin._fix_vndk_apex()

        # 不应被覆盖
        assert target_file.read_bytes() == b"existing content"

    def test_fix_vndk_apex_reads_from_vendor_props(self, tmp_path):
        """测试从 vendor 目录的 .prop 文件读取版本。"""
        ctx = _create_mock_context(tmp_path)
        ctx.stock.get_prop.return_value = None

        # 在 vendor 目录创建包含版本信息的 .prop 文件
        vendor_dir = ctx.stock.extracted_dir / "vendor"
        vendor_dir.mkdir(parents=True, exist_ok=True)
        prop_file = vendor_dir / "build.prop"
        prop_file.write_text("ro.vndk.version=33\nro.build.type=user\n", encoding="utf-8")

        # 在 stock 中创建 APEX
        stock_apex_dir = ctx.stock.extracted_dir / "system_ext" / "apex"
        stock_apex_dir.mkdir(parents=True, exist_ok=True)
        apex_file = stock_apex_dir / "com.android.vndk.v33.apex"
        apex_file.write_bytes(b"v33 apex")

        # target 目标目录
        target_apex_dir = ctx.target_dir / "system_ext" / "apex"
        target_apex_dir.mkdir(parents=True, exist_ok=True)

        plugin = VNDKFixPlugin(ctx)
        plugin._fix_vndk_apex()

        target_file = target_apex_dir / "com.android.vndk.v33.apex"
        assert target_file.exists()


# ──────────────────────────────────────────────────────────────────────
# 测试 _fix_vintf_manifest
# ──────────────────────────────────────────────────────────────────────


class TestVNDKFixVintfManifest:
    """VINTF manifest 修复测试。"""

    def test_fix_vintf_manifest_injects_version(self, tmp_path):
        """测试向 manifest.xml 注入 VNDK 版本。"""
        ctx = _create_mock_context(tmp_path)
        ctx.stock.get_prop.return_value = "34"

        # 创建 manifest.xml
        system_ext = ctx.target_dir / "system_ext"
        system_ext.mkdir(parents=True, exist_ok=True)
        manifest = system_ext / "manifest.xml"
        manifest.write_text(
            '<?xml version="1.0" encoding="utf-8"?>\n<manifest>\n</manifest>\n',
            encoding="utf-8",
        )

        plugin = VNDKFixPlugin(ctx)
        plugin._fix_vintf_manifest()

        content = manifest.read_text(encoding="utf-8")
        assert "<version>34</version>" in content
        assert "<vendor-ndk>" in content

    def test_fix_vintf_manifest_already_has_version(self, tmp_path):
        """测试 manifest 已包含版本时不重复注入。"""
        ctx = _create_mock_context(tmp_path)
        ctx.stock.get_prop.return_value = "34"

        system_ext = ctx.target_dir / "system_ext"
        system_ext.mkdir(parents=True, exist_ok=True)
        manifest = system_ext / "manifest.xml"
        original_content = (
            '<?xml version="1.0"?>\n<manifest>\n'
            '    <vendor-ndk>\n        <version>34</version>\n    </vendor-ndk>\n'
            '</manifest>\n'
        )
        manifest.write_text(original_content, encoding="utf-8")

        plugin = VNDKFixPlugin(ctx)
        plugin._fix_vintf_manifest()

        # 内容不应改变
        assert manifest.read_text(encoding="utf-8") == original_content

    def test_fix_vintf_manifest_no_version(self, tmp_path):
        """测试无法获取 VNDK 版本时不修改 manifest。"""
        ctx = _create_mock_context(tmp_path)
        ctx.stock.get_prop.return_value = None

        # vendor build.prop 也不存在
        vendor_dir = ctx.target_dir / "vendor"
        vendor_dir.mkdir(parents=True, exist_ok=True)

        system_ext = ctx.target_dir / "system_ext"
        system_ext.mkdir(parents=True, exist_ok=True)
        manifest = system_ext / "manifest.xml"
        original = '<?xml version="1.0"?>\n<manifest>\n</manifest>\n'
        manifest.write_text(original, encoding="utf-8")

        plugin = VNDKFixPlugin(ctx)
        plugin._fix_vintf_manifest()

        assert manifest.read_text(encoding="utf-8") == original

    def test_fix_vintf_manifest_no_manifest_file(self, tmp_path):
        """测试 manifest.xml 不存在时不报错。"""
        ctx = _create_mock_context(tmp_path)
        ctx.stock.get_prop.return_value = "34"

        plugin = VNDKFixPlugin(ctx)
        # 不应抛出异常
        plugin._fix_vintf_manifest()


# ──────────────────────────────────────────────────────────────────────
# 测试 _find_file_recursive
# ──────────────────────────────────────────────────────────────────────


class TestVNDKFindFileRecursive:
    """文件递归查找测试。"""

    def test_find_file_recursive_found(self, tmp_path):
        """测试能找到嵌套文件。"""
        ctx = _create_mock_context(tmp_path)
        plugin = VNDKFixPlugin(ctx)

        nested = tmp_path / "a" / "b" / "c"
        nested.mkdir(parents=True)
        target = nested / "test.xml"
        target.write_text("<test/>")

        result = plugin._find_file_recursive(tmp_path, "test.xml")
        assert result == target

    def test_find_file_recursive_not_found(self, tmp_path):
        """测试文件不存在时返回 None。"""
        ctx = _create_mock_context(tmp_path)
        plugin = VNDKFixPlugin(ctx)

        result = plugin._find_file_recursive(tmp_path, "nonexistent.xml")
        assert result is None

    def test_find_file_recursive_dir_not_exists(self, tmp_path):
        """测试目录不存在时返回 None。"""
        ctx = _create_mock_context(tmp_path)
        plugin = VNDKFixPlugin(ctx)

        result = plugin._find_file_recursive(Path("/nonexistent"), "file.xml")
        assert result is None
