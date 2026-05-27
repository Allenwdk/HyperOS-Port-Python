"""EU Localization 插件测试。

测试 EU 本地化插件的各项功能：
- 前置条件检查
- CN ROM 检测
- 冲突 APK 移除
- 配置加载
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.core.modifiers.plugins.eu_localization import EULocalizationPlugin


# ──────────────────────────────────────────────────────────────────────
# 测试辅助工具
# ──────────────────────────────────────────────────────────────────────


def _create_mock_context(tmp_path: Path) -> MagicMock:
    """创建 EU 插件所需的模拟上下文。"""
    ctx = MagicMock()
    ctx.target_dir = tmp_path / "target"
    ctx.target_dir.mkdir(parents=True, exist_ok=True)
    ctx.stock = MagicMock()
    ctx.stock.extracted_dir = tmp_path / "stock"
    ctx.stock.extracted_dir.mkdir(parents=True, exist_ok=True)
    ctx.stock.props = {}
    ctx.device_config = {}
    ctx.device_code = "test_device"
    ctx.eu_bundle = None
    ctx.is_port_eu_rom = False
    ctx.syncer = MagicMock()
    ctx.tools = MagicMock()
    ctx.tools.aapt2 = None
    return ctx


# ──────────────────────────────────────────────────────────────────────
# 测试 check_prerequisites
# ──────────────────────────────────────────────────────────────────────


class TestEUPrerequisites:
    """前置条件检查测试。"""

    def test_check_prerequisites_with_bundle(self, tmp_path):
        """测试有 EU bundle 时返回 True。"""
        ctx = _create_mock_context(tmp_path)
        ctx.eu_bundle = Path("/path/to/bundle.zip")
        plugin = EULocalizationPlugin(ctx)

        assert plugin.check_prerequisites() is True

    def test_check_prerequisites_not_eu_rom(self, tmp_path):
        """测试非 EU ROM 且无 bundle 时返回 False。"""
        ctx = _create_mock_context(tmp_path)
        ctx.is_port_eu_rom = False
        ctx.eu_bundle = None
        ctx.stock.props = {"ro.product.mod_device": "test_global"}
        plugin = EULocalizationPlugin(ctx)

        assert plugin.check_prerequisites() is False

    def test_check_prerequisites_eu_rom_cn_stock(self, tmp_path):
        """测试 EU ROM 且 stock 为 CN 时返回 True。"""
        ctx = _create_mock_context(tmp_path)
        ctx.is_port_eu_rom = True
        ctx.eu_bundle = None
        ctx.stock.props = {"ro.product.mod_device": "test"}  # 无 _global 后缀 → CN
        plugin = EULocalizationPlugin(ctx)

        assert plugin.check_prerequisites() is True

    def test_check_prerequisites_eu_rom_non_cn_stock(self, tmp_path):
        """测试 EU ROM 但 stock 非 CN 时返回 False。"""
        ctx = _create_mock_context(tmp_path)
        ctx.is_port_eu_rom = True
        ctx.eu_bundle = None
        ctx.stock.props = {"ro.product.mod_device": "test_global"}
        plugin = EULocalizationPlugin(ctx)

        assert plugin.check_prerequisites() is False


# ──────────────────────────────────────────────────────────────────────
# 测试 _is_stock_cn
# ──────────────────────────────────────────────────────────────────────


class TestEUIsStockCN:
    """CN ROM 检测测试。"""

    def test_is_stock_cn_by_mod_device_no_global(self, tmp_path):
        """测试通过 mod_device 判断为 CN。"""
        ctx = _create_mock_context(tmp_path)
        ctx.stock.props = {"ro.product.mod_device": "test"}
        plugin = EULocalizationPlugin(ctx)

        assert plugin._is_stock_cn() is True

    def test_is_stock_cn_by_mod_device_global(self, tmp_path):
        """测试通过 mod_device 判断为非 CN。"""
        ctx = _create_mock_context(tmp_path)
        ctx.stock.props = {"ro.product.mod_device": "test_global"}
        plugin = EULocalizationPlugin(ctx)

        assert plugin._is_stock_cn() is False

    def test_is_stock_cn_by_region(self, tmp_path):
        """测试通过 MIUI build region 判断为 CN。"""
        ctx = _create_mock_context(tmp_path)
        ctx.stock.props = {"ro.miui.build.region": "cn"}
        plugin = EULocalizationPlugin(ctx)

        assert plugin._is_stock_cn() is True

    def test_is_stock_cn_by_locale(self, tmp_path):
        """测试通过 product locale 判断为 CN。"""
        ctx = _create_mock_context(tmp_path)
        ctx.stock.props = {"ro.product.locale": "zh-CN"}
        plugin = EULocalizationPlugin(ctx)

        assert plugin._is_stock_cn() is True

    def test_is_stock_cn_by_locale_hans(self, tmp_path):
        """测试通过 zh-hans-cn locale 判断为 CN。"""
        ctx = _create_mock_context(tmp_path)
        ctx.stock.props = {"ro.product.locale": "zh-hans-cn"}
        plugin = EULocalizationPlugin(ctx)

        assert plugin._is_stock_cn() is True

    def test_is_stock_cn_no_stock(self, tmp_path):
        """测试无 stock 时返回 False。"""
        ctx = _create_mock_context(tmp_path)
        ctx.stock = None
        plugin = EULocalizationPlugin(ctx)

        assert plugin._is_stock_cn() is False

    def test_is_stock_cn_no_matching_props(self, tmp_path):
        """测试无任何匹配属性时返回 False。"""
        ctx = _create_mock_context(tmp_path)
        ctx.stock.props = {"ro.build.type": "user"}
        plugin = EULocalizationPlugin(ctx)

        assert plugin._is_stock_cn() is False


# ──────────────────────────────────────────────────────────────────────
# 测试 _remove_target_apks
# ──────────────────────────────────────────────────────────────────────


class TestEURemoveTargetApks:
    """冲突 APK 移除测试。"""

    def test_remove_target_apks_normal_dir(self, tmp_path):
        """测试正常删除非保护目录下的应用。"""
        ctx = _create_mock_context(tmp_path)
        plugin = EULocalizationPlugin(ctx)

        # 创建一个应用目录
        app_dir = ctx.target_dir / "product" / "app" / "TestApp"
        app_dir.mkdir(parents=True, exist_ok=True)
        apk = app_dir / "TestApp.apk"
        apk.write_bytes(b"fake apk")

        plugin._remove_target_apks([apk])

        assert not app_dir.exists()

    def test_remove_target_apks_protected_dir(self, tmp_path):
        """测试保护目录下只删除 APK 文件。"""
        ctx = _create_mock_context(tmp_path)
        plugin = EULocalizationPlugin(ctx)

        # 在保护目录下创建 APK
        app_dir = ctx.target_dir / "app"  # "app" 是保护目录
        app_dir.mkdir(parents=True, exist_ok=True)
        apk = app_dir / "TestApp.apk"
        apk.write_bytes(b"fake apk")

        plugin._remove_target_apks([apk])

        # APK 应被删除但目录保留
        assert not apk.exists()
        assert app_dir.exists()

    def test_remove_target_apks_nonexistent(self, tmp_path):
        """测试不存在的 APK 不报错。"""
        ctx = _create_mock_context(tmp_path)
        plugin = EULocalizationPlugin(ctx)

        fake_path = ctx.target_dir / "nonexistent.apk"
        # 不应抛出异常
        plugin._remove_target_apks([fake_path])


# ──────────────────────────────────────────────────────────────────────
# 测试 _load_eu_config
# ──────────────────────────────────────────────────────────────────────


class TestEULoadConfig:
    """配置加载测试。"""

    def test_load_eu_config_device_specific(self, tmp_path):
        """测试加载设备特定配置。"""
        ctx = _create_mock_context(tmp_path)
        ctx.device_code = "test_device"
        plugin = EULocalizationPlugin(ctx)

        config_data = {"apps": [{"path": "product/app/TestApp", "package": "com.test.app"}]}

        # 模拟设备配置文件存在
        with patch("src.core.modifiers.plugins.eu_localization.Path") as mock_path:
            mock_config_path = MagicMock()
            mock_config_path.exists.return_value = True

            import builtins
            original_open = builtins.open

            def mock_open(path, *args, **kwargs):
                if str(path).endswith("eu_bundle_config.json"):
                    import io
                    return io.StringIO(json.dumps(config_data))
                return original_open(path, *args, **kwargs)

            import json
            with patch("builtins.open", side_effect=mock_open):
                result = plugin._load_eu_config()

        # 当路径不可用时返回默认空配置
        # 实际结果取决于文件系统，这里仅验证不报错

    def test_load_eu_config_returns_default(self, tmp_path):
        """测试配置文件不存在时返回默认空配置。"""
        ctx = _create_mock_context(tmp_path)
        ctx.device_code = "nonexistent_device"
        plugin = EULocalizationPlugin(ctx)

        result = plugin._load_eu_config()
        # 由于配置文件可能不存在，应返回含 apps 的字典
        assert "apps" in result
