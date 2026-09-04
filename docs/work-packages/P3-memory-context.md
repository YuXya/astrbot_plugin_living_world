# P3 记忆与上下文工程师任务卡

> **English Summary:** P3 owns the AI's shared life ledger and the ephemeral bridge to AstrBot's already-isolated conversation history. It must preserve the hard boundary: AI life facts may be global, user message content may not be promoted across sessions or persisted by this plugin.

## 目标

- 将 AI 自己的活动、观察、计划结果和发送行为记录为全局共享生活账本。
- 提供生活事实检索、摘要、去重、溯源、保留与重建能力。
- 按单个目标读取 AstrBot 原生会话历史，组装一次性上下文并在使用后释放。
- 向 audience 暴露不读正文的最小 operational 会话目录；仅在 proactive 选定目标后，一次读取一个会话的历史。
- 在每次上下文组装中，经总线分别查询 `world`、`planner` 当前/近期计划最小投影、`state`、`memory` 与必要的 `environment`，保持 `session_scoped` 且无模块直连。
- 建立自动化隐私测试，证明用户聊天信息没有跨会话提升或插件侧持久副本。

## 非目标

- 不复制 AstrBot 消息历史，不建立第二套会话数据库、身份合并或用户画像。
- 不决定日程、主动目标、生成措辞或 OneBot 投递。
- 不把任何用户消息正文提升为全局生活事实或写入日志/导出。
- 不批量读取会话正文，不在目标选择前预取历史，也不把会话目录扩展成画像或消息摘要。

## 所有权

- 未来实现：`living_world/memory/`、`living_world/conversation/`。
- 对应测试：`tests/memory/`、`tests/conversation/`。
- 两张模块职责卡、P3 任务卡及经 P0 批准的记忆/隐私 ADR。

## 消费/发布的事件族

- Memory 职责消费：`world.*`、`planner.*`、`state.*`、`environment.*`、`exploration.*` 中被契约允许提升的 AI 生活 Event，以及 proactive 自身 family 的隐私断开 `proactive.life_action_completed` ai_life Event；`memory.*` 仅用于 memory 自身的管理 Command 与只读 Query/Response。
- Memory 订阅领域 Event 后自主执行准入、幂等与记账；领域生产者不向 `memory.*` 发布生活事实写入 Command。
- Conversation 职责消费：`conversation.*` 最小目录/单目标上下文 Query，`world.*`、`planner.*`、`state.*`、`memory.*` 与必要的 `environment.*` Query 响应/失败，以及 `kernel.*` 生命周期。
- Memory 职责发布：`memory.*` 已记录/去重/摘要/查询响应。
- Conversation 职责发布：`conversation.*` 最小 operational 会话目录响应与上下文成功/失败；分别向 `world.*`、`planner.*`、`state.*`、`memory.*` 与必要的 `environment.*` 发布 `producer=conversation` 的 Query。
- Conversation 不代替上述目标领域发布业务 Event、Response 或 Failure；所有查询均经总线并关联到当前上下文请求。
- 用户消息正文只从 AstrBot 端口进入一次请求的临时内存，不作为任何 `memory.*` 全局事实发布。

## 允许修改范围

- 可修改 `living_world/memory/`、`living_world/conversation/` 及其对应测试、模块卡和本任务卡。
- 记忆/隐私 ADR 与契约变更只提交提案，由 P0/P8 审核。
- 不得修改 AstrBot 会话存储、其他业务模块实现或其私有数据。

## 模拟依赖

- P2/P4/P5 发布的允许提升与禁止提升生活事实 fixture。
- P6 发布的合法/越界 `proactive.life_action_completed` fixture：合法事件仅含动作类型、结果、时间、源生活事件，并使用与 session 链无关的新 correlation/causation/trace。
- P2 提供的 `world.*` 世界投影、`planner.*` 当前/近期计划最小投影与 `state.*` 当前状态 Query 响应器，P3 memory 侧的生活记忆 Query 响应器，以及 P4 提供的必要/非必要 `environment.*` 投影响应器。
- 只返回 opaque `session_ref`、会话类型、可达性的 AstrBot 目录端口，以及按会话隔离并能断言调用顺序/单目标次数的历史读取端口、跨会话 canary 和失败模拟器。
- P1 总线、虚拟时钟、存储/恢复、超时/取消、响应乱序/版本不兼容和 token 预算 fixture。

