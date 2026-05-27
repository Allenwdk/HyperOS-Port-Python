"""AVB 和打包相关的常量定义。

包含 AOSP AVB 分区集合、设备尺寸映射表、默认算法等硬编码常量，
从原 packer.py 中提取以便集中管理和复用。
"""

from typing import Dict, List

# AVB 默认签名算法
AVB_DEFAULT_ALGORITHM: str = "SHA256_RSA4096"

# AOSP 标准 AVB 分区集合
# 这些分区在 AOSP 中通常由 AVB 进行完整性校验
AOSP_AVB_PARTITIONS: set[str] = {
    "boot",
    "init_boot",
    "dtbo",
    "odm",
    "product",
    "pvmfw",
    "recovery",
    "system",
    "system_ext",
    "vendor",
    "vendor_boot",
    "vendor_kernel_boot",
    "vendor_dlkm",
    "odm_dlkm",
    "system_dlkm",
}

# 设备代码到 super 分区大小的映射表
# 键为 super 分区大小（字节），值为使用该大小的设备代码列表
DEVICE_SIZE_MAP: Dict[int, List[str]] = {
    9663676416: ["FUXI", "NUWA", "ISHTAR", "MARBLE", "SOCRATES", "BABYLON"],
    9122611200: ["SUNSTONE"],
    11811160064: ["YUDI"],
    13411287040: ["PANDORA", "POPSICLE", "PUDDING", "NEZHA"],
}

# 默认 super 分区大小（当设备不在映射表中时使用）
SUPER_SIZE_DEFAULT: int = 9126805504

# AVB footer 操作的 block 对齐大小（4KB）
AVB_BLOCK_SIZE: int = 4096

# 分区大小二分搜索的最大尝试次数
AVB_MAX_PROBE_ATTEMPTS: int = 32

# AVB 公共 key 文件名
AVB_PUBKEY_FILENAME: str = "avb_testkey.avbpubkey"

# testkey 搜索候选路径（相对于 otatools 目录）
TESTKEY_CANDIDATES: List[str] = [
    "build/make/target/product/security/testkey.pem",
    "security/testkey.pem",
]

# pk8 转换路径（相对于 otatools 目录）
TESTKEY_PK8_PATH: str = "build/make/target/product/security/testkey.pk8"
TESTKEY_PEM_PATH: str = "build/make/target/product/security/testkey.pem"
