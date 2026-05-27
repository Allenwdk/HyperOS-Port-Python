# 插件开发指南

## 概述

HyperOS 移植工具 v2.0 采用插件化架构，允许开发者轻松扩展系统功能。本指南详细介绍如何开发自定义插件。

## 插件架构

### 插件生命周期

```
注册 → 初始化 → 前置检查 → 执行 → 完成
  │        │         │        │      │
  │        │         │        │      └── 清理资源
  │        │         │        └── 执行修改逻辑
  │        │         └── 检查前置条件
  │        └── 加载配置
  └── 注册到插件管理器
```

### 插件优先级

优先级决定插件执行顺序，数值越小越先执行：

| 优先级范围 | 用途 | 示例 |
|-----------|------|------|
| 0-10 | 关键系统设置 | 内核模块安装、AVB 禁用 |
| 11-20 | 文件操作 | 文件替换、资源覆盖 |
| 21-40 | 系统配置 | 功能解锁、VNDK 修复 |
| 41-60 | 本地化和 UI | EU 本地化、主题适配 |
| 61-100 | 可选/自定义 | 用户扩展、调试插件 |

## 快速开始

### 1. 创建基本插件

```python
from src.core.modifiers import ModifierPlugin, ModifierRegistry

@ModifierRegistry.register
class MyFirstPlugin(ModifierPlugin):
    """我的第一个插件"""
    
    name = "my_first_plugin"
    description = "示例插件"
    version = "1.0.0"
    priority = 50
    
    def check_prerequisites(self) -> bool:
        """检查前置条件"""
        # 返回 True 表示可以执行
        return True
    
    def modify(self) -> bool:
        """执行修改"""
        try:
            # 获取目标目录
            target_dir = self.ctx.target_dir
            
            # 执行修改逻辑
            self._do_something(target_dir)
            
            return True
        except Exception as e:
            self.logger.error(f"插件执行失败: {e}")
            return False
    
    def _do_something(self, target_dir):
        """具体的修改逻辑"""
        # 在这里实现你的修改
        pass
```

### 2. 注册插件

插件通过装饰器自动注册：

```python
@ModifierRegistry.register
class MyPlugin(ModifierPlugin):
    # ...
    pass
```

或者手动注册：

```python
from src.core.modifiers import ModifierRegistry, PluginManager

# 手动注册
ModifierRegistry.register(MyPlugin)

# 或者在插件管理器中注册
manager = PluginManager(ctx)
manager.register(MyPlugin)
```

## 插件基类详解

### ModifierPlugin 属性

```python
class ModifierPlugin:
    """插件基类"""
    
    # 必需属性
    name: str                    # 插件唯一标识
    description: str             # 插件描述
    
    # 可选属性
    version: str = "1.0.0"      # 插件版本
    priority: int = 50           # 执行优先级
    dependencies: List[str] = [] # 依赖的其他插件
    enabled: bool = True         # 是否启用
    
    # 内置属性（自动设置）
    ctx: PortingContext          # 移植上下文
    logger: logging.Logger       # 日志记录器
    config: Dict[str, Any]       # 设备配置
```

### ModifierPlugin 方法

```python
class ModifierPlugin:
    def check_prerequisites(self) -> bool:
        """
        检查前置条件
        
        返回:
            True: 可以执行
            False: 跳过执行
        """
        return True
    
    def modify(self) -> bool:
        """
        执行修改（必须实现）
        
        返回:
            True: 执行成功
            False: 执行失败
        """
        raise NotImplementedError
    
    def get_config(self, key: str, default=None) -> Any:
        """
        获取配置值
        
        参数:
            key: 配置键
            default: 默认值
        """
        return self.config.get(key, default)
    
    def log_info(self, message: str):
        """记录信息日志"""
        self.logger.info(f"[{self.name}] {message}")
    
    def log_error(self, message: str):
        """记录错误日志"""
        self.logger.error(f"[{self.name}] {message}")
```

## 高级功能

### 1. 插件依赖

