"""AVB 管理器测试套件。

从 test_packer_avb_misc.py 迁移并扩展，覆盖 AVBManager 的所有公共方法。
"""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.core.monitoring import MetricsCollector
from src.core.packing.avb import AVBManager, parse_avbtool_info_output
from src.core.packing.constants import (
    AOSP_AVB_PARTITIONS,
    AVB_DEFAULT_ALGORITHM,
    DEVICE_SIZE_MAP,
    SUPER_SIZE_DEFAULT,
)


def _make_ctx(tmp_path: Path, **overrides) -> SimpleNamespace:
    """创建测试用的 context 对象。"""
    defaults = {
        "stock_rom_code": "pudding",
        "device_config": {"pack": {"super_size": 13411287040}},
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_avb_manager(tmp_path: Path, ctx=None, **overrides) -> AVBManager:
    """创建测试用的 AVBManager 实例。"""
    if ctx is None:
        ctx = _make_ctx(tmp_path, **overrides)
    collector = MetricsCollector()
    return AVBManager(ctx, collector=collector)


# === 1. parse_avbtool_info_output 测试 ===


def test_parse_avbtool_info_output_extracts_chain_and_descriptors() -> None:
    """测试解析 avbtool info_image 输出的完整解析。"""
    output = """
Image size:               1048576 bytes
Original image size:      32 bytes
Algorithm:                SHA256_RSA4096
Rollback Index:           1767225600
Flags:                    1
Descriptors:
    Chain Partition descriptor:
      Partition Name:          boot
      Rollback Index Location: 3
    Chain Partition descriptor:
      Partition Name:          recovery
      Rollback Index Location: 1
    Hash descriptor:
      Partition Name:        dtbo
    Hashtree descriptor:
      Partition Name:        system
    Hashtree descriptor:
      Partition Name:        system_ext
"""
    parsed = parse_avbtool_info_output(output)

    assert parsed["image_size"] == 1048576
    assert parsed["original_image_size"] == 32
    assert parsed["algorithm"] == "SHA256_RSA4096"
    assert parsed["rollback_index"] == 1767225600
    assert parsed["flags"] == 1
    assert parsed["chain_partitions"] == [("boot", 3), ("recovery", 1)]
    assert parsed["hash_partitions"] == ["dtbo"]
    assert parsed["hashtree_partitions"] == ["system", "system_ext"]


def test_parse_avbtool_info_output_empty_input() -> None:
    """测试空输入的解析。"""
    parsed = parse_avbtool_info_output("")
    assert parsed["image_size"] is None
    assert parsed["algorithm"] is None
    assert parsed["chain_partitions"] == []
    assert parsed["hash_partitions"] == []
    assert parsed["hashtree_partitions"] == []


def test_parse_avbtool_info_output_minimal() -> None:
    """测试最小有效输出的解析。"""
    output = "Algorithm: SHA256_RSA2048\nFlags: 0\n"
    parsed = parse_avbtool_info_output(output)
    assert parsed["algorithm"] == "SHA256_RSA2048"
    assert parsed["flags"] == 0


# === 2. sync_partition_info_from_stock_avb 测试 ===


def test_sync_partition_info_from_stock_avb(monkeypatch, tmp_path: Path) -> None:
    """测试将 stock AVB 数据同步到 partition_info.json。"""
    monkeypatch.chdir(tmp_path)
    device_dir = tmp_path / "devices/pudding"
    device_dir.mkdir(parents=True)
    (device_dir / "partition_info.json").write_text(
        (
            "{\n"
            '  "device_code": "pudding",\n'
            '  "super_size": 13411287040,\n'
            '  "dynamic_partitions": ["system", "vendor"]\n'
            "}\n"
        ),
        encoding="utf-8",
    )
    stock = tmp_path / "build/stockrom/images"
    stock.mkdir(parents=True)
    (stock / "boot.img").write_bytes(b"x")
    (stock / "pvmfw.img").write_bytes(b"x")
    (stock / "vbmeta.img").write_bytes(b"x")

    manager = _make_avb_manager(tmp_path)

    profile = {
        "hash_parts": {"boot", "pvmfw"},
        "hashtree_parts": {"system"},
        "chain_parts": [("boot", 3)],
    }

    def fake_info(_avbtool, image):
        if image.name == "boot.img":
            return {"image_size": 100663296}
        if image.name == "pvmfw.img":
            return {"image_size": 1048576}
        return {}

    monkeypatch.setattr(manager, "run_avbtool_info_image", fake_info)
    manager.sync_partition_info_from_stock_avb(profile)

    content = (device_dir / "partition_info.json").read_text(encoding="utf-8")
    assert '"boot": 100663296' in content
    assert '"pvmfw": 1048576' in content
    assert '"avb_hash_partitions": [' in content
    assert '"avb_strict_partitions": [' in content


# === 3. calculate_min_partition_size_for_image 测试 ===


def test_calculate_min_partition_size_for_image() -> None:
    """测试二分搜索最小分区大小算法。"""
    manager = _make_avb_manager(Path("/tmp"))

    # 模拟 max_image_size = partition_size - 8192
    manager.calc_avb_max_image_size = lambda _a, _b, p: p - 8192  # type: ignore[method-assign]

    # 需要至少 image_size + 8192，对齐到 4K
    result = manager.calculate_min_partition_size_for_image(
        Path("/tmp/avbtool"), "add_hashtree_footer", image_size=100000
    )
    assert result == 110592


def test_calculate_min_partition_size_retries_on_invalid_probe() -> None:
    """测试二分搜索在小分区探测失败时的重试逻辑。"""
    manager = _make_avb_manager(Path("/tmp"))

    # 模拟 avbtool 对小分区大小返回 None
    manager._try_calc_avb_max_image_size = (  # type: ignore[method-assign]
        lambda _a, _b, p: None if p < 131072 else p - 8192
    )

    result = manager.calculate_min_partition_size_for_image(
        Path("/tmp/avbtool"), "add_hashtree_footer", image_size=100000
    )
    assert result >= 110592


# === 4. apply_avb_to_custom_images 测试 ===


def test_apply_avb_to_custom_images_signs_non_aosp_partitions(monkeypatch, tmp_path: Path) -> None:
    """测试为非 AOSP 分区添加 AVB footer。"""
    monkeypatch.chdir(tmp_path)
    images_out = tmp_path / "out/target/product/pudding/IMAGES"
    images_out.mkdir(parents=True)
    (images_out / "countrycode.img").write_bytes(b"x")
    (images_out / "mi_ext.img").write_bytes(b"x")

    manager = _make_avb_manager(tmp_path)
    manager.shell = MagicMock()
    manager.shell.run = MagicMock()

    monkeypatch.setattr(
        manager,
        "collect_stock_avb_profile",
        lambda: {
            "hash_parts": {"countrycode"},
            "hashtree_parts": {"mi_ext"},
        },
    )
    monkeypatch.setattr(
        manager,
        "calculate_min_partition_size_for_image",
        lambda _avbtool, _footer_cmd, image_size: image_size + 4096,
    )
    monkeypatch.setattr(
        manager,
        "build_footer_props_args",
        lambda part, include_hash_algorithm: [
            "--prop",
            f"com.android.build.{part}.fingerprint:test/fp",
        ],
    )

    manager.apply_avb_to_custom_images(["countrycode", "mi_ext"])

    cmds = [call.args[0] for call in manager.shell.run.call_args_list]
    assert any("add_hash_footer" in cmd for cmd in cmds)
    assert any("add_hashtree_footer" in cmd for cmd in cmds)
    assert any("com.android.build.countrycode.fingerprint:test/fp" in cmd for cmd in cmds)
    assert any("com.android.build.mi_ext.fingerprint:test/fp" in cmd for cmd in cmds)


def test_apply_avb_to_custom_images_chain_partitions_use_stock_size(
    monkeypatch, tmp_path: Path
) -> None:
    """测试 chain 分区使用 stock 镜像大小。"""
    monkeypatch.chdir(tmp_path)
    images_out = tmp_path / "out/target/product/pudding/IMAGES"
    images_out.mkdir(parents=True)
    stock_images = tmp_path / "build/stockrom/images"
    stock_images.mkdir(parents=True)

    (images_out / "boot.img").write_bytes(b"A" * 64 + b"\x00" * 64)
    (stock_images / "boot.img").write_bytes(b"S" * 96)
    (stock_images / "vbmeta.img").write_bytes(b"vbmeta")

    manager = _make_avb_manager(tmp_path)
    manager.shell = MagicMock()
    manager.shell.run = MagicMock()

    monkeypatch.setattr(
        manager,
        "collect_stock_avb_profile",
        lambda: {
            "hash_parts": set(),
            "hashtree_parts": set(),
            "chain_parts": [("boot", 3)],
        },
    )
    key_path = tmp_path / "otatools/security/testkey.pem"
    key_path.parent.mkdir(parents=True)
    key_path.write_text("dummy", encoding="utf-8")
    monkeypatch.setattr(manager, "get_avb_testkey_path", lambda: key_path)
    monkeypatch.setattr("src.core.packing.avb._algorithm_for_key", lambda preferred, _key: preferred)
    monkeypatch.setattr(
        manager,
        "run_avbtool_info_image",
        lambda _avbtool, _image: {},
    )
    monkeypatch.setattr(
        manager,
        "calc_avb_max_image_size",
        lambda _avbtool, _footer_cmd, partition_size: partition_size - 16,
    )
    monkeypatch.setattr(
        manager,
        "build_footer_props_args",
        lambda _part, include_hash_algorithm=False: [],
    )

    manager.apply_avb_to_custom_images(["boot"])

    cmd = manager.shell.run.call_args.args[0]
    partition_size = cmd[cmd.index("--partition_size") + 1]
    assert partition_size == "96"
    assert (images_out / "boot.img").stat().st_size == 128


def test_apply_avb_to_custom_images_physical_hash_partitions_use_stock_size(
    monkeypatch, tmp_path: Path
) -> None:
    """测试物理 hash 分区使用 stock 镜像大小。"""
    monkeypatch.chdir(tmp_path)
    images_out = tmp_path / "out/target/product/pudding/IMAGES"
    images_out.mkdir(parents=True)
    stock_images = tmp_path / "build/stockrom/images"
    stock_images.mkdir(parents=True)

    (images_out / "vendor_boot.img").write_bytes(b"A" * 64 + b"\x00" * 64)
    (stock_images / "vendor_boot.img").write_bytes(b"S" * 96)
    (stock_images / "vbmeta.img").write_bytes(b"vbmeta")

    manager = _make_avb_manager(tmp_path)
    manager.shell = MagicMock()
    manager.shell.run = MagicMock()

    monkeypatch.setattr(
        manager,
        "collect_stock_avb_profile",
        lambda: {
            "hash_parts": {"vendor_boot"},
            "hashtree_parts": set(),
            "chain_parts": [],
        },
    )
    monkeypatch.setattr(
        manager,
        "calculate_min_partition_size_for_image",
        lambda _avbtool, _footer_cmd, _image_size: 128,
    )
    monkeypatch.setattr(
        manager,
        "calc_avb_max_image_size",
        lambda _avbtool, _footer_cmd, partition_size: partition_size - 16,
    )
    monkeypatch.setattr(
        manager,
        "build_footer_props_args",
        lambda _part, include_hash_algorithm=False: [],
    )

    manager.apply_avb_to_custom_images(["vendor_boot"])

    cmd = manager.shell.run.call_args.args[0]
    partition_size = cmd[cmd.index("--partition_size") + 1]
    assert partition_size == "96"
    assert (images_out / "vendor_boot.img").stat().st_size == 128


# === 5. verify_avb_images 测试 ===


def test_verify_avb_images_runs_verify_image(monkeypatch, tmp_path: Path) -> None:
    """测试 AVB 验证执行 verify_image 命令。"""
    monkeypatch.chdir(tmp_path)
    images_out = tmp_path / "out/target/product/pudding/IMAGES"
    images_out.mkdir(parents=True)
    (images_out / "vbmeta.img").write_bytes(b"x")
    avbtool = tmp_path / "otatools/bin/avbtool"
    avbtool.parent.mkdir(parents=True)
    avbtool.write_text("", encoding="utf-8")

    manager = _make_avb_manager(tmp_path)
    manager.shell = MagicMock()
    manager.shell.run = MagicMock()

    manager.verify_avb_images()

    cmd = manager.shell.run.call_args.args[0]
    assert "verify_image" in cmd
    assert "--follow_chain_partitions" in cmd


def test_verify_avb_images_skips_when_vbmeta_missing(monkeypatch, tmp_path: Path) -> None:
    """测试 vbmeta.img 不存在时跳过验证。"""
    monkeypatch.chdir(tmp_path)
    images_out = tmp_path / "out/target/product/pudding/IMAGES"
    images_out.mkdir(parents=True)
    avbtool = tmp_path / "otatools/bin/avbtool"
    avbtool.parent.mkdir(parents=True)
    avbtool.write_text("", encoding="utf-8")

    manager = _make_avb_manager(tmp_path)
    manager.shell = MagicMock()
    manager.shell.run = MagicMock()

    manager.verify_avb_images()

    manager.shell.run.assert_not_called()


# === 6. rebuild_vbmeta_images 测试 ===


def test_rebuild_vbmeta_images_follows_stock_structure(monkeypatch, tmp_path: Path) -> None:
    """测试 vbmeta 重建遵循 stock 结构。"""
    monkeypatch.chdir(tmp_path)
    images_out = tmp_path / "out/target/product/pudding/IMAGES"
    meta_out = tmp_path / "out/target/product/pudding/META"
    images_out.mkdir(parents=True)
    meta_out.mkdir(parents=True)
    for name in (
        "boot.img", "recovery.img", "vbmeta_system.img",
        "system.img", "system_ext.img", "product.img", "dtbo.img",
    ):
        (images_out / name).write_bytes(b"x")

    key_path = tmp_path / "otatools/build/make/target/product/security/testkey.pem"
    key_path.parent.mkdir(parents=True)
    key_path.write_text("dummy", encoding="utf-8")
    avbtool = tmp_path / "otatools/bin/avbtool"
    avbtool.parent.mkdir(parents=True)
    avbtool.write_text("", encoding="utf-8")

    manager = _make_avb_manager(tmp_path)
    manager.shell = MagicMock()
    manager.shell.run = MagicMock()

    monkeypatch.setattr(
        manager,
        "collect_stock_avb_profile",
        lambda: {
            "vbmeta": {"algorithm": "SHA256_RSA4096", "rollback_index": 0, "flags": 0},
            "vbmeta_system": {
                "algorithm": "SHA256_RSA4096",
                "rollback_index": 1767225600,
                "flags": 0,
                "hashtree_partitions": ["system", "system_ext", "product"],
            },
            "hash_parts": {"dtbo"},
            "hashtree_parts": {"system", "system_ext", "product"},
            "chain_parts": [("boot", 3), ("recovery", 1), ("vbmeta_system", 2)],
        },
    )

    def fake_check_output(cmd, text, stderr, **kwargs):
        if isinstance(cmd, list) and "--output" in cmd:
            output_idx = cmd.index("--output")
            Path(cmd[output_idx + 1]).write_bytes(b"pub")
            return ""
        if isinstance(cmd, list) and cmd[:2] == ["openssl", "pkey"]:
            return "Private-Key: (2048 bit, 2 primes)\n"
        return ""

    monkeypatch.setattr("src.core.packing.avb.subprocess.check_output", fake_check_output)

    manager.rebuild_vbmeta_images(
        ["boot", "recovery", "vbmeta_system", "system", "system_ext", "product", "dtbo"]
    )

    all_cmds = [call.args[0] for call in manager.shell.run.call_args_list]
    assert any("make_vbmeta_image" in cmd for cmd in all_cmds)
    top_cmd = all_cmds[-1]
    assert "--chain_partition" in top_cmd
    assert any("boot:3:" in str(part) for part in top_cmd)


# === 7. build_footer_props_args 测试 ===


def test_build_footer_props_args_from_target_props(monkeypatch, tmp_path: Path) -> None:
    """测试从 target build.prop 构建 footer 属性参数。"""
    target_dir = tmp_path / "target"
    (target_dir / "system").mkdir(parents=True)
    (target_dir / "vendor").mkdir(parents=True)
    (target_dir / "system" / "build.prop").write_text(
        "ro.build.version.release=16\n"
        "ro.build.version.security_patch=2026-01-01\n"
        "ro.build.fingerprint=foo/system\n",
        encoding="utf-8",
    )
    (target_dir / "vendor" / "build.prop").write_text(
        "ro.vendor.build.fingerprint=foo/vendor\n",
        encoding="utf-8",
    )

    def get_target_prop_file(part: str):
        p = target_dir / part / "build.prop"
        return p if p.exists() else None

    ctx = SimpleNamespace(
        stock_rom_code="pudding",
        device_config={"pack": {"super_size": 13411287040}},
        target_dir=target_dir,
        get_target_prop_file=get_target_prop_file,
    )
    manager = AVBManager(ctx)
    args = manager.build_footer_props_args("vendor", include_hash_algorithm=True)

    joined = " ".join(args)
    assert "--hash_algorithm sha256" in joined
    assert "com.android.build.vendor.fingerprint:foo/vendor" in joined
    assert "com.android.build.vendor.os_version:16" in joined
    assert "com.android.build.vendor.security_patch:2026-01-01" in joined


# === 8. EventBus 集成测试 ===


def test_avb_manager_publishes_events_on_success(monkeypatch, tmp_path: Path) -> None:
    """测试 AVBManager 在操作成功时发布事件。"""
    monkeypatch.chdir(tmp_path)
    images_out = tmp_path / "out/target/product/pudding/IMAGES"
    images_out.mkdir(parents=True)
    (images_out / "vbmeta.img").write_bytes(b"x")
    avbtool = tmp_path / "otatools/bin/avbtool"
    avbtool.parent.mkdir(parents=True)
    avbtool.write_text("", encoding="utf-8")

    ctx = _make_ctx(tmp_path)
    collector = MetricsCollector()
    event_bus = MagicMock()
    manager = AVBManager(ctx, collector=collector, event_bus=event_bus)
    manager.shell = MagicMock()
    manager.shell.run = MagicMock()

    manager.verify_avb_images()

    # 验证事件总线被调用（MonitoredComponent 和自定义发布都会调用）
    assert event_bus.publish.call_count >= 2
    # 检查包含 phase.start 和 phase.end 事件
    first_arg_strs = []
    for call in event_bus.publish.call_args_list:
        arg = call.args[0]
        if hasattr(arg, "event_type"):
            first_arg_strs.append(arg.event_type)
        elif isinstance(arg, str):
            first_arg_strs.append(arg)
    assert "phase.start" in first_arg_strs
    assert "phase.end" in first_arg_strs


def test_avb_manager_publishes_error_event_on_failure(monkeypatch, tmp_path: Path) -> None:
    """测试 AVBManager 在操作失败时发布错误事件。"""
    monkeypatch.chdir(tmp_path)
    images_out = tmp_path / "out/target/product/pudding/IMAGES"
    images_out.mkdir(parents=True)
    (images_out / "vbmeta.img").write_bytes(b"x")
    avbtool = tmp_path / "otatools/bin/avbtool"
    avbtool.parent.mkdir(parents=True)
    avbtool.write_text("", encoding="utf-8")

    ctx = _make_ctx(tmp_path)
    collector = MetricsCollector()
    event_bus = MagicMock()
    manager = AVBManager(ctx, collector=collector, event_bus=event_bus)
    manager.shell = MagicMock()
    manager.shell.run = MagicMock(side_effect=RuntimeError("验证失败"))

    try:
        manager.verify_avb_images()
    except RuntimeError:
        pass

    # 检查包含 error 事件
    first_arg_strs = []
    for call in event_bus.publish.call_args_list:
        arg = call.args[0]
        if hasattr(arg, "event_type"):
            first_arg_strs.append(arg.event_type)
        elif isinstance(arg, str):
            first_arg_strs.append(arg)
    assert "error" in first_arg_strs


# === 9. 监控指标采集测试 ===


def test_avb_manager_records_metrics_on_footer_applied(monkeypatch, tmp_path: Path) -> None:
    """测试 AVB 添加 footer 时自动采集指标。"""
    monkeypatch.chdir(tmp_path)
    images_out = tmp_path / "out/target/product/pudding/IMAGES"
    images_out.mkdir(parents=True)
    (images_out / "dtbo.img").write_bytes(b"x")

    manager = _make_avb_manager(tmp_path)
    manager.shell = MagicMock()
    manager.shell.run = MagicMock()

    monkeypatch.setattr(
        manager,
        "collect_stock_avb_profile",
        lambda: {"hash_parts": {"dtbo"}, "hashtree_parts": set(), "chain_parts": []},
    )
    monkeypatch.setattr(
        manager,
        "calculate_min_partition_size_for_image",
        lambda _a, _b, image_size: image_size + 4096,
    )
    monkeypatch.setattr(
        manager,
        "build_footer_props_args",
        lambda _part, include_hash_algorithm=False: [],
    )

    manager.apply_avb_to_custom_images(["dtbo"])

    assert manager._collector.get_counter("avb.footer_applied") == 1


def test_avb_manager_records_metrics_on_verify_success(monkeypatch, tmp_path: Path) -> None:
    """测试 AVB 验证成功时自动采集指标。"""
    monkeypatch.chdir(tmp_path)
    images_out = tmp_path / "out/target/product/pudding/IMAGES"
    images_out.mkdir(parents=True)
    (images_out / "vbmeta.img").write_bytes(b"x")
    avbtool = tmp_path / "otatools/bin/avbtool"
    avbtool.parent.mkdir(parents=True)
    avbtool.write_text("", encoding="utf-8")

    manager = _make_avb_manager(tmp_path)
    manager.shell = MagicMock()
    manager.shell.run = MagicMock()

    manager.verify_avb_images()

    assert manager._collector.get_counter("avb.verify_success") == 1


# === 10. 常量模块测试 ===


def test_constants_avb_default_algorithm() -> None:
    """测试默认 AVB 算法常量。"""
    assert AVB_DEFAULT_ALGORITHM == "SHA256_RSA4096"


def test_constants_aosp_avb_partitions() -> None:
    """测试 AOSP AVB 分区集合包含关键分区。"""
    assert "boot" in AOSP_AVB_PARTITIONS
    assert "system" in AOSP_AVB_PARTITIONS
    assert "vendor" in AOSP_AVB_PARTITIONS
    assert "dtbo" in AOSP_AVB_PARTITIONS


def test_constants_device_size_map() -> None:
    """测试设备尺寸映射表包含已知设备。"""
    assert 9663676416 in DEVICE_SIZE_MAP
    assert "FUXI" in DEVICE_SIZE_MAP[9663676416]
    assert "PUDDING" in DEVICE_SIZE_MAP[13411287040]


def test_constants_super_size_default() -> None:
    """测试默认 super 分区大小。"""
    assert SUPER_SIZE_DEFAULT == 9126805504


# === 11. 独立导入测试 ===


def test_avb_manager_import() -> None:
    """测试 AVBManager 可以独立导入。"""
    from src.core.packing.avb import AVBManager as ImportedAVBManager
    assert ImportedAVBManager is AVBManager


def test_parse_avbtool_info_output_import() -> None:
    """测试 parse_avbtool_info_output 可以独立导入。"""
    from src.core.packing.avb import parse_avbtool_info_output as imported_func
    assert imported_func is parse_avbtool_info_output
