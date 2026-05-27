"""监控系统与主移植工作流的集成模块。

提供以下集成能力：
- MonitoredPhaseWrapper: 将 Phase 包装为带自动监控的版本
- MonitoredPortingOrchestrator: 带监控的编排器，自动采集阶段指标并生成报告
- create_monitored_orchestrator: 工厂方法，快速创建带监控的编排器
- MonitoredPortingWorkflow: 旧版工作流监控封装（向后兼容）
"""

import logging
from pathlib import Path
from typing import Any, Optional

from src.core.monitoring import (
    EventBus as MonitoringEventBus,
)
from src.core.monitoring import (
    MetricsCollector,
    Monitor,
    get_monitor,
)
from src.core.monitoring.console_ui import ConsoleReporter
from src.core.workflow.phases import Phase
from src.core.workflow.pipeline import Pipeline, PipelineResult

logger = logging.getLogger(__name__)


class MonitoredPhaseWrapper(Phase):
    """将 Phase 包装为带自动监控能力的版本。

    在阶段执行前后自动采集耗时、成功/失败指标，并通过 EventBus 发布事件。
    委托所有实际逻辑给被包装的 Phase 实例。
    """

    def __init__(
        self,
        inner: Phase,
        collector: Optional[MetricsCollector] = None,
        event_bus: Optional[MonitoringEventBus] = None,
    ):
        self._inner = inner
        self._collector = collector or MetricsCollector()
        self._event_bus = event_bus

    @property
    def name(self) -> str:
        return self._inner.name

    @property
    def description(self) -> str:
        return self._inner.description

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        prefix = f"phase.{self._inner.name}"
        self._collector.increment(f"{prefix}.attempts")
        import time
        start = time.time()
        success = False
        try:
            result = self._inner.execute(context)
            success = True
            return result
        finally:
            duration = time.time() - start
            self._collector.record(f"{prefix}.duration", duration, unit="s")
            if success:
                self._collector.increment(f"{prefix}.success")
            else:
                self._collector.increment(f"{prefix}.failures")
            if self._event_bus is not None:
                self._event_bus.publish(
                    "monitor.phase",
                    {
                        "phase": self._inner.name,
                        "duration": duration,
                        "success": success,
                    },
                )

    def rollback(self, context: dict[str, Any]) -> dict[str, Any]:
        return self._inner.rollback(context)


class MonitoredPortingOrchestrator:
    """带监控的移植流程编排器。

    自动将每个 Phase 包装为 MonitoredPhaseWrapper，采集执行指标，
    生成监控报告，并支持 ConsoleReporter 实时进度显示。
    """

    def __init__(
        self,
        monitor: Optional[Monitor] = None,
        phases: Optional[list[Phase]] = None,
        report_path: Optional[Path] = None,
        reporter: Optional[ConsoleReporter] = None,
    ):
        self._monitor = monitor or Monitor()
        self._phases = phases or []
        self._report_path = report_path
        self._reporter = reporter

    def run(self, context: dict[str, Any]) -> PipelineResult:
        self._monitor.start()

        wrapped_phases = [
            MonitoredPhaseWrapper(
                phase,
                collector=self._monitor.report.metrics_collector,
                event_bus=None,
            )
            for phase in self._phases
        ]

        pipeline = Pipeline(monitor=self._monitor)
        for phase in wrapped_phases:
            pipeline.add_phase(phase)

        try:
            if self._reporter:
                for phase in wrapped_phases:
                    self._reporter.on_phase_start(phase.name)

            result = pipeline.run(context)

            if self._reporter:
                for phase in wrapped_phases:
                    self._reporter.on_phase_end(phase.name, result.success, result.total_duration)

            return result

        finally:
            self._monitor.stop()
            if self._report_path:
                self._monitor.save_report(self._report_path)


def create_monitored_orchestrator(
    report_path: Optional[Path] = None,
) -> MonitoredPortingOrchestrator:
    """工厂方法：创建带默认配置的监控编排器。"""
    monitor = Monitor()
    return MonitoredPortingOrchestrator(monitor=monitor, report_path=report_path)


