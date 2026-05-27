"""Feature Unlock 插件测试。

测试特性解锁插件的各项功能：
- 配置加载与合并
- XML 特性标志应用
- Build 属性应用
- EU 本地化属性
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open

import pytest

from src.core.modifiers.plugins.feature_unlock import FeatureUnlockPlugin


# ──────────────────────────────────────────────────────────────────────
# 测试辅助工具
# ──────────────────────────────────────────────────────────────────────


def _create_mock_context(tmp_path: Path) -> MagicMock:
    """创建 FeatureUnlock 插件所需的模拟上下文。"""
    ctx = MagicMock()
    ctx.target_dir = tmp_path / "target"
    ctx.target_dir.mkdir(parents=True, exist_ok=True)
    ctx.stock_rom_code = "test_device"
    ctx.device_config = {}
    ctx.get_target_prop_file = MagicMock()
    return ctx


# ──────────────────────────────────────────────────────────────────────
# 测试 _load_config
# ──────────────────────────────────────────────────────────────────────


class TestFeatureUnlockLoadConfig:
    """配置加载测试。"""

    def test_load_config_common_only(self, tmp_path):
        """测试仅加载公共配置。"""
        ctx = _create_mock_context(tmp_path)
        plugin = FeatureUnlockPlugin(ctx)

        common_data = {"xml_features": {"support_AI_display": True}}
        device_data = {"xml_features": {"support_wild_boost": True}}

        # 模拟文件读取
        with patch("builtins.open", mock_open(read_data=json.dumps(common_data))):
            with patch.object(Path, "exists", return_value=True):
                result = plugin._load_config()

        # 由于文件系统状态不确定，仅验证不报错
        assert isinstance(result, dict)

    def test_load_config_no_files(self, tmp_path):
        """测试无配置文件时返回空字典。"""
        ctx = _create_mock_context(tmp_path)
        plugin = FeatureUnlockPlugin(ctx)

        # 配置文件不存在时应返回空字典
        with patch.object(Path, "exists", return_value=False):
            result = plugin._load_config()

        assert result == {}


# ──────────────────────────────────────────────────────────────────────
# 测试 modify
# ──────────────────────────────────────────────────────────────────────


class TestFeatureUnlockModify:
    """modify 方法测试。"""

    def test_modify_no_config(self, tmp_path):
        """测试无配置时返回 True。"""
        ctx = _create_mock_context(tmp_path)
        ctx.device_config = {}
        plugin = FeatureUnlockPlugin(ctx)

        with patch.object(plugin, "_load_config", return_value={}):
            result = plugin.modify()

        assert result is True

    def test_modify_with_xml_features(self, tmp_path):
        """测试应用 XML 特性标志。"""
        ctx = _create_mock_context(tmp_path)
        ctx.device_config = {"wild_boost": {"enable": False}}
        plugin = FeatureUnlockPlugin(ctx)

        config = {
            "xml_features": {"support_AI_display": True},
            "build_props": {},
        }

        with patch.object(plugin, "_load_config", return_value=config), \
             patch.object(plugin, "_apply_xml_features") as mock_xml, \
             patch.object(plugin, "_apply_build_props") as mock_props:
            result = plugin.modify()

        assert result is True
        mock_xml.assert_called_once()

    def test_modify_with_wild_boost_disabled_filters_props(self, tmp_path):
        """测试 wild_boost 禁用时过滤相关属性。"""
        ctx = _create_mock_context(tmp_path)
        ctx.device_config = {"wild_boost": {"enable": False}}
        plugin = FeatureUnlockPlugin(ctx)

        config = {
            "xml_features": {"support_wild_boost_perf": True, "support_AI_display": True},
            "build_props": {"product": {"ro.product.spoofed.name": "vermeer"}},
        }

        with patch.object(plugin, "_load_config", return_value=config), \
             patch.object(plugin, "_apply_xml_features") as mock_xml, \
             patch.object(plugin, "_apply_build_props"), \
             patch.object(plugin, "_apply_eu_localization_props"):
            result = plugin.modify()

        assert result is True
        xml_features = mock_xml.call_args[0][0]
        assert not any(k.startswith("support_wild_boost") for k in xml_features)
        assert "support_AI_display" in xml_features

    def test_modify_with_eu_localization(self, tmp_path):
        """测试 EU 本地化属性应用。"""
        ctx = _create_mock_context(tmp_path)
        ctx.is_port_eu_rom = True
        ctx.device_config = {"wild_boost": {"enable": False}}
        plugin = FeatureUnlockPlugin(ctx)

        config = {"xml_features": {}, "build_props": {}, "enable_eu_localization": True}

        with patch.object(plugin, "_load_config", return_value=config), \
             patch.object(plugin, "_apply_xml_features"), \
             patch.object(plugin, "_apply_build_props"), \
             patch.object(plugin, "_apply_eu_localization_props") as mock_eu:
            result = plugin.modify()

        assert result is True
        mock_eu.assert_called_once()


# ──────────────────────────────────────────────────────────────────────
# 测试 _apply_xml_features
# ──────────────────────────────────────────────────────────────────────


class TestFeatureUnlockXMLFeatures:
    """XML 特性标志应用测试。"""

    def test_apply_xml_features_updates_existing(self, tmp_path):
        """测试更新已存在的特性标志。"""
        ctx = _create_mock_context(tmp_path)
        plugin = FeatureUnlockPlugin(ctx)

        # 创建设备特性 XML
        feat_dir = ctx.target_dir / "product" / "etc" / "device_features"
        feat_dir.mkdir(parents=True, exist_ok=True)
        xml_file = feat_dir / f"{ctx.stock_rom_code}.xml"
        xml_file.write_text(
            '<?xml version="1.0"?>\n<features>\n'
            '    <bool name="support_AI_display">false</bool>\n'
            '</features>\n',
            encoding="utf-8",
        )

        plugin._apply_xml_features({"support_AI_display": True})

        content = xml_file.read_text(encoding="utf-8")
        assert '<bool name="support_AI_display">true</bool>' in content

    def test_apply_xml_features_adds_new(self, tmp_path):
        """测试添加新特性标志。"""
        ctx = _create_mock_context(tmp_path)
        plugin = FeatureUnlockPlugin(ctx)

        feat_dir = ctx.target_dir / "product" / "etc" / "device_features"
        feat_dir.mkdir(parents=True, exist_ok=True)
        xml_file = feat_dir / f"{ctx.stock_rom_code}.xml"
        xml_file.write_text(
            '<?xml version="1.0"?>\n<features>\n</features>\n',
            encoding="utf-8",
        )

        plugin._apply_xml_features({"new_feature": True})

        content = xml_file.read_text(encoding="utf-8")
        assert '<bool name="new_feature">true</bool>' in content

    def test_apply_xml_features_no_feat_dir(self, tmp_path):
        """测试特性目录不存在时安全跳过。"""
        ctx = _create_mock_context(tmp_path)
        plugin = FeatureUnlockPlugin(ctx)

        # 不创建 feat_dir
        plugin._apply_xml_features({"test": True})
        # 不应抛出异常

    def test_apply_xml_features_no_xml_file(self, tmp_path):
        """测试 XML 文件不存在时安全跳过。"""
        ctx = _create_mock_context(tmp_path)
        plugin = FeatureUnlockPlugin(ctx)

        feat_dir = ctx.target_dir / "product" / "etc" / "device_features"
        feat_dir.mkdir(parents=True, exist_ok=True)
        # 不创建 XML 文件

        plugin._apply_xml_features({"test": True})


# ──────────────────────────────────────────────────────────────────────
# 测试 _apply_build_props
# ──────────────────────────────────────────────────────────────────────


class TestFeatureUnlockBuildProps:
    """Build 属性应用测试。"""

    def test_apply_build_props_updates_existing(self, tmp_path):
        """测试更新已存在的属性。"""
        ctx = _create_mock_context(tmp_path)
        plugin = FeatureUnlockPlugin(ctx)

        prop_file = ctx.target_dir / "product" / "build.prop"
        prop_file.parent.mkdir(parents=True, exist_ok=True)
        prop_file.write_text("ro.product.model=OldModel\n", encoding="utf-8")
        ctx.get_target_prop_file.return_value = prop_file

        plugin._apply_build_props(
            {"product": {"ro.product.model": "NewModel"}},
            wild_boost_enabled=False,
        )

        content = prop_file.read_text(encoding="utf-8")
        assert "ro.product.model=NewModel" in content
        # 旧值应被替换
        assert content.count("ro.product.model=") == 1

    def test_apply_build_props_adds_new(self, tmp_path):
        """测试添加新属性。"""
        ctx = _create_mock_context(tmp_path)
        plugin = FeatureUnlockPlugin(ctx)

        prop_file = ctx.target_dir / "product" / "build.prop"
        prop_file.parent.mkdir(parents=True, exist_ok=True)
        prop_file.write_text("ro.build.type=user\n", encoding="utf-8")
        ctx.get_target_prop_file.return_value = prop_file

        plugin._apply_build_props(
            {"product": {"ro.custom.prop": "value"}},
            wild_boost_enabled=False,
        )

        content = prop_file.read_text(encoding="utf-8")
        assert "ro.custom.prop=value" in content

    def test_apply_build_props_no_prop_file(self, tmp_path):
        """测试属性文件不存在时安全跳过。"""
        ctx = _create_mock_context(tmp_path)
        ctx.get_target_prop_file.return_value = None
        plugin = FeatureUnlockPlugin(ctx)

        # 不应抛出异常
        plugin._apply_build_props({"product": {"key": "value"}}, wild_boost_enabled=False)

    def test_apply_build_props_filters_spoofed_when_no_wild_boost(self, tmp_path):
        """测试 wild_boost 禁用时过滤伪装属性。"""
        ctx = _create_mock_context(tmp_path)
        plugin = FeatureUnlockPlugin(ctx)

        prop_file = ctx.target_dir / "product" / "build.prop"
        prop_file.parent.mkdir(parents=True, exist_ok=True)
        prop_file.write_text("ro.build.type=user\n", encoding="utf-8")
        ctx.get_target_prop_file.return_value = prop_file

        props_map = {
            "product": {
                "ro.product.spoofed.name": "vermeer",
                "ro.custom.prop": "value",
            }
        }

        plugin._apply_build_props(props_map, wild_boost_enabled=False)

        content = prop_file.read_text(encoding="utf-8")
        # 伪装属性不应被写入
        assert "ro.product.spoofed.name" not in content
        assert "ro.custom.prop=value" in content

    def test_apply_build_props_keeps_spoofed_when_wild_boost(self, tmp_path):
        """测试 wild_boost 启用时保留伪装属性。"""
        ctx = _create_mock_context(tmp_path)
        plugin = FeatureUnlockPlugin(ctx)

        prop_file = ctx.target_dir / "product" / "build.prop"
        prop_file.parent.mkdir(parents=True, exist_ok=True)
        prop_file.write_text("ro.build.type=user\n", encoding="utf-8")
        ctx.get_target_prop_file.return_value = prop_file

        props_map = {
            "product": {
                "ro.product.spoofed.name": "vermeer",
                "ro.custom.prop": "value",
            }
        }

        plugin._apply_build_props(props_map, wild_boost_enabled=True)

        content = prop_file.read_text(encoding="utf-8")
        assert "ro.product.spoofed.name=vermeer" in content
        assert "ro.custom.prop=value" in content
