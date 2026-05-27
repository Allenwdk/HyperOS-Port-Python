# 迁移指南：从 v1.x 升级到 v2.0

## 概述

HyperOS 移植工具 v2.0 引入了全新的模块化架构，提供了更好的可扩展性、性能和可维护性。本指南帮助您从 v1.x 平滑升级到 v2.0。

## 主要变更

### 1. 架构变更

| 方面 | v1.x | v2.0 |
|------|------|------|
| 项目结构 | 扁平结构 | 分层模块化结构 |
| 配置管理 | 单一配置文件 | 上下文对象 + 配置文件 |
| 修改系统 | 硬编码修改器 | 插件化架构 |
| 工作流 | 线性流程 | 事件驱动工作流 |
| 打包系统 | 单一打包器 | 模块化打包系统 |

### 2. 目录结构变更

**v1.x 结构:**
```
src/
├── core/
│   ├── context.py          # 单一上下文文件
│   ├── modifier.py         # 单一修改器
│   └── packer.py           # 单一打包器
└── ...
```

**v2.0 结构:**
```
src/
├── core/
│   ├── context/            # 上下文模块化
│   │   ├── device.py
│   │   ├── pack.py
│   │   ├── avb.py
│   │   └── workflow.py
│   ├── workflow/           # 工作流引擎
│   │   ├── orchestrator.py
│   │   ├── phases.py
│   │   └── pipeline.py
│   ├── modifiers/          # 插件化修改系统
│   │   ├── plugins/
│   │   └── plugin_system.py
│   └── packing/            # 模块化打包系统
│       ├── ota.py
│       ├── super.py
│       └── avb.py
└── ...
```

## 迁移步骤

### 步骤 1：备份现有配置

在升级前，备份您的设备配置：

```bash
# 备份设备配置
cp -r devices/ devices_backup/

# 备份自定义修改
cp -r src/core/modifier.py modifier_backup.py  # 如果有自定义修改
```

### 步骤 2：更新代码库

```bash
# 拉取最新代码
git fetch origin
git checkout v2.0.0

# 或者克隆新版本
git clone https://github.com/toraidl/HyperOS-Port-Python.git
cd HyperOS-Port-Python
git checkout v2.0.0
```

### 步骤 3：更新依赖

```bash
# 更新 Python 依赖
pip install -r requirements.txt

# 如果使用开发环境
pip install -r requirements-dev.txt -r requirements-test.txt
```

### 步骤 4：迁移设备配置

v2.0 保持了向后兼容的配置格式，您的 `config.json`、`features.json` 等文件无需修改。

**配置文件位置不变：**
```
devices/<device_code>/
├── config.json
├── features.json
├── replacements.json
└── partition_info.json
```

### 步骤 5：验证迁移

```bash
# 运行预检验证配置
sudo python3 main.py --stock <底包路径> --preflight-only

# 运行完整移植测试
sudo python3 main.py --stock <底包路径> --port <移植包路径>
```

## API 变更

### 1. 导入路径变更

**v1.x:**
```python
from src.core.context import PortingContext
from src.core.modifier import SystemModifier
from src.core.packer import ImagePacker
```

**v2.0:**
```python
from src.core.context import PortingContext  # 保持不变
from src.core.modifiers import SystemModifier  # 路径变更
from src.core.packing import ImagePacker  # 路径变更
```

### 2. 上下文使用变更

**v1.x:**
```python
# 直接访问属性
ctx.stock_path
ctx.port_path
ctx.device_code
```

**v2.0:**
```python
# 使用上下文对象
ctx.device.device_code
ctx.pack.pack_type
ctx.avb.custom_avb_chain

# 兼容模式（仍可使用旧方式）
ctx.stock_path  # 仍然可用
ctx.port_path   # 仍然可用
```

### 3. 修改器使用变更

**v1.x:**
```python
from src.core.modifier import SystemModifier

modifier = SystemModifier(ctx)
modifier.run()
```

**v2.0:**
```python
from src.core.modifiers import SystemModifier

# 方式 1：直接使用（兼容旧代码）
modifier = SystemModifier(ctx)
modifier.run()

# 方式 2：使用插件系统（推荐）
from src.core.modifiers import PluginManager

manager = PluginManager(ctx)
manager.execute()
```

