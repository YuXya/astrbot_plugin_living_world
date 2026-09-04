# ADR-0005：1.0 优先支持 OneBot QQ

> **English Summary:** Version 1.0 targets OneBot QQ through `aiocqhttp`, especially for proactive delivery. Other platforms may work incidentally but are not release commitments.

- 状态：Accepted
- 决策日期：项目初始化
- 决策人：P0

## 背景

主动消息在不同平台的目标标识、权限、限流、群/私聊语义和失败反馈差异很大。首版同时支持所有 AstrBot 平台会扩大测试矩阵，并弱化最关键的主动链路质量。

## 决策

1. 1.0 的正式适配和端到端验收目标为 OneBot QQ / `aiocqhttp`。
2. `/living_world` 健康检查必须在 OneBot 私聊和群聊可用。
3. `proactive` 的领域语义不直接依赖 OneBot 对象；OneBot 细节停留在适配层。
4. 对象池使用稳定的内部目标引用，适配层负责将其解析为 OneBot 目标。
5. 其他平台不是 1.0 发布承诺；不得仅因健康命令偶然可用而宣称完整支持。

## 后果

### 正面

- 可以集中验证私聊、群聊、主动发送、权限、去重和失败恢复。
- 领域模块仍保持平台中立，为后续适配保留空间。
- 发布文档能给出明确且可复现的支持矩阵。

### 代价

- 非 OneBot 用户需要等待后续版本。
- OneBot 特有的目标解析和错误需要适配层专门测试。
- 新平台接入必须补齐主动投递与隐私测试，而非只添加命令入口。

## 验证

- OneBot 群聊和私聊完成加载、命令、目标解析、个性化生成、免打扰、防重和投递审计测试。
- 模拟适配器证明核心模块不导入 `aiocqhttp` 实现类型。
- 文档和元数据准确描述 OneBot 优先状态，不做全平台承诺。
