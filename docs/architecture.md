# HyperOS 移植工具架构文档

## 概述

HyperOS 移植工具 v2.0 采用模块化、事件驱动的架构设计，旨在提供更好的可扩展性、可维护性和性能。

## 架构设计原则

1. **关注点分离**: 每个模块负责单一职责
2. **插件化设计**: 核心功能通过插件扩展
3. **事件驱动**: 组件间通过事件通信，降低耦合
4. **上下文传递**: 统一的上下文管理，避免参数传递混乱
5. **性能优先**: 缓存、增量处理、并行执行

## 系统架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                        应用层 (src/app)                         │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐       │
│  │  CLI     │  │ Bootstrap│  │ Preflight│  │ Snapshots│       │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘       │
│       └──────────────┴──────────────┴──────────────┘            │
│                              │                                  │
└──────────────────────────────┼──────────────────────────────────┘
                               │
┌──────────────────────────────┼──────────────────────────────────┐
│                        核心层 (src/core)                        │
│                              │                                  │
│  ┌───────────────────────────┼───────────────────────────┐      │
│  │                   工作流引擎                           │      │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐   │      │
│  │  │Orchestrator │  │   Phases    │  │  Pipeline   │   │      │
│  │  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘   │      │
│  │         └────────────────┼────────────────┘          │      │
│  └───────────────────────────┼───────────────────────────┘      │
│                              │                                  │
│  ┌──────────┐  ┌──────────┐  │  ┌──────────┐  ┌──────────┐    │
│  │ Context  │  │  Events  │  │  │ Modifiers│  │  Packing │    │
│  │          │  │          │  │  │          │  │          │    │
│  │ • device │  │ • bus    │  │  │ • plugins│  │ • ota    │    │
│  │ • pack   │  │ • events │  │  │ • framework│ │ • super  │    │
│  │ • avb    │  │ • handlers│ │  │ • unified│  │ • avb    │    │
│  │ • workflow│ │          │  │  │          │  │          │    │
│  └──────────┘  └──────────┘  │  └──────────┘  └──────────┘    │
│                              │                                  │
│  ┌───────────────────────────┼───────────────────────────┐      │
│  │                   性能优化层                           │      │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐   │      │
│  │  │   Cache     │  │   Hasher    │  │ Incremental │   │      │
│  │  └─────────────┘  └─────────────┘  └─────────────┘   │      │
│  └───────────────────────────────────────────────────────┘      │
│                              │                                  │
└──────────────────────────────┼──────────────────────────────────┘
                               │
┌──────────────────────────────┼──────────────────────────────────┐
│                        工具层 (src/utils)                       │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐       │
│  │  Shell   │  │ SmaliKit │  │  XML     │  │ Payload  │       │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘       │
└─────────────────────────────────────────────────────────────────┘
```

## 核心模块详解

### 1. 应用层 (src/app)

应用层负责用户交互和应用引导。

#### CLI (cli.py)
- 解析命令行参数
- 验证输入参数
- 调用工作流引擎

#### Bootstrap (bootstrap.py)
- 初始化应用环境
- 加载配置
- 设置日志系统

#### Preflight (preflight.py)
- 预检系统，验证移植前置条件
- 检查设备兼容性
- 生成预检报告

#### Snapshots (snapshots.py)
- 工作目录快照管理
- 支持保存和恢复快照
- 用于断点续传和回滚

### 2. 工作流引擎 (src/core/workflow)

工作流引擎是系统的核心，负责编排整个移植过程。

#### Orchestrator (orchestrator.py)
```python
class WorkflowOrchestrator:
    """工作流编排器，协调各个阶段的执行"""
    
    def run(self, ctx: PortingContext):
        """执行完整工作流"""
        self.preflight(ctx)
        self.extract(ctx)
        self.initialize(ctx)
        self.modify(ctx)
        self.pack(ctx)
        self.finalize(ctx)
