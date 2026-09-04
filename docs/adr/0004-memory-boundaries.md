# ADR-0004：只全局共享 AI 生活记忆

> **English Summary:** The plugin globally stores only the AI's own life, settings, and public observations. AstrBot remains the sole owner of isolated user conversation history, which is read temporarily per target and never copied into a shared memory store.

- 状态：Accepted
- 决策日期：项目初始化
- 决策人：P0

## 背景

同一个 AI 需要记住自己的生活，才能在多个会话中表现连续；但用户期望群聊和私聊按 AstrBot 原有机制正常隔离。将用户消息、摘要或嵌入放进全局记忆会造成难以察觉的跨会话泄漏。

## 决策

1. `memory` 只持久化 AI 身份相关生活事实、日程结果、状态变化、AI 自己的经历以及有来源的公开观察。
2. AstrBot 是用户原始消息和会话历史的唯一所有者；本插件不复制正文、不建立第二套消息数据库，也不建立可复原对话的共享嵌入索引。
3. `conversation` 仅在为某一目标生成内容时读取该目标的 AstrBot 原生会话上下文；消息内容、摘要、推断和临时 `ContextBundle` 均标记为 `session_scoped`。
4. `session_scoped` 内容不得进入全局生活账本、其他会话上下文或跨目标审计，也不得经摘要、改写或转发提升为 `ai_life`。
5. 持久化所必需的 opaque target reference、免打扰/退出/权重、路由结果和脱敏投递审计属于受限 `operational` 数据，仅供 `audience`、`proactive`、`admin` 最小使用，受访问、导出和删除控制；不得进入生活记忆或跨目标内容。
6. 主动行为完成后，`proactive` 可以发布自身 family 的隐私断开 `ai_life Event`（如 `proactive.life_action_completed`，`producer=proactive`）；`memory` 订阅并通过隐私准入后记账，不接受 `memory.*` 事实请求。它只含动作类型、结果、时间和源生活事件，不含目标/会话 ID、文本或可反查标识，也不沿用 `session_scoped` 链路的 correlation/causation/trace。这是 AI 行为事实，不是会话内容提升。
7. 每个 `proactive` 候选最多形成一条目标无关的聚合 AI 行为事实。首次创建的稳定幂等身份不得由目标、会话或逐目标 delivery key 推导，也不得与受限投递审计建立可反查映射；重试/重放复用同一 `message_id`、幂等身份和首次新建的隐私断开 correlation/trace，`memory` 不重复入账。事实不含目标数量、逐目标结果明细或逐目标键；具体字段与算法后续另立 ADR。
8. 正常群聊和私聊隔离完全采用 AstrBot 机制，不额外实现消息隔离层。
9. 1.0 不提供用户对话向全局记忆提升的授权流程；未来若需要，必须重新进行产品、隐私与 ADR 评审。

## 后果

### 正面

- AI 的生活能够全局连续，同时用户对话保持会话隔离。
- 插件减少敏感数据副本、删除负担和泄漏面。
- AstrBot 历史策略变化不会要求迁移一套重复消息库。

### 代价

- AI 不能在不同会话间“记住”某个用户私下说过的话。
- 每次个性化需要即时读取目标会话，不能依赖插件全局摘要缓存。
- 生活记忆写入必须区分 AI 经历与用户提供的信息，边界测试不可省略。

## 验证

- 用会话 A 的唯一标记进行端到端测试，确保它不出现在会话 B、全局生活账本或运营审计正文中。
- 检查插件存储，不存在原始用户消息、附件、会话摘要或可复原对话的索引。
- 每个主动目标分别请求、分别组装和分别销毁临时上下文。
- 检查生活回写只含动作类型、结果、时间和源生活事件，并使用与会话链路断开的新根 correlation/trace；目标引用只存在于可访问、导出和删除的受限运营审计。
- 对同一候选重复完成、重试和重放，确认始终复用首次创建的 message/幂等/correlation/trace 且账本只有一条；身份不能导向投递审计，事实中不存在目标数量、逐目标明细或逐目标键。
