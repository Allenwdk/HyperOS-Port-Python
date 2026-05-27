"""移植流程 Phase 类单元测试。

测试 ExtractionPhase、InitializationPhase、ModificationPhase、PackingPhase
的实际业务逻辑，包括 EventBus 集成和监控集成。
"""

import time
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch, call

import pytest

from src.core.workflow.phases import (
    ExtractionPhase,
    InitializationPhase,
    ModificationPhase,
    PackingPhase,
    Phase,
    InitPhase,
    ModifyPhase,
    PackPhase,
)
from src.core.events.bus import EventBus
from src.core.events.events import PhaseStartEvent, PhaseEndEvent


def _make_mock_rom(label: str = "Stock") -> MagicMock:
    rom = MagicMock()
    rom.label = label
    rom.rom_type = MagicMock()
    rom.rom_type.name = "PAYLOAD"
    rom.props = {"ro.product.name_for_attestation": "test_device"}
    rom.payload_info = {}
    rom.get_prop.return_value = "test_device"
    rom.extract_images.return_value = None
    rom.export_props.return_value = None
    return rom


def _make_context(**overrides) -> dict:
    ctx = {
        "stock_rom_path": "/tmp/stock.zip",
        "port_rom_path": "/tmp/port.zip",
        "stock_work_dir": Path("/tmp/work/stockrom"),
        "port_work_dir": Path("/tmp/work/portrom"),
        "target_work_dir": Path("/tmp/work/target"),
        "work_dir": Path("/tmp/work"),
        "is_official_modify": False,
        "cache_manager": None,
        "phases_to_run": ["system", "apk", "framework", "firmware"],
        "pack_type": "payload",
        "fs_type": "erofs",
        "args": MagicMock(),
    }
    ctx.update(overrides)
    return ctx


class TestExtractionPhase:
    """测试 ExtractionPhase ROM 解包逻辑。"""

    def test_extraction_phase_name_and_description(self):
        phase = ExtractionPhase()
        assert phase.name == "extraction"
        assert "解压" in phase.description or "ROM" in phase.description

    def test_extraction_creates_stock_rom_package(self):
        phase = ExtractionPhase()
        ctx = _make_context()
        mock_stock = _make_mock_rom("Stock")
        mock_port = _make_mock_rom("Port")

        with patch("src.core.rom.RomPackage", side_effect=[mock_stock, mock_port]) as mock_cls:
            result = phase.execute(ctx)

        assert mock_cls.call_count == 2
        mock_stock.extract_images.assert_called_once()
        assert result["stock_rom"] is mock_stock

    def test_extraction_creates_port_rom_package(self):
        phase = ExtractionPhase()
        ctx = _make_context()
        mock_stock = _make_mock_rom("Stock")
        mock_port = _make_mock_rom("Port")

        with patch("src.core.rom.RomPackage", side_effect=[mock_stock, mock_port]) as mock_cls:
            result = phase.execute(ctx)

        assert mock_cls.call_count == 2
        assert result["port_rom"] is mock_port
        mock_port.extract_images.assert_called_once()

    def test_extraction_official_modify_uses_stock_as_port(self):
        phase = ExtractionPhase()
        ctx = _make_context(is_official_modify=True)
        mock_stock = _make_mock_rom("Stock")

        with patch("src.core.rom.RomPackage", return_value=mock_stock) as mock_cls:
            result = phase.execute(ctx)

        assert mock_cls.call_count == 1
        assert result["stock_rom"] is result["port_rom"]

    def test_extraction_sets_done_flag(self):
        phase = ExtractionPhase()
        ctx = _make_context()

        with patch("src.core.rom.RomPackage", return_value=_make_mock_rom()):
            result = phase.execute(ctx)

        assert result.get("extraction_done") is True

    def test_extraction_rollback_clears_state(self):
        phase = ExtractionPhase()
        ctx = _make_context(extraction_done=True, stock_rom=_make_mock_rom())
        result = phase.rollback(ctx)
        assert "extraction_done" not in result


