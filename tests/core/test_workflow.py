"""工作流模块单元测试。

测试 Phase 抽象基类、Pipeline 执行引擎和具体 Phase 实现。
"""

import time
from unittest.mock import MagicMock, Mock, patch

import pytest

from src.core.workflow.phases import (
    ExtractionPhase,
    InitPhase,
    ModifyPhase,
    PackPhase,
    Phase,
)
from src.core.workflow.pipeline import Pipeline, PipelineResult


# ============================================================
# 辅助类：用于测试的 Phase 子类
# ============================================================


class RecordingPhase(Phase):
    """记录执行顺序的测试 Phase。"""

    def __init__(self, name: str, execution_log: list | None = None):
        super().__init__(name=name, description=f"测试阶段 {name}")
        self._log = execution_log

    def execute(self, context: dict) -> dict:
        if self._log is not None:
            self._log.append(self.name)
        return context

    def rollback(self, context: dict) -> dict:
        return context


class FailingPhase(Phase):
    """执行时抛出异常的测试 Phase。"""

    def __init__(self, name: str = "failing", error_msg: str = "测试错误"):
        super().__init__(name=name, description="失败阶段")
        self._error_msg = error_msg

    def execute(self, context: dict) -> dict:
        raise ValueError(self._error_msg)

    def rollback(self, context: dict) -> dict:
        return context


class RollbackTrackingPhase(Phase):
    """记录回滚调用的测试 Phase。"""

    def __init__(self, name: str, rollback_log: list | None = None, should_fail: bool = False):
        super().__init__(name=name, description=f"回滚测试 {name}")
        self._rollback_log = rollback_log
        self._should_fail = should_fail

    def execute(self, context: dict) -> dict:
        if self._should_fail:
            raise RuntimeError(f"阶段 {self.name} 执行失败")
        return context

    def rollback(self, context: dict) -> dict:
        if self._rollback_log is not None:
            self._rollback_log.append(self.name)
        return context


class ContextModifyingPhase(Phase):
    """修改上下文的测试 Phase。"""

    def __init__(self, name: str, key: str, value: object):
        super().__init__(name=name, description=f"修改上下文 {name}")
        self._key = key
        self._value = value

    def execute(self, context: dict) -> dict:
        context[self._key] = self._value
        return context

    def rollback(self, context: dict) -> dict:
        if self._key in context:
            del context[self._key]
        return context


# ============================================================
# Phase 抽象基类测试
# ============================================================


class TestPhaseAbstract:
    """测试 Phase 抽象基类。"""

    def test_phase_has_name_and_description(self):
        """验证 Phase 子类必须提供 name 和 description。"""
        phase = RecordingPhase("test", [])
        assert phase.name == "test"
        assert phase.description == "测试阶段 test"

    def test_phase_is_abstract_cannot_instantiate(self):
        """验证不能直接实例化 Phase 抽象基类。"""
        with pytest.raises(TypeError):
            Phase(name="test", description="测试")

    def test_phase_execute_is_abstract(self):
        """验证 Phase 子类必须实现 execute 方法。"""
        class IncompletePhase(Phase):
            pass

        with pytest.raises(TypeError):
            IncompletePhase(name="test", description="测试")

    def test_phase_execute_returns_context(self):
        """验证 execute 方法返回上下文。"""
        phase = RecordingPhase("test", [])
        ctx = {"key": "value"}
        result = phase.execute(ctx)
        assert result == ctx

    def test_phase_rollback_returns_context(self):
        """验证 rollback 方法返回上下文。"""
        phase = RecordingPhase("test", [])
        ctx = {"key": "value"}
        result = phase.rollback(ctx)
        assert result == ctx


# ============================================================
# Pipeline 顺序执行测试
# ============================================================


