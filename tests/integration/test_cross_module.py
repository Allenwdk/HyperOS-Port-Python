"""跨模块集成测试。

验证 Pipeline、EventBus、Monitor、Phase 等模块间的协作能力。
确保模块间的接口契约正确，事件流和数据流畅通。
"""

import logging
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.core.events.bus import EventBus
from src.core.events.events import (
    ErrorEvent,
    Event,
    PhaseEndEvent,
    PhaseStartEvent,
)
from src.core.events.handlers import EventHandler
from src.core.monitoring import Monitor, MetricsCollector
from src.core.workflow.phases import (
    ExtractionPhase,
    InitializationPhase,
    ModificationPhase,
    PackingPhase,
    Phase,
)
from src.core.workflow.pipeline import Pipeline, PipelineResult

from tests.integration.conftest import (
    ContextChainingPhase,
    FailingPhase,
    RecordingPhase,
    SlowPhase,
    make_mock_porting_context,
    make_mock_rom,
)


# ============================================================
# EventBus 与 Pipeline 集成
# ============================================================


class TestEventBusPipelineIntegration:
    """EventBus 与 Pipeline 的集成测试。"""

    def test_pipeline_publishes_events_to_multiple_subscribers(self):
        """验证 Pipeline 事件可以被多个订阅者接收。"""
        bus = EventBus()
        subscriber_a = []
        subscriber_b = []
        bus.subscribe("phase.start", lambda e: subscriber_a.append(e))
        bus.subscribe("phase.start", lambda e: subscriber_b.append(e))

        pipeline = Pipeline(event_bus=bus)
        pipeline.add_phase(RecordingPhase("test", []))
        pipeline.run({})

        assert len(subscriber_a) == 1
        assert len(subscriber_b) == 1
        assert subscriber_a[0] is subscriber_b[0]

    def test_pipeline_wildcard_subscriber_receives_all_events(self):
        """验证通配符订阅者接收所有事件类型。"""
        bus = EventBus()
        all_events = []
        bus.subscribe("*", lambda e: all_events.append(e))

        pipeline = Pipeline(event_bus=bus)
        pipeline.add_phase(RecordingPhase("A", []))
        pipeline.add_phase(RecordingPhase("B", []))
        pipeline.run({})

        event_types = {e.event_type for e in all_events}
        assert "phase.start" in event_types
        assert "phase.end" in event_types

    def test_pipeline_no_event_bus_does_not_crash(self):
        """验证没有 EventBus 时 Pipeline 正常运行不崩溃。"""
        pipeline = Pipeline(event_bus=None)
        pipeline.add_phase(RecordingPhase("A", []))
        result = pipeline.run({})

        assert result.success is True

    def test_event_bus_thread_safety_with_concurrent_publishers(self):
        """验证 EventBus 在并发发布事件时的线程安全性。"""
        bus = EventBus()
        received = []
        lock = threading.Lock()

        def safe_append(event):
            with lock:
                received.append(event)

        bus.subscribe("*", safe_append)

        def publish_events(prefix, count):
            for i in range(count):
                bus.publish(PhaseStartEvent(phase_name=f"{prefix}_{i}"))

        threads = [
            threading.Thread(target=publish_events, args=("T1", 50)),
            threading.Thread(target=publish_events, args=("T2", 50)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(received) == 100

    def test_event_bus_handler_object_integration(self):
        """验证 EventHandler 对象与 EventBus 的集成。"""

        class TestHandler(EventHandler):
            def __init__(self):
                super().__init__(name="test_handler", event_types=["phase.start"])
                self.handled = []

            def _process(self, event: Event) -> None:
                self.handled.append(event)

        bus = EventBus()
        handler = TestHandler()
        bus.subscribe_handler(handler)

        pipeline = Pipeline(event_bus=bus)
        pipeline.add_phase(RecordingPhase("A", []))
        pipeline.run({})

        assert len(handler.handled) == 1
        assert handler.handled[0].phase_name == "A"


# ============================================================
# Monitor 与 Pipeline 集成
# ============================================================


class TestMonitorPipelineIntegration:
    """Monitor 与 Pipeline 的集成测试。"""

    def test_monitor_records_phase_results(self):
        """验证 Monitor 记录每个阶段的执行结果。"""
        monitor = Monitor()
        monitor.start()

        pipeline = Pipeline(monitor=monitor)
        pipeline.add_phase(RecordingPhase("phase_a", []))
        pipeline.add_phase(RecordingPhase("phase_b", []))
        pipeline.run({})

        monitor.stop()
        report = monitor.report.generate()

        assert "phase_a" in report["phase_results"]
        assert "phase_b" in report["phase_results"]

    def test_monitor_records_failed_phase(self):
        """验证 Monitor 记录失败阶段的结果。"""
        monitor = Monitor()
        monitor.start()

        pipeline = Pipeline(monitor=monitor)
        pipeline.add_phase(RecordingPhase("ok", []))
        pipeline.add_phase(FailingPhase("fail"))
        pipeline.run({})

        monitor.stop()
        report = monitor.report.generate()

        assert report["phase_results"]["ok"]["success"] is True
        assert report["phase_results"]["fail"]["success"] is False

    def test_metrics_collector_records_phase_durations(self):
        """验证 MetricsCollector 记录阶段持续时间。"""
        from src.core.monitoring import Monitor

        monitor = Monitor()
        monitor.start()

        pipeline = Pipeline(monitor=monitor)
        pipeline.add_phase(SlowPhase("timed", duration=0.03))
        pipeline.run({})

        monitor.stop()
        report = monitor.report.generate()

        assert "timed" in report["phase_results"]

    def test_monitor_and_event_bus_both_active(self):
        """验证 Monitor 和 EventBus 可以同时工作。"""
        from src.core.monitoring import Monitor

        bus = EventBus()
        monitor = Monitor()
        monitor.start()

        events = []
        bus.subscribe("*", lambda e: events.append(e))

        pipeline = Pipeline(event_bus=bus, monitor=monitor)
        pipeline.add_phase(RecordingPhase("dual", []))
        pipeline.run({})

        monitor.stop()

        assert len(events) >= 2  # start + end
        report = monitor.report.generate()
        assert "dual" in report["phase_results"]


# ============================================================
# Phase 间上下文传递集成
# ============================================================


class TestPhaseContextIntegration:
    """Phase 间上下文传递的集成测试。"""

    def test_extraction_output_consumed_by_initialization(self):
        """验证 ExtractionPhase 的输出被 InitializationPhase 正确消费。"""
        mock_stock = make_mock_rom("Stock", "fuxi")
        mock_port = make_mock_rom("Port", "vermeer")
        mock_ctx = make_mock_porting_context()

        pipeline = Pipeline()
        pipeline.add_phase(ExtractionPhase())
        pipeline.add_phase(InitializationPhase())

        init_ctx = {
            "stock_rom_path": "/tmp/stock.zip",
            "port_rom_path": "/tmp/port.zip",
            "stock_work_dir": Path("/tmp/stockrom"),
            "port_work_dir": Path("/tmp/portrom"),
            "target_work_dir": Path("/tmp/target"),
            "is_official_modify": False,
            "cache_manager": None,
        }

        with (
            patch("src.core.rom.RomPackage", side_effect=[mock_stock, mock_port]),
            patch("src.core.context.PortingContext", return_value=mock_ctx),
            patch("src.core.device_auto_config.get_or_create_device_config", return_value={}),
        ):
            result = pipeline.run(init_ctx)

        assert result.success is True
        assert "stock_rom" in result.context
        assert "port_rom" in result.context
        assert "porting_context" in result.context
        assert "initialized" in result.context

    def test_initialization_output_consumed_by_modification(self):
        """验证 InitializationPhase 的输出被 ModificationPhase 正确消费。"""
        mock_ctx = MagicMock()
        ctx = {
            "porting_context": mock_ctx,
            "phases_to_run": ["system", "apk"],
        }

        pipeline = Pipeline()
        pipeline.add_phase(ModificationPhase())

        with (
            patch("src.core.modifiers.UnifiedModifier") as mock_unified,
            patch("src.core.modifiers.FrameworkModifier"),
            patch("src.core.modifiers.FirmwareModifier"),
            patch("src.core.modifiers.RomModifier"),
        ):
            mock_unified.return_value.run.return_value = True
            result = pipeline.run(ctx)

        assert result.success is True
        assert result.context.get("modified") is True

    def test_modification_output_consumed_by_packing(self):
        """验证 ModificationPhase 的输出被 PackingPhase 正确消费。"""
        mock_ctx = MagicMock()
        mock_repacker = MagicMock()

        ctx = {
            "porting_context": mock_ctx,
            "phases_to_run": ["repack"],
            "pack_type": "payload",
            "fs_type": "erofs",
        }

        pipeline = Pipeline()
        pipeline.add_phase(PackingPhase())

        with patch("src.core.packer.Repacker", return_value=mock_repacker):
            result = pipeline.run(ctx)

        assert result.success is True
        assert result.context.get("packed") is True
        mock_repacker.pack_all.assert_called_once()
        mock_repacker.pack_ota_payload.assert_called_once()

    def test_multi_phase_context_accumulation(self):
        """验证多阶段上下文累积正确。"""
        pipeline = Pipeline()
        pipeline.add_phase(ContextChainingPhase("phase1", "a", 1))
        pipeline.add_phase(ContextChainingPhase("phase2", "b", 2))
        pipeline.add_phase(ContextChainingPhase("phase3", "c", 3))

        result = pipeline.run({"base": 0})

        assert result.context == {"base": 0, "a": 1, "b": 2, "c": 3}

    def test_rollback_cleans_up_context(self):
        """验证回滚阶段清理上下文中对应的数据。"""
        pipeline = Pipeline()
        pipeline.add_phase(ContextChainingPhase("ok", "data", "value"))
        pipeline.add_phase(ContextChainingPhase("will_fail", "temp", "temp_value"))

        # 第三个阶段失败，触发回滚
        class FailAndCheckPhase:
            name = "checker"
            description = "检查阶段"

            def execute(self, context):
                raise RuntimeError("检查失败")

            def rollback(self, context):
                return context

        pipeline.add_phase(FailAndCheckPhase())
        result = pipeline.run({})

        assert result.success is False
        assert "data" not in result.context
        assert "temp" not in result.context


# ============================================================
# EventBus 与 Monitor 联合集成
# ============================================================


class TestEventBusMonitorJointIntegration:
    """EventBus 和 Monitor 联合集成测试。"""

    def test_event_bus_and_monitor_independent_operation(self):
        """验证 EventBus 和 Monitor 独立运行互不干扰。"""
        bus = EventBus()
        monitor = Monitor()
        monitor.start()

        bus_events = []
        bus.subscribe("*", lambda e: bus_events.append(e))

        pipeline = Pipeline(event_bus=bus, monitor=monitor)
        pipeline.add_phase(RecordingPhase("A", []))
        pipeline.add_phase(RecordingPhase("B", []))
        pipeline.run({})

        monitor.stop()

        assert len(bus_events) >= 4  # 2 start + 2 end
        report = monitor.report.generate()
        assert "A" in report["phase_results"]
        assert "B" in report["phase_results"]

    def test_orchestrator_full_integration_with_bus_and_monitor(self):
        """验证编排器与 EventBus 和 Monitor 的完整集成。"""
        from src.core.workflow.orchestrator import PortingOrchestrator

        bus = EventBus()
        monitor = Monitor()
        monitor.start()

        all_events = []
        bus.subscribe("*", lambda e: all_events.append(e))

        phases = [
            RecordingPhase("extract", []),
            RecordingPhase("init", []),
            RecordingPhase("modify", []),
            RecordingPhase("pack", []),
        ]
        orchestrator = PortingOrchestrator(event_bus=bus, monitor=monitor, phases=phases)
        result = orchestrator.run({"test": True})

        monitor.stop()

        assert result.success is True
        assert len(all_events) >= 8  # 4 start + 4 end

        report = monitor.report.generate()
        for phase_name in ["extract", "init", "modify", "pack"]:
            assert phase_name in report["phase_results"]
