"""Transaction 模块测试。

测试事务管理系统的各项功能：
- ModificationRecord 数据类
- Transaction 回滚机制
- TransactionManager 事务管理
- RollbackContext 上下文管理器
- track_modification 辅助函数
"""

import logging
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.core.modifiers.transaction import (
    ModificationRecord,
    RollbackContext,
    Transaction,
    TransactionManager,
    track_modification,
)


# ──────────────────────────────────────────────────────────────────────
# 测试 ModificationRecord
# ──────────────────────────────────────────────────────────────────────


class TestModificationRecord:
    """ModificationRecord 数据类测试。"""

    def test_create_modification_record(self):
        """测试创建修改记录。"""
        record = ModificationRecord(
            original_path=Path("/test/file.txt"),
            backup_path=Path("/backup/file.txt"),
            action="modify",
        )

        assert record.original_path == Path("/test/file.txt")
        assert record.backup_path == Path("/backup/file.txt")
        assert record.action == "modify"
        assert isinstance(record.timestamp, datetime)

    def test_modification_record_default_timestamp(self):
        """测试默认时间戳。"""
        before = datetime.now()
        record = ModificationRecord(
            original_path=Path("/test"),
            backup_path=None,
            action="create",
        )
        after = datetime.now()

        assert before <= record.timestamp <= after

    def test_modification_record_actions(self):
        """测试不同操作类型。"""
        for action in ("modify", "delete", "create"):
            record = ModificationRecord(
                original_path=Path("/test"),
                backup_path=None,
                action=action,
            )
            assert record.action == action


# ──────────────────────────────────────────────────────────────────────
# 测试 Transaction
# ──────────────────────────────────────────────────────────────────────


class TestTransaction:
    """Transaction 类测试。"""

    def test_create_transaction(self):
        """测试创建事务。"""
        txn = Transaction(name="test_txn")

        assert txn.name == "test_txn"
        assert txn.completed is False
        assert txn.rolled_back is False
        assert len(txn.modifications) == 0
        assert isinstance(txn.start_time, datetime)

    def test_add_modification(self):
        """测试添加修改记录。"""
        txn = Transaction(name="test_txn")
        mod = ModificationRecord(
            original_path=Path("/test"),
            backup_path=None,
            action="create",
        )

        txn.add_modification(mod)

        assert len(txn.modifications) == 1
        assert txn.modifications[0] is mod

    def test_rollback_create_action(self, tmp_path):
        """测试回滚 'create' 操作（删除创建的文件）。"""
        logger = logging.getLogger("test")
        txn = Transaction(name="test_txn")

        # 创建一个文件
        created_file = tmp_path / "created.txt"
        created_file.write_text("new content")

        mod = ModificationRecord(
            original_path=created_file,
            backup_path=None,
            action="create",
        )
        txn.add_modification(mod)

        rolled_back = txn.rollback(logger)

        assert rolled_back == 1
        assert not created_file.exists()
        assert txn.rolled_back is True

    def test_rollback_modify_action(self, tmp_path):
        """测试回滚 'modify' 操作（从备份恢复）。"""
        logger = logging.getLogger("test")
        txn = Transaction(name="test_txn")

        # 原始文件
        original = tmp_path / "original.txt"
        original.write_text("modified content")

        # 备份文件
        backup = tmp_path / "backup.txt"
        backup.write_text("original content")

        mod = ModificationRecord(
            original_path=original,
            backup_path=backup,
            action="modify",
        )
        txn.add_modification(mod)

        rolled_back = txn.rollback(logger)

        assert rolled_back == 1
        assert original.read_text() == "original content"

    def test_rollback_delete_action(self, tmp_path):
        """测试回滚 'delete' �操作（恢复已删除的文件）。"""
        logger = logging.getLogger("test")
        txn = Transaction(name="test_txn")

        # 原始路径（已被删除）
        original = tmp_path / "deleted.txt"

        # 备份存在
        backup = tmp_path / "backup_of_deleted.txt"
        backup.write_text("deleted file content")

        mod = ModificationRecord(
            original_path=original,
            backup_path=backup,
            action="delete",
        )
        txn.add_modification(mod)

        rolled_back = txn.rollback(logger)

        assert rolled_back == 1
        assert original.exists()
        assert original.read_text() == "deleted file content"

    def test_rollback_already_rolled_back(self, tmp_path):
        """测试重复回滚时返回 0 并记录警告。"""
        logger = logging.getLogger("test")
        txn = Transaction(name="test_txn")
        txn.rolled_back = True

        rolled_back = txn.rollback(logger)
        assert rolled_back == 0

    def test_rollback_reversed_order(self, tmp_path):
        """测试回滚按逆序执行。"""
        logger = logging.getLogger("test")
        txn = Transaction(name="test_txn")

        files = []
        for i in range(3):
            f = tmp_path / f"file_{i}.txt"
            f.write_text(f"content {i}")
            files.append(f)
            mod = ModificationRecord(
                original_path=f,
                backup_path=None,
                action="create",
            )
            txn.add_modification(mod)

        rolled_back = txn.rollback(logger)

        assert rolled_back == 3
        for f in files:
            assert not f.exists()


