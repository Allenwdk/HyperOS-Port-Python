"""AVB 管理模块。

从 packer.py 提取的 Android Verified Boot (AVB) 相关逻辑，
封装为独立的 AVBManager 类，支持事件发布和监控指标采集。
"""

import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, cast

from src.core.events.bus import EventBus
from src.core.events.events import ErrorEvent, PhaseEndEvent, PhaseStartEvent
from src.core.monitoring import MetricsCollector, MonitoredComponent
from src.core.packing.constants import (
    AOSP_AVB_PARTITIONS,
    AVB_BLOCK_SIZE,
    AVB_DEFAULT_ALGORITHM,
    AVB_MAX_PROBE_ATTEMPTS,
    AVB_PUBKEY_FILENAME,
    DEVICE_SIZE_MAP,
    SUPER_SIZE_DEFAULT,
    TESTKEY_CANDIDATES,
    TESTKEY_PEM_PATH,
    TESTKEY_PK8_PATH,
)
from src.utils.shell import ShellRunner

logger = logging.getLogger("AVBManager")


def _append_unique(items: List[str], value: str) -> None:
    if value and value not in items:
        items.append(value)


def parse_avbtool_info_output(output: str) -> Dict[str, Any]:
    """解析 `avbtool info_image` 输出为结构化字典。"""
    result: Dict[str, Any] = {
        "image_size": None, "original_image_size": None, "algorithm": None,
        "rollback_index": None, "flags": None, "chain_partitions": [],
        "hash_partitions": [], "hashtree_partitions": [],
    }
    current_desc: Optional[str] = None
    chain_name: Optional[str] = None
    chain_loc: Optional[int] = None
    rollback_re = re.compile(r"^Rollback Index:\s+(\d+)$")
    flags_re = re.compile(r"^Flags:\s+(\d+)$")
    alg_re = re.compile(r"^Algorithm:\s+(.+)$")
    image_size_re = re.compile(r"^Image size:\s+(\d+)\s+bytes$")
    original_image_size_re = re.compile(r"^Original image size:\s+(\d+)\s+bytes$")
    part_re = re.compile(r"^Partition Name:\s+(.+)$")
    chain_loc_re = re.compile(r"^Rollback Index Location:\s+(\d+)$")

    for raw in output.splitlines():
        line = raw.strip()
        if not line:
            continue
        alg_match = alg_re.match(line)
        if alg_match:
            result["algorithm"] = alg_match.group(1).strip()
            continue
        image_size_match = image_size_re.match(line)
        if image_size_match:
            result["image_size"] = int(image_size_match.group(1))
            continue
        original_image_size_match = original_image_size_re.match(line)
        if original_image_size_match:
            result["original_image_size"] = int(original_image_size_match.group(1))
            continue
        rollback_match = rollback_re.match(line)
        if rollback_match:
            result["rollback_index"] = int(rollback_match.group(1))
            continue
        flags_match = flags_re.match(line)
        if flags_match:
            result["flags"] = int(flags_match.group(1))
            continue
        if line == "Chain Partition descriptor:":
            current_desc, chain_name, chain_loc = "chain", None, None
            continue
        if line == "Hash descriptor:":
            current_desc = "hash"
            continue
        if line == "Hashtree descriptor:":
            current_desc = "hashtree"
            continue
        part_match = part_re.match(line)
        if part_match:
            part_name = part_match.group(1).strip()
            if current_desc == "chain":
                chain_name = part_name
                if chain_loc is not None:
                    cast(List[Tuple[str, int]], result["chain_partitions"]).append((chain_name, chain_loc))
                    chain_name, chain_loc = None, None
            elif current_desc == "hash":
                _append_unique(cast(List[str], result["hash_partitions"]), part_name)
            elif current_desc == "hashtree":
                _append_unique(cast(List[str], result["hashtree_partitions"]), part_name)
            continue
        if current_desc == "chain":
            chain_loc_match = chain_loc_re.match(line)
            if chain_loc_match:
                chain_loc = int(chain_loc_match.group(1))
                if chain_name is not None:
                    cast(List[Tuple[str, int]], result["chain_partitions"]).append((chain_name, chain_loc))
                    chain_name, chain_loc = None, None
    return result


def read_build_prop(path: Path) -> Dict[str, str]:
    """读取 build.prop 文件为键值对字典。"""
    props: Dict[str, str] = {}
    if not path.exists():
        return props
    try:
        for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            props[k.strip()] = v.strip()
    except OSError:
        return {}
    return props