class TestPipelineExecution:
    """测试 Pipeline 顺序执行。"""

    def test_pipeline_executes_phases_in_order(self):
        """验证 Pipeline 按顺序执行所有 Phase。"""
        execution_order = []
        pipeline = Pipeline()
        pipeline.add_phase(RecordingPhase("A", execution_order))
        pipeline.add_phase(RecordingPhase("B", execution_order))
        pipeline.add_phase(RecordingPhase("C", execution_order))

        result = pipeline.run({})

        assert execution_order == ["A", "B", "C"]
        assert result.success is True

    def test_pipeline_passes_context_between_phases(self):
        """验证 Pipeline 在 Phase 之间传递上下文。"""
        pipeline = Pipeline()
        pipeline.add_phase(ContextModifyingPhase("init", "step1", "done"))
        pipeline.add_phase(ContextModifyingPhase("modify", "step2", "done"))

        result = pipeline.run({})

        assert result.success is True
        assert result.context["step1"] == "done"
        assert result.context["step2"] == "done"

    def test_pipeline_empty_returns_success(self):
        """验证空 Pipeline 返回成功结果。"""
        pipeline = Pipeline()
        result = pipeline.run({})

        assert result.success is True
        assert result.completed_phases == []
        assert result.context == {}

    def test_pipeline_records_duration(self):
        """验证 Pipeline 记录总执行时间。"""
        pipeline = Pipeline()
        pipeline.add_phase(RecordingPhase("A", []))

        start = time.time()
        result = pipeline.run({})
        elapsed = time.time() - start

        assert result.total_duration >= 0
        assert result.total_duration <= elapsed + 0.1  # 允许微小误差


# ============================================================
# Pipeline 错误处理和回滚测试
# ============================================================


class TestPipelineRollback:
    """测试 Pipeline 错误回滚。"""

    def test_pipeline_rolls_back_on_failure(self):
        """验证 Pipeline 在阶段失败时回滚已执行的阶段。"""
        rollback_log = []
        pipeline = Pipeline()
        pipeline.add_phase(RollbackTrackingPhase("A", rollback_log))
        pipeline.add_phase(RollbackTrackingPhase("B", rollback_log, should_fail=True))

        result = pipeline.run({})

        assert result.success is False
        assert result.error is not None
        assert "A" in rollback_log  # A 已执行，应回滚

    def test_pipeline_stops_on_first_failure(self):
        """验证 Pipeline 在第一个失败阶段后停止执行。"""
        execution_order = []
        pipeline = Pipeline()
        pipeline.add_phase(RecordingPhase("A", execution_order))
        pipeline.add_phase(FailingPhase("B"))
        pipeline.add_phase(RecordingPhase("C", execution_order))

        result = pipeline.run({})

        assert result.success is False
        assert "A" in execution_order
        assert "C" not in execution_order

    def test_pipeline_rollback_reverses_order(self):
        """验证回滚按逆序执行。"""
        rollback_log = []
        pipeline = Pipeline()
        pipeline.add_phase(RollbackTrackingPhase("A", rollback_log))
        pipeline.add_phase(RollbackTrackingPhase("B", rollback_log))
        pipeline.add_phase(FailingPhase("C"))

        pipeline.run({})

        # 回滚应按 B, A 的逆序执行
        assert rollback_log == ["B", "A"]

    def test_pipeline_rollback_failure_does_not_crash(self):
        """验证回滚阶段本身失败不会导致崩溃。"""

        class BadRollbackPhase(Phase):
            def __init__(self, name: str):
                super().__init__(name=name, description=f"坏回滚 {name}")

            def execute(self, context: dict) -> dict:
                return context

            def rollback(self, context: dict) -> dict:
                raise RuntimeError("回滚失败")

        pipeline = Pipeline()
        pipeline.add_phase(RollbackTrackingPhase("A", []))
        pipeline.add_phase(BadRollbackPhase("B"))
        pipeline.add_phase(FailingPhase("C"))

        # 不应抛出异常
        result = pipeline.run({})
        assert result.success is False


# ============================================================
# Pipeline 结果对象测试
# ============================================================


class TestPipelineResult:
    """测试 PipelineResult 数据类。"""

    def test_pipeline_result_success_fields(self):
        """验证成功结果的字段值。"""
        result = PipelineResult(
            success=True,
            context={"key": "value"},
            completed_phases=["A", "B"],
            total_duration=1.5,
        )
        assert result.success is True
        assert result.context == {"key": "value"}
        assert result.completed_phases == ["A", "B"]
        assert result.total_duration == 1.5
        assert result.error is None

    def test_pipeline_result_failure_fields(self):
        """验证失败结果的字段值。"""
        err = ValueError("测试错误")
        result = PipelineResult(
            success=False,
            context={},
            completed_phases=["A"],
            total_duration=0.5,
            error=err,
            failed_phase="B",
        )
        assert result.success is False
        assert result.error is err
        assert result.failed_phase == "B"