### 4. 打包器使用变更

**v1.x:**
```python
from src.core.packer import ImagePacker

packer = ImagePacker(ctx)
packer.pack()
```

**v2.0:**
```python
from src.core.packing import OTAPacker, SuperPacker

# 根据打包类型选择
if ctx.pack.pack_type == 'payload':
    packer = OTAPacker()
else:
    packer = SuperPacker()

packer.pack(ctx)
```

## 自定义修改迁移

### 从硬编码修改到插件

如果您在 v1.x 中有自定义的修改逻辑，需要迁移到插件系统。

**v1.x 方式（已废弃）:**
```python
# 在 SystemModifier 中添加方法
class SystemModifier:
    def my_custom_mod(self):
        # 自定义逻辑
        pass
    
    def run(self):
        # ... 其他修改
        self.my_custom_mod()
```

**v2.0 方式（推荐）:**
```python
from src.core.modifiers import ModifierPlugin, ModifierRegistry

@ModifierRegistry.register
class MyCustomPlugin(ModifierPlugin):
    name = "my_custom"
    description = "我的自定义修改"
    priority = 50  # 执行优先级
    
    def check_prerequisites(self) -> bool:
        """检查前置条件"""
        return True
    
    def modify(self) -> bool:
        """执行修改"""
        target_dir = self.ctx.target_dir
        # 自定义逻辑
        return True
```

## 新增功能

### 1. 事件系统

v2.0 引入了事件系统，允许您监听和响应工作流事件：

```python
from src.core.events import EventBus

# 监听阶段完成事件
def on_phase_complete(event):
    print(f"阶段 {event.phase} 完成")

EventBus.subscribe("phase.complete", on_phase_complete)
```

### 2. 快照系统

支持工作目录快照，用于断点续传和回滚：

```bash
# 保存快照
sudo python3 main.py --stock <底包> --enable-snapshots

# 从快照恢复
sudo python3 main.py --stock <底包> --rollback-to-snapshot <快照名>
```

### 3. 性能优化

v2.0 包含多项性能优化：

- **分层缓存**: 避免重复解包和反编译
- **增量处理**: 只处理变更的文件
- **并行执行**: 独立任务并行处理

```bash
# 查看缓存统计
python main.py --show-cache-stats

# 禁用缓存（调试用）
sudo python3 main.py --stock <底包> --no-cache
```

## 常见问题

### Q1: 升级后配置文件需要修改吗？

**A:** 不需要。v2.0 保持了向后兼容的配置格式，您的 `config.json`、`features.json` 等文件可以直接使用。

### Q2: 自定义修改器还能用吗？

**A:** 可以，但建议迁移到插件系统。v2.0 提供了兼容模式，旧的修改器仍然可以工作，但新功能（如事件监听、优先级控制）需要使用插件系统。

### Q3: 如何回滚到 v1.x？

**A:** 如果需要回滚：

```bash
# 切换回 v1.x 分支
git checkout v1.x

# 恢复备份的配置
cp -r devices_backup/* devices/
```

### Q4: 性能有提升吗？

**A:** 是的。v2.0 在以下场景有明显性能提升：

- 重复移植同一 ROM：缓存机制减少 50-70% 时间
- 仅修改配置重打包：增量处理减少 70-80% 时间

### Q5: 如何报告问题？

**A:** 请在 GitHub Issues 中报告问题，并包含：

1. 版本信息（`python3 main.py --version`）
2. 完整的错误日志
3. 配置文件内容
4. 复现步骤

## 迁移检查清单

- [ ] 备份现有配置
- [ ] 更新代码库到 v2.0
- [ ] 更新 Python 依赖
- [ ] 运行预检验证配置
- [ ] 测试完整移植流程
- [ ] 迁移自定义修改器到插件（如有）
- [ ] 验证所有功能正常

## 获取帮助

- **文档**: 查看 [架构文档](architecture.md) 了解详细设计
- **插件开发**: 查看 [插件开发指南](plugins.md) 学习插件开发
- **GitHub Issues**: 报告问题或获取帮助
- **社区**: 加入社区讨论

---

> 返回 [README](../README.md) | 查看 [架构文档](architecture.md) | 查看 [插件开发指南](plugins.md)