class TestInitializationPhase:
    """测试 InitializationPhase 上下文初始化和分区安装。"""

    def test_initialization_phase_name_and_description(self):
        phase = InitializationPhase()
        assert phase.name == "initialization"
        assert "初始化" in phase.description

    def test_initialization_creates_porting_context(self):
        phase = InitializationPhase()
        mock_stock = _make_mock_rom("Stock")
        mock_port = _make_mock_rom("Port")
        ctx = _make_context(stock_rom=mock_stock, port_rom=mock_port)
        mock_porting_ctx = MagicMock()

        with patch("src.core.context.PortingContext", return_value=mock_porting_ctx), \
             patch("src.core.device_auto_config.get_or_create_device_config", return_value={}):
            result = phase.execute(ctx)

        mock_porting_ctx.initialize_target.assert_called_once()
        assert result["porting_context"] is mock_porting_ctx

    def test_initialization_sets_cache_manager(self):
        phase = InitializationPhase()
        mock_cache = MagicMock()
        mock_stock = _make_mock_rom("Stock")
        mock_port = _make_mock_rom("Port")
        ctx = _make_context(stock_rom=mock_stock, port_rom=mock_port, cache_manager=mock_cache)
        mock_porting_ctx = MagicMock()

        with patch("src.core.context.PortingContext", return_value=mock_porting_ctx), \
             patch("src.core.device_auto_config.get_or_create_device_config", return_value={}):
            result = phase.execute(ctx)

        assert mock_porting_ctx.cache_manager is mock_cache

    def test_initialization_handles_device_config(self):
        phase = InitializationPhase()
        mock_stock = _make_mock_rom("Stock")
        mock_port = _make_mock_rom("Port")
        ctx = _make_context(stock_rom=mock_stock, port_rom=mock_port)
        mock_porting_ctx = MagicMock()
        mock_device_config = {"pack": {"type": "payload"}}

        with patch("src.core.context.PortingContext", return_value=mock_porting_ctx), \
             patch("src.core.device_auto_config.get_or_create_device_config", return_value=mock_device_config):
            result = phase.execute(ctx)

        assert result["porting_context"] is mock_porting_ctx

    def test_initialization_sets_done_flag(self):
        phase = InitializationPhase()
        mock_stock = _make_mock_rom("Stock")
        mock_port = _make_mock_rom("Port")
        ctx = _make_context(stock_rom=mock_stock, port_rom=mock_port)

        with patch("src.core.context.PortingContext", return_value=MagicMock()), \
             patch("src.core.device_auto_config.get_or_create_device_config", return_value={}):
            result = phase.execute(ctx)

        assert result.get("initialized") is True


class TestModificationPhase:
    """测试 ModificationPhase ROM 修改逻辑。"""

    def test_modification_phase_name_and_description(self):
        phase = ModificationPhase()
        assert phase.name == "modification"
        assert "修改" in phase.description

    def test_modification_runs_unified_modifier(self):
        phase = ModificationPhase()
        mock_ctx = MagicMock()
        ctx = _make_context(porting_context=mock_ctx, phases_to_run=["system", "apk"])
        mock_unified = MagicMock()
        mock_unified.run.return_value = True

        with patch("src.core.modifiers.UnifiedModifier", return_value=mock_unified), \
             patch("src.core.modifiers.FrameworkModifier"), \
             patch("src.core.modifiers.FirmwareModifier"), \
             patch("src.core.modifiers.RomModifier"):
            result = phase.execute(ctx)

        mock_unified.run.assert_called_once()
        assert result.get("modified") is True

    def test_modification_runs_framework_modifier(self):
        phase = ModificationPhase()
        mock_ctx = MagicMock()
        ctx = _make_context(porting_context=mock_ctx, phases_to_run=["framework"])
        mock_framework = MagicMock()

        with patch("src.core.modifiers.UnifiedModifier"), \
             patch("src.core.modifiers.FrameworkModifier", return_value=mock_framework), \
             patch("src.core.modifiers.FirmwareModifier"), \
             patch("src.core.modifiers.RomModifier"):
            result = phase.execute(ctx)

        mock_framework.run.assert_called_once()

    def test_modification_runs_firmware_modifier(self):
        phase = ModificationPhase()
        mock_ctx = MagicMock()
        ctx = _make_context(porting_context=mock_ctx, phases_to_run=["firmware"])
        mock_firmware = MagicMock()

        with patch("src.core.modifiers.UnifiedModifier"), \
             patch("src.core.modifiers.FrameworkModifier"), \
             patch("src.core.modifiers.FirmwareModifier", return_value=mock_firmware), \
             patch("src.core.modifiers.RomModifier"):
            result = phase.execute(ctx)

        mock_firmware.run.assert_called_once()

    def test_modification_always_runs_rom_modifier(self):
        phase = ModificationPhase()
        mock_ctx = MagicMock()
        ctx = _make_context(porting_context=mock_ctx, phases_to_run=[])
        mock_rom = MagicMock()

        with patch("src.core.modifiers.UnifiedModifier"), \
             patch("src.core.modifiers.FrameworkModifier"), \
             patch("src.core.modifiers.FirmwareModifier"), \
             patch("src.core.modifiers.RomModifier", return_value=mock_rom):
            result = phase.execute(ctx)

        mock_rom.run_all_modifications.assert_called_once()


