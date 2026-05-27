"""工作流编排器。

提供 PortingOrchestrator 类和预配置的 Pipeline 工厂方法。
PortingOrchestrator 组合 Pipeline 和各 Phase，集成 EventBus 和监控系统，
自动发布阶段事件和采集执行指标。
"""

import logging
from typing import Any, Optional

from src.core.events.bus import EventBus
from src.core.monitoring import Monitor
from src.core.workflow.phases import (
    ExtractionPhase,
    InitializationPhase,
    ModificationPhase,
    PackingPhase,
    Phase,
)
from src.core.workflow.pipeline import Pipeline, PipelineResult

logger = logging.getLogger(__name__)


class PortingOrchestrator:
    """移植流程编排器。

    组合 Pipeline 和各 Phase，提供完整的移植流程编排能力。
    集成 EventBus 和监控系统，每个 Phase 的开始/结束自动发布事件和采集指标。
    支持错误处理和回滚机制。

    Args:
        event_bus: 事件总线实例（可选），用于发布阶段事件
        monitor: 监控实例（可选），用于采集执行指标
        phases: 自定义阶段列表（可选），默认使用四阶段移植流程
    """

    def __init__(
        self,
        event_bus: Optional[EventBus] = None,
        monitor: Optional[Monitor] = None,
        phases: Optional[list[Phase]] = None,
    ):
        self._event_bus = event_bus
        self._monitor = monitor
        self._phases = phases

    def run(self, context: dict[str, Any]) -> PipelineResult:
        """执行完整的移植流程。

        创建 Pipeline 并按顺序执行所有阶段。集成 EventBus 和监控系统，
        自动发布阶段开始/结束事件和采集执行指标。失败时自动回滚已执行阶段。

        Args:
            context: 初始执行上下文，包含移植流程所需的所有数据

        Returns:
            PipelineResult: 包含执行结果、耗时和错误信息
        """
        pipeline = self._build_pipeline()
        phase_count = len(pipeline._phases)

        logger.info("编排器启动移植流程（%d 个阶段）", phase_count)

        try:
            result = pipeline.run(context)
        except Exception as exc:
            logger.error("移植流程异常终止: %s", exc)
            return PipelineResult(success=False, context=context, error=exc)

        if result.success:
            logger.info("移植流程完成，耗时 %.2fs", result.total_duration)
        else:
            logger.error(
                "移植流程在阶段 '%s' 失败: %s",
                result.failed_phase,
                result.error,
            )

        return result

    def _build_pipeline(self) -> Pipeline:
        """构建配置好 EventBus 和 Monitor 的 Pipeline。"""
        pipeline = Pipeline(event_bus=self._event_bus, monitor=self._monitor)

        if self._phases is not None:
            for phase in self._phases:
                pipeline.add_phase(phase)
        else:
            pipeline.add_phase(ExtractionPhase())
            pipeline.add_phase(InitializationPhase())
            pipeline.add_phase(ModificationPhase())
            pipeline.add_phase(PackingPhase())

        return pipeline


def create_default_pipeline(
    event_bus: Optional[EventBus] = None,
    monitor: Optional[Monitor] = None,
) -> Pipeline:
    """创建默认的四阶段移植 Pipeline。

    按顺序添加 ExtractionPhase -> InitPhase -> ModifyPhase -> PackPhase。

    Args:
        event_bus: 事件总线实例（可选）
        monitor: 监控实例（可选）

    Returns:
        配置好的 Pipeline 实例
    """
    pipeline = Pipeline(event_bus=event_bus, monitor=monitor)
    pipeline.add_phase(ExtractionPhase())
    pipeline.add_phase(InitializationPhase())
    pipeline.add_phase(ModificationPhase())
    pipeline.add_phase(PackingPhase())
    return pipeline


def create_custom_pipeline(
    phases: list[Phase],
    event_bus: Optional[EventBus] = None,
    monitor: Optional[Monitor] = None,
) -> Pipeline:
    """创建自定义阶段组合的 Pipeline。

    Args:
        phases: 要执行的 Phase 列表（按执行顺序）
        event_bus: 事件总线实例（可选）
        monitor: 监控实例（可选）

    Returns:
        配置好的 Pipeline 实例
    """
    pipeline = Pipeline(event_bus=event_bus, monitor=monitor)
    for phase in phases:
        pipeline.add_phase(phase)
    return pipeline


def run_porting_pipeline(
    context: dict[str, Any],
    event_bus: Optional[EventBus] = None,
    monitor: Optional[Monitor] = None,
) -> PipelineResult:
    """执行默认的完整移植流程。

    创建默认 Pipeline 并执行，返回执行结果。

    Args:
        context: 初始执行上下文
        event_bus: 事件总线实例（可选）
        monitor: 监控实例（可选）

    Returns:
        PipelineResult: 执行结果
    """
    orchestrator = PortingOrchestrator(event_bus=event_bus, monitor=monitor)
    logger.info("启动默认移植流程（4 阶段）")
    return orchestrator.run(context)
