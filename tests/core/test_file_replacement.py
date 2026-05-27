"""File Replacement 插件测试。

测试文件替换插件的各项功能：
- 规则类型处理（unzip_override、copy_file_internal、remove_files、
  hexpatch、append_text、copy_local、legacy replacement）
- 配置加载与合并
- 条件评估
"""

import json
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch, call

import pytest

from src.core.modifiers.plugins.file_replacement import FileReplacementPlugin


def _create_mock_context(tmp_path: Path) -> MagicMock:
    ctx = MagicMock()
    ctx.target_dir = tmp_path / "target"
    ctx.target_dir.mkdir(parents=True, exist_ok=True)
    ctx.stock = MagicMock()
    ctx.stock.extracted_dir = tmp_path / "stock"
    ctx.stock.extracted_dir.mkdir(parents=True, exist_ok=True)
    ctx.device_config = {}
    ctx.stock_rom_code = "test_device"
    ctx.base_chipset_family = "sm8550"
    ctx.port_android_version = 15
    ctx.base_android_version = 15
    ctx.port = MagicMock()
    ctx.port.get_prop.return_value = "V816.0.25.0.VNCCNXM"
    ctx.is_port_eu_rom = False
    return ctx


@pytest.fixture
def plugin(tmp_path):
    ctx = _create_mock_context(tmp_path)

    with patch("src.core.config_merger.ConfigMerger") as mock_merger_cls, \
         patch("src.core.conditions.ConditionEvaluator") as mock_eval_cls, \
         patch("src.utils.download.AssetDownloader") as mock_dl_cls, \
         patch("src.utils.shell.ShellRunner"):
        mock_merger = MagicMock()
        mock_merger_cls.return_value = mock_merger
        mock_merger.load_and_merge.return_value = ({"replacements": []}, MagicMock(loaded_files=[]))

        mock_eval = MagicMock()
        mock_eval_cls.return_value = mock_eval
        mock_eval.evaluate_with_reason.return_value = (True, "ok")

        mock_dl = MagicMock()
        mock_dl_cls.return_value = mock_dl

        p = FileReplacementPlugin(ctx)
        p._mock_merger = mock_merger
        p._mock_evaluator = mock_eval
        p._mock_downloader = mock_dl
        yield p


class TestFileReplacementModify:

    def test_modify_no_replacements_returns_true(self, plugin):
        plugin._mock_merger.load_and_merge.return_value = ({"replacements": []}, MagicMock(loaded_files=[]))
        result = plugin.modify()
        assert result is True

    def test_modify_skips_condition_false(self, plugin, tmp_path):
        rule = {"description": "跳过的规则", "type": "file", "search_path": "system"}
        plugin._mock_merger.load_and_merge.return_value = (
            {"replacements": [rule]},
            MagicMock(loaded_files=["test.json"]),
        )
        plugin._mock_evaluator.evaluate_with_reason.return_value = (False, "条件不满足")

        result = plugin.modify()
        assert result is True

    def test_modify_processes_replacement(self, plugin, tmp_path):
        rule = {"description": "测试规则", "type": "remove_files", "search_path": "system", "files": []}
        plugin._mock_merger.load_and_merge.return_value = (
            {"replacements": [rule]},
            MagicMock(loaded_files=["test.json"]),
        )
        plugin._mock_evaluator.evaluate_with_reason.return_value = (True, "ok")

        result = plugin.modify()
        assert result is True


class TestHandleUnzipOverride:

    def test_unzip_override_source_exists(self, plugin, tmp_path):
        source_zip = tmp_path / "test.zip"
        with zipfile.ZipFile(source_zip, "w") as zf:
            zf.writestr("test.txt", "hello world")

        rule = {"source": str(source_zip)}
        plugin._handle_unzip_override(rule, plugin.ctx.target_dir)

        extracted = plugin.ctx.target_dir / "test.txt"
        assert extracted.exists()
        assert extracted.read_text() == "hello world"

    def test_unzip_override_source_not_exists(self, plugin, tmp_path):
        rule = {"source": str(tmp_path / "nonexistent.zip")}
        plugin._handle_unzip_override(rule, plugin.ctx.target_dir)

    def test_unzip_override_with_target_dir(self, plugin, tmp_path):
        source_zip = tmp_path / "test.zip"
        with zipfile.ZipFile(source_zip, "w") as zf:
            zf.writestr("data.txt", "content")

        rule = {"source": str(source_zip), "target": "subdir"}
        plugin._handle_unzip_override(rule, plugin.ctx.target_dir)

        extracted = plugin.ctx.target_dir / "subdir" / "data.txt"
        assert extracted.exists()


class TestHandleCopyFileInternal:

    def test_copy_file_internal_file(self, plugin, tmp_path):
        source = plugin.ctx.target_dir / "src_file.txt"
        source.write_text("source content")

        rule = {"source": "src_file.txt", "target": "dst_file.txt"}
        plugin._handle_copy_file_internal(rule, plugin.ctx.target_dir)

        dest = plugin.ctx.target_dir / "dst_file.txt"
        assert dest.exists()
        assert dest.read_text() == "source content"

    def test_copy_file_internal_directory(self, plugin, tmp_path):
        source_dir = plugin.ctx.target_dir / "src_dir"
        source_dir.mkdir(parents=True, exist_ok=True)
        (source_dir / "file.txt").write_text("dir content")

        rule = {"source": "src_dir", "target": "dst_dir"}
        plugin._handle_copy_file_internal(rule, plugin.ctx.target_dir)

        dest = plugin.ctx.target_dir / "dst_dir" / "file.txt"
        assert dest.exists()

    def test_copy_file_internal_source_not_exists(self, plugin, tmp_path):
        rule = {"source": "nonexistent.txt", "target": "dst.txt"}
        plugin._handle_copy_file_internal(rule, plugin.ctx.target_dir)

    def test_copy_file_internal_ensure_exists_warns(self, plugin, tmp_path):
        rule = {"source": "nonexistent.txt", "target": "dst.txt", "ensure_exists": True}
        plugin._handle_copy_file_internal(rule, plugin.ctx.target_dir)