# ============================================================
# 具体 Phase 实现测试
# ============================================================


class TestExtractionPhase:
    """测试 ExtractionPhase 阶段。"""

    def test_extraction_phase_metadata(self):
        """验证解压阶段的元数据。"""
        phase = ExtractionPhase()
        assert phase.name == "extraction"
        assert "解压" in phase.description

    def test_extraction_phase_execute(self):
        """验证解压阶段执行逻辑。"""
        from pathlib import Path
        from unittest.mock import patch

        mock_rom = Mock()
        mock_rom.extract_images.return_value = None
        ctx = {
            "stock_rom_path": "/tmp/stock.zip",
            "port_rom_path": "/tmp/port.zip",
            "stock_work_dir": Path("/tmp/work/stockrom"),
            "port_work_dir": Path("/tmp/work/portrom"),
            "is_official_modify": True,
        }
        phase = ExtractionPhase()
        with patch("src.core.rom.RomPackage", return_value=mock_rom):
            result = phase.execute(ctx)
        assert "stock_rom" in result


class TestInitPhase:
    """测试 InitPhase 阶段。"""

    def test_init_phase_metadata(self):
        """验证初始化阶段的元数据。"""
        phase = InitPhase()
        assert phase.name == "initialization"
        assert "初始化" in phase.description

    def test_init_phase_execute(self):
        """验证初始化阶段执行逻辑。"""
        from pathlib import Path
        from unittest.mock import patch

        mock_stock = Mock()
        mock_stock.get_prop.return_value = "test_device"
        mock_stock.rom_type.name = "PAYLOAD"
        mock_stock.props = {}
        mock_stock.payload_info = {}
        mock_port = Mock()
        mock_ctx = Mock()
        ctx = {
            "stock_rom": mock_stock,
            "port_rom": mock_port,
            "target_work_dir": Path("/tmp/work/target"),
            "is_official_modify": False,
        }
        phase = InitPhase()
        with patch("src.core.context.PortingContext", return_value=mock_ctx), \
             patch("src.core.device_auto_config.get_or_create_device_config", return_value={}):
            result = phase.execute(ctx)
        assert "initialized" in result


class TestModifyPhase:
    """测试 ModifyPhase 阶段。"""

    def test_modify_phase_metadata(self):
        """验证修改阶段的元数据。"""
        phase = ModifyPhase()
        assert phase.name == "modification"
        assert "修改" in phase.description

    def test_modify_phase_execute(self):
        """验证修改阶段执行逻辑。"""
        from unittest.mock import patch

        mock_ctx = Mock()
        ctx = {"porting_context": mock_ctx, "phases_to_run": []}
        phase = ModifyPhase()
        with patch("src.core.modifiers.UnifiedModifier"), \
             patch("src.core.modifiers.FrameworkModifier"), \
             patch("src.core.modifiers.FirmwareModifier"), \
             patch("src.core.modifiers.RomModifier"):
            result = phase.execute(ctx)
        assert "modified" in result


class TestPackPhase:
    """测试 PackPhase 阶段。"""

    def test_pack_phase_metadata(self):
        """验证打包阶段的元数据。"""
        phase = PackPhase()
        assert phase.name == "repack"
        assert "打包" in phase.description

    def test_pack_phase_execute(self):
        """验证打包阶段执行逻辑。"""
        from unittest.mock import patch

        mock_ctx = Mock()
        ctx = {
            "porting_context": mock_ctx,
            "phases_to_run": ["repack"],
            "pack_type": "payload",
            "fs_type": "erofs",
        }
        phase = PackPhase()
        with patch("src.core.packer.Repacker"):
            result = phase.execute(ctx)
        assert "packed" in result


# ============================================================
# Pipeline 与 EventBus 集成测试
# ============================================================


