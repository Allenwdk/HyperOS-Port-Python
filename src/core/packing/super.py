"""Super 镜像构建模块。"""

import hashlib
import json
import logging
import os
import shutil
import subprocess
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.events.bus import EventBus
from src.core.monitoring import MetricsCollector, MonitoredComponent
from src.core.packing.constants import DEVICE_SIZE_MAP, SUPER_SIZE_DEFAULT
from src.utils.shell import ShellRunner

logger = logging.getLogger(__name__)

DEFAULT_PARTITIONS: List[str] = [
    "system", "system_ext", "product", "vendor",
    "odm", "mi_ext", "system_dlkm", "vendor_dlkm",
]

DYNAMIC_PARTITIONS: List[str] = [
    "odm", "mi_ext", "system", "system_ext", "product",
    "vendor", "odm_dlkm", "vendor_dlkm", "system_dlkm", "product_dlkm",
]


@dataclass
class PartitionLayout:
    """分区布局信息。"""
    partitions: List[str] = field(default_factory=list)
    partition_sizes: Dict[str, int] = field(default_factory=dict)
    super_size: int = 0
    is_ab: bool = False
    group_name: str = "qti_dynamic_partitions"

    @property
    def total_partition_size(self) -> int:
        return sum(self.partition_sizes.values())

    @property
    def metadata_slots(self) -> int:
        return 3 if self.is_ab else 2


