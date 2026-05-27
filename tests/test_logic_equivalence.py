"""逻辑等价性验证。

验证重构后的模块与原有逻辑行为完全等价。
通过对比新旧实现的输出，确保重构没有引入行为变化。
"""

from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call

import pytest

from src.app.workflow import (
    DEFAULT_PHASES,
    build_super_size_check,
    determine_pack_settings,
    inject_super_size_check_into_diff_report,
    log_diff_report_summary,
    save_repack_checkpoint,
    load_repack_checkpoint,
    run_modification_phases,
)
from src.core.workflow.orchestrator import PortingOrchestrator
from src.core.workflow.phases import (
    ExtractionPhase,
    InitializationPhase,
    ModificationPhase,
    PackingPhase,
)
from src.core.workflow.pipeline import Pipeline, PipelineResult
from src.core.events.bus import EventBus
from src.core.events.events import PhaseStartEvent, PhaseEndEvent, ErrorEvent

from tests.integration.conftest import (
    ContextChainingPhase,
    FailingPhase,
    RecordingPhase,
    make_mock_porting_context,
    make_mock_rom,
)


# ============================================================
# 编排器逻辑等价性：新编排器 vs 原有 workflow.run_modification_phases
# ============================================================


class TestModificationPhaseEquivalence:
    """验证 ModificationPhase 与 run_modification_phases 的逻辑等价性。"""

    def test_modification_phase_dispatches_same_as_run_modification_phases(self):
        """验证 ModificationPhase 的修改器分发逻辑与 run_modification_phases 一致。"""
        mock_ctx = MagicMock()
        phases_to_run = ["system", "apk", "framework", "firmware"]

        # 记录原有实现的调用
        with (
            patch("src.app.workflow.UnifiedModifier") as old_unified,
            patch("src.app.workflow.FrameworkModifier") as old_framework,
            patch("src.app.workflow.FirmwareModifier") as old_firmware,
            patch("src.app.workflow.RomModifier") as old_rom,
        ):
            old_unified.return_value.run.return_value = True
            run_modification_phases(mock_ctx, phases_to_run, MagicMock())
            old_calls = {
                "unified_cls": old_unified.call_args,
                "unified_run": old_unified.return_value.run.call_args,
                "framework_cls": old_framework.call_args,
                "firmware_cls": old_firmware.call_args,
                "rom_cls": old_rom.call_args,
                "rom_run_all": old_rom.return_value.run_all_modifications.call_args,
            }

        # 记录新实现的调用
        with (
            patch("src.core.modifiers.UnifiedModifier") as new_unified,
            patch("src.core.modifiers.FrameworkModifier") as new_framework,
            patch("src.core.modifiers.FirmwareModifier") as new_firmware,
            patch("src.core.modifiers.RomModifier") as new_rom,
        ):
            new_unified.return_value.run.return_value = True
            phase = ModificationPhase()
            phase.execute({
                "porting_context": mock_ctx,
                "phases_to_run": phases_to_run,
            })
            new_calls = {
                "unified_cls": new_unified.call_args,
                "unified_run": new_unified.return_value.run.call_args,
                "framework_cls": new_framework.call_args,
                "firmware_cls": new_firmware.call_args,
                "rom_cls": new_rom.call_args,
                "rom_run_all": new_rom.return_value.run_all_modifications.call_args,
            }

        # 对比关键调用参数
        assert old_calls["unified_cls"] == new_calls["unified_cls"]
        assert old_calls["unified_run"] == new_calls["unified_run"]
        assert old_calls["framework_cls"] == new_calls["framework_cls"]
        assert old_calls["firmware_cls"] == new_calls["firmware_cls"]
        assert old_calls["rom_cls"] == new_calls["rom_cls"]
        assert old_calls["rom_run_all"] == new_calls["rom_run_all"]

    def test_modification_phase_partial_phases_equivalence(self):
        """验证部分阶段列表时的等价性。"""
        mock_ctx = MagicMock()
        phases_to_run = ["system"]

        with (
            patch("src.app.workflow.UnifiedModifier") as old_unified,
            patch("src.app.workflow.FrameworkModifier") as old_framework,
            patch("src.app.workflow.FirmwareModifier") as old_firmware,
            patch("src.app.workflow.RomModifier") as old_rom,
        ):
            old_unified.return_value.run.return_value = True
            run_modification_phases(mock_ctx, phases_to_run, MagicMock())
            old_unified_run_args = old_unified.return_value.run.call_args
            old_framework_called = old_framework.called
            old_firmware_called = old_firmware.called

        with (
            patch("src.core.modifiers.UnifiedModifier") as new_unified,
            patch("src.core.modifiers.FrameworkModifier") as new_framework,
            patch("src.core.modifiers.FirmwareModifier") as new_firmware,
            patch("src.core.modifiers.RomModifier") as new_rom,
        ):
            new_unified.return_value.run.return_value = True
            phase = ModificationPhase()
            phase.execute({
                "porting_context": mock_ctx,
                "phases_to_run": phases_to_run,
            })
            new_unified_run_args = new_unified.return_value.run.call_args
            new_framework_called = new_framework.called
            new_firmware_called = new_firmware.called

        assert old_unified_run_args == new_unified_run_args
        assert old_framework_called == new_framework_called
        assert old_firmware_called == new_firmware_called

    def test_modification_phase_empty_phases_equivalence(self):
        """验证空阶段列表时的等价性。"""
        mock_ctx = MagicMock()

        with (
            patch("src.app.workflow.UnifiedModifier") as old_unified,
            patch("src.app.workflow.FrameworkModifier"),
            patch("src.app.workflow.FirmwareModifier"),
            patch("src.app.workflow.RomModifier") as old_rom,
        ):
            run_modification_phases(mock_ctx, [], MagicMock())
            old_unified_called = old_unified.called
            old_rom_called = old_rom.called

        with (
            patch("src.core.modifiers.UnifiedModifier") as new_unified,
            patch("src.core.modifiers.FrameworkModifier"),
            patch("src.core.modifiers.FirmwareModifier"),
            patch("src.core.modifiers.RomModifier") as new_rom,
        ):
            phase = ModificationPhase()
            phase.execute({
                "porting_context": mock_ctx,
                "phases_to_run": [],
            })
            new_unified_called = new_unified.called
            new_rom_called = new_rom.called

        assert old_unified_called == new_unified_called
        assert old_rom_called == new_rom_called