class TestPackingPhase:
    """测试 PackingPhase 重打包逻辑。"""

    def test_packing_phase_name_and_description(self):
        phase = PackingPhase()
        assert phase.name == "repack"
        assert "打包" in phase.description

    def test_packing_calls_repacker_pack_all(self):
        phase = PackingPhase()
        mock_ctx = MagicMock()
        ctx = _make_context(
            porting_context=mock_ctx, phases_to_run=["repack"],
            pack_type="payload", fs_type="erofs",
        )
        mock_repacker = MagicMock()

        with patch("src.core.packer.Repacker", return_value=mock_repacker):
            phase.execute(ctx)

        mock_repacker.pack_all.assert_called_once()

    def test_packing_generates_ota_payload(self):
        phase = PackingPhase()
        mock_ctx = MagicMock()
        ctx = _make_context(
            porting_context=mock_ctx, phases_to_run=["repack"],
            pack_type="payload", fs_type="erofs",
        )
        mock_repacker = MagicMock()

        with patch("src.core.packer.Repacker", return_value=mock_repacker):
            phase.execute(ctx)

        mock_repacker.pack_ota_payload.assert_called_once()
        mock_repacker.pack_super_image.assert_not_called()

    def test_packing_generates_super_image(self):
        phase = PackingPhase()
        mock_ctx = MagicMock()
        ctx = _make_context(
            porting_context=mock_ctx, phases_to_run=["repack"],
            pack_type="super", fs_type="erofs",
        )
        mock_repacker = MagicMock()

        with patch("src.core.packer.Repacker", return_value=mock_repacker):
            phase.execute(ctx)

        mock_repacker.pack_super_image.assert_called_once()
        mock_repacker.pack_ota_payload.assert_not_called()

    def test_packing_sets_done_flag(self):
        phase = PackingPhase()
        mock_ctx = MagicMock()
        ctx = _make_context(porting_context=mock_ctx, phases_to_run=["repack"])

        with patch("src.core.packer.Repacker", return_value=MagicMock()):
            result = phase.execute(ctx)

        assert result.get("packed") is True


class TestPhaseEventBusIntegration:
    """测试 Phase 类与 EventBus 集成。"""

    def test_extraction_phase_publishes_events_via_pipeline(self):
        from src.core.workflow.pipeline import Pipeline

        bus = EventBus()
        start_events = []
        end_events = []
        bus.subscribe("phase.start", lambda e: start_events.append(e))
        bus.subscribe("phase.end", lambda e: end_events.append(e))

        pipeline = Pipeline(event_bus=bus)
        phase = ExtractionPhase()

        with patch("src.core.rom.RomPackage", return_value=_make_mock_rom()):
            pipeline.add_phase(phase)
            pipeline.run(_make_context())

        assert len(start_events) >= 1
        assert start_events[0].phase_name == "extraction"
        assert len(end_events) >= 1
        assert end_events[0].phase_name == "extraction"


class TestBackwardCompatibility:
    """测试旧类名的向后兼容性。"""

    def test_init_phase_alias_exists(self):
        assert InitPhase is not None
        phase = InitPhase()
        assert phase.name == "initialization"

    def test_modify_phase_alias_exists(self):
        assert ModifyPhase is not None
        phase = ModifyPhase()
        assert phase.name == "modification"

    def test_pack_phase_alias_exists(self):
        assert PackPhase is not None
        phase = PackPhase()
        assert phase.name == "repack"


class TestPhaseInheritance:
    """测试 Phase 类继承关系。"""

    def test_extraction_phase_is_phase_subclass(self):
        assert issubclass(ExtractionPhase, Phase)

    def test_initialization_phase_is_phase_subclass(self):
        assert issubclass(InitializationPhase, Phase)

    def test_modification_phase_is_phase_subclass(self):
        assert issubclass(ModificationPhase, Phase)

    def test_packing_phase_is_phase_subclass(self):
        assert issubclass(PackingPhase, Phase)
