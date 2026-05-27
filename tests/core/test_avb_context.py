"""AVBContext 数据类的单元测试。

测试覆盖：
1. 默认初始化
2. 自定义参数初始化
3. 从 partition_info.json 加载配置
4. 文件不存在时的回退行为
"""

from pathlib import Path

from src.core.context.avb import AVBContext


class TestAVBContext默认初始化:
    """测试 AVBContext 的默认参数初始化。"""

    def test_默认状态下自定义AVB链未启用(self) -> None:
        """验证默认状态下 custom_avb_chain 为 False。"""
        ctx = AVBContext()
        assert ctx.custom_avb_chain is False

    def test_默认状态下AVB密钥路径为空(self) -> None:
        """验证默认状态下 avb_key_path 为 None。"""
        ctx = AVBContext()
        assert ctx.avb_key_path is None

    def test_默认状态下分区列表为空(self) -> None:
        """验证默认状态下所有分区列表为空。"""
        ctx = AVBContext()
        assert ctx.avb_hash_partitions == []
        assert ctx.avb_hashtree_partitions == []
        assert ctx.avb_chain_partitions == []
        assert ctx.avb_strict_partitions == []
        assert ctx.physical_partition_sizes == {}


class TestAVBContext自定义初始化:
    """测试 AVBContext 的自定义参数初始化。"""

    def test_启用自定义AVB链(self) -> None:
        """验证可以启用自定义 AVB 链。"""
        ctx = AVBContext(custom_avb_chain=True)
        assert ctx.custom_avb_chain is True

    def test_设置AVB密钥路径(self) -> None:
        """验证可以设置 AVB 密钥路径。"""
        key_path = Path("/path/to/testkey.pem")
        ctx = AVBContext(avb_key_path=key_path)
        assert ctx.avb_key_path == key_path

    def test_设置哈希分区列表(self) -> None:
        """验证可以设置 AVB 哈希分区列表。"""
        partitions = ["boot", "dtbo", "vendor_boot"]
        ctx = AVBContext(avb_hash_partitions=partitions)
        assert ctx.avb_hash_partitions == partitions

    def test_设置哈希树分区列表(self) -> None:
        """验证可以设置 AVB 哈希树分区列表。"""
        partitions = ["system", "vendor", "product"]
        ctx = AVBContext(avb_hashtree_partitions=partitions)
        assert ctx.avb_hashtree_partitions == partitions

    def test_设置链分区描述符(self) -> None:
        """验证可以设置 AVB 链分区描述符。"""
        chain = [
            {"name": "boot", "rollback_index_location": 3},
            {"name": "recovery", "rollback_index_location": 1},
        ]
        ctx = AVBContext(avb_chain_partitions=chain)
        assert ctx.avb_chain_partitions == chain

    def test_设置严格分区列表(self) -> None:
        """验证可以设置严格物理分区上限保护列表。"""
        strict = ["boot", "dtbo", "recovery"]
        ctx = AVBContext(avb_strict_partitions=strict)
        assert ctx.avb_strict_partitions == strict

    def test_设置物理分区大小(self) -> None:
        """验证可以设置物理分区大小映射。"""
        sizes = {"boot": 100663296, "dtbo": 23068672}
        ctx = AVBContext(physical_partition_sizes=sizes)
        assert ctx.physical_partition_sizes == sizes


class TestAVBContext从文件加载:
    """测试 AVBContext 从 partition_info.json 加载配置。"""

    def test_从JSON文件加载完整配置(self, tmp_path: Path) -> None:
        """验证可以从 partition_info.json 正确加载 AVB 配置。"""
        config = {
            "avb_hash_partitions": ["countrycode", "dtbo", "init_boot", "vendor_boot"],
            "avb_hashtree_partitions": ["system", "vendor", "product"],
            "avb_chain_partitions": [
                {"name": "boot", "rollback_index_location": 3},
                {"name": "recovery", "rollback_index_location": 1},
            ],
            "avb_strict_partitions": ["boot", "dtbo", "recovery"],
            "physical_partition_sizes": {
                "boot": 100663296,
                "dtbo": 23068672,
            },
        }
        config_path = tmp_path / "partition_info.json"
        import json

        config_path.write_text(json.dumps(config), encoding="utf-8")

        ctx = AVBContext.from_partition_info(config_path)
        assert ctx.avb_hash_partitions == ["countrycode", "dtbo", "init_boot", "vendor_boot"]
        assert ctx.avb_hashtree_partitions == ["system", "vendor", "product"]
        assert len(ctx.avb_chain_partitions) == 2
        assert ctx.avb_chain_partitions[0]["name"] == "boot"
        assert ctx.avb_strict_partitions == ["boot", "dtbo", "recovery"]
        assert ctx.physical_partition_sizes == {"boot": 100663296, "dtbo": 23068672}

    def test_文件不存在时返回默认配置(self, tmp_path: Path) -> None:
        """验证当 partition_info.json 不存在时返回默认空配置。"""
        non_existent = tmp_path / "non_existent.json"
        ctx = AVBContext.from_partition_info(non_existent)
        assert ctx.custom_avb_chain is False
        assert ctx.avb_hash_partitions == []
        assert ctx.avb_hashtree_partitions == []
        assert ctx.avb_chain_partitions == []
        assert ctx.avb_strict_partitions == []
        assert ctx.physical_partition_sizes == {}

    def test_JSON缺少AVB字段时使用默认值(self, tmp_path: Path) -> None:
        """验证当 JSON 文件缺少 AVB 相关字段时使用默认空值。"""
        config = {"device_code": "test_device", "super_size": 1024}
        config_path = tmp_path / "partition_info.json"
        import json

        config_path.write_text(json.dumps(config), encoding="utf-8")

        ctx = AVBContext.from_partition_info(config_path)
        assert ctx.avb_hash_partitions == []
        assert ctx.avb_hashtree_partitions == []
        assert ctx.avb_chain_partitions == []
        assert ctx.avb_strict_partitions == []
        assert ctx.physical_partition_sizes == {}