class SuperImageBuilder(MonitoredComponent):
    """Super 镜像构建器，集成 EventBus 和监控。"""

    def __init__(
        self,
        context: Any,
        collector: Optional[MetricsCollector] = None,
        event_bus: Optional[EventBus] = None,
    ):
        super().__init__(collector=collector, name="super_image_builder", event_bus=event_bus)
        self.ctx = context
        self.logger = logging.getLogger("SuperImageBuilder")
        self.shell = ShellRunner()
        self.ota_tools_dir: Path = Path("otatools").resolve()
        self.out_dir: Path = Path("out").resolve()

    def build(self) -> None:
        self.logger.info("开始打包 super.img...")
        self._publish_event("super.build.start", {})
        with self.track_execution("super.build"):
            lpmake_path = self.ota_tools_dir / "bin" / "lpmake"
            if not lpmake_path.exists():
                self.logger.error(f"lpmake 未找到: {lpmake_path}")
                self._publish_event("super.build.error", {"error": "lpmake_not_found"})
                return
            layout = self._detect_layout()
            super_img = self.ctx.target_dir / "super.img"
            args = self._build_lpmake_args(layout, lpmake_path, super_img)
            try:
                self.shell.run(args)
                self.logger.info("super.img 生成成功")
            except subprocess.CalledProcessError as e:
                self.logger.error(f"生成 super.img 失败: {e}")
                self._publish_event("super.build.error", {"error": str(e)})
                return
            zst_path = self._compress_super(super_img)
            self._generate_flash_script(zst_path if zst_path.exists() else super_img)
        self._publish_event("super.build.complete", {})
        self.logger.info("super.img 打包完成")

    def _detect_layout(self) -> PartitionLayout:
        return PartitionLayout(
            partitions=self._get_partition_list(),
            partition_sizes=self._collect_partition_sizes(),
            super_size=self._get_super_size(),
            is_ab=self.ctx.is_ab_device,
            group_name="qti_dynamic_partitions",
        )

    def _get_partition_list(self) -> List[str]:
        """优先级：device_config > partition_info.json > 默认列表。"""
        config_partitions = self.ctx.device_config.get("pack", {}).get("partitions")
        if config_partitions:
            self.logger.info(f"使用设备配置中的分区列表: {config_partitions}")
            return list(config_partitions)
        partition_info_path = Path(f"devices/{self.ctx.stock_rom_code}/partition_info.json")
        if partition_info_path.exists():
            try:
                info = json.loads(partition_info_path.read_text(encoding="utf-8"))
                partitions = info.get("dynamic_partitions", [])
                if partitions:
                    self.logger.info(f"使用 partition_info.json 中的分区列表: {partitions}")
                    return list(partitions)
            except (json.JSONDecodeError, OSError) as e:
                self.logger.warning(f"读取 partition_info.json 失败: {e}")
        self.logger.info(f"使用默认分区列表: {DEFAULT_PARTITIONS}")
        return list(DEFAULT_PARTITIONS)

    def _get_super_size(self) -> int:
        """优先级：device_config > partition_info.json > 内置映射表 > 默认值。"""
        config_size = self.ctx.device_config.get("pack", {}).get("super_size")
        if config_size:
            self.logger.info(f"使用设备配置中的 super_size: {config_size}")
            return int(config_size)
        partition_info_path = Path(f"devices/{self.ctx.stock_rom_code}/partition_info.json")
        if partition_info_path.exists():
            try:
                info = json.loads(partition_info_path.read_text(encoding="utf-8"))
                size = info.get("super_size")
                if size:
                    self.logger.info(f"使用 partition_info.json 中的 super_size: {size}")
                    return int(size)
            except (json.JSONDecodeError, OSError) as e:
                self.logger.debug(f"读取 partition_info.json 中的 super_size 失败: {e}")
        device_code = self.ctx.stock_rom_code.upper()
        for size, devices in DEVICE_SIZE_MAP.items():
            if device_code in devices:
                self.logger.info(f"使用内置映射中的 super_size: {device_code} -> {size}")
                return size
        self.logger.info(f"使用默认 super_size: {SUPER_SIZE_DEFAULT}")
        return SUPER_SIZE_DEFAULT

    def _collect_partition_sizes(self) -> Dict[str, int]:
        sizes: Dict[str, int] = {}
        for part in DYNAMIC_PARTITIONS:
            img_path = self.ctx.target_dir / f"{part}.img"
            if img_path.exists():
                sizes[part] = img_path.stat().st_size
        return sizes

    def _build_lpmake_args(
        self, layout: PartitionLayout, lpmake_path: Path, super_img: Path,
    ) -> List[str]:
        args: List[str] = [
            str(lpmake_path), "--metadata-size", "65536",
            "--super-name", "super", "--block-size", "4096",
            "--device", f"super:{layout.super_size}", "--output", str(super_img),
        ]
        if not layout.is_ab:
            self.logger.info("打包 A-only super.img")
            args.extend([
                "--metadata-slots", str(layout.metadata_slots),
                "--group", f"{layout.group_name}:{layout.super_size}", "-F",
            ])
            for part in layout.partitions:
                img_path = self.ctx.target_dir / f"{part}.img"
                if img_path.exists():
                    size = layout.partition_sizes.get(part, img_path.stat().st_size)
                    args.extend([
                        "--partition", f"{part}:none:{size}:{layout.group_name}",
                        "--image", f"{part}={img_path}",
                    ])
        else:
            self.logger.info("打包 V-AB super.img")
            args.extend([
                "--virtual-ab", "--metadata-slots", str(layout.metadata_slots),
                "--group", f"{layout.group_name}_a:{layout.super_size}",
                "--group", f"{layout.group_name}_b:{layout.super_size}", "-F",
            ])
            for part in layout.partitions:
                img_path = self.ctx.target_dir / f"{part}.img"
                if img_path.exists():
                    size = layout.partition_sizes.get(part, img_path.stat().st_size)
                    args.extend([
                        "--partition", f"{part}_a:none:{size}:{layout.group_name}_a",
                        "--image", f"{part}_a={img_path}",
                        "--partition", f"{part}_b:none:0:{layout.group_name}_b",
                    ])
        return args

    def _compress_super(self, super_img: Path) -> Path:
        self.logger.info("压缩 super.img 为 super.zst...")
        zst_path = self.ctx.target_dir / "super.zst"
        try:
            self.shell.run(["zstd", "--rm", str(super_img), "-o", str(zst_path)])
            self.logger.info("super.zst 压缩完成")
        except subprocess.CalledProcessError as e:
            self.logger.warning(f"zstd 压缩失败: {e}")
        return zst_path

    def _generate_flash_script(self, super_image_path: Path) -> None:
        self.logger.info("生成混合刷机脚本...")
        out_name = f"{self.ctx.stock_rom_code}_{self.ctx.target_rom_version}_hybrid"
        out_path = self.out_dir / out_name
        if out_path.exists():
            shutil.rmtree(out_path)
        out_path.mkdir(parents=True, exist_ok=True)

        bin_windows = out_path / "bin/windows"
        bin_windows.mkdir(parents=True, exist_ok=True)
        firmware_update = out_path / "firmware-update"
        firmware_update.mkdir(parents=True, exist_ok=True)
        meta_inf = out_path / "META-INF/com/google/android"
        meta_inf.mkdir(parents=True, exist_ok=True)

        self.logger.info(f"复制 {super_image_path.name}...")
        shutil.copy2(super_image_path, out_path / "super.zst")
        if self.ctx.repack_images_dir.exists():
            for fw in self.ctx.repack_images_dir.glob("*.img"):
                if fw.name == "boot.img":
                    shutil.copy2(fw, out_path / "boot.img")
                else:
                    shutil.copy2(fw, firmware_update)

        flash_template = Path("bin/flash")
        if flash_template.exists():
            self._copy_flash_tools(flash_template, bin_windows, out_path, meta_inf)
            self._process_flash_scripts(flash_template, out_path, meta_inf, firmware_update)
        self._zip_hybrid_package(out_path, firmware_update)

    def _copy_flash_tools(
        self, flash_template: Path, bin_windows: Path, out_path: Path, meta_inf: Path,
    ) -> None:
        if (flash_template / "platform-tools-windows").exists():
            shutil.copytree(flash_template / "platform-tools-windows", bin_windows, dirs_exist_ok=True)
        zstd_bin = flash_template / "zstd"
        if zstd_bin.exists():
            shutil.copy2(zstd_bin, out_path / "META-INF/zstd")
        (meta_inf / "updater-script").write_text("# dummy\n", encoding="utf-8")

    def _process_flash_scripts(
        self, flash_template: Path, out_path: Path, meta_inf: Path, firmware_update: Path,
    ) -> None:
        files_to_process: Dict[str, Path] = {
            "windows_flash_script.bat": out_path / "windows_flash_script.bat",
            "mac_linux_flash_script.sh": out_path / "mac_linux_flash_script.sh",
            "update-binary": meta_inf / "update-binary",
        }
        for src_name, dest_path in files_to_process.items():
            src_file = flash_template / src_name
            if src_file.exists():
                shutil.copy2(src_file, dest_path)
                self._process_script_placeholders(dest_path)
                if "flash_script" in src_name:
                    if not self.ctx.is_ab_device:
                        self._patch_script_for_a_only(dest_path)
                    self._patch_script_for_firmware(dest_path, firmware_update)
                if src_name == "update-binary":
                    if not self.ctx.is_ab_device:
                        self._patch_update_binary_for_a_only(dest_path)
                    self._patch_update_binary_firmware(dest_path, firmware_update)

    def _process_script_placeholders(self, file_path: Path) -> None:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
        replacements: Dict[str, str] = {
            "device_code": self.ctx.stock_rom_code,
            "baseversion": str(self.ctx.base_android_version),
            "portversion": str(self.ctx.target_rom_version),
        }
        for key, value in replacements.items():
            content = content.replace(key, value)
        file_path.write_text(content, encoding="utf-8")

    def _patch_script_for_a_only(self, script_path: Path) -> None:
        content = script_path.read_text(encoding="utf-8", errors="ignore")
        content = content.replace("_a", "").replace("_b", "")
        new_lines = [line for line in content.splitlines() if "_b" not in line]
        script_path.write_text("\n".join(new_lines), encoding="utf-8")

    def _patch_update_binary_for_a_only(self, script_path: Path) -> None:
        content = script_path.read_text(encoding="utf-8", errors="ignore")
        content = (
            content.replace("boot_a", "boot").replace("boot_b", "boot")
            .replace("dtbo_a", "dtbo").replace("dtbo_b", "dtbo")
        )
        content = content.replace("bootctl set-active-boot-slot a", "")
        new_lines = [line for line in content.splitlines() if "lptools unmap" not in line]
        script_path.write_text("\n".join(new_lines), encoding="utf-8")

    def _patch_update_binary_firmware(self, script_path: Path, firmware_dir: Path) -> None:
        fw_files = [f.name for f in firmware_dir.glob("*")]
        if not fw_files:
            return
        content = script_path.read_text(encoding="utf-8", errors="ignore")
        insertion: List[str] = []
        for fw in fw_files:
            part = self._firmware_name_to_partition(fw)
            if "dtbo" in fw or "cust" in fw or fw == "boot.img":
                continue
            if self.ctx.is_ab_device:
                insertion.append(f'package_extract_file "firmware-update/{fw}" "/dev/block/bootdevice/by-name/{part}_a"')
                insertion.append(f'package_extract_file "firmware-update/{fw}" "/dev/block/bootdevice/by-name/{part}_b"')
            else:
                insertion.append(f'package_extract_file "firmware-update/{fw}" "/dev/block/bootdevice/by-name/{part}"')
        marker = "# firmware"
        if marker in content:
            parts = content.split(marker)
            new_content = parts[0] + marker + "\n" + "\n".join(insertion) + parts[1]
            script_path.write_text(new_content, encoding="utf-8")
        else:
            self.logger.warning(f"标记 '{marker}' 未在 update-binary 中找到")

    def _patch_script_for_firmware(self, script_path: Path, firmware_dir: Path) -> None:
        fw_files = [f.name for f in firmware_dir.glob("*")]
        if not fw_files:
            return
        content = script_path.read_text(encoding="utf-8", errors="ignore")
        is_windows = script_path.suffix == ".bat"
        insertion: List[str] = []
        for fw in fw_files:
            part = self._firmware_name_to_partition(fw)
            if "dtbo" in fw or "cust" in fw or fw == "boot.img":
                continue
            if self.ctx.is_ab_device:
                if is_windows:
                    insertion.append(f"bin\\windows\\fastboot.exe flash {part}_a %~dp0firmware-update\\{fw}")
                    insertion.append(f"bin\\windows\\fastboot.exe flash {part}_b %~dp0firmware-update\\{fw}")
                else:
                    insertion.append(f"fastboot flash {part}_a firmware-update/{fw}")
                    insertion.append(f"fastboot flash {part}_b firmware-update/{fw}")
            else:
                if is_windows:
                    insertion.append(f"bin\\windows\\fastboot.exe flash {part} %~dp0firmware-update\\{fw}")
                else:
                    insertion.append(f"fastboot flash {part} firmware-update/{fw}")
        marker = "REM firmware" if is_windows else "# firmware"
        if marker in content:
            parts = content.split(marker)
            new_content = parts[0] + marker + "\n" + "\n".join(insertion) + parts[1]
            script_path.write_text(new_content, encoding="utf-8")

    @staticmethod
    def _firmware_name_to_partition(fw_name: str) -> str:
        mapping: Dict[str, str] = {
            "uefi_sec.mbn": "uefisecapp", "qupv3fw.elf": "qupfw",
            "NON-HLOS.bin": "modem", "km4.mbn": "keymaster",
            "BTFM.bin": "bluetooth", "dspso.bin": "dsp",
        }
        return mapping.get(fw_name, fw_name.split(".")[0])

    def _zip_hybrid_package(self, out_path: Path, firmware_update: Path) -> None:
        self.logger.info("打包混合刷机包 ZIP...")
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        final_zip_name = f"{self.ctx.stock_rom_code}-hybrid-{self.ctx.target_rom_version}-{timestamp}.zip"
        final_zip_path = self.out_dir / final_zip_name
        with zipfile.ZipFile(final_zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for root, _dirs, files in os.walk(out_path):
                for file in files:
                    file_path = Path(root) / file
                    arcname = file_path.relative_to(out_path)
                    zf.write(file_path, arcname, compress_type=zipfile.ZIP_STORED if file == "super.zst" else zipfile.ZIP_DEFLATED)
        md5 = hashlib.md5(final_zip_path.read_bytes()).hexdigest()[:10]
        renamed_zip_name = f"{self.ctx.stock_rom_code}_Hybrid_{self.ctx.target_rom_version}_{self.ctx.security_patch}_{md5}_{timestamp}.zip"
        renamed_zip_path = self.out_dir / renamed_zip_name
        final_zip_path.rename(renamed_zip_path)
        self.logger.info(f"混合刷机包已生成: {renamed_zip_path}")
        shutil.rmtree(out_path)

    def _publish_event(self, event_type: str, data: Dict[str, Any]) -> None:
        if self._event_bus is not None:
            from src.core.events.events import Event
            self._event_bus.publish(Event(event_type=event_type, data=data, source="super_builder"))