def get_partition_build_props(ctx: Any, part: str, cache: Dict[str, Dict[str, str]]) -> Dict[str, str]:
    """获取指定分区的 build.prop 属性（带缓存）。"""
    if part in cache:
        return cache[part]
    path: Optional[Path] = None
    getter = getattr(ctx, "get_target_prop_file", None)
    if callable(getter):
        try:
            candidate = getter(part)
            if isinstance(candidate, Path):
                path = candidate
        except Exception:
            path = None
    if path is None:
        target_dir = getattr(ctx, "target_dir", None)
        if isinstance(target_dir, Path):
            for c in [target_dir / part / "build.prop", target_dir / part / "system" / "build.prop",
                      target_dir / part / "etc" / "build.prop"]:
                if c.exists():
                    path = c
                    break
    props = read_build_prop(path) if path else {}
    cache[part] = props
    return props


def get_prop_value(ctx: Any, part: str, kind: str, cache: Dict[str, Dict[str, str]]) -> Optional[str]:
    """从 build.prop 中获取指定类型的属性值。"""
    props = get_partition_build_props(ctx, part, cache)
    system_props = get_partition_build_props(ctx, "system", cache)
    if kind == "fingerprint":
        keys = [f"ro.{part}.build.fingerprint", "ro.build.fingerprint"]
    elif kind == "os_version":
        keys = [f"ro.{part}.build.version.release", "ro.build.version.release",
                "ro.build.version.release_or_codename"]
    else:
        keys = [f"ro.{part}.build.version.security_patch", f"ro.{part}.build.security_patch",
                "ro.build.version.security_patch"]
    for key in keys:
        if key in props and props[key]:
            return props[key]
    for key in keys:
        if key in system_props and system_props[key]:
            return system_props[key]
    return None


def get_partition_info_path(stock_rom_code: str) -> Path:
    return Path(f"devices/{stock_rom_code}/partition_info.json")


def get_partition_list_from_config(ctx: Any) -> List[str]:
    """获取逻辑分区列表。"""
    config_partitions = getattr(ctx, "device_config", {}).get("pack", {}).get("partitions")
    if config_partitions:
        return cast(List[str], config_partitions)
    partition_info_path = get_partition_info_path(ctx.stock_rom_code)
    if partition_info_path.exists():
        try:
            info = json.loads(partition_info_path.read_text(encoding="utf-8"))
            partitions = info.get("dynamic_partitions", [])
            if partitions:
                return cast(List[str], partitions)
        except (json.JSONDecodeError, OSError):
            pass
    return ["system", "system_ext", "product", "vendor", "odm", "mi_ext", "system_dlkm", "vendor_dlkm"]


def get_super_size_from_config(ctx: Any) -> int:
    """获取 super 分区大小。"""
    super_size = getattr(ctx, "device_config", {}).get("pack", {}).get("super_size")
    if super_size:
        return int(super_size)
    partition_info_path = get_partition_info_path(ctx.stock_rom_code)
    if partition_info_path.exists():
        try:
            info = json.loads(partition_info_path.read_text(encoding="utf-8"))
            super_size = info.get("super_size")
            if super_size:
                return int(super_size)
        except (json.JSONDecodeError, OSError):
            pass
    device_code = ctx.stock_rom_code.upper()
    for size, devices in DEVICE_SIZE_MAP.items():
        if device_code in devices:
            return size
    return SUPER_SIZE_DEFAULT


