# 模块职责卡索引

## English Summary

These twelve module cards define one owner and one reason to change per module. They are implementation boundaries, not empty packages for `0.0.1`. Cross-module work uses the versioned contracts in `03-event-contracts.md`; cards list event families and illustrative event names only.

## 使用规则

- `0.0.1` 只交付这些职责卡，不创建空业务包。
- 模块卡说明业务边界；[`../03-event-contracts.md`](../03-event-contracts.md) 是消息信封、正式类型名和版本规则的唯一真源。
- 卡片中的具体事件名是契约评审输入，不代表实现者可以绕过 ADR 自行新增 wire type。
- 业务模块不得直接导入另一业务模块实现、读取其表或修改其状态。
- 查询同样通过消息总线，以请求/响应事件和 `correlation_id` 完成。
- 每类数据只有一个写入所有者；订阅者维护的只读投影必须可以从事件重建。

## 十二个模块

| 工作流 | 模块卡 | 唯一职责 |
|---|---|---|
| 基础 | [`contracts`](01-contracts.md) | 公共消息契约 |
| 基础 | [`kernel`](02-kernel.md) | 总线、时钟、生命周期与基础设施 |
| 世界模拟 | [`world`](03-world.md) | 插件身份与世界设定 |
| 世界模拟 | [`planner`](04-planner.md) | AI 计划做什么 |
| 世界模拟 | [`state`](05-state.md) | AI 实际正在做什么 |
| 记忆与上下文 | [`memory`](06-memory.md) | AI 生活事件账本 |
| 记忆与上下文 | [`conversation`](07-conversation.md) | 当前 AstrBot 会话的临时上下文 |
| 感知 | [`environment`](08-environment.md) | 现实天气与环境观察 |
| 感知 | [`exploration`](09-exploration.md) | 新闻、搜索与 B 站观察 |
| 社交与行动 | [`audience`](10-audience.md) | 主动对象资格与排序 |
| 社交与行动 | [`proactive`](11-proactive.md) | 主动候选、生成、投递与审计 |
| 运营 | [`admin`](12-admin.md) | 配置、管理页、诊断与审计投影 |

## 固定卡片结构

每张模块卡必须包含：目标、非目标、所有权、消费/发布的事件族、允许修改范围、模拟依赖、交付物、联调对象和验收门槛。新增职责前先判断是否仍只有一个改变理由；若否，应提交架构 ADR，而不是把模块变成共享工具箱。