class TestHandleRemoveFiles:

    def test_remove_files_removes_matching(self, plugin, tmp_path):
        search_dir = plugin.ctx.target_dir / "system" / "app"
        search_dir.mkdir(parents=True, exist_ok=True)
        (search_dir / "OldApp.apk").write_bytes(b"apk")
        (search_dir / "KeepApp.apk").write_bytes(b"apk")

        rule = {"search_path": "system/app", "files": ["OldApp.apk"]}
        plugin._handle_remove_files(rule, plugin.ctx.target_dir)

        assert not (search_dir / "OldApp.apk").exists()
        assert (search_dir / "KeepApp.apk").exists()

    def test_remove_files_directory(self, plugin, tmp_path):
        search_dir = plugin.ctx.target_dir / "system" / "app"
        target_dir = search_dir / "OldDir"
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "file.txt").write_text("data")

        rule = {"search_path": "system/app", "files": ["OldDir"]}
        plugin._handle_remove_files(rule, plugin.ctx.target_dir)

        assert not target_dir.exists()

    def test_remove_files_no_match(self, plugin, tmp_path):
        search_dir = plugin.ctx.target_dir / "system"
        search_dir.mkdir(parents=True, exist_ok=True)

        rule = {"search_path": "system", "files": ["nonexistent.apk"]}
        plugin._handle_remove_files(rule, plugin.ctx.target_dir)


class TestHandleHexpatch:

    def test_hexpatch_applies_to_file(self, plugin, tmp_path):
        target_file = plugin.ctx.target_dir / "lib64" / "test.so"
        target_file.parent.mkdir(parents=True, exist_ok=True)
        old_bytes = bytes.fromhex("AABBCCDD")
        target_file.write_bytes(b"prefix" + old_bytes + b"suffix")

        rule = {
            "target": "lib64/test.so",
            "patches": [{"old": "AABBCCDD", "new": "11223344"}],
        }
        plugin._handle_hexpatch(rule, plugin.ctx.target_dir)

        content = target_file.read_bytes()
        assert bytes.fromhex("11223344") in content
        assert old_bytes not in content

    def test_hexpatch_no_match(self, plugin, tmp_path):
        target_file = plugin.ctx.target_dir / "test.so"
        target_file.write_bytes(b"no match here")

        from src.core.performance.cache import PathCache
        plugin._target_cache = PathCache(plugin.ctx.target_dir)

        rule = {
            "target": "test.so",
            "patches": [{"old": "AABB", "new": "1122"}],
        }
        plugin._handle_hexpatch(rule, plugin.ctx.target_dir)

        assert target_file.read_bytes() == b"no match here"

    def test_hexpatch_target_not_found(self, plugin, tmp_path):
        from src.core.performance.cache import PathCache
        plugin._target_cache = PathCache(plugin.ctx.target_dir)

        rule = {
            "target": "nonexistent.so",
            "patches": [{"old": "AABB", "new": "1122"}],
        }
        plugin._handle_hexpatch(rule, plugin.ctx.target_dir)


class TestHandleAppendText:

    def test_append_text_adds_text(self, plugin, tmp_path):
        target_file = plugin.ctx.target_dir / "build.prop"
        target_file.write_text("ro.build.type=user\n", encoding="utf-8")

        rule = {"target": "build.prop", "text": "persist.sys.test=true"}
        plugin._handle_append_text(rule, plugin.ctx.target_dir)

        content = target_file.read_text(encoding="utf-8")
        assert "persist.sys.test=true" in content

    def test_append_text_already_exists(self, plugin, tmp_path):
        target_file = plugin.ctx.target_dir / "build.prop"
        target_file.write_text("persist.sys.test=true\n", encoding="utf-8")

        rule = {"target": "build.prop", "text": "persist.sys.test=true"}
        plugin._handle_append_text(rule, plugin.ctx.target_dir)

        content = target_file.read_text(encoding="utf-8")
        count = content.count("persist.sys.test=true")
        assert count == 1

    def test_append_text_file_not_exists(self, plugin, tmp_path):
        rule = {"target": "nonexistent.prop", "text": "key=value"}
        plugin._handle_append_text(rule, plugin.ctx.target_dir)

    def test_append_text_empty_text(self, plugin, tmp_path):
        target_file = plugin.ctx.target_dir / "build.prop"
        original = "ro.build.type=user\n"
        target_file.write_text(original, encoding="utf-8")

        rule = {"target": "build.prop", "text": ""}
        plugin._handle_append_text(rule, plugin.ctx.target_dir)

        assert target_file.read_text(encoding="utf-8") == original


class TestHandleRule:

    def test_handle_rule_wild_boost_skipped(self, plugin, tmp_path):
        rule = {"type": "wild_boost", "description": "狂暴引擎"}
        plugin._handle_rule(rule, "wild_boost", tmp_path, plugin.ctx.target_dir)

    def test_handle_rule_unknown_type(self, plugin, tmp_path):
        stock_dir = tmp_path / "stock"
        stock_dir.mkdir(parents=True, exist_ok=True)

        rule = {"type": "unknown_type", "search_path": "", "files": []}
        plugin._handle_rule(rule, "unknown_type", stock_dir, plugin.ctx.target_dir)
