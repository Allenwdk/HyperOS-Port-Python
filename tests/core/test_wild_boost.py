"""Wild Boost 插件测试。

测试狂暴引擎插件的各项功能：
- 前置条件检查
- 内核版本检测
- 内核模块安装
- libmigui.so 补丁应用
- 属性添加
"""

import zipfile
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from src.core.modifiers.plugins.wild_boost import WildBoostPlugin


# ──────────────────────────────────────────────────────────────────────
# 测试辅助工具
# ──────────────────────────────────────────────────────────────────────


def _create_mock_context(tmp_path: Path) -> MagicMock:
    """创建 WildBoost 插件所需的模拟上下文。"""
    ctx = MagicMock()
    ctx.target_dir = tmp_path / "target"
    ctx.target_dir.mkdir(parents=True, exist_ok=True)
    ctx.repack_images_dir = tmp_path / "images"
    ctx.repack_images_dir.mkdir(parents=True, exist_ok=True)
    ctx.device_config = {}
    ctx.tools = MagicMock()
    ctx.tools.magiskboot = Path("/mock/magiskboot")
    return ctx


# ──────────────────────────────────────────────────────────────────────
# 测试 check_prerequisites
# ──────────────────────────────────────────────────────────────────────


class TestWildBoostPrerequisites:
    """前置条件检查测试。"""

    def test_check_prerequisites_enabled(self, tmp_path):
        """测试配置启用 wild_boost 时返回 True。"""
        ctx = _create_mock_context(tmp_path)
        ctx.device_config = {"wild_boost": {"enable": True}}

        plugin = WildBoostPlugin(ctx)
        assert plugin.check_prerequisites() is True

    def test_check_prerequisites_disabled(self, tmp_path):
        """测试配置禁用 wild_boost 时返回 False。"""
        ctx = _create_mock_context(tmp_path)
        ctx.device_config = {"wild_boost": {"enable": False}}

        plugin = WildBoostPlugin(ctx)
        assert plugin.check_prerequisites() is False

    def test_check_prerequisites_no_config(self, tmp_path):
        """测试无配置时返回 False。"""
        ctx = _create_mock_context(tmp_path)
        ctx.device_config = {}

        plugin = WildBoostPlugin(ctx)
        assert plugin.check_prerequisites() is False

    def test_check_prerequisites_no_device_config(self, tmp_path):
        """测试无 device_config 属性时返回 False。"""
        ctx = _create_mock_context(tmp_path)
        ctx.device_config = None

        plugin = WildBoostPlugin(ctx)
        assert plugin.check_prerequisites() is False


# ──────────────────────────────────────────────────────────────────────
# 测试 _get_kernel_version
# ──────────────────────────────────────────────────────────────────────


class TestWildBoostKernelVersion:
    """内核版本检测测试。"""

    def test_get_kernel_version_no_boot_img(self, tmp_path):
        """测试 boot.img 不存在时返回 'unknown'。"""
        ctx = _create_mock_context(tmp_path)
        # boot.img 不存在
        plugin = WildBoostPlugin(ctx)

        version = plugin._get_kernel_version()
        assert version == "unknown"

    def test_get_kernel_version_with_boot_img(self, tmp_path):
        """测试有 boot.img 时尝试解析 KMI。"""
        ctx = _create_mock_context(tmp_path)
        # 创建一个假的 boot.img
        boot_img = ctx.repack_images_dir / "boot.img"
        boot_img.write_bytes(b"\x00" * 100)

        plugin = WildBoostPlugin(ctx)

        # 模拟 _analyze_kmi 返回 KMI 版本
        with patch.object(plugin, "_analyze_kmi", return_value="android14-5.15"):
            version = plugin._get_kernel_version()
            assert version == "android14-5.15"

    def test_get_kernel_version_analyze_returns_empty(self, tmp_path):
        """测试 _analyze_kmi 返回空字符串时返回 'unknown'。"""
        ctx = _create_mock_context(tmp_path)
        boot_img = ctx.repack_images_dir / "boot.img"
        boot_img.write_bytes(b"\x00" * 100)

        plugin = WildBoostPlugin(ctx)

        with patch.object(plugin, "_analyze_kmi", return_value=""):
            version = plugin._get_kernel_version()
            assert version == "unknown"


# ──────────────────────────────────────────────────────────────────────
# 测试 _apply_libmigui_hexpatch
# ──────────────────────────────────────────────────────────────────────


class TestWildBoostHexPatch:
    """libmigui.so HexPatch 测试。"""

    def test_apply_hexpatch_no_libmigui(self, tmp_path):
        """测试没有 libmigui.so 文件时返回 False。"""
        ctx = _create_mock_context(tmp_path)
        plugin = WildBoostPlugin(ctx)

        result = plugin._apply_libmigui_hexpatch()
        assert result is False

    def test_apply_hexpatch_success(self, tmp_path):
        """测试成功应用补丁。"""
        ctx = _create_mock_context(tmp_path)
        plugin = WildBoostPlugin(ctx)

        # 创建一个包含待替换字节的文件
        lib_dir = ctx.target_dir / "system" / "lib64"
        lib_dir.mkdir(parents=True, exist_ok=True)
        lib_file = lib_dir / "libmigui.so"

        # 包含 old hex patch bytes
        old_bytes = bytes.fromhex("726F2E70726F647563742E70726F647563742E6E616D65")
        lib_file.write_bytes(b"prefix" + old_bytes + b"suffix")

        result = plugin._apply_libmigui_hexpatch()
        assert result is True

        # 验证文件被修改
        content = lib_file.read_bytes()
        new_bytes = bytes.fromhex("726F2E70726F647563742E73706F6F6665642E6E616D65")
        assert new_bytes in content
        assert old_bytes not in content

    def test_apply_hexpatch_no_match(self, tmp_path):
        """测试文件内容不匹配补丁时返回 False（未修改）。"""
        ctx = _create_mock_context(tmp_path)
        plugin = WildBoostPlugin(ctx)

        lib_dir = ctx.target_dir / "system" / "lib64"
        lib_dir.mkdir(parents=True, exist_ok=True)
        lib_file = lib_dir / "libmigui.so"
        lib_file.write_bytes(b"no matching content here")

        result = plugin._apply_libmigui_hexpatch()
        assert result is False


