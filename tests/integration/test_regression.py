"""回归测试。

确保所有现有功能在重构后继续正常工作。
验证原有 API 的向后兼容性和行为一致性。
"""

from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.app.workflow import (
    DEFAULT_PHASES,
    build_super_size_check,
    determine_pack_settings,
    inject_super_size_check_into_diff_report,
    save_repack_checkpoint,
    load_repack_checkpoint,
)
from src.core.workflow.orchestrator import (
    PortingOrchestrator,
    create_custom_pipeline,
    create_default_pipeline,
    run_porting_pipeline,
)
from src.core.workflow.phases import (
    ExtractionPhase,
    InitializationPhase,
    InitPhase,
    ModificationPhase,
    ModifyPhase,
    PackingPhase,
    PackPhase,
    Phase,
)
from src.core.workflow.pipeline import Pipeline, PipelineResult
from src.core.events.bus import EventBus
from src.core.events.events import Event, PhaseStartEvent, PhaseEndEvent, ErrorEvent
from src.core.events.handlers import EventHandler
from src.core.monitoring import Monitor
from src.core.monitoring import MetricsCollector

from tests.integration.conftest import (
    FailingPhase,
    RecordingPhase,
    make_mock_porting_context,
    make_mock_rom,
)


# ============================================================
# 向后兼容性回归测试
# ============================================================


class TestBackwardCompatibility:
    """向后兼容性回归测试。"""

    def test_old_class_aliases_still_work(self):
        """验证旧类名别名仍然可用。"""
        assert InitPhase is InitializationPhase
        assert ModifyPhase is ModificationPhase
        assert PackPhase is PackingPhase

    def test_old_class_aliases_have_correct_names(self):
        """验证旧别名创建的实例具有正确的阶段名。"""
        assert InitPhase().name == "initialization"
        assert ModifyPhase().name == "modification"
        assert PackPhase().name == "repack"

    def test_create_default_pipeline_returns_four_phases(self):
        """验证 create_default_pipeline 工厂函数返回四阶段 Pipeline。"""
        pipeline = create_default_pipeline()
        assert len(pipeline._phases) == 4
        names = [p.name for p in pipeline._phases]
        assert names == ["extraction", "initialization", "modification", "repack"]

    def test_create_default_pipeline_with_event_bus(self):
        """验证 create_default_pipeline 支持 EventBus 参数。"""
        bus = EventBus()
        pipeline = create_default_pipeline(event_bus=bus)
        assert pipeline._event_bus is bus

    def test_create_custom_pipeline(self):
        """验证 create_custom_pipeline 工厂函数正确创建自定义 Pipeline。"""
        phases = [RecordingPhase("A", []), RecordingPhase("B", [])]
        pipeline = create_custom_pipeline(phases)
        assert len(pipeline._phases) == 2

    def test_run_porting_pipeline_signature(self):
        """验证 run_porting_pipeline 函数签名保持不变。"""
        import inspect

        sig = inspect.signature(run_porting_pipeline)
        param_names = list(sig.parameters.keys())
        assert "context" in param_names
        assert "event_bus" in param_names
        assert "monitor" in param_names

    def test_pipeline_result_dataclass_fields(self):
        """验证 PipelineResult 数据类字段保持不变。"""
        result = PipelineResult()
        assert hasattr(result, "success")
        assert hasattr(result, "context")
        assert hasattr(result, "completed_phases")
        assert hasattr(result, "total_duration")
        assert hasattr(result, "error")
        assert hasattr(result, "failed_phase")

    def test_default_phases_constant(self):
        """验证 DEFAULT_PHASES 常量值保持不变。"""
        assert DEFAULT_PHASES == ["system", "apk", "framework", "firmware"]


# ============================================================
# Pipeline 行为回归测试
# ============================================================