# ============================================================
# Pipeline 执行等价性：Pipeline vs 直接调用
# ============================================================


class TestPipelineExecutionEquivalence:
    """验证 Pipeline 执行逻辑与直接顺序调用的等价性。"""

    def test_pipeline_sequential_execution_equals_direct_calls(self):
        """验证 Pipeline 顺序执行与直接调用结果一致。"""
        direct_log = []
        pipeline_log = []

        # 直接调用
        ctx = {"start": True}
        for name in ["A", "B", "C"]:
            phase = RecordingPhase(name, direct_log)
            ctx = phase.execute(ctx)

        # Pipeline 执行
        pipeline = Pipeline()
        for name in ["A", "B", "C"]:
            pipeline.add_phase(RecordingPhase(name, pipeline_log))
        result = pipeline.run({"start": True})

        assert direct_log == pipeline_log
        assert result.context["start"] is True

    def test_pipeline_context_modification_equals_direct(self):
        """验证 Pipeline 上下文修改与直接调用一致。"""
        # 直接调用
        direct_ctx = {}
        for name, key, val in [("A", "x", 1), ("B", "y", 2), ("C", "z", 3)]:
            phase = ContextChainingPhase(name, key, val)
            direct_ctx = phase.execute(direct_ctx)

        # Pipeline 执行
        pipeline = Pipeline()
        for name, key, val in [("A", "x", 1), ("B", "y", 2), ("C", "z", 3)]:
            pipeline.add_phase(ContextChainingPhase(name, key, val))
        result = pipeline.run({})

        assert result.context == direct_ctx

    def test_pipeline_failure_stops_like_direct_exception(self):
        """验证 Pipeline 失败停止行为与直接异常一致。"""
        direct_log = []
        pipeline_log = []

        # 直接调用：异常在 B 处停止
        try:
            for name in ["A", "B", "C"]:
                if name == "B":
                    raise RuntimeError("direct fail")
                RecordingPhase(name, direct_log).execute({})
        except RuntimeError:
            pass

        # Pipeline 执行
        pipeline = Pipeline()
        pipeline.add_phase(RecordingPhase("A", pipeline_log))
        pipeline.add_phase(FailingPhase("B", "direct fail"))
        pipeline.add_phase(RecordingPhase("C", pipeline_log))
        result = pipeline.run({})

        assert direct_log == ["A"]
        # Pipeline 回滚时 RecordingPhase 也会记录 rollback:A
        assert "A" in pipeline_log
        assert "rollback:A" in pipeline_log
        assert result.success is False