def _detect_rsa_key_bits(key_path: Path) -> Optional[int]:
    try:
        output = subprocess.check_output(
            ["openssl", "pkey", "-in", str(key_path), "-text", "-noout"],
            text=True, stderr=subprocess.STDOUT,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return None
    match = re.search(r"Private-Key:\s*\((\d+)\s+bit", output)
    return int(match.group(1)) if match else None


def _algorithm_for_key(preferred: str, key_path: Optional[Path]) -> str:
    if not key_path:
        return preferred
    bits = _detect_rsa_key_bits(key_path)
    if bits is None:
        return preferred
    if bits >= 4096:
        return "SHA256_RSA4096"
    if bits >= 2048:
        return "SHA256_RSA2048"
    return preferred


def _build_footer_props_args(ctx: Any, part: str, include_hash_algorithm: bool,
                              cache: Dict[str, Dict[str, str]]) -> List[str]:
    """构建 AVB footer 的属性参数列表。"""
    args: List[str] = []
    if include_hash_algorithm:
        args.extend(["--hash_algorithm", "sha256"])
    prop_prefix = f"com.android.build.{part}"
    for kind, key in [("os_version", "os_version"), ("fingerprint", "fingerprint"),
                      ("security_patch", "security_patch")]:
        value = get_prop_value(ctx, part, kind, cache)
        if value:
            args.extend(["--prop", f"{prop_prefix}.{key}:{value}"])
    return args


def _trim_trailing_zero_padding(image: Path, max_size: int, log: logging.Logger) -> int:
    """截断镜像尾部零填充。"""
    current_size = image.stat().st_size
    if current_size <= max_size:
        return current_size
    with open(image, "rb+") as fp:
        fp.seek(max_size)
        tail = fp.read()
        if any(byte != 0 for byte in tail):
            raise RuntimeError(
                f"{image.name} ({current_size}) 超过 AVB 最大负载大小 {max_size}，且尾部非零填充；拒绝截断。"
            )
        fp.truncate(max_size)
    log.info("截断 %s 的零填充: %d -> %d 字节", image.name, current_size, max_size)
    return max_size


class AVBManager(MonitoredComponent):
    """AVB 管理器，封装 Android Verified Boot 相关操作。"""

    def __init__(self, context: Any, collector: Optional[MetricsCollector] = None,
                 event_bus: Optional[EventBus] = None):
        super().__init__(collector=collector, name="AVBManager", event_bus=event_bus)
        self.ctx = context
        self.logger = logger
        self.shell: ShellRunner = ShellRunner()
        self.ota_tools_dir: Path = Path("otatools").resolve()
        self.out_dir: Path = Path("out").resolve()
        self.product_out: Path = self.out_dir / "target" / "product" / self.ctx.stock_rom_code
        self.images_out: Path = self.product_out / "IMAGES"
        self.meta_out: Path = self.product_out / "META"
        self._avb_partition_size: Dict[str, int] = {}
        self._build_prop_cache: Dict[str, Dict[str, str]] = {}

    def _publish_phase_start(self, phase_name: str) -> None:
        if self._event_bus is not None:
            self._event_bus.publish(PhaseStartEvent(phase_name, source="AVBManager"))

    def _publish_phase_end(self, phase_name: str, success: bool = True, duration: float = 0.0) -> None:
        if self._event_bus is not None:
            self._event_bus.publish(PhaseEndEvent(phase_name, success=success, duration=duration, source="AVBManager"))

    def _publish_error(self, error_type: str, error_message: str, phase: Optional[str] = None) -> None:
        if self._event_bus is not None:
            self._event_bus.publish(ErrorEvent(error_type=error_type, error_message=error_message, phase=phase, source="AVBManager"))

    def _avb_env(self) -> Dict[str, str]:
        env = os.environ.copy()
        env["PATH"] = f"{self.ota_tools_dir / 'bin'}:{env.get('PATH', '')}"
        return env

    def get_avb_testkey_path(self) -> Optional[Path]:
        """获取 AVB 签名密钥路径。"""
        custom_key: Optional[Path] = getattr(self.ctx, "avb_key_path", None)
        if custom_key and custom_key.exists():
            self.logger.info(f"使用自定义 AVB 密钥: {custom_key}")
            return custom_key
        for candidate_rel in TESTKEY_CANDIDATES:
            candidate = self.ota_tools_dir / candidate_rel
            if candidate.exists():
                return candidate
        pk8_path = self.ota_tools_dir / TESTKEY_PK8_PATH
        pem_path = self.ota_tools_dir / TESTKEY_PEM_PATH
        if pk8_path.exists() and not pem_path.exists():
            try:
                subprocess.run(["openssl", "pkcs8", "-in", str(pk8_path), "-inform", "DER",
                                "-out", str(pem_path), "-nocrypt"], check=True, capture_output=True)
                self.logger.info(f"从 {pk8_path.name} 生成 AVB 签名密钥")
                return pem_path
            except Exception as e:
                self.logger.warning(f"从 {pk8_path} 生成密钥失败: {e}")
        return None

    def run_avbtool_info_image(self, avbtool: Path, image: Path) -> Optional[Dict[str, Any]]:
        if not image.exists():
            return None
        try:
            output = subprocess.check_output(
                [str(avbtool), "info_image", "--image", str(image)],
                text=True, stderr=subprocess.STDOUT,
            )
        except (subprocess.SubprocessError, FileNotFoundError) as e:
            self.logger.debug("通过 avbtool 检查 %s 失败: %s", image.name, e)
            return None
        return parse_avbtool_info_output(output)

    def collect_stock_avb_profile(self) -> Dict[str, Any]:
        """从 stock vbmeta 镜像收集 AVB 描述符配置。"""
        avbtool = self.ota_tools_dir / "bin" / "avbtool"
        stock_images_dir = Path("build/stockrom/images")
        if not avbtool.exists() or not stock_images_dir.exists():
            return {}
        vbmeta_info = self.run_avbtool_info_image(avbtool, stock_images_dir / "vbmeta.img")
        if not vbmeta_info:
            return {}
        vbmeta_system_info = self.run_avbtool_info_image(avbtool, stock_images_dir / "vbmeta_system.img")
        hash_parts = set(cast(List[str], vbmeta_info.get("hash_partitions", [])))
        hashtree_parts = set(cast(List[str], vbmeta_info.get("hashtree_partitions", [])))
        if vbmeta_system_info:
            hashtree_parts.update(cast(List[str], vbmeta_system_info.get("hashtree_partitions", [])))
        return {
            "vbmeta": vbmeta_info, "vbmeta_system": vbmeta_system_info,
            "hash_parts": hash_parts, "hashtree_parts": hashtree_parts,
            "chain_parts": cast(List[Tuple[str, int]], vbmeta_info.get("chain_partitions", [])),
        }

    def sync_partition_info_from_stock_avb(self, profile: Dict[str, Any]) -> None:
        """将 stock AVB 数据同步到 partition_info.json。"""
        if not profile:
            return
        partition_info_path = get_partition_info_path(self.ctx.stock_rom_code)
        partition_info_path.parent.mkdir(parents=True, exist_ok=True)
        payload: Dict[str, Any] = {}
        if partition_info_path.exists():
            try:
                payload = cast(Dict[str, Any], json.loads(partition_info_path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                payload = {}
        payload.setdefault("device_code", self.ctx.stock_rom_code)
        payload.setdefault("super_size", get_super_size_from_config(self.ctx))
        dynamic_partitions = cast(List[str], payload.get("dynamic_partitions", get_partition_list_from_config(self.ctx)))
        payload["dynamic_partitions"] = dynamic_partitions
        hash_parts = set(cast(set[str], profile.get("hash_parts", set())))
        hashtree_parts = set(cast(set[str], profile.get("hashtree_parts", set())))
        chain_parts = cast(List[Tuple[str, int]], profile.get("chain_parts", []))
        chain_part_names = {name for name, _loc in chain_parts if name in {"boot", "recovery"}}
        strict_parts = sorted(((hash_parts | hashtree_parts) - set(dynamic_partitions)) | chain_part_names)
        avbtool = self.ota_tools_dir / "bin" / "avbtool"
        stock_images_dir = Path("build/stockrom/images")
        physical_partition_sizes = cast(Dict[str, int], payload.get("physical_partition_sizes", {}))
        for part in strict_parts:
            image = stock_images_dir / f"{part}.img"
            if not image.exists():
                continue
            info = self.run_avbtool_info_image(avbtool, image) or {}
            image_size = cast(Optional[int], info.get("image_size"))
            physical_partition_sizes[part] = int(image_size or image.stat().st_size)
        payload["physical_partition_sizes"] = dict(sorted(physical_partition_sizes.items()))
        payload["avb_hash_partitions"] = sorted(hash_parts)
        payload["avb_hashtree_partitions"] = sorted(hashtree_parts)
        payload["avb_chain_partitions"] = [{"name": name, "rollback_index_location": loc} for name, loc in chain_parts]
        payload["avb_strict_partitions"] = strict_parts
        partition_info_path.write_text(json.dumps(payload, indent=4, ensure_ascii=False) + "\n", encoding="utf-8")
        self.logger.info("已更新 %s 的 stock AVB 分区数据", partition_info_path)

    def calc_avb_max_image_size(self, avbtool: Path, footer_cmd: str, partition_size: int) -> int:
        output = subprocess.check_output(
            [str(avbtool), footer_cmd, "--partition_size", str(partition_size), "--calc_max_image_size"],
            text=True, stderr=subprocess.STDOUT, env=self._avb_env(),
        ).strip()
        return int(output)

    def _try_calc_avb_max_image_size(self, avbtool: Path, footer_cmd: str, partition_size: int) -> Optional[int]:
        try:
            return self.calc_avb_max_image_size(avbtool, footer_cmd, partition_size)
        except (subprocess.SubprocessError, ValueError, TypeError):
            return None

    def calculate_min_partition_size_for_image(self, avbtool: Path, footer_cmd: str, image_size: int) -> int:
        """二分搜索能容纳当前镜像的最小 partition_size。"""
        block = AVB_BLOCK_SIZE
        lo = max(block, ((image_size + block - 1) // block) * block)
        hi = lo
        max_size = self._try_calc_avb_max_image_size(avbtool, footer_cmd, hi)
        attempts = 0
        while max_size is None or max_size < image_size:
            hi *= 2
            max_size = self._try_calc_avb_max_image_size(avbtool, footer_cmd, hi)
            attempts += 1
            if attempts > AVB_MAX_PROBE_ATTEMPTS:
                raise RuntimeError(f"无法找到有效的 partition_size，footer_cmd={footer_cmd}, image_size={image_size}, 最后尝试={hi}")
        while lo < hi:
            mid = ((lo + hi) // (2 * block)) * block
            if mid <= 0:
                mid = block
            max_size = self._try_calc_avb_max_image_size(avbtool, footer_cmd, mid)
            if max_size is not None and max_size >= image_size:
                hi = mid
            else:
                lo = mid + block
        return hi

    def build_footer_props_args(self, part: str, include_hash_algorithm: bool) -> List[str]:
        return _build_footer_props_args(self.ctx, part, include_hash_algorithm, self._build_prop_cache)

    def extract_avb_public_key(self, avbtool: Path, key_path: Path) -> Path:
        pubkey_path = self.meta_out / AVB_PUBKEY_FILENAME
        subprocess.check_output(
            [str(avbtool), "extract_public_key", "--key", str(key_path), "--output", str(pubkey_path)],
            text=True, stderr=subprocess.STDOUT, env=self._avb_env(),
        )
        return pubkey_path

    def apply_avb_to_custom_images(self, partition_list: List[str]) -> None:
        """为所有 stock AVB 配置中描述的分区添加 AVB footer。"""
        start_time = time.time()
        self._publish_phase_start("avb.apply_footer")
        try:
            with self.track_execution("avb.apply_footer"):
                self._do_apply_avb_to_custom_images(partition_list)
            self._publish_phase_end("avb.apply_footer", success=True, duration=time.time() - start_time)
        except Exception as e:
            self._publish_error("AVBFooterError", str(e), phase="avb.apply_footer")
            self._publish_phase_end("avb.apply_footer", success=False, duration=time.time() - start_time)
            raise

    def _do_apply_avb_to_custom_images(self, partition_list: List[str]) -> None:
        profile = self.collect_stock_avb_profile()
        if not profile:
            return
        avbtool = self.ota_tools_dir / "bin" / "avbtool"
        stock_images_dir = Path("build/stockrom/images")
        known_parts = set(partition_list)
        dynamic_partitions = set(get_partition_list_from_config(self.ctx))
        hash_parts = cast(set[str], profile["hash_parts"])
        hashtree_parts = cast(set[str], profile["hashtree_parts"])
        target_hash_parts = sorted(hash_parts & known_parts)
        target_hashtree_parts = sorted(hashtree_parts & known_parts)
        chain_parts = cast(List[Tuple[str, int]], profile.get("chain_parts", []))
        chain_part_names = [name for name, _loc in chain_parts if name in {"boot", "recovery"}]
        strict_physical_caps = ((hash_parts | hashtree_parts) - dynamic_partitions) | set(chain_part_names)
        key_path = self.get_avb_testkey_path()

        def sign_partition(part: str, footer_cmd: str, *, with_key: bool = False,
                           rollback_index: Optional[int] = None) -> None:
            image = self.images_out / f"{part}.img"
            if not image.exists():
                return
            image_size = image.stat().st_size
            stock_image = stock_images_dir / f"{part}.img"
            stock_partition_size = stock_image.stat().st_size if stock_image.exists() else 0
            if part in strict_physical_caps:
                try:
                    self.shell.run([str(avbtool), "erase_footer", "--image", str(image)], env=self._avb_env())
                    image_size = image.stat().st_size
                except subprocess.CalledProcessError:
                    pass
            min_partition_size = self.calculate_min_partition_size_for_image(avbtool, footer_cmd, image_size)
            partition_size = stock_partition_size if part in strict_physical_caps and stock_partition_size > 0 else max(min_partition_size, stock_partition_size)

            def build_cmd(part_size: int) -> List[str]:
                cmd = [str(avbtool), footer_cmd, "--image", str(image), "--partition_name", part, "--partition_size", str(part_size)]
                cmd.extend(self.build_footer_props_args(part, include_hash_algorithm=(footer_cmd == "add_hashtree_footer")))
                if with_key and key_path:
                    cmd.extend(["--key", str(key_path), "--algorithm", _algorithm_for_key(AVB_DEFAULT_ALGORITHM, key_path)])
                if rollback_index is not None:
                    cmd.extend(["--rollback_index", str(rollback_index)])
                return cmd

            cmd = build_cmd(partition_size)
            try:
                self.shell.run(cmd, env=self._avb_env())
                self._avb_partition_size[part] = partition_size
                self._collector.increment("avb.footer_applied")
                self.logger.info("已应用 %s footer 到 AVB 分区 %s (镜像=%d, 分区=%d)", footer_cmd, part, image_size, partition_size)
            except subprocess.CalledProcessError as e:
                if part in strict_physical_caps and stock_partition_size > 0:
                    try:
                        max_payload_size = self.calc_avb_max_image_size(avbtool, footer_cmd, partition_size)
                        image_size = _trim_trailing_zero_padding(image, max_payload_size, self.logger)
                        cmd = build_cmd(partition_size)
                        self.shell.run(cmd, env=self._avb_env())
                        self._avb_partition_size[part] = partition_size
                        self._collector.increment("avb.footer_applied")
                        return
                    except (subprocess.CalledProcessError, RuntimeError) as retry_err:
                        raise RuntimeError(f"为严格分区 {part} 应用 AVB {footer_cmd} 失败: {retry_err}") from retry_err
                raise RuntimeError(f"为自定义分区 {part} 应用 AVB {footer_cmd} 失败: {e}") from e

        for part in target_hash_parts:
            sign_partition(part, "add_hash_footer")
        for part in target_hashtree_parts:
            sign_partition(part, "add_hashtree_footer")
        if key_path:
            stock_boot_info = self.run_avbtool_info_image(avbtool, stock_images_dir / "boot.img") or {}
            stock_recovery_info = self.run_avbtool_info_image(avbtool, stock_images_dir / "recovery.img") or {}
            rollback_map = {"boot": stock_boot_info.get("rollback_index"), "recovery": stock_recovery_info.get("rollback_index")}
            for part in chain_part_names:
                sign_partition(part, "add_hash_footer", with_key=True, rollback_index=cast(Optional[int], rollback_map.get(part)))

    def rebuild_vbmeta_images(self, partition_list: List[str]) -> None:
        """重建 vbmeta 镜像以匹配重新打包的镜像。"""
        start_time = time.time()
        self._publish_phase_start("avb.rebuild_vbmeta")
        try:
            with self.track_execution("avb.rebuild_vbmeta"):
                self._do_rebuild_vbmeta_images(partition_list)
            self._publish_phase_end("avb.rebuild_vbmeta", success=True, duration=time.time() - start_time)
        except Exception as e:
            self._publish_error("VBMetaRebuildError", str(e), phase="avb.rebuild_vbmeta")
            self._publish_phase_end("avb.rebuild_vbmeta", success=False, duration=time.time() - start_time)
            raise

    def _do_rebuild_vbmeta_images(self, partition_list: List[str]) -> None:
        profile = self.collect_stock_avb_profile()
        if not profile:
            return
        avbtool = self.ota_tools_dir / "bin" / "avbtool"
        key_path = self.get_avb_testkey_path()
        if not avbtool.exists() or not key_path:
            return
        known_parts = set(partition_list)
        vbmeta_info = cast(Dict[str, Any], profile["vbmeta"])
        vbmeta_system_info = cast(Optional[Dict[str, Any]], profile.get("vbmeta_system"))
        hash_parts = cast(set[str], profile["hash_parts"])
        hashtree_parts = cast(set[str], profile["hashtree_parts"])
        chain_parts = cast(List[Tuple[str, int]], profile["chain_parts"])

        vbmeta_system_parts: List[str] = []
        if vbmeta_system_info:
            vbmeta_system_parts = [p for p in cast(List[str], vbmeta_system_info.get("hashtree_partitions", []))
                                   if p in known_parts and (self.images_out / f"{p}.img").exists()]
        if vbmeta_system_parts:
            algo = _algorithm_for_key(str((vbmeta_system_info or {}).get("algorithm") or AVB_DEFAULT_ALGORITHM), key_path)
            cmd = [str(avbtool), "make_vbmeta_image", "--output", str(self.images_out / "vbmeta_system.img"),
                   "--key", str(key_path), "--algorithm", algo]
            ri = (vbmeta_system_info or {}).get("rollback_index")
            if ri is not None:
                cmd.extend(["--rollback_index", str(ri)])
            flags = (vbmeta_system_info or {}).get("flags")
            if flags is not None:
                cmd.extend(["--flags", str(flags)])
            for part in vbmeta_system_parts:
                cmd.extend(["--include_descriptors_from_image", str(self.images_out / f"{part}.img")])
            self.shell.run(cmd, env=self._avb_env())

        include_parts = sorted((hash_parts | hashtree_parts) & known_parts)
        if vbmeta_system_parts:
            include_parts = [p for p in include_parts if p not in vbmeta_system_parts]
        pubkey_path = self.extract_avb_public_key(avbtool, key_path)
        chain_entries = [(name, loc) for name, loc in chain_parts
                         if (name == "vbmeta_system" and (self.images_out / "vbmeta_system.img").exists())
                         or (name != "vbmeta_system" and (self.images_out / f"{name}.img").exists())]
        vbmeta_algo = _algorithm_for_key(str(vbmeta_info.get("algorithm") or AVB_DEFAULT_ALGORITHM), key_path)
        cmd = [str(avbtool), "make_vbmeta_image", "--output", str(self.images_out / "vbmeta.img"),
               "--key", str(key_path), "--algorithm", vbmeta_algo]
        ri = vbmeta_info.get("rollback_index")
        if ri is not None:
            cmd.extend(["--rollback_index", str(ri)])
        flags = vbmeta_info.get("flags")
        if flags is not None:
            cmd.extend(["--flags", str(flags)])
        for part in include_parts:
            cmd.extend(["--include_descriptors_from_image", str(self.images_out / f"{part}.img")])
        for name, loc in chain_entries:
            cmd.extend(["--chain_partition", f"{name}:{loc}:{pubkey_path}"])
        self.shell.run(cmd, env=self._avb_env())
        self._collector.increment("avb.vbmeta_rebuilt")

    def verify_avb_images(self) -> None:
        """验证顶层 vbmeta 和链路分区。"""
        start_time = time.time()
        self._publish_phase_start("avb.verify")
        try:
            with self.track_execution("avb.verify"):
                self._do_verify_avb_images()
            self._publish_phase_end("avb.verify", success=True, duration=time.time() - start_time)
        except Exception as e:
            self._publish_error("AVBVerifyError", str(e), phase="avb.verify")
            self._publish_phase_end("avb.verify", success=False, duration=time.time() - start_time)
            raise

    def _do_verify_avb_images(self) -> None:
        vbmeta_img = self.images_out / "vbmeta.img"
        if not vbmeta_img.exists():
            return
        avbtool = self.ota_tools_dir / "bin" / "avbtool"
        if not avbtool.exists():
            raise RuntimeError(f"avbtool 不存在于 {avbtool}，无法验证 AVB 链路。")
        self.shell.run([str(avbtool), "verify_image", "--image", str(vbmeta_img), "--follow_chain_partitions"], env=self._avb_env())
        self._collector.increment("avb.verify_success")

    def generate_care_map(self) -> None:
        """为 AVB hashtree 分区生成 care_map.pb。"""
        profile = self.collect_stock_avb_profile()
        if not profile:
            return
        care_map_gen = self.ota_tools_dir / "bin" / "care_map_generator"
        if not care_map_gen.exists():
            return
        hashtree_parts = cast(set[str], profile.get("hashtree_parts", set()))
        if not hashtree_parts:
            return
        care_map_lines: List[str] = []
        for part in sorted(hashtree_parts):
            image = self.images_out / f"{part}.img"
            if not image.exists():
                continue
            care_map_lines.append(f"/{part}")
            num_blocks = (image.stat().st_size + AVB_BLOCK_SIZE - 1) // AVB_BLOCK_SIZE
            care_map_lines.append(f"0,{num_blocks}")
        if not care_map_lines:
            return
        care_map_txt = self.meta_out / "care_map.txt"
        self.meta_out.mkdir(parents=True, exist_ok=True)
        care_map_txt.write_text("\n".join(care_map_lines) + "\n", encoding="utf-8")
        try:
            self.shell.run([str(care_map_gen), str(care_map_txt), str(self.meta_out / "care_map.pb")], env=self._avb_env())
            self._collector.increment("avb.care_map_generated")
        except subprocess.CalledProcessError as e:
            self.logger.warning("生成 care_map.pb 失败: %s", e)
        finally:
            if care_map_txt.exists():
                care_map_txt.unlink()

    def build_avb_misc_lines_from_stock(self, partition_list: List[str]) -> List[str]:
        """从 stock 镜像推断 AVB 相关的 misc_info 行。"""
        profile = self.collect_stock_avb_profile()
        if not profile:
            return []
        vbmeta_info = cast(Dict[str, Any], profile["vbmeta"])
        stock_images_dir = Path("build/stockrom/images")
        avbtool = self.ota_tools_dir / "bin" / "avbtool"
        vbmeta_system_info = self.run_avbtool_info_image(avbtool, stock_images_dir / "vbmeta_system.img")
        boot_info = self.run_avbtool_info_image(avbtool, stock_images_dir / "boot.img")
        recovery_info = self.run_avbtool_info_image(avbtool, stock_images_dir / "recovery.img")
        testkey = self.get_avb_testkey_path()
        if not testkey:
            return []
        key_algo = _algorithm_for_key(AVB_DEFAULT_ALGORITHM, testkey)
        known_parts = set(partition_list)
        lines: List[str] = ["avb_enable=true", "avb_building_vbmeta_image=true", "avb_avbtool=avbtool",
                            f"avb_vbmeta_key_path={testkey}", f"avb_vbmeta_algorithm={key_algo}"]
        chain_parts = cast(List[Tuple[str, int]], vbmeta_info.get("chain_partitions", []))
        chain_loc_by_name = {name: loc for name, loc in chain_parts}

        if vbmeta_system_info:
            vs_parts = [p for p in cast(List[str], vbmeta_system_info.get("hashtree_partitions", [])) if p in known_parts]
            if vs_parts:
                lines.extend([f"avb_vbmeta_system={' '.join(vs_parts)}", f"avb_vbmeta_system_key_path={testkey}", f"avb_vbmeta_system_algorithm={key_algo}"])
                if vbmeta_system_info.get("rollback_index") is not None:
                    lines.append(f"avb_vbmeta_system_rollback_index={vbmeta_system_info['rollback_index']}")
            if "vbmeta_system" in chain_loc_by_name:
                lines.append(f"avb_vbmeta_system_rollback_index_location={chain_loc_by_name['vbmeta_system']}")

        for part, part_info in (("boot", boot_info), ("recovery", recovery_info)):
            if part not in known_parts:
                continue
            lines.append(f"avb_{part}_algorithm={key_algo}")
            if part_info and part_info.get("rollback_index") is not None:
                lines.append(f"avb_{part}_rollback_index={part_info['rollback_index']}")
            lines.append(f"avb_{part}_key_path={testkey}")
            if part in chain_loc_by_name:
                lines.append(f"avb_{part}_rollback_index_location={chain_loc_by_name[part]}")
            add_hash_args = self.build_footer_props_args(part, include_hash_algorithm=False)
            if part_info and part_info.get("rollback_index") is not None:
                add_hash_args.extend(["--rollback_index", str(part_info["rollback_index"])])
            if add_hash_args:
                lines.append(f"avb_{part}_add_hash_footer_args={' '.join(add_hash_args)}")

        hash_parts = set(cast(List[str], profile.get("hash_parts", [])))
        hashtree_parts = set(cast(List[str], profile.get("hashtree_parts", [])))
        if vbmeta_system_info:
            hashtree_parts.update(cast(List[str], vbmeta_system_info.get("hashtree_partitions", [])))
        custom_parts = sorted(((hash_parts | hashtree_parts) - AOSP_AVB_PARTITIONS) & known_parts)
        if custom_parts:
            lines.append(f"avb_custom_images_partition_list={' '.join(custom_parts)}")
            for part in custom_parts:
                lines.append(f"avb_{part}_image_list={part}.img")
        for part in sorted(hash_parts & known_parts):
            lines.append(f"avb_{part}_hash_enable=true")
            img = self.images_out / f"{part}.img"
            lines.append(f"avb_{part}_partition_size={self._avb_partition_size.get(part, img.stat().st_size if img.exists() else 0)}")
            add_hash_args = self.build_footer_props_args(part, include_hash_algorithm=False)
            if add_hash_args:
                lines.append(f"avb_{part}_add_hash_footer_args={' '.join(add_hash_args)}")
        for part in sorted(hashtree_parts & known_parts):
            lines.append(f"avb_{part}_hashtree_enable=true")
            img = self.images_out / f"{part}.img"
            lines.append(f"avb_{part}_partition_size={self._avb_partition_size.get(part, img.stat().st_size if img.exists() else 0)}")
            add_hashtree_args = self.build_footer_props_args(part, include_hash_algorithm=True)
            if add_hashtree_args:
                lines.append(f"avb_{part}_add_hashtree_footer_args={' '.join(add_hashtree_args)}")
        return lines

    def run_full_avb_chain(self, partition_list: List[str]) -> None:
        """执行完整的 AVB 处理链路。"""
        start_time = time.time()
        profile = self.collect_stock_avb_profile()
        self.sync_partition_info_from_stock_avb(profile)
        self.apply_avb_to_custom_images(partition_list)
        self.rebuild_vbmeta_images(partition_list)
        self.generate_care_map()
        self.verify_avb_images()
        self._collector.record("avb.full_chain.duration", time.time() - start_time, unit="s")