插件可以声明依赖关系，确保执行顺序：

```python
@ModifierRegistry.register
class BasePlugin(ModifierPlugin):
    name = "base_plugin"
    priority = 10
    
    def modify(self) -> bool:
        # 基础修改
        return True

@ModifierRegistry.register
class DependentPlugin(ModifierPlugin):
    name = "dependent_plugin"
    priority = 20
    dependencies = ["base_plugin"]  # 依赖 base_plugin
    
    def modify(self) -> bool:
        # 在 base_plugin 之后执行
        return True
```

### 2. 条件执行

根据条件决定是否执行插件：

```python
@ModifierRegistry.register
class ConditionalPlugin(ModifierPlugin):
    name = "conditional_plugin"
    
    def check_prerequisites(self) -> bool:
        """检查是否满足执行条件"""
        # 检查设备配置
        if not self.get_config("wild_boost.enable", False):
            self.log_info("狂暴引擎未启用，跳过")
            return False
        
        # 检查内核版本
        kernel_version = self.ctx.device.kernel_version
        if kernel_version < "5.10":
            self.log_info(f"内核版本 {kernel_version} 不支持")
            return False
        
        return True
    
    def modify(self) -> bool:
        # 只有在前置条件满足时才执行
        return True
```

### 3. 使用上下文

通过上下文访问系统资源：

```python
@ModifierRegistry.register
class ContextAwarePlugin(ModifierPlugin):
    name = "context_aware"
    
    def modify(self) -> bool:
        # 访问设备信息
        device_code = self.ctx.device.device_code
        kernel_version = self.ctx.device.kernel_version
        
        # 访问工作目录
        target_dir = self.ctx.target_dir
        system_dir = target_dir / "system"
        
        # 访问 ROM 信息
        is_port_rom = self.ctx.is_port_rom
        stock_path = self.ctx.stock_path
        
        # 访问打包配置
        pack_type = self.ctx.pack.pack_type
        fs_type = self.ctx.pack.fs_type
        
        return True
```

### 4. 文件操作

插件中常用的文件操作：

```python
import shutil
from pathlib import Path

@ModifierRegistry.register
class FileOperationPlugin(ModifierPlugin):
    name = "file_operation"
    
    def modify(self) -> bool:
        target_dir = self.ctx.target_dir
        
        # 复制文件
        src = Path("path/to/source")
        dst = target_dir / "system" / "app" / "MyApp.apk"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        
        # 删除文件
        file_to_delete = target_dir / "system" / "app" / "OldApp.apk"
        if file_to_delete.exists():
            file_to_delete.unlink()
        
        # 修改文件内容
        prop_file = target_dir / "system" / "build.prop"
        if prop_file.exists():
            content = prop_file.read_text()
            content += "\n# Added by my plugin\nro.my.custom.prop=true\n"
            prop_file.write_text(content)
        
        return True
```

### 5. Smali 补丁

应用 Smali 补丁：

```python
from src.utils.smalikit import SmaliKit

@ModifierRegistry.register
class SmaliPatchPlugin(ModifierPlugin):
    name = "smali_patch"
    
    def modify(self) -> bool:
        # APK 路径
        apk_path = self.ctx.target_dir / "system" / "app" / "MyApp.apk"
        
        # 创建 SmaliKit 实例
        smali = SmaliKit(apk_path)
        
        # 反编译
        smali.decompile()
        
        # 应用补丁
        smali.patch_method(
            class_name="Lcom/example/MyClass;",
            method_name="doInBackground",
            patch_code="""invoke-static {p0}, Lcom/example/Patcher;->patch(Ljava/lang/Object;)V"""
        )
        
        # 重新编译
        smali.compile()
        
        return True
```

## 内置插件参考

### FileReplacementPlugin

**名称**: `file_replacement`
**优先级**: 20
**用途**: 执行文件/目录替换

```python
class FileReplacementPlugin(ModifierPlugin):
    name = "file_replacement"
    description = "执行 replacements.json 中定义的文件替换"
    priority = 20
```

