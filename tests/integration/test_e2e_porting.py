"""端到端集成测试。

使用 mock 模拟完整移植流程，验证从解包到打包的完整链路。
确保所有阶段按正确顺序执行，上下文正确传递。
"""

import logging
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call

import pytest

from src.core.workflow.orchestrator import PortingOrchestrator
from src.core.workflow.pipeline import Pipeline, PipelineResult
from src.core.workflow.phases import (
    ExtractionPhase,
    InitializationPhase,
    ModificationPhase,
    PackingPhase,
)

from tests.integration.conftest import (
    ContextChainingPhase,
    FailingPhase,
    RecordingPhase,
    SlowPhase,
    make_mock_porting_context,
    make_mock_rom,
)


class TestEndToEndPortingFlow:
    """端到端移植流程集成测试。"""

    def test_full_four_phase_pipeline_executes_in_order(self):
        """验证四阶段 Pipeline 按 extraction -> initialization -> modification -> repack 顺序执行。"""
        log = []
        pipeline = Pipeline()
        pipeline.add_phase(RecordingPhase("extraction", log))
        pipeline.add_phase(RecordingPhase("initialization", log))
        pipeline.add_phase(RecordingPhase("modification", log))
        pipeline.add_phase(RecordingPhase("repack", log))

        result = pipeline.run({})

        assert result.success is True
        assert log == ["extraction", "initialization", "modification", "repack"]
        assert result.completed_phases == ["extraction", "initialization", "modification", "repack"]

    def test_orchestrator_default_pipeline_matches_four_phases(self):
        """验证 PortingOrchestrator 默认创建四阶段 Pipeline。"""
        orchestrator = PortingOrchestrator()
        pipeline = orchestrator._build_pipeline()
        phase_names = [p.name for p in pipeline._phases]

        assert phase_names == ["extraction", "initialization", "modification", "repack"]

    def test_context_flows_through_all_phases(self):
        """验证上下文在所有阶段间正确传递和累积。"""
        pipeline = Pipeline()
        pipeline.add_phase(ContextChainingPhase("extraction", "stock_rom", "mock_stock"))
        pipeline.add_phase(ContextChainingPhase("initialization", "porting_ctx", "mock_ctx"))
        pipeline.add_phase(ContextChainingPhase("modification", "modified", True))
        pipeline.add_phase(ContextChainingPhase("repack", "packed", True))

        result = pipeline.run({"input": "test"})

        assert result.success is True
        assert result.context["input"] == "test"
        assert result.context["stock_rom"] == "mock_stock"
        assert result.context["porting_ctx"] == "mock_ctx"
        assert result.context["modified"] is True
        assert result.context["packed"] is True

    def test_orchestrator_with_custom_phases(self):
        """验证编排器支持自定义阶段列表。"""
        log = []
        orchestrator = PortingOrchestrator(
            phases=[
                RecordingPhase("extract", log),
                RecordingPhase("modify", log),
            ]
        )
        result = orchestrator.run({"test": True})

        assert result.success is True
        assert log == ["extract", "modify"]

    def test_orchestrator_failure_triggers_rollback(self):
        """验证编排器在阶段失败时触发回滚。"""
        rollback_log = []
        phases = [
            RecordingPhase("A", [], ["done"]),
            RecordingPhase("B", [], ["done"]),
            FailingPhase("C"),
        ]
        # 给 RecordingPhase 添加 rollback 记录
        for phase in phases[:2]:
            original_rollback = phase.rollback
            def make_rollback(p):
                def rollback_with_log(ctx):
                    rollback_log.append(p.name)
                    return ctx
                return rollback_with_log
            phase.rollback = make_rollback(phase)

        orchestrator = PortingOrchestrator(phases=phases)
        result = orchestrator.run({})

        assert result.success is False
        assert result.failed_phase == "C"
        # 回滚应按逆序执行
        assert rollback_log == ["B", "A"]

    def test_orchestrator_records_duration(self):
        """验证编排器记录总执行时间。"""
        orchestrator = PortingOrchestrator(
            phases=[SlowPhase("slow", duration=0.05)]
        )
        result = orchestrator.run({})

        assert result.success is True
        assert result.total_duration >= 0.04

    def test_orchestrator_exception_returns_failure_result(self):
        """验证编排器捕获异常并返回失败结果。"""

        class ExceptionPhase:
            name = "crash"
            description = "崩溃阶段"

            def execute(self, context):
                raise ValueError("意外错误")

            def rollback(self, context):
                return context

        # 测试 Pipeline 的异常处理（Pipeline 内部捕获）
        pipeline = Pipeline()
        pipeline.add_phase(ExceptionPhase())
        result = pipeline.run({})

        assert result.success is False
        assert isinstance(result.error, ValueError)

    def test_official_modify_mode_context_setup(self):
        """验证官改模式下 Port ROM 复用 Stock ROM。"""
        mock_stock = make_mock_rom("Stock", "fuxi")

        ctx = {
            "stock_rom_path": "/tmp/stock.zip",
            "port_rom_path": "/tmp/stock.zip",
            "stock_work_dir": Path("/tmp/stockrom"),
            "port_work_dir": Path("/tmp/portrom"),
            "is_official_modify": True,
        }

        phase = ExtractionPhase()
        with patch("src.core.rom.RomPackage", return_value=mock_stock) as mock_cls:
            result = phase.execute(ctx)

        # 官改模式只创建一个 RomPackage
        assert mock_cls.call_count == 1
        assert result["stock_rom"] is result["port_rom"]

    def test_modification_phase_dispatches_to_correct_modifiers(self):
        """验证修改阶段根据 phases_to_run 正确分发到各修改器。"""
        mock_ctx = MagicMock()
        ctx = {
            "porting_context": mock_ctx,
            "phases_to_run": ["system", "framework", "firmware"],
        }

        phase = ModificationPhase()
        with (
            patch("src.core.modifiers.UnifiedModifier") as mock_unified,
            patch("src.core.modifiers.FrameworkModifier") as mock_framework,
            patch("src.core.modifiers.FirmwareModifier") as mock_firmware,
            patch("src.core.modifiers.RomModifier") as mock_rom,
        ):
            mock_unified.return_value.run.return_value = True
            result = phase.execute(ctx)

        mock_unified.assert_called_once_with(mock_ctx, enable_apk_mods=False)
        mock_framework.assert_called_once_with(mock_ctx)
        mock_firmware.assert_called_once_with(mock_ctx)
        mock_rom.assert_called_once_with(mock_ctx)
        assert result.get("modified") is True

    def test_packing_phase_selects_correct_output_format(self):
        """验证打包阶段根据 pack_type 选择正确的输出格式。"""
        mock_ctx = MagicMock()
        mock_repacker = MagicMock()

        # 测试 payload 格式
        ctx_payload = {
            "porting_context": mock_ctx,
            "phases_to_run": ["repack"],
            "pack_type": "payload",
            "fs_type": "erofs",
        }
        phase = PackingPhase()
        with patch("src.core.packer.Repacker", return_value=mock_repacker):
            phase.execute(ctx_payload)

        mock_repacker.pack_ota_payload.assert_called_once()
        mock_repacker.pack_super_image.assert_not_called()

        # 重置 mock
        mock_repacker.reset_mock()

        # 测试 super 格式
        ctx_super = {
            "porting_context": mock_ctx,
            "phases_to_run": ["repack"],
            "pack_type": "super",
            "fs_type": "erofs",
        }
        with patch("src.core.packer.Repacker", return_value=mock_repacker):
            phase.execute(ctx_super)

        mock_repacker.pack_super_image.assert_called_once()
        mock_repacker.pack_ota_payload.assert_not_called()


