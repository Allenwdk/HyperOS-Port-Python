"""快速哈希模块

对大文件使用分段哈希策略（头/中/尾各 10MB），大幅减少哈希计算时间。
支持后台线程池并发哈希预计算。
"""

import hashlib
import logging
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

SEGMENT_SIZE = 10 * 1024 * 1024  # 分段大小：10MB
LARGE_FILE_THRESHOLD = 100 * 1024 * 1024  # 大文件阈值：100MB


class FastHasher:
    """快速哈希器。

    小文件使用全文 MD5，大文件使用分段策略（头/中/尾各 10MB）。
    与 cache_manager.py 中的 _compute_rom_hash 保持兼容。
    """

    def hash_file(self, file_path: Path) -> str:
        """计算文件的 MD5 哈希值。文件不存在时抛出 FileNotFoundError。"""
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {path}")

        file_size = path.stat().st_size
        hash_md5 = hashlib.md5()

        with open(path, "rb") as f:
            if file_size < LARGE_FILE_THRESHOLD:
                hash_md5.update(f.read())
            else:
                hash_md5.update(f.read(SEGMENT_SIZE))

                f.seek(file_size // 2)
                hash_md5.update(f.read(SEGMENT_SIZE))

                f.seek(-SEGMENT_SIZE, 2)
                hash_md5.update(f.read(SEGMENT_SIZE))

        result = hash_md5.hexdigest()
        logger.debug(f"文件哈希: {path.name} -> {result[:16]}...")
        return result

    def hash_string(self, text: str) -> str:
        return hashlib.md5(text.encode("utf-8")).hexdigest()


class BackgroundHasher:
    """后台哈希预计算器。

    使用 ThreadPoolExecutor 在后台并发计算文件哈希，
    适合在缓存 rglob 结果后立即预计算所有文件的哈希值。
    """

    def __init__(self, max_workers: int = 4) -> None:
        self._hasher = FastHasher()
        self._results: Dict[str, str] = {}
        self._futures: Dict[str, Future] = {}
        self._executor: Optional[ThreadPoolExecutor] = None
        self._max_workers = max_workers

    def _ensure_executor(self) -> ThreadPoolExecutor:
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=self._max_workers)
        return self._executor

    def submit_files(self, files: List[Path]) -> None:
        """提交文件列表到后台线程池计算哈希。"""
        executor = self._ensure_executor()
        for f in files:
            path_str = str(f)
            if path_str in self._results or path_str in self._futures:
                continue
            future = executor.submit(self._hasher.hash_file, f)
            self._futures[path_str] = future

    def get_hash(self, file_path: Path) -> Optional[str]:
        """获取文件哈希值，若尚未完成则阻塞等待。"""
        path_str = str(file_path)
        if path_str in self._results:
            return self._results[path_str]

        future = self._futures.get(path_str)
        if future is None:
            return None

        try:
            result = future.result(timeout=30)
            self._results[path_str] = result
            return result
        except Exception as e:
            logger.warning(f"后台哈希计算失败: {file_path}: {e}")
            return None

    def wait_all(self) -> None:
        """等待所有提交的哈希任务完成。"""
        for path_str, future in list(self._futures.items()):
            if path_str in self._results:
                continue
            try:
                self._results[path_str] = future.result(timeout=60)
            except Exception as e:
                logger.warning(f"后台哈希计算失败: {path_str}: {e}")

    def cancel_all(self) -> None:
        """取消所有未完成的哈希任务并关闭线程池。"""
        for future in self._futures.values():
            future.cancel()
        self._futures.clear()
        self._shutdown_executor()

    def _shutdown_executor(self) -> None:
        if self._executor is not None:
            self._executor.shutdown(wait=False)
            self._executor = None