```

#### Phases (phases.py)
定义工作流的各个阶段：

| 阶段 | 说明 | 主要任务 |
|------|------|----------|
| Preflight | 预检 | 验证前置条件 |
| Extract | 解包 | 提取 ROM 分区 |
| Initialize | 初始化 | 创建设备配置 |
| Modify | 修改 | 应用补丁和修改 |
| Pack | 打包 | 重新打包镜像 |
| Finalize | 完成 | 生成最终产物 |

#### Pipeline (pipeline.py)
- 阶段流水线管理
- 支持阶段间数据传递
- 错误处理和恢复

### 3. 上下文管理 (src/core/context)

上下文模块提供统一的数据传递机制。

#### DeviceContext (device.py)
```python
@dataclass
class DeviceContext:
    """设备相关上下文"""
    device_code: str
    kernel_version: str
    is_gki: bool
    partition_info: Dict[str, Any]
    config: Dict[str, Any]
```

#### PackContext (pack.py)
```python
@dataclass
class PackContext:
    """打包相关上下文"""
    pack_type: str  # 'payload' or 'super'
    fs_type: str    # 'erofs' or 'ext4'
    output_dir: Path
    work_dir: Path
```

#### AVBContext (avb.py)
```python
@dataclass
class AVBContext:
    """AVB 验证相关上下文"""
    custom_avb_chain: bool
    avb_partitions: List[str]
    physical_partition_sizes: Dict[str, int]
```

### 4. 事件系统 (src/core/events)

事件系统实现组件间的松耦合通信。

#### EventBus (bus.py)
```python
class EventBus:
    """事件总线，管理事件订阅和发布"""
    
    def subscribe(self, event_type: str, handler: Callable):
        """订阅事件"""
        
    def publish(self, event_type: str, data: Any = None):
        """发布事件"""
```

#### Events (events.py)
定义系统事件类型：

```python
# 工作流事件
EVENT_WORKFLOW_START = "workflow.start"
EVENT_WORKFLOW_COMPLETE = "workflow.complete"
EVENT_WORKFLOW_ERROR = "workflow.error"

# 阶段事件
EVENT_PHASE_START = "phase.start"
EVENT_PHASE_COMPLETE = "phase.complete"

# ROM 事件
EVENT_ROM_EXTRACTED = "rom.extracted"
EVENT_ROM_MODIFIED = "rom.modified"
EVENT_ROM_PACKED = "rom.packed"
```

### 5. 修改系统 (src/core/modifiers)

修改系统采用插件架构，支持灵活扩展。

#### PluginSystem (plugin_system.py)
```python
class PluginManager:
    """插件管理器"""
    
    def register(self, plugin_class: Type[ModifierPlugin]):
        """注册插件"""
        
    def execute(self, ctx: PortingContext):
        """执行所有插件"""
```

#### 内置插件
- **FileReplacementPlugin**: 文件替换
- **WildBoostPlugin**: 狂暴引擎
- **FeatureUnlockPlugin**: 功能解锁
- **VNDKFixPlugin**: VNDK 修复
- **EULocalizationPlugin**: EU 本地化

#### FrameworkModifier (framework/)
框架级修改器，处理 Smali 补丁：

```python
class FrameworkModifier:
    """框架修改器"""
    
    def apply_patches(self, ctx: PortingContext):
        """应用 Smali 补丁"""
```

### 6. 打包系统 (src/core/packing)

打包系统负责将修改后的分区重新打包。

#### OTA 打包 (ota.py)
```python
class OTAPacker:
    """OTA 包打包器"""
    
    def pack(self, ctx: PackContext) -> Path:
        """生成 OTA 包"""
```

#### Super 打包 (super.py)
```python
class SuperPacker:
    """Super 镜像打包器"""
    
    def pack(self, ctx: PackContext) -> Path:
        """生成 super.img"""
```

#### AVB 处理 (avb.py)
```python
class AVBHandler:
    """AVB 验证处理"""
    
    def disable_avb(self, vbmeta_path: Path):
        """禁用 AVB"""
        
    def rebuild_chain(self, ctx: AVBContext):
        """重建 AVB 链"""
