"""Repacker 协调器。

协调 AVBManager、OTAPacker、SuperImageBuilder 三个子组件，
保持与原有 Repacker 类相同的公共 API（向后兼容）。
集成 EventBus 和 MetricsCollector 实现统一的事件发布和监控。
"""

import concurrent.futures
import logging
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.monitoring import EventBus, MetricsCollector, MonitoredComponent
from src.core.packing.avb import AVBManager
from src.core.packing.ota import OTAPacker
from src.core.packing.super import SuperImageBuilder
from src.utils.contextpatch import ContextPatcher
from src.utils.fspatch import patch_fs_config
from src.utils.shell import ShellRunner

logger = logging.getLogger("Repacker")

DEFAULT_MAX_WORKERS = 4
FIX_TIMESTAMP = "1230768000"


class Repacker(MonitoredComponent):
    """打包协调器，统一调度 AVB、OTA、Super 子组件。

    保持与原 packer.py:Repacker 相同的公共 API：
    - pack_all(pack_type, is_rw): 并行打包所有分区
    - pack_super_image(): 构建 super.img
    - pack_ota_payload(): 构建 OTA payload
    """

    def __init__(
        self,
        context: Any,
        collector: Optional[MetricsCollector] = None,
        event_bus: Optional[EventBus] = None,
    ):
        event_bus = event_bus or EventBus()
        collector = collector or MetricsCollector()
        super().__init__(collector=collector, name="Repacker", event_bus=event_bus)
        self.ctx = context
        self.logger = logger
        self.shell = ShellRunner()
        self.selinux_patcher = ContextPatcher()

        self._avb = AVBManager(context, collector=self._collector)
        self._ota = OTAPacker(context, collector=self._collector)
        self._super = SuperImageBuilder(context, collector=self._collector)

    def _emit(self, event_type: str, data: Optional[Dict[str, Any]] = None) -> None:
        if self._event_bus is not None:
            self._event_bus.publish(event_type, data or {})

    def pack_all(self, pack_type: str = "EROFS", is_rw: bool = False) -> None:
        import time
        start = time.time()
        self._emit("repack.start", {"pack_type": pack_type})
        self.logger.info("开始重新打包，格式: %s", pack_type)

        partitions: List[str] = [
            item.name for item in self.ctx.target_dir.iterdir()
            if item.is_dir() and item.name not in ("config", "repack_images")
        ]

        with self.track_execution("repack.all"):
            with concurrent.futures.ThreadPoolExecutor(max_workers=DEFAULT_MAX_WORKERS) as executor:
                futures = {
                    executor.submit(self._pack_partition, part, pack_type, is_rw): part
                    for part in partitions
                }
                for future in concurrent.futures.as_completed(futures):
                    part_name = futures[future]
                    try:
                        future.result()
                    except (concurrent.futures.CancelledError, concurrent.futures.TimeoutError) as e:
                        self.logger.error("分区 %s 打包失败: %s", part_name, e)
                        self._emit("repack.error", {"partition": part_name, "error": str(e)})
                        raise
                    except RuntimeError as e:
                        self.logger.error("分区 %s 打包失败: %s", part_name, e)
                        self._emit("repack.error", {"partition": part_name, "error": str(e)})
                        raise

        self._emit("repack.complete", {"duration": time.time() - start})
        self.logger.info("所有分区打包完成")

    def _pack_partition(self, part_name: str, pack_type: str, is_rw: bool) -> None:
        src_dir = self.ctx.target_dir / part_name
        img_output = self.ctx.target_dir / f"{part_name}.img"
        fs_config = self.ctx.target_config_dir / f"{part_name}_fs_config"
        file_contexts = self.ctx.target_config_dir / f"{part_name}_file_contexts"

        self.logger.info("打包 [%s] 为 %s...", part_name, pack_type)
        self._run_patch_tools(src_dir, fs_config, file_contexts)

        if pack_type == "EXT":
            self._pack_ext4(part_name, src_dir, img_output, fs_config, file_contexts, is_rw)
        else:
            self._pack_erofs(part_name, src_dir, img_output, fs_config, file_contexts)

    def _run_patch_tools(self, src_dir: Path, fs_config: Path, file_contexts: Path) -> None:
        if fs_config.exists():
            try:
                patch_fs_config(src_dir, fs_config)
            except OSError as e:
                self.logger.error("修补 fs_config 出错: %s", e)
        else:
            self.logger.warning("未找到 %s 的 fs_config，跳过 fspatch。", src_dir.name)

        if file_contexts.exists():
            try:
                self.selinux_patcher.patch(src_dir, file_contexts)
            except OSError as e:
                self.logger.error("修补 file_contexts 出错: %s", e)
        else:
            self.logger.warning("未找到 %s 的 file_contexts，跳过 contextpatch。", src_dir.name)

    def _pack_erofs(
        self, part_name: str, src_dir: Path, img_output: Path, fs_config: Path, file_contexts: Path
    ) -> None:
        cmd = [
            "mkfs.erofs", "-zlz4hc,9", "-T", FIX_TIMESTAMP,
            "--mount-point", f"/{part_name}",
            "--fs-config-file", str(fs_config),
            "--file-contexts", str(file_contexts),
            str(img_output), str(src_dir),
        ]
        try:
            self.shell.run(cmd)
            self.logger.info("成功打包 %s.img (EROFS)", part_name)
        except subprocess.CalledProcessError as e:
            self.logger.error("打包 %s 失败: %s", part_name, e)

    def _pack_ext4(
        self, part_name: str, src_dir: Path, img_output: Path,
        fs_config: Path, file_contexts: Path, is_rw: bool,
    ) -> None:
        size_orig = self._get_dir_size(src_dir)
        if size_orig < 1048576:
            size = 1048576
        elif size_orig < 104857600:
            size = int(size_orig * 1.15)
        elif size_orig < 1073741824:
            size = int(size_orig * 1.08)
        else:
            size = int(size_orig * 1.03)
        size = (size // 4096) * 4096

        lost_found = src_dir / "lost+found"
        lost_found.mkdir(exist_ok=True)

        inode_count = 5000
        try:
            with open(fs_config, "r") as f:
                inode_count = sum(1 for _ in f) + 8
        except OSError:
            pass

        self._make_ext4_image(part_name, src_dir, img_output, size, inode_count, fs_config, file_contexts, is_rw)
        self.shell.run(["resize2fs", "-f", "-M", str(img_output)])

        if part_name == "mi_ext":
            return

        free_blocks = self._get_free_blocks(img_output)
        if free_blocks > 0:
            free_size = free_blocks * 4096
            current_img_size = img_output.stat().st_size
            new_size = (current_img_size - free_size) // 4096 * 4096
            self.logger.info("使用优化大小重新生成 %s.img: %d", part_name, new_size)
            img_output.unlink()
            self._make_ext4_image(part_name, src_dir, img_output, new_size, inode_count, fs_config, file_contexts, is_rw)
            self.shell.run(["resize2fs", "-f", "-M", str(img_output)])

    def _make_ext4_image(
        self, part_name: str, src_dir: Path, img_path: Path,
        size: int, inodes: int, fs_config: Path, file_contexts: Path, is_rw: bool,
    ) -> None:
        mkfs_cmd = [
            "mke2fs", "-O", "^has_journal", "-L", part_name,
            "-I", "256", "-N", str(inodes), "-M", f"/{part_name}",
            "-m", "0", "-t", "ext4", "-b", "4096",
            str(img_path), str(size // 4096),
        ]
        self.shell.run(mkfs_cmd)

        e2fs_cmd = [
            "e2fsdroid", "-e", "-T", FIX_TIMESTAMP,
            "-C", str(fs_config), "-S", str(file_contexts),
            "-f", str(src_dir), "-a", f"/{part_name}", str(img_path),
        ]
        if not is_rw:
            e2fs_cmd.insert(-1, "-s")
        self.shell.run(e2fs_cmd)

    @staticmethod
    def _get_dir_size(path: Path) -> int:
        try:
            output = subprocess.check_output(["du", "-sb", str(path)], text=True)
            return int(output.split()[0])
        except (subprocess.SubprocessError, ValueError, FileNotFoundError):
            total = 0
            for p in path.rglob("*"):
                if p.is_file() and not p.is_symlink():
                    total += p.stat().st_size
            return total if total > 0 else 4096

    @staticmethod
    def _get_free_blocks(img_path: Path) -> int:
        try:
            output = subprocess.check_output(["tune2fs", "-l", str(img_path)], text=True)
            for line in output.splitlines():
                if "Free blocks:" in line:
                    return int(line.split(":")[1].strip())
        except (subprocess.SubprocessError, ValueError, FileNotFoundError):
            return 0
        return 0

    def pack_super_image(self) -> None:
        self._super.build()

    def pack_ota_payload(self) -> None:
        self._ota.pack()