class MonitoredPortingWorkflow:
    """ROM 移植工作流监控封装（向后兼容）。

    包装标准移植流程，添加全面的监控能力。
    """

    def __init__(self, context, report_path: Optional[Path] = None):
        self.ctx = context
        self.report_path = report_path or Path("porting_report.json")
        self.monitor = get_monitor()
        self.reporter = ConsoleReporter()
        self.monitor.add_progress_listener(self.reporter.on_progress_update)

    def run(self) -> bool:
        """Execute the full porting workflow with monitoring."""
        from src.core.modifiers import (
            ApkModifier,
            FirmwareModifier,
            FrameworkModifier,
            RomModifier,
            SystemModifier,
        )
        from src.core.packer import Repacker
        from src.core.props import PropertyModifier

        self.monitor.start()

        try:
            # Phase 1: System Modification
            with self.monitor.phase("system_modification"):
                self.reporter.on_phase_start("System Modification")
                system_modifier = SystemModifier(self.ctx)
                system_modifier.run()
                self.reporter.on_phase_end(
                    "System Modification",
                    True,
                    self.monitor.report.execution_tracer.get_summary()["total_duration"],
                )

            # Phase 2: Property Modification
            with self.monitor.phase("property_modification"):
                self.reporter.on_phase_start("Property Modification")
                PropertyModifier(self.ctx).run()
                self.reporter.on_phase_end(
                    "Property Modification",
                    True,
                    0,  # Duration tracked by tracer
                )

            # Phase 3: Framework Modification
            with self.monitor.phase("framework_modification"):
                self.reporter.on_phase_start("Framework Modification")
                fw_modifier = FrameworkModifier(self.ctx)
                fw_modifier.run()
                self.reporter.on_phase_end("Framework Modification", True, 0)

            # Phase 4: Firmware Modification
            with self.monitor.phase("firmware_modification"):
                self.reporter.on_phase_start("Firmware Modification")
                FirmwareModifier(self.ctx).run()
                self.reporter.on_phase_end("Firmware Modification", True, 0)

            # Phase 5: ROM Modification
            with self.monitor.phase("rom_modification"):
                self.reporter.on_phase_start("ROM Modification")
                RomModifier(self.ctx).run_all_modifications()
                self.reporter.on_phase_end("ROM Modification", True, 0)

            # Phase 6: App Patching
            with self.monitor.phase("app_patching"):
                self.reporter.on_phase_start("App Patching")
                apk_modifier = ApkModifier(self.ctx)
                apk_modifier.run()
                self.reporter.on_phase_end("App Patching", True, 0)

            # Phase 7: Repacking
            with self.monitor.phase("repacking"):
                self.reporter.on_phase_start("Repacking")

                packer = Repacker(self.ctx)
                packer.pack_all()

                # Determine packing strategy
                if getattr(self.ctx, "pack_type", "payload") == "super":
                    packer.pack_super_image()
                else:
                    packer.pack_ota_payload()

                self.reporter.on_phase_end("Repacking", True, 0)

            success = True

        except Exception as e:
            success = False
            self.monitor.report.add_error("porting", e)
            raise

        finally:
            # Always generate report
            self.monitor.stop()
            self.monitor.save_report(self.report_path)
            self.monitor.print_report()

        return success


def run_monitored_porting(context, report_path: Optional[Path] = None) -> bool:
    """Run ROM porting with full monitoring.

    This is a convenience function for running the porting process
    with monitoring enabled.

    Args:
        context: The porting context (PortingContext instance)
        report_path: Optional path to save the monitoring report

    Returns:
        bool: True if successful

    Example:
        ctx = PortingContext(stock_rom, port_rom, target_dir)
        success = run_monitored_porting(ctx, Path("my_report.json"))
    """
    workflow = MonitoredPortingWorkflow(context, report_path)
    return workflow.run()