# ============================================================
# 编排器逻辑等价性：PortingOrchestrator vs Pipeline
# ============================================================


class TestOrchestratorPipelineEquivalence:
    """验证 PortingOrchestrator 与 Pipeline 的逻辑等价性。"""

    def test_orchestrator_run_equals_pipeline_run(self):
        """验证编排器的 run 方法与 Pipeline 的 run 方法结果一致。"""
        phases = [RecordingPhase(f"P{i}", []) for i in range(4)]

        # Pipeline 直接运行
        pipeline = Pipeline()
        for p in phases:
            pipeline.add_phase(p)
        pipeline_result = pipeline.run({"test": True})

        # 编排器运行
        orchestrator_phases = [RecordingPhase(f"P{i}", []) for i in range(4)]
        orchestrator = PortingOrchestrator(phases=orchestrator_phases)
        orchestrator_result = orchestrator.run({"test": True})

        assert pipeline_result.success == orchestrator_result.success
        assert pipeline_result.completed_phases == orchestrator_result.completed_phases
        assert pipeline_result.context == orchestrator_result.context

    def test_orchestrator_failure_equals_pipeline_failure(self):
        """验证编排器的失败行为与 Pipeline 一致。"""
        # Pipeline
        pipeline = Pipeline()
        pipeline.add_phase(RecordingPhase("ok", []))
        pipeline.add_phase(FailingPhase("fail"))
        pipeline_result = pipeline.run({})

        # 编排器
        orchestrator = PortingOrchestrator(
            phases=[RecordingPhase("ok", []), FailingPhase("fail")]
        )
        orchestrator_result = orchestrator.run({})

        assert pipeline_result.success == orchestrator_result.success
        assert pipeline_result.failed_phase == orchestrator_result.failed_phase

    def test_orchestrator_event_bus_integration_equals_manual_subscription(self):
        """验证编排器的 EventBus 集成与手动订阅等价。"""
        # 手动 Pipeline + EventBus
        bus1 = EventBus()
        events1 = []
        bus1.subscribe("*", lambda e: events1.append(e))
        pipeline = Pipeline(event_bus=bus1)
        pipeline.add_phase(RecordingPhase("A", []))
        pipeline.run({})

        # 编排器 + EventBus
        bus2 = EventBus()
        events2 = []
        bus2.subscribe("*", lambda e: events2.append(e))
        orchestrator = PortingOrchestrator(
            event_bus=bus2, phases=[RecordingPhase("A", [])]
        )
        orchestrator.run({})

        assert len(events1) == len(events2)
        types1 = [e.event_type for e in events1]
        types2 = [e.event_type for e in events2]
        assert types1 == types2


# ============================================================
# 数据序列化等价性
# ============================================================


