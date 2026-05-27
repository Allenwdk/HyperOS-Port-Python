"""配置系统优化测试

测试配置缓存、热重载、验证、合并性能优化和配置继承功能。
"""

import json
import time

import pytest

from src.core.config_loader import ConfigMerger, load_device_config
from src.core.config_merger import ConfigMerger as EnhancedConfigMerger


class TestConfigCache:
    """配置缓存测试类"""

    def test_config_cache_hit(self, tmp_path, monkeypatch):
        """测试配置缓存命中：重复加载相同配置应返回缓存结果"""
        monkeypatch.chdir(tmp_path)

        devices_dir = tmp_path / "devices"
        common_dir = devices_dir / "common"
        common_dir.mkdir(parents=True)
        (common_dir / "config.json").write_text(
            json.dumps({"pack": {"type": "payload"}}), encoding="utf-8"
        )

        merger = ConfigMerger()

        config1 = merger.load_device_config("test_device")
        config2 = merger.load_device_config("test_device")

        assert config1 == config2
        assert hasattr(merger, "_config_cache")
        assert len(merger._config_cache) > 0

    def test_config_cache_invalidation_on_file_change(self, tmp_path, monkeypatch):
        """测试缓存失效：文件修改后缓存应自动失效"""
        monkeypatch.chdir(tmp_path)

        devices_dir = tmp_path / "devices"
        common_dir = devices_dir / "common"
        common_dir.mkdir(parents=True)
        config_file = common_dir / "config.json"

        config_file.write_text(
            json.dumps({"pack": {"type": "payload"}}), encoding="utf-8"
        )

        merger = ConfigMerger()

        config1 = merger.load_device_config("test_device")
        assert config1.get("pack", {}).get("type") == "payload"

        time.sleep(0.05)
        config_file.write_text(
            json.dumps({"pack": {"type": "super"}}), encoding="utf-8"
        )

        config2 = merger.load_device_config("test_device")
        assert config2.get("pack", {}).get("type") == "super"

    def test_config_cache_clear(self, tmp_path, monkeypatch):
        """测试手动清除缓存"""
        monkeypatch.chdir(tmp_path)

        devices_dir = tmp_path / "devices"
        common_dir = devices_dir / "common"
        common_dir.mkdir(parents=True)
        (common_dir / "config.json").write_text(
            json.dumps({"pack": {"type": "payload"}}), encoding="utf-8"
        )

        merger = ConfigMerger()
        merger.load_device_config("test_device")

        merger.clear_cache()
        assert len(merger._config_cache) == 0


class TestConfigHotReload:
    """配置热重载测试类"""

    def test_hot_reload_detects_changes(self, tmp_path, monkeypatch):
        """测试热重载能检测到配置文件变更"""
        monkeypatch.chdir(tmp_path)

        devices_dir = tmp_path / "devices"
        common_dir = devices_dir / "common"
        common_dir.mkdir(parents=True)
        config_file = common_dir / "config.json"

        config_file.write_text(
            json.dumps({"pack": {"type": "payload"}}), encoding="utf-8"
        )

        merger = ConfigMerger()
        merger.load_device_config("test_device")

        time.sleep(0.05)
        config_file.write_text(
            json.dumps({"pack": {"type": "super"}}), encoding="utf-8"
        )

        has_changed = merger.has_config_changed("test_device")
        assert has_changed is True

    def test_hot_reload_no_change_detected(self, tmp_path, monkeypatch):
        """测试文件未修改时不触发热重载"""
        monkeypatch.chdir(tmp_path)

        devices_dir = tmp_path / "devices"
        common_dir = devices_dir / "common"
        common_dir.mkdir(parents=True)
        (common_dir / "config.json").write_text(
            json.dumps({"pack": {"type": "payload"}}), encoding="utf-8"
        )

        merger = ConfigMerger()
        merger.load_device_config("test_device")

        has_changed = merger.has_config_changed("test_device")
        assert has_changed is False