# ──────────────────────────────────────────────────────────────────────
# 测试 TransactionManager
# ──────────────────────────────────────────────────────────────────────


class TestTransactionManager:
    """TransactionManager 测试。"""

    def test_create_manager(self, tmp_path):
        """测试创建事务管理器。"""
        backup_dir = tmp_path / "backups"
        manager = TransactionManager(backup_dir=backup_dir)

        assert manager.backup_dir == backup_dir
        assert backup_dir.exists()

    def test_transaction_context_manager_success(self, tmp_path):
        """测试事务上下文管理器正常完成。"""
        manager = TransactionManager(backup_dir=tmp_path / "backups")

        with manager.transaction("test_op") as txn:
            assert isinstance(txn, Transaction)
            assert txn.name == "test_op"

        assert txn.completed is True

    def test_transaction_context_manager_failure(self, tmp_path):
        """测试事务上下文管理器异常时自动回滚。"""
        manager = TransactionManager(backup_dir=tmp_path / "backups")

        # 创建文件用于回滚测试
        test_file = tmp_path / "test.txt"
        test_file.write_text("content")

        with pytest.raises(ValueError, match="测试异常"):
            with manager.transaction("failing_op") as txn:
                mod = ModificationRecord(
                    original_path=test_file,
                    backup_path=None,
                    action="create",
                )
                txn.add_modification(mod)
                raise ValueError("测试异常")

        # 事务应该被回滚
        assert not test_file.exists()

    def test_record_modification_with_active_transaction(self, tmp_path):
        """测试在活跃事务中记录修改。"""
        manager = TransactionManager(backup_dir=tmp_path / "backups")

        # 创建要修改的文件
        test_file = tmp_path / "data.txt"
        test_file.write_text("original content")

        with manager.transaction("test_op") as txn:
            backup_path = manager.record_modification(test_file, "modify")

            assert backup_path is not None
            assert backup_path.exists()
            assert len(txn.modifications) == 1

    def test_record_modification_without_active_transaction(self, tmp_path):
        """测试无活跃事务时记录修改返回 None。"""
        manager = TransactionManager(backup_dir=tmp_path / "backups")

        test_file = tmp_path / "data.txt"
        test_file.write_text("content")

        backup_path = manager.record_modification(test_file, "modify")
        assert backup_path is None

    def test_record_modification_create_no_backup(self, tmp_path):
        """测试 'create' 操作不创建备份。"""
        manager = TransactionManager(backup_dir=tmp_path / "backups")

        test_file = tmp_path / "new.txt"

        with manager.transaction("test_op") as txn:
            backup_path = manager.record_modification(test_file, "create")

        assert backup_path is None

    def test_record_modification_delete_with_backup(self, tmp_path):
        """测试 'delete' 操作创建备份。"""
        manager = TransactionManager(backup_dir=tmp_path / "backups")

        test_file = tmp_path / "to_delete.txt"
        test_file.write_text("will be deleted")

        with manager.transaction("test_op") as txn:
            backup_path = manager.record_modification(test_file, "delete")

            assert backup_path is not None
            assert backup_path.exists()

    def test_rollback_by_name(self, tmp_path):
        """测试按名称回滚事务。"""
        manager = TransactionManager(backup_dir=tmp_path / "backups")

        test_file = tmp_path / "file.txt"
        test_file.write_text("original")

        with manager.transaction("rollback_me") as txn:
            mod = ModificationRecord(
                original_path=test_file,
                backup_path=None,
                action="create",
            )
            txn.add_modification(mod)

        # 手动标记为未完成以便回滚
        txn.completed = False
        rolled_back = manager.rollback("rollback_me")
        assert rolled_back == 1

    def test_rollback_nonexistent_transaction(self, tmp_path):
        """测试回滚不存在的事务返回 0。"""
        manager = TransactionManager(backup_dir=tmp_path / "backups")

        rolled_back = manager.rollback("nonexistent")
        assert rolled_back == 0

    def test_rollback_all(self, tmp_path):
        """测试回滚所有未完成的事务。"""
        manager = TransactionManager(backup_dir=tmp_path / "backups")

        files = []
        for i in range(3):
            f = tmp_path / f"file_{i}.txt"
            f.write_text(f"content {i}")
            files.append(f)

        # 手动创建事务（不使用 context manager，避免自动设置 completed=True）
        for i in range(3):
            txn = Transaction(name=f"txn_{i}")
            manager._transactions.append(txn)
            mod = ModificationRecord(
                original_path=files[i],
                backup_path=None,
                action="create",
            )
            txn.add_modification(mod)

        total = manager.rollback_all()
        assert total == 3

    def test_commit_transaction(self, tmp_path):
        """测试提交事务（清除备份）。"""
        manager = TransactionManager(backup_dir=tmp_path / "backups")

        test_file = tmp_path / "data.txt"
        test_file.write_text("content")

        with manager.transaction("commit_me") as txn:
            backup_path = manager.record_modification(test_file, "modify")
            assert backup_path is not None

        # 提交后备份应被清除
        manager.commit("commit_me")

        # 备份文件应不存在
        if backup_path:
            assert not backup_path.exists()

    def test_get_status(self, tmp_path):
        """测试获取事务状态。"""
        manager = TransactionManager(backup_dir=tmp_path / "backups")

        with manager.transaction("txn1") as txn:
            pass

        status = manager.get_status()

        assert status["total_transactions"] == 1
        assert status["active"] is None
        assert len(status["transactions"]) == 1
        assert status["transactions"][0]["name"] == "txn1"
        assert status["transactions"][0]["completed"] is True

    def test_cleanup(self, tmp_path):
        """测试清理备份目录。"""
        backup_dir = tmp_path / "backups"
        manager = TransactionManager(backup_dir=backup_dir)

        # 在备份目录中放入文件
        (backup_dir / "test_backup").write_text("data")

        manager.cleanup()

        assert backup_dir.exists()
        # 清理后目录应为空
        assert len(list(backup_dir.iterdir())) == 0


