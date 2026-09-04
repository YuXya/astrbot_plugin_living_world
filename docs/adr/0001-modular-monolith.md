# ADR-0001：采用单插件模块化单体

> **English Summary:** Living World ships as one AstrBot plugin while enforcing twelve internal module boundaries. This keeps deployment simple without giving up independent ownership and testing.

- 状态：Accepted
- 决策日期：项目初始化
- 决策人：P0

## 背景

Living World 需要日程、世界、状态、记忆、环境、探索、主动行为和管理页面协作。如果拆成多个独立插件或服务，会增加安装、版本匹配、跨进程通信和发布成本；如果写成无边界的大模块，又无法让九个工位并行开发，也难以保护数据与隐私边界。

## 决策

1. 以 `astrbot_plugin_living_world` 单个 AstrBot 插件部署和发布。
2. 内部分为 `contracts`、`kernel`、`world`、`planner`、`state`、`memory`、`conversation`、`environment`、`exploration`、`audience`、`proactive`、`admin` 十二个模块。
3. `main.py` 仅作为 AstrBot 适配与组合根，不承载业务逻辑。
4. 每个模块拥有明确职责、写入数据和测试边界；模块之间遵循 ADR-0002 的消息总线规则。
5. 1.0 不把模块拆为独立进程或独立仓库。

## 后果

### 正面

- 用户只需安装和升级一个插件。
- 同一进程内调试、热重载与故障定位更简单。
- 模块仍可使用模拟总线独立测试和并行开发。
- 将来若确有扩展需要，版本化事件契约提供拆分基础。

### 代价

- 单进程故障可能影响多个工作流，需要内核隔离处理器与任务。
- Python 包边界本身不能阻止越界调用，必须配合导入守卫与评审。
- 发布节奏由整仓集成质量决定，公共契约必须谨慎演进。

## 验证

- 架构测试拒绝业务模块直接导入其他业务模块实现。
- 每个模块可仅凭契约、模拟总线和测试替身运行核心测试。
- 插件加载、热重载和卸载不会遗留跨模块后台任务。