**配置示例** (`replacements.json`):
```json
[
    {
        "description": "系统 Overlays",
        "type": "file",
        "search_path": "product",
        "files": ["DevicesOverlay.apk"]
    }
]
```

### WildBoostPlugin

**名称**: `wild_boost`
**优先级**: 10
**用途**: 安装狂暴引擎

```python
class WildBoostPlugin(ModifierPlugin):
    name = "wild_boost"
    description = "安装狂暴引擎内核模块和设备伪装"
    priority = 10
    
    def check_prerequisites(self) -> bool:
        return self.get_config("wild_boost.enable", False)
```

**配置示例** (`config.json`):
```json
{
    "wild_boost": {
        "enable": true
    }
}
```

### FeatureUnlockPlugin

**名称**: `feature_unlock`
**优先级**: 30
**用途**: 解锁设备功能

```python
class FeatureUnlockPlugin(ModifierPlugin):
    name = "feature_unlock"
    description = "从 features.json 解锁设备功能"
    priority = 30
    dependencies = ["wild_boost"]
```

**配置示例** (`features.json`):
```json
{
    "xml_features": {
        "support_AI_display": true,
        "support_wild_boost": true
    },
    "build_props": {
        "product": {
            "ro.product.spoofed.name": "vermeer"
        }
    }
}
```

### VNDKFixPlugin

**名称**: `vndk_fix`
**优先级**: 40
**用途**: 修复 VNDK APEX 和 VINTF manifest

### EULocalizationPlugin

**名称**: `eu_localization`
**优先级**: 50
**用途**: 应用 EU 本地化包

## 测试插件

### 单元测试

```python
import pytest
from unittest.mock import MagicMock, patch
from src.core.modifiers import MyPlugin

class TestMyPlugin:
    @pytest.fixture
    def mock_context(self):
        """创建模拟上下文"""
        ctx = MagicMock()
        ctx.device.device_code = "test_device"
        ctx.target_dir = Path("/tmp/test")
        return ctx
    
    @pytest.fixture
    def plugin(self, mock_context):
        """创建插件实例"""
        return MyPlugin(ctx=mock_context)
    
    def test_check_prerequisites(self, plugin):
        """测试前置条件检查"""
        assert plugin.check_prerequisites() is True
    
    def test_modify_success(self, plugin):
        """测试修改成功"""
        with patch.object(plugin, '_do_something'):
            assert plugin.modify() is True
    
    def test_modify_failure(self, plugin):
        """测试修改失败"""
        with patch.object(plugin, '_do_something', side_effect=Exception("Test error")):
            assert plugin.modify() is False
```

### 集成测试

```python
import pytest
from src.core.modifiers import PluginManager

class TestPluginIntegration:
    @pytest.fixture
    def real_context(self):
        """创建真实上下文（使用测试数据）"""
        # 设置测试环境
        # ...
        return ctx
    
    def test_plugin_execution(self, real_context):
        """测试插件完整执行流程"""
        manager = PluginManager(real_context)
        manager.register(MyPlugin)
        
        results = manager.execute()
        assert results["my_plugin"] is True
```

## 最佳实践

### 1. 单一职责

每个插件只负责一个功能：

```python
# 好的做法
class WildBoostPlugin(ModifierPlugin):
    """只负责狂暴引擎"""
    name = "wild_boost"

# 不好的做法
class DoEverythingPlugin(ModifierPlugin):
    """做了太多事情"""
    name = "do_everything"
```

### 2. 错误处理

优雅地处理错误，不要让插件崩溃：

```python
def modify(self) -> bool:
    try:
        # 执行修改
        return True
    except FileNotFoundError as e:
        self.log_error(f"文件未找到: {e}")
        return False
    except PermissionError as e:
        self.log_error(f"权限错误: {e}")
        return False
    except Exception as e:
        self.log_error(f"未知错误: {e}")
        return False
```

### 3. 日志记录