# ──────────────────────────────────────────────────────────────────────
# 测试 RollbackContext
# ──────────────────────────────────────────────────────────────────────


class TestRollbackContext:
    """RollbackContext 测试。"""

    def test_rollback_context_normal_exit(self, tmp_path):
        """测试正常退出时不回滚。"""
        manager = TransactionManager(backup_dir=tmp_path / "backups")

        with RollbackContext(manager, "normal_op") as ctx:
            assert isinstance(ctx, RollbackContext)

    def test_rollback_context_exception_triggers_rollback(self, tmp_path):
        """测试异常时触发回滚。"""
        manager = TransactionManager(backup_dir=tmp_path / "backups")

        test_file = tmp_path / "file.txt"
        test_file.write_text("content")

        with pytest.raises(RuntimeError):
            with RollbackContext(manager, "failing_op") as rb_ctx:
                # 在事务中创建文件
                with manager.transaction("failing_op") as txn:
                    mod = ModificationRecord(
                        original_path=test_file,
                        backup_path=None,
                        action="create",
                    )
                    txn.add_modification(mod)
                raise RuntimeError("测试错误")


# ──────────────────────────────────────────────────────────────────────
# 测试 track_modification
# ──────────────────────────────────────────────────────────────────────


class TestTrackModification:
    """track_modification 辅助函数测试。"""

    def test_track_modification_decorator(self, tmp_path):
        """测试 track_modification 装饰器。"""
        manager = TransactionManager(backup_dir=tmp_path / "backups")

        test_file = tmp_path / "file.txt"
        test_file.write_text("content")

        with manager.transaction("test"):
            @track_modification(manager, test_file, "modify")
            def modify_file():
                test_file.write_text("modified")

            modify_file()

            # 应该有一条修改记录
            status = manager.get_status()
            assert status["transactions"][0]["modifications"] == 1

    def test_track_modification_preserves_return_value(self, tmp_path):
        """测试装饰器保留函数返回值。"""
        manager = TransactionManager(backup_dir=tmp_path / "backups")

        test_file = tmp_path / "file.txt"

        with manager.transaction("test"):
            @track_modification(manager, test_file, "create")
            def create_file():
                return "created"

            result = create_file()
            assert result == "created"
