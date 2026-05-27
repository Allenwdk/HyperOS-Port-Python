from pathlib import Path
from unittest.mock import MagicMock

from src.core.context.pack import PackContext


def test_default_values():
    ctx = PackContext(
        pack_type="payload",
        fs_type="erofs",
        target_dir=Path("/tmp/target"),
    )

    assert ctx.pack_type == "payload"
    assert ctx.fs_type == "erofs"
    assert ctx.target_dir == Path("/tmp/target")
    assert ctx.target_config_dir == Path("/tmp/target/config")
    assert ctx.repack_images_dir == Path("/tmp/target/repack_images")
    assert ctx.enable_ksu is False
    assert ctx.enable_custom_avb_chain is False
    assert ctx.avb_key_path is None
    assert ctx.stock_rom_code == "unknown"
    assert ctx.is_ab_device is False
    assert ctx.security_patch == "Unknown"


def test_custom_values():
    ctx = PackContext(
        pack_type="super",
        fs_type="ext4",
        target_dir=Path("/custom/target"),
        enable_ksu=True,
        enable_custom_avb_chain=True,
        is_ab_device=True,
        stock_rom_code="fuxi",
        security_patch="2025-01-01",
    )

    assert ctx.pack_type == "super"
    assert ctx.fs_type == "ext4"
    assert ctx.enable_ksu is True
    assert ctx.enable_custom_avb_chain is True
    assert ctx.is_ab_device is True
    assert ctx.stock_rom_code == "fuxi"
    assert ctx.security_patch == "2025-01-01"


def test_build_partition_layout_returns_expected_mapping():
    stock = MagicMock()
    port = MagicMock()

    ctx = PackContext(
        pack_type="payload",
        fs_type="erofs",
        target_dir=Path("/tmp/target"),
    )
    layout = ctx.build_partition_layout(stock=stock, port=port)

    assert layout["vendor"] is stock
    assert layout["odm"] is stock
    assert layout["vendor_dlkm"] is stock
    assert layout["odm_dlkm"] is stock
    assert layout["system_dlkm"] is stock

    assert layout["system"] is port
    assert layout["system_ext"] is port
    assert layout["product"] is port
    assert layout["mi_ext"] is port
    assert layout["product_dlkm"] is port


def test_build_partition_layout_contains_all_expected_partitions():
    stock = MagicMock()
    port = MagicMock()
    ctx = PackContext(
        pack_type="payload",
        fs_type="erofs",
        target_dir=Path("/tmp/target"),
    )
    layout = ctx.build_partition_layout(stock=stock, port=port)

    expected_keys = {
        "vendor", "odm", "vendor_dlkm", "odm_dlkm", "system_dlkm",
        "system", "system_ext", "product", "mi_ext", "product_dlkm",
    }
    assert set(layout.keys()) == expected_keys


def test_directory_paths_auto_derived_from_target_dir():
    ctx = PackContext(
        pack_type="payload",
        fs_type="erofs",
        target_dir=Path("/work/build/target"),
    )

    assert ctx.target_config_dir == Path("/work/build/target/config")
    assert ctx.repack_images_dir == Path("/work/build/target/repack_images")


def test_from_porting_context_factory():
    mock_ctx = MagicMock()
    mock_ctx.target_dir = Path("/mock/target")
    mock_ctx.enable_ksu = True
    mock_ctx.enable_custom_avb_chain = True
    mock_ctx.avb_key_path = Path("/mock/key.pem")
    mock_ctx.stock_rom_code = "fuxi"
    mock_ctx.is_ab_device = True
    mock_ctx.security_patch = "2025-03-01"
    mock_ctx.base_android_version = "15"
    mock_ctx.port_android_version = "15"
    mock_ctx.target_rom_version = "OS2.0.1.0"
    mock_ctx.is_port_eu_rom = False
    mock_ctx.is_port_global_rom = True
    mock_ctx.port_global_region = "IN"
    mock_ctx.stock_region = "CN"

    pack_ctx = PackContext.from_porting_context(mock_ctx, pack_type="super", fs_type="ext4")

    assert pack_ctx.pack_type == "super"
    assert pack_ctx.fs_type == "ext4"
    assert pack_ctx.enable_ksu is True
    assert pack_ctx.enable_custom_avb_chain is True
    assert pack_ctx.avb_key_path == Path("/mock/key.pem")
    assert pack_ctx.stock_rom_code == "fuxi"
    assert pack_ctx.is_ab_device is True
    assert pack_ctx.security_patch == "2025-03-01"
    assert pack_ctx.base_android_version == "15"
    assert pack_ctx.port_android_version == "15"
    assert pack_ctx.target_rom_version == "OS2.0.1.0"
    assert pack_ctx.is_port_global_rom is True
    assert pack_ctx.port_global_region == "IN"
    assert pack_ctx.stock_region == "CN"
