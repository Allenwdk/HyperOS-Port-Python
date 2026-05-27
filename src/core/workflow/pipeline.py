"""Pipeline 执行引擎。

管理 Phase 的顺序执行、错误处理和回滚。
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from src.core.events.bus import EventBus
from src.core.events.events import ErrorEvent, PhaseEndEvent, PhaseStartEvent
from src.core.monitoring import Monitor
from src.core.workflow.phases import Phase

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    """Pipeline 执行结果。"""

    success: bool = True
    context: dict[str, Any] = field(default_factory=dict)
    completed_phases: list[str] = field(default_factory=list)
    total_duration: float = 0.0
    error: Optional[Exception] = None
    failed_phase: Optional[str] = None


class Pipeline:
    """工作流执行管道。

    管理多个 Phase 的顺序执行，支持错误回滚、事件发布和监控集成。

    Args:
        event_bus: 事件总线实例（可选），用于发布阶段事件
        monitor: 监控实例（可选），用于采集执行指标
    """

    def __init__(
        self,
        event_bus: Optional[EventBus] = None,
        monitor: Optional[Monitor] = None,
    ):
        self._phases: list[Phase] = []
        self._event_bus = event_bus
        self._monitor = monitor

    def add_phase(self, phase: Phase) -> None:
        """添加阶段到管道。

        Args:
            phase: 要添加的 Phase 实例
        """
        self._phases.append(phase)
        logger.debug("添加阶段: %s (%s)", phase.name, phase.description)

    def run(self, context: dict[str, Any]) -> PipelineResult:
        """执行管道中的所有阶段。

        按顺序执行所有已添加的阶段。如果某个阶段失败，
        逆序回滚已成功执行的阶段，并返回失败结果。

        Args:
            context: 初始执行上下文

        Returns:
            PipelineResult: 包含执行结果、耗时和错误信息
        """
        start_time = time.time()
        completed_phases: list[str] = []
        current_context = dict(context)

        for phase in self._phases:
            phase_start = time.time()

            self._publish_start(phase.name)
            self._monitor_start(phase.name)

            try:
                logger.info("开始执行阶段: %s", phase.name)
                current_context = phase.execute(current_context)
                phase_duration = time.time() - phase_start

                completed_phases.append(phase.name)

                self._publish_end(phase.name, success=True, duration=phase_duration)
                self._monitor_end(phase.name, success=True, duration=phase_duration)

                logger.info("阶段完成: %s (%.2fs)", phase.name, phase_duration)

            except Exception as exc:
                phase_duration = time.time() - phase_start

                self._publish_end(phase.name, success=False, duration=phase_duration)
                self._monitor_end(phase.name, success=False, duration=phase_duration)
                self._publish_error(phase.name, exc)

                logger.error("阶段失败: %s - %s", phase.name, exc)

                self._rollback_phases(completed_phases, current_context)

                total_duration = time.time() - start_time
                return PipelineResult(
                    success=False,
                    context=current_context,
                    completed_phases=completed_phases,
                    total_duration=total_duration,
                    error=exc,
                    failed_phase=phase.name,
                )

        total_duration = time.time() - start_time
        logger.info("管道执行完成，共 %d 个阶段，耗时 %.2fs", len(completed_phases), total_duration)

        return PipelineResult(
            success=True,
            context=current_context,
            completed_phases=completed_phases,
            total_duration=total_duration,
        )

    def _rollback_phases(self, completed_phases: list[str], context: dict[str, Any]) -> None:
        """逆序回滚已执行的阶段。"""
        for phase_name in reversed(completed_phases):
            phase = next((p for p in self._phases if p.name == phase_name), None)
            if phase is None:
                continue
            try:
                logger.info("回滚阶段: %s", phase_name)
                phase.rollback(context)
            except Exception as exc:
                logger.error("回滚阶段 %s 失败: %s", phase_name, exc)

    def _publish_start(self, phase_name: str) -> None:
        if self._event_bus:
            self._event_bus.publish(PhaseStartEvent(phase_name=phase_name))

    def _publish_end(self, phase_name: str, success: bool, duration: float) -> None:
        if self._event_bus:
            self._event_bus.publish(
                PhaseEndEvent(phase_name=phase_name, success=success, duration=duration)
            )

    def _publish_error(self, phase_name: str, error: Exception) -> None:
        if self._event_bus:
            self._event_bus.publish(
                ErrorEvent(
                    error_type=type(error).__name__,
                    error_message=str(error),
                    phase=phase_name,
                )
            )

    def _monitor_start(self, phase_name: str) -> None:
        if self._monitor:
            self._monitor.logger.info("监控阶段开始: %s", phase_name)

    def _monitor_end(self, phase_name: str, success: bool, duration: float) -> None:
        if self._monitor:
            self._monitor.report.add_phase_result(
                phase_name, success, {"duration": duration}
            )
            self._monitor.record_metric(f"phase.{phase_name}.duration", duration, "s")