# ──────────────────────────────────────────────────────────────────────
# 测试 _add_feas_property
# ──────────────────────────────────────────────────────────────────────


class TestWildBoostFeasProperty:
    """属性添加测试。"""

    def test_add_feas_property_creates_file(self, tmp_path):
        """测试首次添加属性时创建文件。"""
        ctx = _create_mock_context(tmp_path)
        plugin = WildBoostPlugin(ctx)

        plugin._add_feas_property()

        prop_file = ctx.target_dir / "mi_ext" / "etc" / "build.prop"
        assert prop_file.exists()
        content = prop_file.read_text(encoding="utf-8")
        assert "persist.sys.feas.enable=true" in content

    def test_add_feas_property_already_exists(self, tmp_path):
        """测试属性已存在时不会重复添加。"""
        ctx = _create_mock_context(tmp_path)
        plugin = WildBoostPlugin(ctx)

        # 预先创建包含该属性的文件
        prop_file = ctx.target_dir / "mi_ext" / "etc" / "build.prop"
        prop_file.parent.mkdir(parents=True, exist_ok=True)
        prop_file.write_text("persist.sys.feas.enable=true\n", encoding="utf-8")

        plugin._add_feas_property()

        content = prop_file.read_text(encoding="utf-8")
        # 确保只有一行
        lines = [l for l in content.splitlines() if "persist.sys.feas.enable=true" in l]
        assert len(lines) == 1

    def test_add_feas_property_appends_to_existing(self, tmp_path):
        """测试在已有 build.prop 追加属性。"""
        ctx = _create_mock_context(tmp_path)
        plugin = WildBoostPlugin(ctx)

        prop_file = ctx.target_dir / "mi_ext" / "etc" / "build.prop"
        prop_file.parent.mkdir(parents=True, exist_ok=True)
        prop_file.write_text("ro.build.type=user\n", encoding="utf-8")

        plugin._add_feas_property()

        content = prop_file.read_text(encoding="utf-8")
        assert "ro.build.type=user" in content
        assert "persist.sys.feas.enable=true" in content


# ──────────────────────────────────────────────────────────────────────
# 测试 _install_kernel_modules
# ──────────────────────────────────────────────────────────────────────


class TestWildBoostInstallKernelModules:
    """内核模块安装测试。"""

    def test_install_kernel_modules_unknown_version(self, tmp_path):
        """测试内核版本未知时返回 False。"""
        ctx = _create_mock_context(tmp_path)
        plugin = WildBoostPlugin(ctx)

        with patch.object(plugin, "_get_kernel_version", return_value="unknown"):
            result = plugin._install_kernel_modules()
            assert result is False

    def test_install_kernel_modules_no_matching_zip(self, tmp_path):
        """测试没有匹配的 zip 包时返回 False。"""
        ctx = _create_mock_context(tmp_path)
        plugin = WildBoostPlugin(ctx)

        with patch.object(plugin, "_get_kernel_version", return_value="android14-5.15"):
            # 不存在 devices/common 目录
            result = plugin._install_kernel_modules()
            assert result is False

    def test_install_kernel_modules_with_custom_source(self, tmp_path):
        """测试使用自定义源路径。"""
        ctx = _create_mock_context(tmp_path)
        plugin = WildBoostPlugin(ctx)

        # _install_kernel_modules 用 custom_source 的 stem 作为 base_name
        # 然后拼接 kmi_version 构造候选文件名
        # custom_source="base.zip" → 候选: base_android14-5.15.zip, base_5.15.zip
        source_dir = tmp_path / "custom_source"
        source_dir.mkdir()
        base_zip = source_dir / "wild_boost.zip"

        # 创建实际匹配的候选 zip
        matching_zip = source_dir / "wild_boost_android14-5.15.zip"
        with zipfile.ZipFile(matching_zip, "w") as zf:
            zf.writestr("perfmgr.ko", b"\x00" * 10)

        with patch.object(plugin, "_get_kernel_version", return_value="android14-5.15"):
            vendor_dlkm = ctx.target_dir / "vendor_dlkm"
            vendor_dlkm.mkdir(parents=True, exist_ok=True)

            with patch.object(plugin, "_install_vendor_dlkm", return_value=True) as mock_install:
                result = plugin._install_kernel_modules(custom_source=base_zip)
                assert result is True
                mock_install.assert_called_once()

    def test_install_kernel_modules_empty_zip(self, tmp_path):
        """测试 zip 包内无 .ko 文件时返回 False。"""
        ctx = _create_mock_context(tmp_path)
        plugin = WildBoostPlugin(ctx)

        source_dir = tmp_path / "custom_source"
        source_dir.mkdir()
        base_zip = source_dir / "wild_boost.zip"

        # 创建无 .ko 的候选 zip
        matching_zip = source_dir / "wild_boost_android14-5.15.zip"
        with zipfile.ZipFile(matching_zip, "w") as zf:
            zf.writestr("readme.txt", "no ko files")

        with patch.object(plugin, "_get_kernel_version", return_value="android14-5.15"):
            result = plugin._install_kernel_modules(custom_source=base_zip)
            assert result is False