class TestPipelineEventBus:
    """测试 Pipeline 与 EventBus 集成。"""

    def test_pipeline_publishes_phase_start_event(self):
        """验证 Pipeline 在阶段开始时发布 PhaseStartEvent。"""
        from src.core.events.bus import EventBus

        bus = EventBus()
        events_received = []
        bus.subscribe("phase.start", lambda e: events_received.append(e))

        pipeline = Pipeline(event_bus=bus)
        pipeline.add_phase(RecordingPhase("A", []))
        pipeline.run({})

        assert len(events_received) == 1
        assert events_received[0].phase_name == "A"

    def test_pipeline_publishes_phase_end_event(self):
        """验证 Pipeline 在阶段结束时发布 PhaseEndEvent。"""
        from src.core.events.bus import EventBus

        bus = EventBus()
        events_received = []
        bus.subscribe("phase.end", lambda e: events_received.append(e))

        pipeline = Pipeline(event_bus=bus)
        pipeline.add_phase(RecordingPhase("A", []))
        pipeline.run({})

        assert len(events_received) == 1
        assert events_received[0].phase_name == "A"
        assert events_received[0].success is True

    def test_pipeline_publishes_error_event_on_failure(self):
        """验证 Pipeline 在阶段失败时发布 ErrorEvent。"""
        from src.core.events.bus import EventBus

        bus = EventBus()
        events_received = []
        bus.subscribe("error", lambda e: events_received.append(e))

        pipeline = Pipeline(event_bus=bus)
        pipeline.add_phase(FailingPhase("B"))
        pipeline.run({})

        assert len(events_received) >= 1


# ============================================================
# Pipeline 与 Monitor 集成测试
# ============================================================


class TestPipelineMonitor:
    """测试 Pipeline 与 Monitor 集成。"""

    def test_pipeline_collects_metrics_with_monitor(self):
        """验证 Pipeline 通过 Monitor 采集执行指标。"""
        from src.core.monitoring import Monitor

        monitor = Monitor()
        monitor.start()

        pipeline = Pipeline(monitor=monitor)
        pipeline.add_phase(RecordingPhase("A", []))
        pipeline.run({})

        monitor.stop()

        # 验证监控报告包含阶段结果
        report = monitor.report.generate()
        assert "A" in report["phase_results"]


# ============================================================
# PortingOrchestrator 测试
# ============================================================