class TestConfigValidation:
    """配置验证测试类"""

    def test_validate_valid_config(self, tmp_path):
        """测试验证合法的配置文件"""
        config_data = {
            "wild_boost": {"enable": True},
            "pack": {"type": "payload", "fs_type": "erofs"},
            "ksu": {"enable": False},
        }
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config_data), encoding="utf-8")

        merger = ConfigMerger()
        is_valid, errors = merger.validate_config(config_file)
        assert is_valid is True
        assert len(errors) == 0

    def test_validate_invalid_config_type(self, tmp_path):
        """测试验证类型错误的配置"""
        config_data = {
            "wild_boost": {"enable": "not_a_bool"},
            "pack": {"type": 123},
        }
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config_data), encoding="utf-8")

        merger = ConfigMerger()
        is_valid, errors = merger.validate_config(config_file)
        assert is_valid is False
        assert len(errors) > 0

    def test_validate_config_not_object(self, tmp_path):
        """测试验证非对象类型的配置"""
        config_file = tmp_path / "config.json"
        config_file.write_text('["not", "an", "object"]', encoding="utf-8")

        merger = ConfigMerger()
        is_valid, errors = merger.validate_config(config_file)
        assert is_valid is False


class TestMergePerformance:
    """合并性能优化测试类"""

    def test_optimized_deep_merge(self):
        """测试优化后的深度合并功能"""
        merger = EnhancedConfigMerger()

        base = {
            "level1": {"level2": {"a": 1, "b": 2, "c": 3}},
            "list_items": [
                {"description": "item1", "value": "old"},
                {"description": "item2", "value": "keep"},
            ],
        }
        override = {
            "level1": {"level2": {"b": 20, "d": 4}},
            "list_items": [
                {"description": "item1", "value": "new"},
            ],
        }

        result = merger.merge(base, override)

        assert result["level1"]["level2"]["a"] == 1
        assert result["level1"]["level2"]["b"] == 20
        assert result["level1"]["level2"]["c"] == 3
        assert result["level1"]["level2"]["d"] == 4

    def test_merge_with_large_config(self):
        """测试大配置文件的合并性能"""
        merger = EnhancedConfigMerger()

        base = {f"key_{i}": {"nested": {"value": i}} for i in range(1000)}
        override = {f"key_{i}": {"nested": {"value": i * 2}} for i in range(500, 1500)}

        start = time.time()
        result = merger.merge(base, override)
        elapsed = time.time() - start

        assert result["key_0"]["nested"]["value"] == 0
        assert result["key_500"]["nested"]["value"] == 1000
        assert result["key_1000"]["nested"]["value"] == 2000

        assert elapsed < 1.0, f"合并耗时过长: {elapsed:.3f}s"


class TestConfigInheritance:
    """配置继承测试类"""

    def test_device_config_inherits_from_base(self, tmp_path, monkeypatch):
        """测试设备配置继承自基础设备"""
        monkeypatch.chdir(tmp_path)

        devices_dir = tmp_path / "devices"
        common_dir = devices_dir / "common"
        base_dir = devices_dir / "base_device"
        device_dir = devices_dir / "child_device"

        common_dir.mkdir(parents=True)
        base_dir.mkdir(parents=True)
        device_dir.mkdir(parents=True)

        (common_dir / "config.json").write_text(
            json.dumps({"pack": {"type": "payload", "fs_type": "erofs"}}),
            encoding="utf-8",
        )

        (base_dir / "config.json").write_text(
            json.dumps({
                "wild_boost": {"enable": True},
                "pack": {"fs_type": "ext4"},
            }),
            encoding="utf-8",
        )

        (device_dir / "config.json").write_text(
            json.dumps({
                "inherits": "base_device",
                "wild_boost": {"enable": False},
            }),
            encoding="utf-8",
        )

        merger = ConfigMerger()
        config = merger.load_device_config("child_device")

        assert config["pack"]["type"] == "payload"
        assert config["pack"]["fs_type"] == "ext4"
        assert config["wild_boost"]["enable"] is False

    def test_config_priority_preserved(self, tmp_path, monkeypatch):
        """测试配置优先级保持不变：CLI > device > common"""
        monkeypatch.chdir(tmp_path)

        devices_dir = tmp_path / "devices"
        common_dir = devices_dir / "common"
        device_dir = devices_dir / "test_device"

        common_dir.mkdir(parents=True)
        device_dir.mkdir(parents=True)

        (common_dir / "config.json").write_text(
            json.dumps({
                "wild_boost": {"enable": False},
                "pack": {"type": "payload", "fs_type": "erofs"},
                "ksu": {"enable": False},
            }),
            encoding="utf-8",
        )

        (device_dir / "config.json").write_text(
            json.dumps({
                "wild_boost": {"enable": True},
                "pack": {"type": "super"},
            }),
            encoding="utf-8",
        )

        merger = ConfigMerger()
        config = merger.load_device_config("test_device")

        assert config["wild_boost"]["enable"] is True
        assert config["pack"]["type"] == "super"
        assert config["pack"]["fs_type"] == "erofs"
        assert config["ksu"]["enable"] is False
