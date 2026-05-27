"""Repacker 协调器测试 - 验证公共 API 兼容性和子组件协调。"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.core.packing.repacker import Repacker


def _make_ctx(tmp_path: Path) -> MagicMock:
    ctx = MagicMock()
    ctx.stock_rom_code = "test_device"
    ctx.target_dir = tmp_path / "target"
    ctx.target_dir.mkdir(parents=True, exist_ok=True)
    ctx.target_config_dir = ctx.target_dir / "config"
    ctx.target_config_dir.mkdir(parents=True, exist_ok=True)
    ctx.repack_images_dir = ctx.target_dir / "repack_images"
    ctx.repack_images_dir.mkdir(parents=True, exist_ok=True)
    ctx.is_ab_device = True
    ctx.device_config = {}
    ctx.target_rom_version = "1.0.0"
    ctx.security_patch = "2025-01-01"
    ctx.base_android_version = "15"
    ctx.port_android_version = "15"
    ctx.is_port_eu_rom = False
    ctx.is_port_global_rom = False
    ctx.port_global_region = ""
    ctx.enable_custom_avb_chain = False
    ctx.enable_ksu = False
    ctx.get_target_prop_file = MagicMock(return_value=None)
    return ctx


class TestRepackerImport:
    def test_import_from_packing_module(self):
        from src.core.packing import Repacker as ImportedRepacker
        assert ImportedRepacker is Repacker

    def test_instantiation(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        repacker = Repacker(ctx)
        assert repacker.ctx is ctx
        assert repacker.logger is not None

    def test_has_public_api_methods(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        repacker = Repacker(ctx)
        assert hasattr(repacker, "pack_all") and callable(repacker.pack_all)
        assert hasattr(repacker, "pack_super_image") and callable(repacker.pack_super_image)
        assert hasattr(repacker, "pack_ota_payload") and callable(repacker.pack_ota_payload)


class TestRepackerCoordination:
    def test_creates_avb_manager(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        repacker = Repacker(ctx)
        from src.core.packing.avb import AVBManager
        assert isinstance(repacker._avb, AVBManager)

    def test_creates_ota_packer(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        repacker = Repacker(ctx)
        from src.core.packing.ota import OTAPacker
        assert isinstance(repacker._ota, OTAPacker)

    def test_creates_super_builder(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        repacker = Repacker(ctx)
        from src.core.packing.super import SuperImageBuilder
        assert isinstance(repacker._super, SuperImageBuilder)

    def test_has_event_bus(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        repacker = Repacker(ctx)
        assert repacker._event_bus is not None

    def test_shares_metrics_collector(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        repacker = Repacker(ctx)
        collector = repacker._collector
        assert collector is not None
        assert repacker._avb._collector is collector
        assert repacker._ota._collector is collector
        assert repacker._super._collector is collector


class TestPackAll:
    @patch("src.core.packing.repacker.Repacker._pack_partition")
    def test_pack_all_calls_pack_partition_for_each_dir(self, mock_pack, tmp_path):
        ctx = _make_ctx(tmp_path)
        for name in ["system", "vendor", "product"]:
            (ctx.target_dir / name).mkdir()

        repacker = Repacker(ctx)
        repacker.pack_all(pack_type="EROFS", is_rw=False)

        assert mock_pack.call_count == 3
        called_parts = {call.args[0] for call in mock_pack.call_args_list}
        assert called_parts == {"system", "vendor", "product"}

    @patch("src.core.packing.repacker.Repacker._pack_partition")
    def test_pack_all_skips_config_and_repack_images(self, mock_pack, tmp_path):
        ctx = _make_ctx(tmp_path)
        (ctx.target_dir / "system").mkdir()

        repacker = Repacker(ctx)
        repacker.pack_all(pack_type="EROFS", is_rw=False)

        assert mock_pack.call_count == 1
        assert mock_pack.call_args.args[0] == "system"

    @patch("src.core.packing.repacker.Repacker._pack_partition")
    def test_pack_all_uses_thread_pool(self, mock_pack, tmp_path):
        ctx = _make_ctx(tmp_path)
        for name in ["system", "vendor"]:
            (ctx.target_dir / name).mkdir()

        repacker = Repacker(ctx)
        repacker.pack_all(pack_type="EROFS", is_rw=False)

        assert mock_pack.call_count == 2


class TestPackSuperImage:
    def test_delegates_to_super_builder(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        repacker = Repacker(ctx)
        repacker._super.build = MagicMock()
        repacker.pack_super_image()
        repacker._super.build.assert_called_once()


class TestPackOtaPayload:
    def test_delegates_to_ota_packer(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        repacker = Repacker(ctx)
        repacker._ota.pack = MagicMock()
        repacker.pack_ota_payload()
        repacker._ota.pack.assert_called_once()


class TestMonitoringIntegration:
    def test_is_monitored_component(self, tmp_path):
        from src.core.monitoring import MonitoredComponent
        ctx = _make_ctx(tmp_path)
        repacker = Repacker(ctx)
        assert isinstance(repacker, MonitoredComponent)

    def test_has_track_execution(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        repacker = Repacker(ctx)
        with repacker.track_execution("test"):
            pass
