"""增量构建追踪模块

基于文件 mtime + size 检测文件变更，支持状态持久化以实现跨构建的增量处理。
"""

import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class IncrementalTracker:
    """增量文件变更追踪器，支持状态持久化。

    通过记录文件的 (mtime, size) 元组来检测变更。
    可选持久化到 JSON 文件，实现跨构建实例的增量检测。
    """

    def __init__(self, root: Path, state_file: Optional[Path] = None) -> None:
        self._root = Path(root).resolve()
        self._file_states: Dict[str, Tuple[float, int]] = {}
        self.cache_hits: int = 0
        self.cache_misses: int = 0
        self._state_file: Optional[Path] = Path(state_file) if state_file else None

    def is_changed(self, file_path: Path) -> bool:
        """检查文件是否发生了变更。首次检查记录状态并返回 True。"""
        path_str = str(Path(file_path).resolve())

        try:
            stat = os.stat(path_str)
            current_state = (stat.st_mtime, stat.st_size)
        except OSError:
            return True

        if path_str in self._file_states:
            if self._file_states[path_str] == current_state:
                self.cache_hits += 1
                logger.debug(f"文件未变更: {file_path}")
                return False
            self.cache_misses += 1
            logger.debug(f"文件已变更: {file_path}")
            self._file_states[path_str] = current_state
            return True

        self.cache_misses += 1
        self._file_states[path_str] = current_state
        logger.debug(f"首次记录文件: {file_path}")
        return True

    def get_changed_files(self, file_paths: List[Path]) -> List[Path]:
        return [fp for fp in file_paths if self.is_changed(fp)]

    def save_state(self) -> None:
        """将文件状态序列化到磁盘。"""
        if self._state_file is None:
            return

        serializable = {
            path: {"mtime": mtime, "size": size}
            for path, (mtime, size) in self._file_states.items()
        }

        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._state_file, "w", encoding="utf-8") as f:
                json.dump(serializable, f)
            logger.debug(f"增量追踪状态已保存: {self._state_file}")
        except OSError as e:
            logger.warning(f"增量追踪状态保存失败: {e}")

    def load_state(self) -> bool:
        """从磁盘加载文件状态，返回是否加载成功。"""
        if self._state_file is None or not self._state_file.exists():
            return False

        try:
            with open(self._state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"增量追踪状态加载失败: {e}")
            return False

        self._file_states = {
            path: (entry["mtime"], entry["size"])
            for path, entry in data.items()
        }
        logger.debug(f"增量追踪状态已加载: {len(self._file_states)} 个文件")
        return True

    def reset(self) -> None:
        self._file_states.clear()
        self.cache_hits = 0
        self.cache_misses = 0
        logger.debug("增量追踪器已重置")