```

### 7. 性能优化 (src/core/performance)

#### Cache (cache.py)
```python
class CacheManager:
    """缓存管理器"""
    
    def get(self, key: str) -> Optional[Any]:
        """获取缓存"""
        
    def set(self, key: str, value: Any, ttl: int = None):
        """设置缓存"""
```

#### Hasher (hasher.py)
```python
class FileHasher:
    """文件哈希计算器"""
    
    def hash_file(self, path: Path) -> str:
        """计算文件哈希"""
        
    def hash_directory(self, path: Path) -> str:
        """计算目录哈希"""
```

#### Incremental (incremental.py)
```python
class IncrementalProcessor:
    """增量处理器"""
    
    def get_changed_files(self, since: datetime) -> List[Path]:
        """获取变更文件"""
```

## 数据流

### 典型移植流程

```
用户输入 (ZIP 文件)
       │
       ▼
┌─────────────┐
│   CLI 解析   │
└──────┬──────┘
       │
       ▼
┌─────────────┐
│   Bootstrap │
└──────┬──────┘
       │
       ▼
┌─────────────┐
│  Preflight  │ ──→ 预检报告
└──────┬──────┘
       │
       ▼
┌─────────────┐
│   Extract   │ ──→ 解包分区
└──────┬──────┘
       │
       ▼
┌─────────────┐
│ Initialize  │ ──→ 设备配置
└──────┬──────┘
       │
       ▼
┌─────────────┐
│   Modify    │ ──→ 应用补丁
└──────┬──────┘
       │
       ▼
┌─────────────┐
│    Pack     │ ──→ 打包镜像
└──────┬──────┘
       │
       ▼
┌─────────────┐
│  Finalize   │ ──→ 最终产物
└─────────────┘
```

## 扩展机制

### 1. 插件扩展

```python
from src.core.modifiers import ModifierPlugin, ModifierRegistry

@ModifierRegistry.register
class MyPlugin(ModifierPlugin):
    name = "my_plugin"
    priority = 50
    
    def modify(self) -> bool:
        # 自定义修改逻辑
        return True
```

### 2. 事件监听

```python
from src.core.events import EventBus

def on_phase_complete(event):
    print(f"Phase {event.phase} completed")

EventBus.subscribe("phase.complete", on_phase_complete)
```

### 3. 自定义阶段

```python
from src.core.workflow import Phase

class CustomPhase(Phase):
    name = "custom"
    
    def execute(self, ctx):
        # 自定义阶段逻辑
        pass
```

## 配置系统

### 配置层级

```
CLI 参数 (最高优先级)
       │
       ▼
devices/<code>/config.json
       │
       ▼
devices/common/config.json
       │
       ▼
默认配置 (最低优先级)
```

### 配置文件

| 文件 | 说明 |
|------|------|
| config.json | 设备基础配置 |
| features.json | 功能开关 |
| replacements.json | 文件替换规则 |
| partition_info.json | 分区信息 |

## 错误处理

### 错误类型

```python
class PortingError(Exception):
    """移植过程错误"""
    pass

class ConfigError(PortingError):
    """配置错误"""
    pass

class ExtractionError(PortingError):
    """解包错误"""
    pass

class ModificationError(PortingError):
    """修改错误"""
    pass

class PackingError(PortingError):
    """打包错误"""
    pass
```

### 错误恢复

- 快照机制支持回滚到上一个正常状态
- 检查点机制支持断点续传
- 详细的错误日志和报告

## 性能优化策略

### 1. 缓存策略
- 分区级缓存：避免重复解包
- APK 缓存：避免重复反编译
- 配置缓存：避免重复解析

### 2. 增量处理
- 只处理变更的文件
- 哈希校验避免重复计算

### 3. 并行执行
- 独立任务并行处理
- 异步 I/O 操作

## 未来规划

- [ ] 支持更多设备平台
- [ ] 图形用户界面
- [ ] 分布式处理
- [ ] 云端配置同步
- [ ] 自动化测试框架

---

> 返回 [README](../README.md) | 查看 [迁移指南](migration.md) | 查看 [插件开发指南](plugins.md)
