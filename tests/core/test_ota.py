import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from src.core.packing.ota import OTAPacker


def _make_ctx(tmp_path, device_code="test_device", device_config=None):
    ctx = Mock()
    ctx.stock_rom_code = device_code
    ctx.target_dir = tmp_path / "target"
    ctx.target_dir.mkdir(exist_ok=True)
    ctx.device_config = device_config or {}
    return ctx


class TestOTAPackerInit:

    def test_basic_init(self, tmp_path):
        packer = OTAPacker(context=_make_ctx(tmp_path))
        assert packer.ctx.stock_rom_code == "test_device"
        assert packer.logger is not None

    def test_init_with_event_bus(self, tmp_path):
        from src.core.events import EventBus
        bus = EventBus()
        packer = OTAPacker(context=_make_ctx(tmp_path), event_bus=bus)
        assert packer._event_bus is bus

    def test_init_with_collector(self, tmp_path):
        from src.core.monitoring import MetricsCollector
        collector = MetricsCollector()
        packer = OTAPacker(context=_make_ctx(tmp_path), collector=collector)
        assert packer._collector is collector


class TestPayloadGenerator:

    def test_payload_generator_exists(self, tmp_path):
        packer = OTAPacker(context=_make_ctx(tmp_path))
        generator = packer.PayloadGenerator(packer)
        assert generator._packer is packer
        assert hasattr(generator, "run")


class TestMetaInfoGeneration:

    def test_creates_all_meta_files(self, tmp_path):
        packer = OTAPacker(context=_make_ctx(tmp_path, device_config={"pack": {"super_size": 9663676416}}))
        packer.images_out.mkdir(parents=True, exist_ok=True)
        packer.meta_out.mkdir(parents=True, exist_ok=True)
        for part in ["system", "vendor", "product", "boot"]:
            (packer.images_out / f"{part}.img").write_bytes(b"\x00" * 1024)

        with patch.object(packer, "_is_vabc_enabled", return_value=False), \
             patch.object(packer, "_avb_misc_lines", return_value=[]):
            packer._generate_meta_info()

        assert (packer.meta_out / "ab_partitions.txt").exists()
        assert (packer.meta_out / "dynamic_partitions_info.txt").exists()
        assert (packer.meta_out / "misc_info.txt").exists()
        assert (packer.meta_out / "update_engine_config.txt").exists()

    def test_ab_partitions_excludes_cust(self, tmp_path):
        packer = OTAPacker(context=_make_ctx(tmp_path, device_config={"pack": {"super_size": 9663676416}}))
        packer.images_out.mkdir(parents=True, exist_ok=True)
        packer.meta_out.mkdir(parents=True, exist_ok=True)
        for part in ["system", "vendor", "boot", "cust"]:
            (packer.images_out / f"{part}.img").write_bytes(b"\x00" * 1024)

        with patch.object(packer, "_is_vabc_enabled", return_value=False), \
             patch.object(packer, "_avb_misc_lines", return_value=[]):
            packer._generate_meta_info()

        content = (packer.meta_out / "ab_partitions.txt").read_text()
        assert "system" in content
        assert "cust" not in content


class TestBuildPropCopying:

    def test_copies_build_prop_to_product_out(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        packer = OTAPacker(context=ctx)
        packer.product_out.mkdir(parents=True, exist_ok=True)

        prop_file = tmp_path / "system_build.prop"
        prop_file.write_text("ro.build.version=test\n", encoding="utf-8")
        ctx.get_target_prop_file = lambda part: prop_file if part == "system" else None

        packer._copy_build_props()

        dest = packer.product_out / "SYSTEM" / "build.prop"
        assert dest.exists()
        assert dest.read_text() == "ro.build.version=test\n"


class TestOTAToolExecution:

    @patch("src.utils.shell.ShellRunner.run")
    def test_run_ota_tool_invokes_shell(self, mock_run, tmp_path):
        ctx = _make_ctx(tmp_path)
        ctx.target_rom_version = "V1.0"
        ctx.security_patch = "2025-01-01"
        ctx.port_android_version = "15"

        packer = OTAPacker(context=ctx)
        packer.out_dir = tmp_path / "out"
        packer.product_out = packer.out_dir / "target" / "product" / ctx.stock_rom_code
        packer.product_out.mkdir(parents=True, exist_ok=True)
        packer.out_dir.mkdir(parents=True, exist_ok=True)

        output_zip = packer.out_dir / f"{ctx.stock_rom_code}-ota_full-20250101120000.zip"
        output_zip.write_bytes(b"fake ota content")

        with patch("src.core.packing.ota.datetime") as mock_dt:
            mock_dt.now.return_value.strftime.return_value = "20250101120000"
            mock_dt.now.return_value = mock_dt.now.return_value
            packer._run_ota_tool()

        assert mock_run.called


class TestEventBusIntegration:

    def test_event_bus_linked(self, tmp_path):
        from src.core.events import EventBus
        bus = EventBus()
        packer = OTAPacker(context=_make_ctx(tmp_path), event_bus=bus)
        assert packer._event_bus is bus


class TestMonitoringIntegration:

    def test_metrics_collector_tracks_execution(self):
        from src.core.monitoring import MetricsCollector, MonitoredComponent

        collector = MetricsCollector()

        class TestComponent(MonitoredComponent):
            def __init__(self):
                super().__init__(collector=collector, name="ota_test")

            def execute(self):
                with self.track_execution():
                    return "done"

        result = TestComponent().execute()
        assert result == "done"
        assert any("ota_test" in m.name for m in collector.get_metrics())


class TestPartitionListDetection:

    def test_from_device_config(self, tmp_path):
        packer = OTAPacker(context=_make_ctx(tmp_path, device_config={"pack": {"partitions": ["system", "vendor"]}}))
        assert packer._get_partition_list() == ["system", "vendor"]

    def test_default_fallback(self, tmp_path):
        packer = OTAPacker(context=_make_ctx(tmp_path, device_code="nonexistent_device"))
        with patch("pathlib.Path.exists", return_value=False):
            partitions = packer._get_partition_list()
        assert "system" in partitions
        assert "vendor" in partitions