class TestOrchestratorEventBusIntegration:
    """编排器与 EventBus 集成测试。"""

    def test_orchestrator_publishes_all_phase_events(self, event_bus, event_collector):
        """验证编排器为每个阶段发布 start 和 end 事件。"""
        phases = [
            RecordingPhase("A", []),
            RecordingPhase("B", []),
            RecordingPhase("C", []),
        ]
        orchestrator = PortingOrchestrator(event_bus=event_bus, phases=phases)
        orchestrator.run({})

        start_events = [e for e in event_collector if e.event_type == "phase.start"]
        end_events = [e for e in event_collector if e.event_type == "phase.end"]

        assert len(start_events) == 3
        assert len(end_events) == 3
        assert [e.phase_name for e in start_events] == ["A", "B", "C"]
        assert [e.phase_name for e in end_events] == ["A", "B", "C"]

    def test_orchestrator_publishes_error_event_on_failure(self, event_bus, event_collector):
        """验证编排器在阶段失败时发布 ErrorEvent。"""
        orchestrator = PortingOrchestrator(
            event_bus=event_bus,
            phases=[FailingPhase("bad")]
        )
        orchestrator.run({})

        error_events = [e for e in event_collector if e.event_type == "error"]
        assert len(error_events) >= 1
        assert error_events[0].phase == "bad"

    def test_orchestrator_end_events_mark_failure(self, event_bus, event_collector):
        """验证失败阶段的 end 事件标记 success=False。"""
        orchestrator = PortingOrchestrator(
            event_bus=event_bus,
            phases=[RecordingPhase("ok", []), FailingPhase("fail")]
        )
        orchestrator.run({})

        end_events = [e for e in event_collector if e.event_type == "phase.end"]
        ok_event = next(e for e in end_events if e.phase_name == "ok")
        fail_event = next(e for e in end_events if e.phase_name == "fail")

        assert ok_event.success is True
        assert fail_event.success is False

    def test_orchestrator_events_include_duration(self, event_bus, event_collector):
        """验证阶段事件包含执行时间。"""
        orchestrator = PortingOrchestrator(
            event_bus=event_bus,
            phases=[SlowPhase("slow", duration=0.02)]
        )
        orchestrator.run({})

        end_events = [e for e in event_collector if e.event_type == "phase.end"]
        assert len(end_events) == 1
        assert end_events[0].duration >= 0.01


class TestOrchestratorMonitorIntegration:
    """编排器与监控系统集成测试。"""

    def test_orchestrator_collects_phase_metrics(self):
        """验证编排器通过 Monitor 采集阶段执行指标。"""
        from src.core.monitoring import Monitor

        monitor = Monitor()
        monitor.start()

        orchestrator = PortingOrchestrator(
            monitor=monitor,
            phases=[RecordingPhase("X", []), RecordingPhase("Y", [])]
        )
        orchestrator.run({})

        monitor.stop()
        report = monitor.report.generate()

        assert "X" in report["phase_results"]
        assert "Y" in report["phase_results"]

    def test_orchestrator_records_duration_metrics(self):
        """验证编排器记录每个阶段的持续时间指标。"""
        from src.core.monitoring import Monitor

        monitor = Monitor()
        monitor.start()

        orchestrator = PortingOrchestrator(
            monitor=monitor,
            phases=[SlowPhase("timed", duration=0.02)]
        )
        orchestrator.run({})

        monitor.stop()
        # 监控系统应记录了持续时间
        report = monitor.report.generate()
        assert "timed" in report["phase_results"]
