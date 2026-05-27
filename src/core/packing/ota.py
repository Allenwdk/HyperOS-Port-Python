"""OTA 打包模块。

从 packer.py 提取的 OTA payload 打包逻辑，封装为独立的 OTAPacker 类。
支持事件发布和监控指标采集。
"""

import hashlib
import json
import logging
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, cast

from src.core.events.bus import EventBus
from src.core.monitoring import MetricsCollector, MonitoredComponent
from src.core.packing.constants import DEVICE_SIZE_MAP, SUPER_SIZE_DEFAULT
from src.utils.shell import ShellRunner

_BUILD_PROP_MAPPING: Dict[str, str] = {
    "system": "SYSTEM", "product": "PRODUCT", "system_ext": "SYSTEM_EXT",
    "vendor": "VENDOR", "odm": "ODM", "system_dlkm": "SYSTEM_DLKM",
    "vendor_dlkm": "VENDOR_DLKM", "odm_dlkm": "ODM_DLKM", "product_dlkm": "PRODUCT_DLKM",
}
_SUPER_PARTITIONS: List[str] = [
    "system", "vendor", "product", "system_ext", "odm", "mi_ext",
    "odm_dlkm", "vendor_dlkm", "system_dlkm", "product_dlkm",
]
_PARTITION_INFO_PATH = "devices/{code}/partition_info.json"
_DEFAULT_PARTITIONS = [
    "system", "system_ext", "product", "vendor", "odm", "mi_ext", "system_dlkm", "vendor_dlkm",
]


