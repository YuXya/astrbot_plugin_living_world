# ADR-0003：人格与身份由插件管理

> **English Summary:** Living World owns the AI identity and world prompt. Users select a blank AstrBot persona for participating sessions so identity layers do not conflict.

- 状态：Accepted
- 决策日期：项目初始化
- 决策人：P0

## 背景

产品要求不同会话面对同一个持续生活的 AI。若主要身份分散在 AstrBot 各会话的人格配置中，设定可能不一致；若再叠加插件动态生活上下文，还会出现自我介绍、语气或世界事实冲突。

## 决策

1. AI 身份、世界知识和生活设定由 `world` 模块拥有并全局一致。
2. 使用 Living World 的相关会话选择空白 AstrBot 人格；部署文档和管理诊断明确提示这一要求。
3. `conversation` 在每次生成时组合插件身份、当前生活、公开观察和当前目标会话上下文。
4. 插件注入的动态上下文不写回或修改 AstrBot 的历史记录。
5. 1.0 只支持一个插件身份，不支持多人格生活、按群分叉身份或自动合并非空 AstrBot 人格。

## 后果

### 正面

- AI 的身份和生活事实跨群聊与私聊保持一致。
- 世界设定、日程和生活记忆可由一个所有者管理和审计。
- 避免两套人格提示互相覆盖。

### 代价

- 用户需要正确选择空白 AstrBot 人格。
- 现有非空人格不能无风险直接迁移，未来如支持导入需单独设计。
- 管理页面必须提供身份配置和冲突诊断。

## 验证

- 不同会话使用同一世界身份快照，同时保留各自独立聊天上下文。
- 非空 AstrBot 人格场景至少产生清晰诊断提示，不进行静默拼接。
- 测试确认动态生活上下文不会成为 AstrBot 历史的额外系统消息副本。