## 交付物

- AI 共享生活账本、检索/摘要/去重/溯源与可重建投影。
- AstrBot 最小 operational 会话目录适配器、严格单目标历史读取适配器、五领域总线 Query 协调器、逐目标 `session_scoped` 临时上下文组装器与释放机制。
- 隐私白名单、落盘扫描、跨会话并发测试和供 P6 使用的 mock。
- 无直连架构守卫，以及必需投影失败、可选环境缺失和最小化降级测试资产。

## 阶段任务

- **Wave 0 后准备：** 锁定允许进入生活账本的事实白名单与跨会话隐私测试样例。
- **Wave 1：** 用模拟生活事件完成账本写入/查询/重放；用模拟 AstrBot 会话完成逐会话临时上下文组装。
- **Wave 2 交接：** 支持 P6 对每个目标独立请求，向 P7 提供不含正文的只读投影。
- **Wave 3：稳定化轮值：** 参与跨会话泄漏、数据迁移、保留/清除和重启恢复修复。

## 禁止越界

- 不创建消息镜像表、第二会话数据库、跨群身份图谱或隐式用户画像。
- 不把动态注入内容写回 AstrBot 历史，不让一个目标的上下文进入另一个目标请求。
- 不决定日程、主动对象、生成措辞或消息投递。
- 不在日志、fixture 或审计投影保存真实聊天正文。

## 联调对象

- 与 P2 联调 `world.*`/`planner.*`/`state.*` Query 响应及生活事实，与 P4 联调必要环境投影 Query 响应。
- 与 P3 memory 侧联调 `memory.*` Query/Response；P5 只提供可入账的探索生活事实，不加入 conversation 上下文聚合查询。
- 从 P1 接收总线、存储/恢复、超时/取消与关联链支持。
- 向 P6 提供逐目标 context 请求/响应 mock、超时与预算边界。
- 向 P6 的 audience 侧提供仅含 opaque `session_ref`、会话类型和可达性的目录；向 proactive 侧要求选择完成后逐目标请求 context。
- 向 P7 提供生活摘要与健康投影，不提供聊天内容。
- 向 P8 提供 canary 隐私数据、落盘扫描规则、跨会话并发和删除/恢复测试。

## 验收门槛

- 重复生活事件幂等，重放可重建相同账本投影，且每条事实可追溯来源。
- Memory 仅订阅并准入合规的 `proactive.life_action_completed`；含目标、会话 ID、文本、opaque `target_ref`、可反查标识或复用 session 链的事件必须拒绝记账。
- 多私聊/群聊并发中，canary 内容只出现在其原会话的一次性上下文。
- 每次上下文组装经总线分别请求 `world`、`planner`、`state`、`memory` 与必要的 `environment` 投影；Query 的 `producer=conversation`，且不代发目标领域业务事实。
- 会话目录只返回 opaque `session_ref`、类型、可达性且读取正文为零；proactive 选定目标前历史读取为零，之后每次请求恰好读取一个会话，不存在批量正文读取。
- 插件持久层、日志、导出和管理投影扫描不到 AstrBot 消息正文。
- `ContextBundle` 始终为 `session_scoped`，不持久化、不参与重启恢复；注入内容不回写 AstrBot 历史，超时/取消后临时对象可释放。
- 任一必需投影不可用时显式失败或最小化降级，可选环境不可用时可省略；禁止通过直接导入、私有调用、读表或跨会话搜索兜底。
- 架构守卫确认 conversation 与 world/planner/state/memory/environment 之间不存在实现直连。
