from __future__ import annotations

from pathlib import Path

import pytest

from src.core.context.device import DeviceContext


class TestDeviceContextDefaults:
    def test_default_values(self) -> None:
        ctx = DeviceContext()
        assert ctx.device_config == {}
        assert ctx.enable_ksu is False
        assert ctx.enable_custom_avb_chain is False
        assert ctx.avb_key_path is None
        assert ctx.is_ab_device is False
        assert ctx.security_patch == "Unknown"
        assert ctx.stock_rom_code == "unknown"
        assert ctx.port_rom_code == "unknown"
        assert ctx.is_port_eu_rom is False
        assert ctx.is_port_global_rom is False
        assert ctx.port_global_region == ""
        assert ctx.stock_region == ""


class TestDeviceContextCustomInit:
    def test_custom_device_config(self) -> None:
        config = {"pack": {"type": "payload", "fs_type": "erofs"}, "ksu": {"enable": True}}
        ctx = DeviceContext(device_config=config)
        assert ctx.device_config["pack"]["type"] == "payload"
        assert ctx.device_config["ksu"]["enable"] is True

    def test_custom_flags(self) -> None:
        ctx = DeviceContext(
            enable_ksu=True,
            enable_custom_avb_chain=True,
            avb_key_path=Path("/path/to/key.pem"),
            is_ab_device=True,
            is_port_eu_rom=True,
            is_port_global_rom=True,
        )
        assert ctx.enable_ksu is True
        assert ctx.enable_custom_avb_chain is True
        assert ctx.avb_key_path == Path("/path/to/key.pem")
        assert ctx.is_ab_device is True
        assert ctx.is_port_eu_rom is True
        assert ctx.is_port_global_rom is True


class TestDeviceContextValidation:
    def test_validate_returns_true_for_valid_config(self) -> None:
        config = {"pack": {"type": "payload"}}
        ctx = DeviceContext(device_config=config)
        assert ctx.validate() is True

    def test_validate_returns_true_for_empty_config(self) -> None:
        ctx = DeviceContext()
        assert ctx.validate() is True

    def test_validate_returns_false_for_invalid_pack_type(self) -> None:
        config = {"pack": {"type": "invalid_type"}}
        ctx = DeviceContext(device_config=config)
        assert ctx.validate() is False

    def test_validate_returns_false_for_invalid_fs_type(self) -> None:
        config = {"pack": {"fs_type": "invalid_fs"}}
        ctx = DeviceContext(device_config=config)
        assert ctx.validate() is False


class TestDeviceContextPackProperties:
    def test_pack_type_from_config(self) -> None:
        config = {"pack": {"type": "super"}}
        ctx = DeviceContext(device_config=config)
        assert ctx.pack_type == "super"

    def test_pack_type_default_payload(self) -> None:
        ctx = DeviceContext()
        assert ctx.pack_type == "payload"

    def test_fs_type_from_config(self) -> None:
        config = {"pack": {"fs_type": "ext4"}}
        ctx = DeviceContext(device_config=config)
        assert ctx.fs_type == "ext4"

    def test_fs_type_default_erofs(self) -> None:
        ctx = DeviceContext()
        assert ctx.fs_type == "erofs"


class TestDeviceContextLoadFromConfig:
    def test_load_from_config_creates_instance(self) -> None:
        config = {
            "pack": {"type": "payload", "fs_type": "erofs"},
            "ksu": {"enable": True},
        }
        ctx = DeviceContext.load_from_config(config, is_ab_device=True, security_patch="2024-01-01")
        assert isinstance(ctx, DeviceContext)
        assert ctx.enable_ksu is True
        assert ctx.is_ab_device is True
        assert ctx.security_patch == "2024-01-01"
        assert ctx.pack_type == "payload"

    def test_load_from_config_with_empty_config(self) -> None:
        ctx = DeviceContext.load_from_config({})
        assert isinstance(ctx, DeviceContext)
        assert ctx.pack_type == "payload"
        assert ctx.fs_type == "erofs"
        assert ctx.enable_ksu is False

    def test_load_from_config_device_code(self) -> None:
        ctx = DeviceContext.load_from_config(
            {}, stock_rom_code="fuxi", port_rom_code="vermeer"
        )
        assert ctx.stock_rom_code == "fuxi"
        assert ctx.port_rom_code == "vermeer"


class TestDeviceContextRepr:
    def test_repr_contains_class_name(self) -> None:
        ctx = DeviceContext()
        assert "DeviceContext" in repr(ctx)

    def test_summary_contains_key_info(self) -> None:
        ctx = DeviceContext(
            device_config={"pack": {"type": "super"}},
            enable_ksu=True,
            is_ab_device=True,
        )
        summary = ctx.summary()
        assert "super" in summary
        assert "KSU" in summary or "ksu" in summary.lower()
        assert "AB" in summary or "ab" in summary.lower()


class TestDeviceContextSize:
    def test_class_lines_under_limit(self) -> None:
        import inspect
        from src.core.context.device import DeviceContext

        source = inspect.getsource(DeviceContext)
        line_count = len(source.splitlines())
        assert line_count < 200, f"DeviceContext 类有 {line_count} 行，超过 200 行限制"