class TestPipelineBehaviorRegression:
    """Pipeline 核心行为回归测试。"""

    def test_pipeline_empty_returns_success(self):
        """验证空 Pipeline 返回成功。"""
        pipeline = Pipeline()
        result = pipeline.run({})
        assert result.success is True
        assert result.completed_phases == []

    def test_pipeline_single_phase(self):
        """验证单阶段 Pipeline 正常运行。"""
        pipeline = Pipeline()
        pipeline.add_phase(RecordingPhase("only", []))
        result = pipeline.run({})
        assert result.success is True
        assert result.completed_phases == ["only"]

    def test_pipeline_preserves_input_context(self):
        """验证 Pipeline 保留输入上下文。"""
        pipeline = Pipeline()
        pipeline.add_phase(RecordingPhase("noop", []))
        result = pipeline.run({"important": "data"})
        assert result.context["important"] == "data"

    def test_pipeline_stops_on_first_failure(self):
        """验证 Pipeline 在第一个失败阶段停止。"""
        order = []
        pipeline = Pipeline()
        pipeline.add_phase(RecordingPhase("A", order))
        pipeline.add_phase(FailingPhase("B"))
        pipeline.add_phase(RecordingPhase("C", order))

        result = pipeline.run({})
        assert result.success is False
        assert "A" in order
        assert "C" not in order

    def test_pipeline_rollback_reverse_order(self):
        """验证回滚按逆序执行。"""
        rollback_log = []

        class TrackPhase:
            def __init__(self, name):
                self.name = name
                self.description = ""

            def execute(self, ctx):
                return ctx

            def rollback(self, ctx):
                rollback_log.append(self.name)
                return ctx

        pipeline = Pipeline()
        pipeline.add_phase(TrackPhase("first"))
        pipeline.add_phase(TrackPhase("second"))
        pipeline.add_phase(FailingPhase("third"))

        pipeline.run({})
        assert rollback_log == ["second", "first"]

    def test_pipeline_records_duration(self):
        """验证 Pipeline 记录执行时间。"""
        pipeline = Pipeline()
        pipeline.add_phase(RecordingPhase("fast", []))
        result = pipeline.run({})
        assert result.total_duration >= 0

    def test_pipeline_result_error_and_failed_phase(self):
        """验证失败结果包含错误和失败阶段信息。"""
        pipeline = Pipeline()
        pipeline.add_phase(FailingPhase("broken", "测试错误"))
        result = pipeline.run({})

        assert result.success is False
        assert result.failed_phase == "broken"
        assert isinstance(result.error, RuntimeError)


# ============================================================
# EventBus 行为回归测试
# ============================================================


class TestEventBusBehaviorRegression:
    """EventBus 核心行为回归测试。"""

    def test_subscribe_and_publish(self):
        """验证基本订阅和发布功能。"""
        bus = EventBus()
        received = []
        bus.subscribe("test.event", lambda e: received.append(e))

        event = Event(event_type="test.event", data={"key": "value"})
        bus.publish(event)

        assert len(received) == 1
        assert received[0] is event

    def test_wildcard_subscription(self):
        """验证通配符订阅功能。"""
        bus = EventBus()
        received = []
        bus.subscribe("*", lambda e: received.append(e))

        bus.publish(Event(event_type="a"))
        bus.publish(Event(event_type="b"))

        assert len(received) == 2

    def test_unsubscribe(self):
        """验证取消订阅功能。"""
        bus = EventBus()
        received = []
        handler = lambda e: received.append(e)
        bus.subscribe("test", handler)

        bus.publish(Event(event_type="test"))
        assert len(received) == 1

        bus.unsubscribe("test", handler)
        bus.publish(Event(event_type="test"))
        assert len(received) == 1  # 不应增加

    def test_subscriber_count(self):
        """验证订阅者计数功能。"""
        bus = EventBus()
        assert bus.subscriber_count() == 0

        bus.subscribe("a", lambda e: None)
        assert bus.subscriber_count() == 1
        assert bus.subscriber_count("a") == 1
        assert bus.subscriber_count("b") == 0

    def test_event_bus_enable_disable(self):
        """验证事件总线启用/禁用功能。"""
        bus = EventBus()
        received = []
        bus.subscribe("*", lambda e: received.append(e))

        bus.disable()
        bus.publish(Event(event_type="test"))
        assert len(received) == 0

        bus.enable()
        bus.publish(Event(event_type="test"))
        assert len(received) == 1

    def test_clear_subscribers(self):
        """验证清空所有订阅者功能。"""
        bus = EventBus()
        bus.subscribe("a", lambda e: None)
        bus.subscribe("b", lambda e: None)
        assert bus.subscriber_count() == 2

        bus.clear()
        assert bus.subscriber_count() == 0


# ============================================================
# Monitor 行为回归测试
# ============================================================


class TestMonitorBehaviorRegression:
    """Monitor 核心行为回归测试。"""

    def test_monitor_start_stop(self):
        """验证 Monitor 启动和停止功能。"""
        from src.core.monitoring import Monitor

        monitor = Monitor()
        monitor.start()
        monitor.stop()

        report = monitor.report.generate()
        assert report is not None

    def test_monitor_records_phase_result(self):
        """验证 Monitor 记录阶段结果。"""
        from src.core.monitoring import Monitor

        monitor = Monitor()
        monitor.start()
        monitor.report.add_phase_result("test_phase", True, {"duration": 1.0})
        monitor.stop()

        report = monitor.report.generate()
        assert "test_phase" in report["phase_results"]

    def test_metrics_collector_record(self):
        """验证 MetricsCollector 记录指标。"""
        collector = MetricsCollector()
        collector.record("test_metric", 42.0, "ms")
        assert len(collector._metrics) == 1
        assert collector._metrics[0].name == "test_metric"

    def test_metrics_collector_counter(self):
        """验证 MetricsCollector 计数器功能。"""
        collector = MetricsCollector()
        collector.increment("requests")
        collector.increment("requests")
        assert collector.get_counter("requests") == 2.0


