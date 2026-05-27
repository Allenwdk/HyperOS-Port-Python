"""监控系统集成到主工作流的单元测试。

测试覆盖：
- MonitoredPhaseWrapper 将 Phase 包装为带监控的版本
- Monitor 集成到 PortingOrchestrator 的完整流程
- 监控报告自动生成
- ConsoleReporter 实时进度显示
- EventBus 监控事件发布
- 监控可配置关闭
"""

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch, call

import pytest

from src.core.monitoring import (
    Monitor,
    MetricsCollector,
    MonitoredPhase,
    EventBus as MonitoringEventBus,
    get_monitor,
    reset_monitor,
)
from src.core.monitoring.workflow_integration import (
    MonitoredPhaseWrapper,
    MonitoredPortingOrchestrator,
    create_monitored_orchestrator,
)
from src.core.workflow.phases import Phase, ExtractionPhase


# ---------------------------------------------------------------------------
# 辅助工具
# ---------------------------------------------------------------------------


class _StubPhase(Phase):
    """用于测试的桩 Phase。"""

    name: str = "stub"
    description: str = "测试桩阶段"

    def __init__(self, name: str = "stub", should_fail: bool = False):
        self.name = name
        self.description = f"测试桩阶段: {name}"
        self._should_fail = should_fail

    def execute(self, context: dict) -> dict:
        if self._should_fail:
            raise RuntimeError(f"阶段 {self.name} 故意失败")
        context[f"{self.name}_done"] = True
        return context


class _SlowPhase(Phase):
    """模拟慢速阶段，用于测试耗时记录。"""

    name: str = "slow"
    description: str = "慢速测试阶段"

    def __init__(self, delay: float = 0.05):
        self._delay = delay

    def execute(self, context: dict) -> dict:
        time.sleep(self._delay)
        context["slow_done"] = True
        return context


# ---------------------------------------------------------------------------
# 测试 MonitoredPhaseWrapper
# ---------------------------------------------------------------------------


class TestMonitoredPhaseWrapper:
    """测试 MonitoredPhaseWrapper 包装器。"""

    def test_wrapper_preserves_phase_name(self):
        """包装器应保留原始阶段名称。"""
        phase = _StubPhase("extraction")
        wrapper = MonitoredPhaseWrapper(phase)
        assert wrapper.name == "extraction"

    def test_wrapper_preserves_phase_description(self):
        """包装器应保留原始阶段描述。"""
        phase = _StubPhase("init", should_fail=False)
        wrapper = MonitoredPhaseWrapper(phase)
        assert "init" in wrapper.description

    def test_wrapper_delegates_execute(self):
        """包装器应委托执行给原始阶段。"""
        phase = _StubPhase("test_phase")
        wrapper = MonitoredPhaseWrapper(phase)
        ctx = {"input": "data"}
        result = wrapper.execute(ctx)
        assert result["test_phase_done"] is True

    def test_wrapper_records_duration_metric(self):
        """包装器应记录阶段执行耗时指标。"""
        collector = MetricsCollector()
        phase = _SlowPhase(delay=0.05)
        wrapper = MonitoredPhaseWrapper(phase, collector=collector)

        wrapper.execute({})
        metrics = collector.get_metrics()
        duration_metrics = [m for m in metrics if "duration" in m.name]
        assert len(duration_metrics) >= 1
        assert duration_metrics[0].value >= 0.04

    def test_wrapper_records_success_counter(self):
        """包装器应记录阶段成功计数器。"""
        collector = MetricsCollector()
        phase = _StubPhase("ok_phase")
        wrapper = MonitoredPhaseWrapper(phase, collector=collector)

        wrapper.execute({})
        assert collector.get_counter("phase.ok_phase.success") == 1.0

    def test_wrapper_records_failure_counter_on_error(self):
        """包装器在阶段失败时应记录失败计数器。"""
        collector = MetricsCollector()
        phase = _StubPhase("fail_phase", should_fail=True)
        wrapper = MonitoredPhaseWrapper(phase, collector=collector)

        with pytest.raises(RuntimeError):
            wrapper.execute({})

        assert collector.get_counter("phase.fail_phase.failures") == 1.0

    def test_wrapper_publishes_event_bus_events(self):
        """包装器应通过 EventBus 发布阶段事件。"""
        bus = MonitoringEventBus()
        events_received = []

        def on_phase_event(event_type, data):
            events_received.append((event_type, data))

        bus.subscribe("monitor.phase", on_phase_event)

        phase = _StubPhase("event_phase")
        wrapper = MonitoredPhaseWrapper(phase, event_bus=bus)
        wrapper.execute({})

        assert len(events_received) >= 1
        assert events_received[0][1]["phase"] == "event_phase"
        assert events_received[0][1]["success"] is True

    def test_wrapper_rollback_delegates_to_inner_phase(self):
        """包装器的回滚应委托给内部阶段。"""
        phase = _StubPhase("rollback_test")
        wrapper = MonitoredPhaseWrapper(phase)

        ctx = {"rollback_test_done": True}
        result = wrapper.rollback(ctx)
        # _StubPhase 的 rollback 是默认实现，直接返回 context
        assert result is ctx


# ---------------------------------------------------------------------------
# 测试 MonitoredPortingOrchestrator
# ---------------------------------------------------------------------------