class TestPortingOrchestrator:
    """测试 PortingOrchestrator 编排器。"""

    def test_orchestrator_import(self):
        """验证 PortingOrchestrator 可以正确导入。"""
        from src.core.workflow.orchestrator import PortingOrchestrator

        assert PortingOrchestrator is not None

    def test_orchestrator_creates_default_pipeline(self):
        """验证编排器默认创建四阶段 Pipeline。"""
        from src.core.workflow.orchestrator import PortingOrchestrator

        orchestrator = PortingOrchestrator()
        pipeline = orchestrator._build_pipeline()
        phase_names = [p.name for p in pipeline._phases]
        assert phase_names == ["extraction", "initialization", "modification", "repack"]

    def test_orchestrator_custom_phases(self):
        """验证编排器支持自定义阶段列表。"""
        from src.core.workflow.orchestrator import PortingOrchestrator

        log = []
        orchestrator = PortingOrchestrator(
            phases=[RecordingPhase("A", log), RecordingPhase("B", log)]
        )
        result = orchestrator.run({})
        assert result.success is True
        assert result.completed_phases == ["A", "B"]
        assert log == ["A", "B"]

    def test_orchestrator_returns_success_result(self):
        """验证编排器成功时返回正确结果。"""
        from src.core.workflow.orchestrator import PortingOrchestrator

        orchestrator = PortingOrchestrator(
            phases=[RecordingPhase("A", [])]
        )
        result = orchestrator.run({"key": "value"})
        assert result.success is True
        assert result.context["key"] == "value"
        assert result.total_duration >= 0

    def test_orchestrator_returns_failure_on_phase_error(self):
        """验证编排器在阶段失败时返回失败结果。"""
        from src.core.workflow.orchestrator import PortingOrchestrator

        orchestrator = PortingOrchestrator(
            phases=[FailingPhase("bad")]
        )
        result = orchestrator.run({})
        assert result.success is False
        assert result.failed_phase == "bad"
        assert result.error is not None

    def test_orchestrator_rollback_on_failure(self):
        """验证编排器在失败时触发回滚。"""
        from src.core.workflow.orchestrator import PortingOrchestrator

        rollback_log = []
        orchestrator = PortingOrchestrator(
            phases=[
                RollbackTrackingPhase("A", rollback_log),
                RollbackTrackingPhase("B", rollback_log, should_fail=True),
            ]
        )
        result = orchestrator.run({})
        assert result.success is False
        assert "A" in rollback_log

    def test_orchestrator_with_event_bus(self):
        """验证编排器集成 EventBus 发布阶段事件。"""
        from src.core.events.bus import EventBus
        from src.core.workflow.orchestrator import PortingOrchestrator

        bus = EventBus()
        start_events = []
        end_events = []
        bus.subscribe("phase.start", lambda e: start_events.append(e))
        bus.subscribe("phase.end", lambda e: end_events.append(e))

        orchestrator = PortingOrchestrator(
            event_bus=bus,
            phases=[RecordingPhase("A", []), RecordingPhase("B", [])],
        )
        orchestrator.run({})

        assert len(start_events) == 2
        assert len(end_events) == 2
        assert start_events[0].phase_name == "A"
        assert end_events[0].phase_name == "A"
        assert end_events[0].success is True

    def test_orchestrator_publishes_error_event_on_failure(self):
        """验证编排器在阶段失败时发布 ErrorEvent。"""
        from src.core.events.bus import EventBus
        from src.core.workflow.orchestrator import PortingOrchestrator

        bus = EventBus()
        error_events = []
        bus.subscribe("error", lambda e: error_events.append(e))

        orchestrator = PortingOrchestrator(event_bus=bus, phases=[])
        orchestrator.run({})
        assert len(error_events) == 0

        orchestrator2 = PortingOrchestrator(
            event_bus=bus, phases=[FailingPhase("err")]
        )
        orchestrator2.run({})
        assert len(error_events) >= 1

    def test_orchestrator_with_monitor(self):
        """验证编排器集成 Monitor 采集执行指标。"""
        from src.core.monitoring import Monitor
        from src.core.workflow.orchestrator import PortingOrchestrator

        monitor = Monitor()
        monitor.start()

        orchestrator = PortingOrchestrator(
            monitor=monitor,
            phases=[RecordingPhase("X", []), RecordingPhase("Y", [])],
        )
        orchestrator.run({})

        monitor.stop()
        report = monitor.report.generate()
        assert "X" in report["phase_results"]
        assert "Y" in report["phase_results"]

    def test_orchestrator_empty_pipeline_returns_success(self):
        """验证空阶段列表返回成功。"""
        from src.core.workflow.orchestrator import PortingOrchestrator

        orchestrator = PortingOrchestrator(phases=[])
        result = orchestrator.run({})
        assert result.success is True
        assert result.completed_phases == []

    def test_orchestrator_context_passed_between_phases(self):
        """验证编排器在阶段之间传递上下文。"""
        from src.core.workflow.orchestrator import PortingOrchestrator

        orchestrator = PortingOrchestrator(
            phases=[
                ContextModifyingPhase("A", "step1", "done"),
                ContextModifyingPhase("B", "step2", "done"),
            ]
        )
        result = orchestrator.run({"initial": True})
        assert result.success is True
        assert result.context["initial"] is True
        assert result.context["step1"] == "done"
        assert result.context["step2"] == "done"

    def test_orchestrator_returns_pipeline(self):
        """验证编排器提供 pipeline 属性访问。"""
        from src.core.workflow.orchestrator import PortingOrchestrator

        orchestrator = PortingOrchestrator(
            phases=[RecordingPhase("A", [])]
        )
        result = orchestrator.run({})
        assert result.success is True

    def test_backward_compat_create_default_pipeline(self):
        """验证原有工厂函数仍然可用。"""
        from src.core.workflow.orchestrator import create_default_pipeline

        pipeline = create_default_pipeline()
        assert len(pipeline._phases) == 4

    def test_backward_compat_run_porting_pipeline(self):
        """验证原有 run_porting_pipeline 函数签名保持不变。"""
        from src.core.workflow.orchestrator import run_porting_pipeline
        import inspect

        sig = inspect.signature(run_porting_pipeline)
        param_names = list(sig.parameters.keys())
        assert "context" in param_names
        assert "event_bus" in param_names
        assert "monitor" in param_names