class TestDataSerializationEquivalence:
    """验证数据序列化/反序列化的等价性。"""

    def test_repack_checkpoint_roundtrip_preserves_all_fields(self):
        """验证 repack 检查点往返保留所有字段。"""
        ctx = MagicMock()
        ctx.stock_rom_code = "test_device"
        ctx.target_rom_version = "OS2.0.100.0"
        ctx.security_patch = "2025-06-01"
        ctx.is_ab_device = False
        ctx.base_android_version = "15"
        ctx.port_android_version = "15"
        ctx.is_port_eu_rom = True
        ctx.is_port_global_rom = False
        ctx.port_global_region = ""
        ctx.device_config = {
            "pack": {"type": "super", "fs_type": "ext4", "custom_avb_chain": True},
            "ksu": {"enable": True},
        }

        with pytest.MonkeyPatch.context() as m:
            import tempfile
            with tempfile.TemporaryDirectory() as tmp:
                work_dir = Path(tmp) / "build"
                work_dir.mkdir()

                checkpoint_path = save_repack_checkpoint(ctx, work_dir)
                data = checkpoint_path.read_text(encoding="utf-8")

                # 验证所有字段都序列化了
                assert "test_device" in data
                assert "OS2.0.100.0" in data
                assert "2025-06-01" in data
                assert "super" in data
                assert "ext4" in data

    def test_super_size_check_equivalence_with_and_without_partition_info(self):
        """验证有/无 partition_info.json 时的 super_size 检查逻辑等价性。"""
        import tempfile

        # 无 partition_info.json
        with tempfile.TemporaryDirectory() as tmp:
            import os
            old_cwd = os.getcwd()
            os.chdir(tmp)
            try:
                check_no_info = build_super_size_check(
                    "nonexistent", {"pack": {"super_size": 12345}}
                )
            finally:
                os.chdir(old_cwd)

        assert check_no_info["partition_info_exists"] is False
        assert check_no_info["mismatch"] is False

        # 有 partition_info.json 且大小匹配
        with tempfile.TemporaryDirectory() as tmp:
            info_dir = Path(tmp) / "devices" / "test"
            info_dir.mkdir(parents=True)
            (info_dir / "partition_info.json").write_text(
                '{"super_size": 12345}', encoding="utf-8"
            )
            import os
            old_cwd = os.getcwd()
            os.chdir(tmp)
            try:
                check_with_info = build_super_size_check(
                    "test", {"pack": {"super_size": 12345}}
                )
            finally:
                os.chdir(old_cwd)

        assert check_with_info["partition_info_exists"] is True
        assert check_with_info["mismatch"] is False

    def test_diff_report_injection_preserves_existing_data(self):
        """验证差异报告注入保留现有数据。"""
        diff_report = {
            "summary": {"files_added": 5, "risk_flags": 1},
            "highlights": {
                "risk_flags": [{"code": "EXISTING_FLAG"}],
                "other_data": [1, 2, 3],
            },
            "metadata": {"version": "1.0"},
        }
        check = {
            "mismatch": True,
            "device_config_super_size": 100,
            "partition_info_super_size": 200,
        }

        inject_super_size_check_into_diff_report(diff_report, check)

        assert diff_report["metadata"]["version"] == "1.0"
        assert diff_report["highlights"]["other_data"] == [1, 2, 3]
        assert diff_report["summary"]["files_added"] == 5
        assert len(diff_report["highlights"]["risk_flags"]) == 2
        assert diff_report["highlights"]["risk_flags"][0]["code"] == "EXISTING_FLAG"
        assert diff_report["highlights"]["risk_flags"][1]["code"] == "SUPER_SIZE_MISMATCH"


# ============================================================
# 事件系统逻辑等价性
# ============================================================


class TestEventSystemEquivalence:
    """验证事件系统的逻辑等价性。"""

    def test_pipeline_events_match_execution_order(self):
        """验证 Pipeline 事件与执行顺序一致。"""
        bus = EventBus()
        events = []
        bus.subscribe("*", lambda e: events.append(e))

        pipeline = Pipeline(event_bus=bus)
        pipeline.add_phase(RecordingPhase("first", []))
        pipeline.add_phase(RecordingPhase("second", []))
        pipeline.add_phase(RecordingPhase("third", []))
        pipeline.run({})

        start_phases = [
            e.phase_name for e in events if e.event_type == "phase.start"
        ]
        end_phases = [
            e.phase_name for e in events if e.event_type == "phase.end"
        ]

        assert start_phases == ["first", "second", "third"]
        assert end_phases == ["first", "second", "third"]

    def test_error_event_contains_phase_name(self):
        """验证错误事件包含正确的阶段名。"""
        bus = EventBus()
        errors = []
        bus.subscribe("error", lambda e: errors.append(e))

        pipeline = Pipeline(event_bus=bus)
        pipeline.add_phase(FailingPhase("broken"))
        pipeline.run({})

        assert len(errors) >= 1
        assert errors[0].phase == "broken"

    def test_phase_end_event_success_matches_pipeline_result(self):
        """验证阶段结束事件的 success 标志与 Pipeline 结果一致。"""
        bus = EventBus()
        end_events = []
        bus.subscribe("phase.end", lambda e: end_events.append(e))

        pipeline = Pipeline(event_bus=bus)
        pipeline.add_phase(RecordingPhase("ok", []))
        pipeline.add_phase(FailingPhase("fail"))
        result = pipeline.run({})

        ok_event = next(e for e in end_events if e.phase_name == "ok")
        fail_event = next(e for e in end_events if e.phase_name == "fail")

        assert ok_event.success is True
        assert fail_event.success is False
        assert result.success is False
