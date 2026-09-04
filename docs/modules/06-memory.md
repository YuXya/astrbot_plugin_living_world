# 模块卡：`memory`

## English Summary

`memory` owns the global ledger of the AI's life events. It stores only AI-owned facts and compliant source summaries; it never stores raw user messages, conversation transcripts, or cross-conversation user profiles.

## 目标

把已发生的 AI 生活事实形成可追溯、可检索、可压缩、可删除和可恢复的共享账本，为日程反思、当前会话表达和主动候选提供 AI 自身的连续经历。

## 非目标

- 不复制 AstrBot 消息、附件、聊天历史或完整会话摘要。
- 不创建用户长期记忆、关系档案、身份合并或跨会话画像。
- 不组装当前会话提示词；该职责属于 `conversation`。
- 不决定日程、状态、对象排序、发送内容或管理页展示权限。

## 所有权

P3 记忆上下文工程师拥有生活账本、准入、检索、压缩、保留和删除语义；P0 会签边界变化；P8 对跨会话泄漏拥有阻断权。

完整隐私规则见 [`../04-memory-and-context-boundaries.md`](../04-memory-and-context-boundaries.md)。

## 消费/发布的事件族

- 消费：经准入的 `world.*`、`planner.*`、`state.*`、`environment.*`、`exploration.*` 与 `proactive.*` AI 生活事实，以及由 `admin` 作为 `producer` 发布的 `memory.*` 删除/导出/维护 Command；不消费 `admin.*` 业务命令。
- 发布：`memory.*` 中的条目追加/拒绝/归档/删除、摘要生成/失效、失败、查询响应和维护诊断，例如 `memory.entry_appended`。
- `admin.*` 只承载管理生命周期和投影更新，不承载要求 `memory` 执行业务写入的命令。
- 明确拒绝：任何携带原始消息正文、完整会话转录、用户画像或秘密凭据的写入请求。

## 允许修改范围

- 允许：AI 生活条目、来源元数据、准入过滤、索引、压缩、检索、保留、导出、删除和迁移。
- 需要 ADR：存储引擎、检索/排序算法、摘要模型、TTL、重要度与备份策略。
- 禁止：AstrBot 历史采集、用户事实抽取、会话合并、提示词拼装和主动投递。

## 模拟依赖

使用原创生活事件夹具、虚拟时钟、临时存储、确定性摘要/嵌入替身和来源可信度样例。隐私夹具使用合成秘密，禁止真实聊天数据。

## 交付物

- 版本化 AI 生活条目和严格准入验证；
- 幂等追加、查询、压缩、归档、导出和删除；
- 来源、时间、事实/生成标记和写入原因；
- 重启恢复、索引重建、迁移失败回滚和缓存失效；
- 跨会话泄漏、敏感信息、重复事件和恶意外部内容测试。

## 联调对象

`world`/`planner`/`state`/`environment`/`exploration`/`proactive` 提供 AI 侧事实，`conversation` 只读检索当前表达所需的 AI 生活片段，`admin` 通过命令执行导出删除，P8 验证隐私与恢复。

## 验收门槛

- 重放同一 `message_id` 不产生重复条目，查询结果保留可核验来源。
- 群或私聊原文、完整摘要、用户秘密、密钥和临时提示词无法通过准入。
- 删除会同步清理索引与缓存；导出不包含 AstrBot 消息副本。
- 压缩不会把不确定观察改写成确定事实，也不会丢失来源范围。
- 在群 A 输入的合成秘密无法从群 B、私聊或全局生活检索中得到。
- `memory` 不直接读取 AstrBot 会话，也不导入任何其他业务模块实现。
