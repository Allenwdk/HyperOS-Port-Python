"""集成测试共享 fixture。

提供模拟完整移植流程所需的通用测试夹具，包括模拟 ROM 包、
工作目录、事件总线和监控系统等。
"""

import logging
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import MagicMock, Mock, patch

import pytest

from src.core.events.bus import EventBus
from src.core.events.events import Event, PhaseEndEvent, PhaseStartEvent


# ============================================================
# 辅助 Phase 类：用于集成测试的轻量级实现
# ============================================================


class RecordingPhase:
    """记录执行顺序的测试 Phase。"""

    def __init__(self, name: str, log: list, context_keys: list | None = None):
        self.name = name
        self.description = f"集成测试阶段 {name}"
        self._log = log
        self._context_keys = context_keys or []

    def execute(self, context: dict) -> dict:
        self._log.append(self.name)
        for key in self._context_keys:
            context[f"{self.name}_{key}"] = True
        return context

    def rollback(self, context: dict) -> dict:
        self._log.append(f"rollback:{self.name}")
        return context


class ContextChainingPhase:
    """在上下文间传递数据的测试 Phase。"""

    def __init__(self, name: str, output_key: str, output_value: Any):
        self.name = name
        self.description = f"上下文传递阶段 {name}"
        self._output_key = output_key
        self._output_value = output_value

    def execute(self, context: dict) -> dict:
        context[self._output_key] = self._output_value
        return context

    def rollback(self, context: dict) -> dict:
        context.pop(self._output_key, None)
        return context


class FailingPhase:
    """执行失败的测试 Phase。"""

    def __init__(self, name: str, error_msg: str = "集成测试错误"):
        self.name = name
        self.description = f"失败阶段 {name}"
        self._error_msg = error_msg

    def execute(self, context: dict) -> dict:
        raise RuntimeError(self._error_msg)

    def rollback(self, context: dict) -> dict:
        return context


class SlowPhase:
    """模拟耗时操作的测试 Phase。"""

    def __init__(self, name: str, duration: float = 0.01):
        self.name = name
        self.description = f"慢速阶段 {name}"
        self._duration = duration

    def execute(self, context: dict) -> dict:
        time.sleep(self._duration)
        context[f"{self.name}_done"] = True
        return context

    def rollback(self, context: dict) -> dict:
        return context


# ============================================================
# 模拟 ROM 包工厂
# ============================================================


def make_mock_rom(label: str = "Stock", device_code: str = "fuxi") -> MagicMock:
    """创建模拟的 RomPackage 实例。"""
    rom = MagicMock()
    rom.label = label
    rom.rom_type = MagicMock()
    rom.rom_type.name = "PAYLOAD"
    rom.props = {
        "ro.product.name_for_attestation": device_code,
        "ro.product.vendor.device": device_code,
        "ro.build.display.id": f"HyperOS 3.0.{'100' if label == 'Stock' else '200'}.0",
        "ro.build.version.sdk": "35",
        "ro.build.version.security_patch": "2026-01-01",
    }
    rom.payload_info = {"partitions": ["system", "vendor", "product"]}
    rom.get_prop.side_effect = lambda key, default=None: rom.props.get(key, default)
    rom.extract_images.return_value = None
    rom.export_props.return_value = None
    return rom


def make_mock_porting_context(stock_code: str = "fuxi") -> MagicMock:
    """创建模拟的 PortingContext 实例。"""
    ctx = MagicMock()
    ctx.stock_rom_code = stock_code
    ctx.target_rom_version = "OS3.0.304.0"
    ctx.security_patch = "2026-01-01"
    ctx.is_ab_device = True
    ctx.base_android_version = "16"
    ctx.port_android_version = "16"
    ctx.is_port_eu_rom = False
    ctx.is_port_global_rom = True
    ctx.port_global_region = "eea"
    ctx.enable_ksu = False
    ctx.enable_custom_avb_chain = False
    ctx.device_config = {
        "pack": {"type": "payload", "fs_type": "erofs"},
        "ksu": {"enable": False},
    }
    ctx.logger = logging.getLogger("MockPortingContext")
    return ctx


# ============================================================
# pytest fixture
# ============================================================


@pytest.fixture
def event_bus():
    """提供一个新的 EventBus 实例。"""
    return EventBus()


@pytest.fixture
def mock_stock_rom():
    """提供模拟的 Stock ROM 包。"""
    return make_mock_rom("Stock", "fuxi")


@pytest.fixture
def mock_port_rom():
    """提供模拟的 Port ROM 包。"""
    return make_mock_rom("Port", "vermeer")


@pytest.fixture
def mock_porting_context():
    """提供模拟的 PortingContext。"""
    return make_mock_porting_context()


@pytest.fixture
def integration_context(tmp_path, mock_stock_rom, mock_port_rom):
    """提供完整的集成测试上下文字典。"""
    stock_dir = tmp_path / "stockrom"
    port_dir = tmp_path / "portrom"
    target_dir = tmp_path / "target"
    stock_dir.mkdir()
    port_dir.mkdir()
    target_dir.mkdir()

    return {
        "stock_rom_path": str(tmp_path / "stock.zip"),
        "port_rom_path": str(tmp_path / "port.zip"),
        "stock_work_dir": stock_dir,
        "port_work_dir": port_dir,
        "target_work_dir": target_dir,
        "is_official_modify": False,
        "cache_manager": None,
        "eu_bundle": None,
        "phases_to_run": ["system", "apk", "framework", "firmware"],
        "pack_type": "payload",
        "fs_type": "erofs",
    }


@pytest.fixture
def event_collector(event_bus):
    """订阅所有事件并收集到列表中。"""
    collected: List[Event] = []
    event_bus.subscribe("*", lambda e: collected.append(e))
    return collected