class TestMonitoredPortingOrchestrator:
    """测试带监控的编排器。"""

    def test_orchestrator_accepts_monitor(self):
        """编排器应接受 Monitor 实例。"""
        monitor = Monitor()
        orchestrator = MonitoredPortingOrchestrator(monitor=monitor)
        assert orchestrator._monitor is monitor

    def test_orchestrator_wraps_phases_with_monitoring(self):
        """编排器应将阶段包装为带监控的版本。"""
        monitor = Monitor()
        phases = [_StubPhase("a"), _StubPhase("b")]
        orchestrator = MonitoredPortingOrchestrator(monitor=monitor, phases=phases)

        result = orchestrator.run({})
        assert result.success is True
        assert result.context.get("a_done") is True
        assert result.context.get("b_done") is True

    def test_orchestrator_generates_report_on_success(self):
        """编排器在成功时应生成监控报告。"""
        monitor = Monitor()
        phase = _StubPhase("report_test")
        orchestrator = MonitoredPortingOrchestrator(
            monitor=monitor, phases=[phase]
        )

        result = orchestrator.run({})
        assert result.success is True
        # 验证报告中有阶段结果
        report = monitor.report.generate()
        assert "report_test" in report["phase_results"]

    def test_orchestrator_generates_report_on_failure(self):
        """编排器在失败时也应生成监控报告。"""
        monitor = Monitor()
        phase = _StubPhase("fail_report", should_fail=True)
        orchestrator = MonitoredPortingOrchestrator(
            monitor=monitor, phases=[phase]
        )

        result = orchestrator.run({})
        assert result.success is False
        report = monitor.report.generate()
        assert report["summary"]["phases_failed"] >= 1

    def test_orchestrator_saves_report_to_file(self, tmp_path):
        """编排器应能将报告保存到文件。"""
        monitor = Monitor()
        phase = _StubPhase("save_test")
        report_path = tmp_path / "monitoring_report.json"
        orchestrator = MonitoredPortingOrchestrator(
            monitor=monitor, phases=[phase], report_path=report_path
        )

        orchestrator.run({})
        assert report_path.exists()
        report_data = json.loads(report_path.read_text(encoding="utf-8"))
        assert report_data["report_type"] == "rom_modification_monitoring"

    def test_orchestrator_with_console_reporter(self):
        """编排器应支持 ConsoleReporter 实时进度显示。"""
        monitor = Monitor()
        reporter = MagicMock()
        phase = _StubPhase("console_test")
        orchestrator = MonitoredPortingOrchestrator(
            monitor=monitor, phases=[phase], reporter=reporter
        )

        orchestrator.run({})
        # 验证 reporter 收到了阶段开始和结束通知
        reporter.on_phase_start.assert_called()
        reporter.on_phase_end.assert_called()


# ---------------------------------------------------------------------------
# 测试工厂方法
# ---------------------------------------------------------------------------


class TestCreateMonitoredOrchestrator:
    """测试工厂方法 create_monitored_orchestrator。"""

    def test_factory_creates_orchestrator_with_default_phases(self):
        """工厂方法应创建包含默认阶段的编排器。"""
        orchestrator = create_monitored_orchestrator()
        assert orchestrator is not None
        assert orchestrator._monitor is not None

    def test_factory_with_custom_report_path(self, tmp_path):
        """工厂方法应支持自定义报告路径。"""
        report_path = tmp_path / "custom_report.json"
        orchestrator = create_monitored_orchestrator(report_path=report_path)
        assert orchestrator._report_path == report_path


# ---------------------------------------------------------------------------
# 测试 Monitor 与 Pipeline 集成
# ---------------------------------------------------------------------------


class TestMonitorPipelineIntegration:
    """测试 Monitor 与 Pipeline 的集成。"""

    def test_pipeline_records_phase_results_in_monitor(self):
        """Pipeline 应将阶段结果记录到 Monitor。"""
        from src.core.workflow.pipeline import Pipeline

        monitor = Monitor()
        monitor.start()
        pipeline = Pipeline(monitor=monitor)
        pipeline.add_phase(_StubPhase("pipe_phase"))

        result = pipeline.run({})
        assert result.success is True
        report = monitor.report.generate()
        assert "pipe_phase" in report["phase_results"]

    def test_pipeline_records_failure_in_monitor(self):
        """Pipeline 应将阶段失败记录到 Monitor。"""
        from src.core.workflow.pipeline import Pipeline

        monitor = Monitor()
        monitor.start()
        pipeline = Pipeline(monitor=monitor)
        pipeline.add_phase(_StubPhase("ok"))
        pipeline.add_phase(_StubPhase("bad", should_fail=True))

        result = pipeline.run({})
        assert result.success is False
        report = monitor.report.generate()
        assert report["summary"]["phases_failed"] >= 1


# ---------------------------------------------------------------------------
# 测试监控可配置关闭
# ---------------------------------------------------------------------------


class TestMonitoringDisableable:
    """测试监控可以被禁用。"""

    def test_no_monitor_means_no_tracking(self):
        """不传 Monitor 时不应有监控跟踪。"""
        from src.core.workflow.pipeline import Pipeline

        pipeline = Pipeline(monitor=None)
        pipeline.add_phase(_StubPhase("no_monitor"))

        result = pipeline.run({})
        assert result.success is True
        # 不应抛出异常

    def test_wrapper_without_collector_still_works(self):
        """不传 collector 时包装器仍应正常工作。"""
        phase = _StubPhase("no_collector")
        wrapper = MonitoredPhaseWrapper(phase, collector=None)
        result = wrapper.execute({})
        assert result["no_collector_done"] is True
