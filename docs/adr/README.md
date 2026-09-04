# Living World 架构决策记录

> **English Summary:** These accepted Architecture Decision Records freeze the foundational choices for the 1.0 line. Later changes must supersede, not silently rewrite, an accepted decision.

## 使用规则

- ADR 一经接受，不通过直接改写结论来掩盖历史；需要改变时新增 ADR，并在新旧文件中标记“已被替代 / 替代”。
- 状态取值：`Proposed`、`Accepted`、`Deprecated`、`Superseded`。
- P0 负责接受架构决策，P8 负责检查实现与决策一致。
- 每个公共契约变更在修改契约定义或实现前，都必须先补充现有契约 ADR 的变更记录，或新增/替代 ADR 并由 P0 接受；随后先补契约测试，最后才更新 [事件契约](../03-event-contracts.md) 与实现。

## 已接受决策

| ADR | 决策 | 状态 |
|---|---|---|
| [0001](0001-modular-monolith.md) | 单插件模块化单体 | Accepted |
| [0002](0002-event-driven-boundaries.md) | 所有跨模块交互经过版本化消息总线 | Accepted |
| [0003](0003-plugin-owned-identity.md) | 人格与身份由插件管理，相关会话使用空白 AstrBot 人格 | Accepted |
| [0004](0004-memory-boundaries.md) | 只全局共享 AI 生活，AstrBot 保持会话历史隔离 | Accepted |
| [0005](0005-onebot-first.md) | 1.0 优先支持 OneBot QQ / `aiocqhttp` | Accepted |
| [0006](0006-mit-clean-room.md) | MIT 发布并执行 clean-room 参考边界 | Accepted |
