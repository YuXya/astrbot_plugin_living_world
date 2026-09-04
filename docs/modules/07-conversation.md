# Conversation 模块职责卡

> **English Summary:** Conversation exposes only a minimal operational session catalog to Audience. After Proactive selects a target, it reads exactly one AstrBot-isolated history and assembles an ephemeral session-scoped context by querying World, Planner, State, Memory, and optional Environment through the bus.

## 目标

通过 AstrBot 端口向 `audience` 提供只含 opaque `session_ref`、会话类型和可达性的最小 `operational` 会话目录，不读取正文。仅在 `proactive` 已选定目标后，按单个会话、单次请求读取一份原生历史，并通过总线分别查询 `world`、`planner` 当前/近期计划最小投影、`state`、`memory` 与本次确有必要的 `environment`，组装临时且只读的 `session_scoped ContextBundle`。整个过程必须保留关联链，并证明没有跨会话串用。

## 非目标

- 不复制、同步或长期保存 AstrBot 消息正文。
- 不实现第二套消息隔离、会话数据库或用户身份合并。
- 不把某个用户的聊天信息提升为全局生活记忆。
- 不决定人格、主动发送对象、提示词策略或消息投递。
- 不把动态注入的生活上下文写回 AstrBot 聊天历史。
- 不通过会话目录读取正文，不批量读取多个目标的历史，也不在 audience 选择完成前预取历史。

## 所有权

- 持久数据所有权：无消息正文持久数据。
- 临时所有权：仅限请求生命周期内且标记为 `session_scoped` 的 `ContextBundle`、来源标记、截断/脱敏诊断与相关 ID；请求结束即释放，不可恢复或持久化。
- 会话目录响应属于 `operational` 且仅含 opaque `session_ref`、会话类型和可达性；AstrBot 是其事实源，conversation 不保存目录副本，audience 只在最小范围内建池。
- AstrBot 始终是会话 ID、消息记录和会话隔离的唯一事实来源；`memory` 始终是 AI 共享生活账本的唯一写入者。

## 消费/发布的事件族

具体事件名、字段和版本以 `docs/03-event-contracts.md` 为唯一真源。本卡示例不构成第二份 wire contract。

- 消费：`conversation.*` 最小会话目录/单目标上下文 Query，`world.*` 世界投影响应/失败，`planner.*` 当前/近期计划最小投影响应/失败，`state.*` 当前状态响应/失败，`memory.*` 生活记忆响应/失败，必要的 `environment.*` 环境投影响应/失败，以及 `kernel.*` 生命周期信号。
- 发布：`conversation.*` 最小会话目录响应与上下文成功/失败响应；分别向 `world.*`、`planner.*`、`state.*`、`memory.*` 与必要的 `environment.*` 发布 Query，且这些 Query 的 `producer=conversation`。
- `conversation` 只发布目标 family 的 Query，绝不代替 `world`、`planner`、`state`、`memory` 或 `environment` 发布 Event、Response、Failure 等业务事实。
- 目录链路：`audience` 查询 → conversation 调用 AstrBot 目录端口 → 返回 opaque `session_ref`、类型、可达性；全程不调用历史正文端口。
- 上下文链路：proactive 已选定一个目标 → 单目标 `conversation.context_requested` → 五类独立总线 Query/Response（环境按需）→ 范围与预算校验 → `conversation.context_ready`。
- 每个 Query/Response 必须保留可追溯到本次上下文请求的关联链；一个目标对应一组独立查询，禁止批量查询后混合不同目标的投影。

## 允许修改范围

- 未来实现目录：`living_world/conversation/`。
- 对应测试：`tests/conversation/`。
- 本模块职责卡，以及经 P0 批准的相关 ADR/契约提案。
- 不得直接修改或导入 `world`、`state`、`memory`、`environment`、`audience`、`proactive` 或 AstrBot 的数据表与实现。

## 模拟依赖

- 仅返回 opaque `session_ref`、会话类型、可达性的 AstrBot 目录端口，以及断言“选定目标后一次只读一个会话”的历史读取端口。
- 可分别返回固定世界快照、当前/近期计划最小投影、当前状态、AI 生活摘要和必要环境投影的 `world.*`、`planner.*`、`state.*`、`memory.*`、`environment.*` Query 响应器。
- 每个投影的成功、超时、失败、版本不兼容、错误作用域和环境非必要场景 fixture。
- 可控的总线、虚拟时钟、取消信号和 LLM token 预算计算器。

## 交付物

- AstrBot 最小会话目录适配器、严格单目标历史读取适配器、五领域总线查询协调器与临时上下文组装器。
- 明确的截断、脱敏、来源标记和失败结果。
- `session_scoped` 校验、跨会话隔离、不落盘、无直连、取消与部分查询超时测试。
- 面向 `proactive` 的请求/响应契约示例与模拟器。

## 联调对象

- `world`：通过目标 family Query 获取 AI 身份与叙事世界只读投影。
- `planner`：通过目标 family Query 获取当前/近期计划的最小只读投影。
- `state`：通过目标 family Query 获取 AI 当前生活状态只读投影。
- `memory`：通过目标 family Query 获取 AI 共享生活上下文。
- `environment`：仅在本次请求需要现实环境时，通过目标 family Query 获取最小只读投影。
- `audience`：只返回 opaque `session_ref`、会话类型和可达性的 operational 目录响应，不读取历史正文。
- `proactive`：必须先完成目标选择，再逐目标请求临时上下文；每次请求只能触发一次单会话历史读取。
- `kernel`：生命周期、超时和任务取消。

## 验收门槛

- 两个私聊、两个群聊的并发测试中，任何输出均不含其他会话的消息内容。
- 每次组装均通过总线分别查询 `world`、`planner`、`state`、`memory` 和必要的 `environment`；架构守卫确认没有直接导入、调用或读取五个模块的私有实现/存储。
- 会话目录测试确认返回字段只有 opaque `session_ref`、会话类型和可达性，历史正文读取次数为零。
- 调用顺序测试确认 proactive 选择目标之前不会读取历史；选择后每个上下文请求恰好读取一个目标，批量正文读取接口不存在。
- 上下文请求完成、失败、取消后均不在插件数据库或日志中留下消息正文。
- `ContextBundle` 始终标记 `session_scoped`，动态生活上下文参与本次生成但不进入持久化、重启恢复或后续 AstrBot 原生历史读取结果。
- 缺失会话、历史不可读、任一必需投影超时、可选环境不可用和超预算均返回可诊断的失败或最小化降级结果，绝不跨会话或直连兜底。
- 架构守卫确认模块没有直接导入其他业务模块实现。