使用日志记录关键操作：

```python
def modify(self) -> bool:
    self.log_info("开始执行修改...")
    
    # 执行操作
    self.log_info(f"处理文件: {file_path}")
    
    # 完成
    self.log_info("修改完成")
    return True
```

### 4. 配置验证

在执行前验证配置：

```python
def check_prerequisites(self) -> bool:
    # 检查必需配置
    if not self.get_config("required_key"):
        self.log_error("缺少必需配置: required_key")
        return False
    
    # 检查配置值范围
    value = self.get_config("numeric_key", 0)
    if not (0 <= value <= 100):
        self.log_error(f"配置值超出范围: {value}")
        return False
    
    return True
```

### 5. 文档和注释

为插件提供清晰的文档：

```python
class MyPlugin(ModifierPlugin):
    """
    我的自定义插件
    
    功能:
    - 功能 1
    - 功能 2
    
    配置:
    - my_plugin.enable: 是否启用
    - my_plugin.option: 配置选项
    
    依赖:
    - base_plugin: 需要先执行基础插件
    """
    name = "my_plugin"
    description = "我的自定义插件"
    dependencies = ["base_plugin"]
```

## 常见问题

### Q1: 插件没有执行？

检查以下几点：
1. 插件是否已注册？
2. `check_prerequisites()` 是否返回 True？
3. 插件是否被禁用？
4. 优先级是否正确？

### Q2: 插件执行顺序不对？

检查优先级设置，数值越小越先执行。如果有依赖关系，使用 `dependencies` 属性。

### Q3: 如何调试插件？

```bash
# 启用调试日志
sudo python3 main.py --stock <底包> --debug

# 或者在插件中添加调试日志
self.logger.debug("调试信息")
```

### Q4: 插件可以修改打包配置吗？

可以，通过上下文修改：

```python
def modify(self) -> bool:
    # 修改打包配置
    self.ctx.pack.fs_type = "ext4"
    return True
```

## 示例插件

### 完整示例：自定义属性修改插件

```python
from src.core.modifiers import ModifierPlugin, ModifierRegistry
from pathlib import Path

@ModifierRegistry.register
class CustomPropPlugin(ModifierPlugin):
    """自定义属性修改插件
    
    功能: 向 build.prop 添加自定义属性
    
    配置:
    - custom_props: 属性字典
    
    示例配置:
    {
        "custom_props": {
            "ro.my.custom.prop": "true",
            "persist.my.setting": "1"
        }
    }
    """
    
    name = "custom_props"
    description = "添加自定义属性"
    version = "1.0.0"
    priority = 35
    
    def check_prerequisites(self) -> bool:
        """检查是否有自定义属性需要添加"""
        custom_props = self.get_config("custom_props", {})
        if not custom_props:
            self.log_info("没有自定义属性，跳过")
            return False
        return True
    
    def modify(self) -> bool:
        """添加自定义属性到 build.prop"""
        try:
            custom_props = self.get_config("custom_props", {})
            
            # 获取 build.prop 路径
            build_prop = self.ctx.target_dir / "system" / "build.prop"
            
            if not build_prop.exists():
                self.log_error("build.prop 不存在")
                return False
            
            # 读取现有内容
            content = build_prop.read_text()
            
            # 添加自定义属性
            lines = ["\n# Custom properties added by custom_props plugin\n"]
            for key, value in custom_props.items():
                lines.append(f"{key}={value}\n")
            
            content += "".join(lines)
            
            # 写入文件
            build_prop.write_text(content)
            
            self.log_info(f"已添加 {len(custom_props)} 个自定义属性")
            return True
            
        except Exception as e:
            self.log_error(f"添加自定义属性失败: {e}")
            return False
```

## 相关文档

- [架构文档](architecture.md) - 系统架构设计
- [迁移指南](migration.md) - 从 v1.x 升级
- [缓存机制](CACHE.md) - 分层缓存系统

---

> 返回 [README](../README.md) | 查看 [架构文档](architecture.md)