class OTAPacker(MonitoredComponent):
    """OTA 打包器，封装 payload 生成、META 结构、build.prop 复制等操作。"""

    def __init__(
        self,
        context: Any,
        collector: Optional[MetricsCollector] = None,
        event_bus: Optional[EventBus] = None,
    ):
        super().__init__(collector=collector, name="ota_packer", event_bus=event_bus)
        self.ctx = context
        self.logger: logging.Logger = logging.getLogger("OTAPacker")
        self.shell: ShellRunner = ShellRunner()
        self.ota_tools_dir: Path = Path("otatools").resolve()
        self.out_dir: Path = Path("out").resolve()
        self.product_out: Path = self.out_dir / "target" / "product" / self.ctx.stock_rom_code
        self.images_out: Path = self.product_out / "IMAGES"
        self.meta_out: Path = self.product_out / "META"

    def pack(self) -> None:
        """执行完整的 OTA payload 打包流程。"""
        self.logger.info("开始 OTA Payload 打包...")
        self._emit("ota.start", {"device": self.ctx.stock_rom_code})

        with self.track_execution("ota.pack"):
            if self.product_out.exists():
                shutil.rmtree(self.product_out)
            self.images_out.mkdir(parents=True, exist_ok=True)
            self.meta_out.mkdir(parents=True, exist_ok=True)
            for part in self._get_partition_list():
                (self.product_out / part.upper()).mkdir(exist_ok=True)
            self._copy_partition_images()
            self._copy_device_custom_images()
            self._handle_avb_chain()
            self._generate_meta_info()
            self._copy_build_props()
            if getattr(self.ctx, "enable_custom_avb_chain", False):
                from src.core.packing.avb import AVBManager
                AVBManager(self.ctx, self._collector, self._event_bus).verify_avb_images()
            self._run_ota_tool()

        self._emit("ota.complete", {"device": self.ctx.stock_rom_code})
        self.logger.info("OTA Payload 打包完成")

    def _emit(self, event_type: str, data: Dict[str, Any]) -> None:
        if self._event_bus is not None:
            from src.core.events.events import Event
            self._event_bus.publish(Event(event_type=event_type, data=data, source="OTAPacker"))

    def _copy_partition_images(self) -> None:
        for img in self.ctx.target_dir.glob("*.img"):
            shutil.copy2(img, self.images_out)
        if self.ctx.repack_images_dir.exists():
            for img in self.ctx.repack_images_dir.glob("*.img"):
                shutil.copy2(img, self.images_out)

    def _copy_device_custom_images(self) -> None:
        device_dir = Path(f"devices/{self.ctx.stock_rom_code}")
        if not device_dir.exists():
            return
        for pattern, dest in [("boot*.img", "boot.img"), ("dtbo*.img", "dtbo.img")]:
            matches = list(device_dir.glob(pattern))
            if matches:
                shutil.copy2(matches[0], self.images_out / dest)
                self.logger.info(f"使用 {matches[0].name} 替换 {dest}")
        for name in ["recovery.img", "init_boot-kernelsu.img"]:
            src = device_dir / name
            if src.exists():
                dest_name = "init_boot.img" if "init_boot" in name else name
                shutil.copy2(src, self.images_out / dest_name)

    def _handle_avb_chain(self) -> None:
        if not getattr(self.ctx, "enable_custom_avb_chain", False):
            return
        from src.core.packing.avb import AVBManager
        avb = AVBManager(self.ctx, self._collector, self._event_bus)
        parts = [img.stem for img in self.images_out.glob("*.img") if img.stem != "cust"]
        profile = avb.collect_stock_avb_profile()
        avb.sync_partition_info_from_stock_avb(profile)
        avb.apply_avb_to_custom_images(parts)
        avb.rebuild_vbmeta_images(parts)
        avb.generate_care_map()

    def _generate_meta_info(self) -> None:
        self.logger.info("生成 META 信息...")
        self.meta_out.mkdir(parents=True, exist_ok=True)
        parts = [img.stem for img in self.images_out.glob("*.img") if img.stem != "cust"]
        self._write_ab_partitions(parts)
        self._write_dynamic_partitions_info(parts)
        self._write_misc_info(parts)
        self._write_update_engine_config()

    def _write_ab_partitions(self, parts: List[str]) -> None:
        with open(self.meta_out / "ab_partitions.txt", "w") as f:
            f.writelines(f"{p}\n" for p in sorted(parts))

    def _write_dynamic_partitions_info(self, parts: List[str]) -> None:
        super_size = self._get_super_size()
        self.logger.info("当前打包 super_size: %d 字节 (%.2f GiB)", super_size, super_size / (1024**3))
        super_parts = [p for p in parts if p in _SUPER_PARTITIONS]
        dp_lines = [
            f"super_partition_size={super_size}",
            "super_partition_groups=qti_dynamic_partitions",
            f"super_qti_dynamic_partitions_group_size={super_size - 1048576}",
            f"super_qti_dynamic_partitions_partition_list={' '.join(super_parts)}",
            "virtual_ab=true",
        ]
        if self._is_vabc_enabled():
            dp_lines.extend(self._vabc_lines())
        with open(self.meta_out / "dynamic_partitions_info.txt", "w") as f:
            f.write("\n".join(dp_lines) + "\n")

    def _write_misc_info(self, parts: List[str]) -> None:
        misc_lines = ["recovery_api_version=3", "fstab_version=2", "ab_update=true"]
        misc_lines.extend(self._avb_misc_lines(parts))
        if self._is_vabc_enabled():
            misc_lines.append("virtual_ab_compression=true")
            misc_lines.extend(self._vabc_lines())
        with open(self.meta_out / "misc_info.txt", "w") as f:
            f.write("\n".join(dict.fromkeys(misc_lines)) + "\n")

    def _write_update_engine_config(self) -> None:
        with open(self.meta_out / "update_engine_config.txt", "w") as f:
            f.write("PAYLOAD_MAJOR_VERSION=2\nPAYLOAD_MINOR_VERSION=8\n")

    def _vabc_lines(self) -> List[str]:
        meta = self._get_dynamic_partition_metadata()
        method = meta.get("vabc_compression_param", "lz4") if meta else "lz4"
        cow = meta.get("cow_version", 3) if meta else 3
        factor = meta.get("compression_factor", 65536) if meta else 65536
        self.logger.info(f"Virtual A/B 压缩已启用: method={method}, cow_version={cow}, factor={factor}")
        return [
            f"virtual_ab_compression_method={method}",
            f"virtual_ab_cow_version={cow}",
            f"virtual_ab_compression_factor={factor}",
        ]

    def _copy_build_props(self) -> None:
        for part_lower, part_upper in _BUILD_PROP_MAPPING.items():
            src = self.ctx.get_target_prop_file(part_lower)
            if src and src.exists():
                dest_dir = self.product_out / part_upper
                dest_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest_dir / "build.prop")
            else:
                self.logger.warning(f"未找到 {part_lower} 的 build.prop")

    def _run_ota_tool(self) -> None:
        self.logger.info("运行 ota_from_target_files...")
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        output_zip = self.out_dir / f"{self.ctx.stock_rom_code}-ota_full-{ts}.zip"
        tmp_dir = self.out_dir / "tmp"
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
        tmp_dir.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        env["PATH"] = f"{self.ota_tools_dir}/bin:{env['PATH']}"
        env["TMPDIR"] = str(tmp_dir)

        try:
            self.shell.run([
                str(self.ota_tools_dir / "bin" / "ota_from_target_files"),
                "-v", "-k", str(self.ota_tools_dir / "security" / "testkey"),
                str(self.product_out), str(output_zip),
            ], env=env)
            md5 = hashlib.md5(open(output_zip, "rb").read()).hexdigest()[:10]
            final = self.out_dir / (
                f"{self.ctx.stock_rom_code}-ota_full-{self.ctx.target_rom_version}"
                f"-{self.ctx.security_patch}-{ts}-{md5}-{self.ctx.port_android_version}.zip"
            )
            output_zip.rename(final)
            self.logger.info(f"最终 OTA 包: {final}")
        except (subprocess.CalledProcessError, OSError) as e:
            self.logger.error(f"OTA 生成失败: {e}")
            self._emit("ota.error", {"error": str(e)})
            raise

    def _get_partition_list(self) -> List[str]:
        config_parts = self.ctx.device_config.get("pack", {}).get("partitions")
        if config_parts:
            self.logger.info(f"使用设备配置中的分区列表: {config_parts}")
            return cast(List[str], config_parts)
        info_path = Path(_PARTITION_INFO_PATH.format(code=self.ctx.stock_rom_code))
        if info_path.exists():
            try:
                parts = json.loads(info_path.read_text(encoding="utf-8")).get("dynamic_partitions", [])
                if parts:
                    self.logger.info(f"使用 partition_info.json 中的分区列表: {parts}")
                    return cast(List[str], parts)
            except Exception as e:
                self.logger.warning(f"读取 partition_info.json 失败: {e}")
        self.logger.info(f"使用默认分区列表: {_DEFAULT_PARTITIONS}")
        return list(_DEFAULT_PARTITIONS)

    def _get_super_size(self) -> int:
        cfg_size = getattr(self.ctx, "device_config", {}).get("pack", {}).get("super_size")
        if cfg_size:
            self.logger.info(f"使用设备配置中的 super_size: {cfg_size}")
            return int(cfg_size)
        info_path = Path(_PARTITION_INFO_PATH.format(code=self.ctx.stock_rom_code))
        if info_path.exists():
            try:
                size = json.loads(info_path.read_text(encoding="utf-8")).get("super_size")
                if size:
                    self.logger.info(f"使用 partition_info.json 中的 super_size: {size}")
                    return int(size)
            except Exception:
                pass
        code = self.ctx.stock_rom_code.upper()
        for size, devices in DEVICE_SIZE_MAP.items():
            if code in devices:
                return size
        self.logger.info(f"使用默认 super_size: {SUPER_SIZE_DEFAULT}")
        return SUPER_SIZE_DEFAULT

    def _get_dynamic_partition_metadata(self) -> Optional[Dict[str, Any]]:
        info_path = Path(_PARTITION_INFO_PATH.format(code=self.ctx.stock_rom_code))
        if not info_path.exists():
            return None
        try:
            meta = json.loads(info_path.read_text(encoding="utf-8")).get("dynamic_partition_metadata")
            return meta if isinstance(meta, dict) else None
        except (json.JSONDecodeError, OSError):
            return None

    def _is_vabc_enabled(self) -> bool:
        meta = self._get_dynamic_partition_metadata()
        if meta and meta.get("vabc_enabled"):
            return True
        vendor_props = self._read_build_prop("vendor")
        return vendor_props.get("ro.virtual_ab.compression.enabled", "").lower() == "true"

    def _read_build_prop(self, part: str) -> Dict[str, str]:
        path = None
        getter = getattr(self.ctx, "get_target_prop_file", None)
        if callable(getter):
            try:
                candidate = getter(part)
                if isinstance(candidate, Path):
                    path = candidate
            except Exception:
                pass
        if path is None:
            target_dir = getattr(self.ctx, "target_dir", None)
            if isinstance(target_dir, Path):
                for c in [target_dir / part / "build.prop", target_dir / part / "system" / "build.prop"]:
                    if c.exists():
                        path = c
                        break
        if not path or not path.exists():
            return {}
        try:
            props: Dict[str, str] = {}
            for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                stripped = raw.strip()
                if not stripped or stripped.startswith("#") or "=" not in stripped:
                    continue
                k, v = stripped.split("=", 1)
                props[k.strip()] = v.strip()
            return props
        except OSError:
            return {}

    def _avb_misc_lines(self, parts: List[str]) -> List[str]:
        from src.core.packing.avb import AVBManager
        avb = AVBManager(self.ctx, self._collector, self._event_bus)
        profile = avb.collect_stock_avb_profile()
        return avb.build_avb_misc_lines(parts, profile) if profile else []

    class PayloadGenerator:
        """OTA payload 生成器，封装 ota_from_target_files 调用。"""

        def __init__(self, packer: "OTAPacker"):
            self._packer = packer
            self.logger = logging.getLogger("OTAPacker.PayloadGenerator")

        def run(self, output_path: Path) -> Path:
            self.logger.info("开始生成 OTA payload...")
            tmp_dir = self._packer.out_dir / "tmp"
            if tmp_dir.exists():
                shutil.rmtree(tmp_dir)
            tmp_dir.mkdir(parents=True, exist_ok=True)
            env = os.environ.copy()
            env["PATH"] = f"{self._packer.ota_tools_dir}/bin:{env['PATH']}"
            env["TMPDIR"] = str(tmp_dir)
            self._packer.shell.run([
                str(self._packer.ota_tools_dir / "bin" / "ota_from_target_files"),
                "-v", "-k", str(self._packer.ota_tools_dir / "security" / "testkey"),
                str(self._packer.product_out), str(output_path),
            ], env=env)
            self.logger.info(f"OTA payload 生成完成: {output_path}")
            return output_path