# ============================================================
# workflow.py 函数回归测试
# ============================================================


class TestWorkflowFunctionsRegression:
    """workflow.py 中关键函数的回归测试。"""

    def test_build_super_size_check_no_partition_info(self, tmp_path, monkeypatch):
        """验证无 partition_info.json 时的 super_size 检查。"""
        monkeypatch.chdir(tmp_path)
        check = build_super_size_check("nonexistent", {"pack": {"super_size": 12345}})

        assert check["partition_info_exists"] is False
        assert check["device_config_super_size"] == 12345
        assert check["mismatch"] is False

    def test_build_super_size_check_matching_sizes(self, tmp_path, monkeypatch):
        """验证大小匹配时 mismatch 为 False。"""
        monkeypatch.chdir(tmp_path)
        info_dir = tmp_path / "devices" / "fuxi"
        info_dir.mkdir(parents=True)
        (info_dir / "partition_info.json").write_text(
            '{"device_code":"fuxi","super_size":12345}', encoding="utf-8"
        )

        check = build_super_size_check("fuxi", {"pack": {"super_size": 12345}})
        assert check["mismatch"] is False

    def test_inject_super_size_check_no_mismatch_no_risk_flag(self):
        """验证无 mismatch 时不添加 risk flag。"""
        diff_report = {
            "summary": {"risk_flags": 0},
            "highlights": {"risk_flags": []},
        }
        check = {"mismatch": False}

        inject_super_size_check_into_diff_report(diff_report, check)

        assert len(diff_report["highlights"]["risk_flags"]) == 0
        assert diff_report["summary"]["risk_flags"] == 0

    def test_save_repack_checkpoint_roundtrip(self, tmp_path):
        """验证 repack 检查点保存和加载的往返一致性。"""
        ctx = MagicMock()
        ctx.stock_rom_code = "pudding"
        ctx.target_rom_version = "OS3.0.304.0"
        ctx.security_patch = "2026-01-01"
        ctx.is_ab_device = True
        ctx.base_android_version = "16"
        ctx.port_android_version = "16"
        ctx.is_port_eu_rom = False
        ctx.is_port_global_rom = True
        ctx.port_global_region = "eea"
        ctx.device_config = {"pack": {"type": "payload", "fs_type": "erofs"}}

        work_dir = tmp_path / "build"
        work_dir.mkdir()

        checkpoint_path = save_repack_checkpoint(ctx, work_dir)
        assert checkpoint_path.exists()

        target_dir = tmp_path / "target"
        (target_dir / "config").mkdir(parents=True)
        (target_dir / "repack_images").mkdir(parents=True)
        (target_dir / "system").mkdir(parents=True)
        (target_dir / "system" / "build.prop").write_text("ro.build.fingerprint=test\n")

        loaded = load_repack_checkpoint(work_dir, target_dir, MagicMock())
        assert loaded.stock_rom_code == "pudding"
        assert loaded.is_ab_device is True
        assert loaded.device_config["pack"]["type"] == "payload"

    def test_determine_pack_settings_defaults(self):
        """验证 determine_pack_settings 的默认值处理。"""
        args = Namespace(pack_type=None, fs_type=None, ksu=False, custom_avb_chain=False, avb_key=None)
        ctx = Namespace(
            device_config={"pack": {"type": "super", "fs_type": "ext4"}},
            enable_ksu=False,
            enable_custom_avb_chain=False,
        )

        pack_type, fs_type = determine_pack_settings(args, ctx, MagicMock())
        assert pack_type == "super"
        assert fs_type == "ext4"

    def test_determine_pack_settings_cli_overrides_config(self):
        """验证 CLI 参数覆盖设备配置。"""
        args = Namespace(pack_type="payload", fs_type="erofs", ksu=True, custom_avb_chain=False, avb_key=None)
        ctx = Namespace(
            device_config={"pack": {"type": "super", "fs_type": "ext4"}},
            enable_ksu=False,
            enable_custom_avb_chain=False,
        )

        pack_type, fs_type = determine_pack_settings(args, ctx, MagicMock())
        assert pack_type == "payload"
        assert fs_type == "erofs"
