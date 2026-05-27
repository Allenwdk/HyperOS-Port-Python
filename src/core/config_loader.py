import json
import logging
import os
from pathlib import Path
from typing import Any, Optional, Tuple, cast

# 配置文件 JSON Schema 定义
_CONFIG_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "wild_boost": {
            "type": "object",
            "properties": {
                "enable": {"type": "boolean"},
            },
        },
        "pack": {
            "type": "object",
            "properties": {
                "type": {"type": "string"},
                "fs_type": {"type": "string"},
            },
        },
        "ksu": {
            "type": "object",
            "properties": {
                "enable": {"type": "boolean"},
            },
        },
        "inherits": {"type": "string"},
        "overrides": {"type": "object"},
    },
}


class ConfigMerger:
    """设备配置合并器，支持深度合并、缓存、热重载和配置继承。"""

    def __init__(self, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger("ConfigMerger")
        self._config_cache: dict[str, dict[str, Any]] = {}
        self._config_mtimes: dict[str, float] = {}

    def deep_merge(
        self, base: dict[str, Any], override: dict[str, Any]
    ) -> dict[str, Any]:
        """深度合并两个字典，override 值优先。"""
        result = base.copy()

        for key, value in override.items():
            if key.startswith("_"):
                continue

            if (
                key in result
                and isinstance(result[key], dict)
                and isinstance(value, dict)
            ):
                result[key] = self.deep_merge(result[key], value)
            else:
                result[key] = value

        return result

    def load_config(self, config_path: Path) -> dict[str, Any]:
        """加载单个配置文件，支持基于 mtime 的缓存。"""
        cache_key = str(config_path)

        if cache_key in self._config_cache:
            cached_mtime = self._config_mtimes.get(cache_key, 0)
            current_mtime = self._get_mtime(config_path)
            if current_mtime <= cached_mtime:
                return self._config_cache[cache_key].copy()

        if not config_path.exists():
            return {}

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config_data = json.load(f)
            if isinstance(config_data, dict):
                result = cast(dict[str, Any], config_data)
                self._config_cache[cache_key] = result.copy()
                self._config_mtimes[cache_key] = self._get_mtime(config_path)
                return result
            self.logger.error(
                f"配置文件 {config_path} 根节点不是对象: {type(config_data).__name__}"
            )
            return {}
        except json.JSONDecodeError as e:
            self.logger.error(f"解析配置文件失败 {config_path}: {e}")
            return {}
        except Exception as e:
            self.logger.error(f"加载配置文件失败 {config_path}: {e}")
            return {}

    def load_device_config(self, device_codename: str) -> dict[str, Any]:
        """加载并合并设备配置，支持配置继承。

        合并层级: common -> [base_device] -> device
        """
        devices_dir = Path("devices")

        common_config = self.load_config(devices_dir / "common" / "config.json")
        if common_config:
            self.logger.info("已加载通用配置")

        device_config_path = devices_dir / device_codename / "config.json"
        device_config = self.load_config(device_config_path)

        inherits_from = device_config.get("inherits")
        if inherits_from and isinstance(inherits_from, str):
            base_config = self._load_inherited_config(
                devices_dir, inherits_from, set()
            )
            merged = self.deep_merge(common_config, base_config)
            merged = self.deep_merge(merged, device_config)
            self.logger.info(
                f"设备 {device_codename} 继承自 {inherits_from}"
            )
        else:
            merged = self.deep_merge(common_config, device_config)

        if device_config:
            self.logger.info(f"已加载设备配置: {device_codename}")

        self._log_config_summary(merged, device_codename)
        return merged

    def _load_inherited_config(
        self,
        devices_dir: Path,
        device_codename: str,
        visited: set[str],
    ) -> dict[str, Any]:
        """递归加载继承链上的设备配置，检测循环继承。"""
        if device_codename in visited:
            self.logger.error(f"检测到循环继承: {device_codename}")
            return {}

        visited.add(device_codename)
        config_path = devices_dir / device_codename / "config.json"
        config = self.load_config(config_path)

        inherits_from = config.get("inherits")
        if inherits_from and isinstance(inherits_from, str):
            parent_config = self._load_inherited_config(
                devices_dir, inherits_from, visited
            )
            return self.deep_merge(parent_config, config)

        return config

    def has_config_changed(self, device_codename: str) -> bool:
        """检测设备配置文件是否发生变更（热重载支持）。"""
        devices_dir = Path("devices")
        paths_to_check = [
            devices_dir / "common" / "config.json",
            devices_dir / device_codename / "config.json",
        ]

        for path in paths_to_check:
            cache_key = str(path)
            if cache_key not in self._config_mtimes:
                if path.exists():
                    return True
                continue

            current_mtime = self._get_mtime(path)
            if current_mtime > self._config_mtimes[cache_key]:
                return True

        return False

    def clear_cache(self) -> None:
        """清除所有配置缓存。"""
        self._config_cache.clear()
        self._config_mtimes.clear()

    def validate_config(
        self, config_path: Path
    ) -> Tuple[bool, list[str]]:
        """验证配置文件结构是否合法。"""
        errors: list[str] = []

        if not config_path.exists():
            return False, [f"配置文件不存在: {config_path}"]

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            return False, [f"JSON 解析失败: {e}"]

        if not isinstance(data, dict):
            return False, ["配置文件根节点必须是对象"]

        self._validate_object(data, _CONFIG_SCHEMA, errors, "")
        return len(errors) == 0, errors

    def _validate_object(
        self,
        data: dict[str, Any],
        schema: dict[str, Any],
        errors: list[str],
        prefix: str,
    ) -> None:
        """递归验证数据结构。"""
        properties = schema.get("properties", {})

        for key, value in data.items():
            if key.startswith("_"):
                continue

            if key not in properties:
                continue

            prop_schema = properties[key]
            expected_type = prop_schema.get("type")
            display_key = f"{prefix}.{key}" if prefix else key

            if expected_type == "object":
                if not isinstance(value, dict):
                    errors.append(
                        f"{display_key}: 期望对象类型，实际为 {type(value).__name__}"
                    )
                elif "properties" in prop_schema:
                    self._validate_object(value, prop_schema, errors, display_key)
            elif expected_type == "boolean":
                if not isinstance(value, bool):
                    errors.append(
                        f"{display_key}: 期望布尔类型，实际为 {type(value).__name__}"
                    )
            elif expected_type == "string":
                if not isinstance(value, str):
                    errors.append(
                        f"{display_key}: 期望字符串类型，实际为 {type(value).__name__}"
                    )

    def _get_mtime(self, path: Path) -> float:
        """获取文件修改时间，文件不存在返回 0。"""
        try:
            return os.path.getmtime(path)
        except OSError:
            return 0.0

    def _log_config_summary(self, config: dict[str, Any], device_codename: str):
        """输出配置摘要日志。"""
        wild_boost = config.get("wild_boost", {})
        pack = config.get("pack", {})
        ksu = config.get("ksu", {})

        self.logger.info(f"设备 {device_codename} 配置:")
        self.logger.info(f"  狂暴引擎: 启用={wild_boost.get('enable', False)}")
        self.logger.info(
            f"  打包: 类型={pack.get('type', 'payload')}, "
            f"文件系统={pack.get('fs_type', 'erofs')}"
        )
        self.logger.info(f"  KernelSU: 启用={ksu.get('enable', False)}")


def load_device_config(
    device_codename: str, logger: Optional[logging.Logger] = None
) -> dict[str, Any]:
    """
    Convenience function to create a new ConfigMerger and load device configuration.
    This avoids the global singleton and ties configuration to the specific task context.

    Args:
        device_codename: Device codename
        logger: Optional logger instance

    Returns:
        Merged configuration dictionary
    """
    merger = ConfigMerger(logger)
    return merger.load_device_config(device_codename)


# Maintain backward compatibility while moving to per-task configuration
# Global registry for task-specific mergers
_config_merger_instances_registry: dict[str, ConfigMerger] = {}


def load_device_config_with_context(
    task_context: str, device_codename: str, logger: Optional[logging.Logger] = None
) -> dict[str, Any]:
    """
    Load device configuration for a specific task context to avoid cross-contamination.

    Args:
        task_context: Unique identifier for the task (e.g., 'device123_port')
        device_codename: Device codename
        logger: Optional logger instance

    Returns:
        Merged configuration dictionary
    """
    # Create a new ConfigMerger instance for the specific context to avoid conflicts
    merger = ConfigMerger(logger)
    _config_merger_instances_registry[task_context] = merger
    return merger.load_device_config(device_codename)


def get_config_merger(logger: Optional[logging.Logger] = None) -> ConfigMerger:
    """
    DEPRECATED: Create a new ConfigMerger instance.
    Use load_device_config() instead for simple cases or attach ConfigMerger to your context directly.

    Args:
        logger: Optional logger instance

    Returns:
        New ConfigMerger instance
    """
    import warnings

    warnings.warn(
        "get_config_merger() is deprecated. Use load_device_config() directly or "
        "attach ConfigMerger to your context object.",
        DeprecationWarning,
        stacklevel=2,
    )
    return ConfigMerger(logger)
