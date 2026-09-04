# Living World 事件契约：11 个运行时事件族

> **English Summary:** Eleven runtime event families use one versioned bus for every cross-module interaction, including queries and responses. The twelfth module, Contracts, only defines and validates schemas and never publishes or consumes a `contracts.*` runtime family.

## 1. 目的与适用范围

本文件规定跨模块消息的公共语义。它覆盖事件、命令、查询、响应和失败通知；模块内部函数调用不属于公共契约，但不得借此跨越模块边界。

本文件只冻结信封、十一种运行时事件族和兼容原则。十二个模块中的 `contracts` 只定义、验证这些契约，不在运行时消费或发布 `contracts.*`。各事件的完整 payload 字段、数据库结构、重试次数、超时和 LLM 预算要在对应模块设计阶段通过契约评审与 ADR 确定。

## 2. 消息类别

| 类别 | 含义 | 命名建议 | 是否期待响应 |
|---|---|---|---|
| Event | 已发生且不可撤销的事实 | 过去式，如 `state.activity_started` | 否 |
| Command | 请求唯一所有者执行动作 | 祈使语义，如 `planner.rebuild_schedule` | 可选结果事件 |
| Query | 请求唯一所有者返回只读信息 | 查询语义，如 `memory.find_life_entries` | 是 |
| Response | 对 Query 的成功响应 | 与查询配对，如 `memory.life_entries_found` | 否 |
| Failure | 命令、查询或适配动作失败 | 领域失败语义，如 `environment.weather_fetch_failed` | 否 |

消息名语法固定为 `<family>.<semantic_path>`：

- `family` 必须是十一种运行时事件族之一：`kernel`、`world`、`planner`、`state`、`memory`、`conversation`、`environment`、`exploration`、`audience`、`proactive`、`admin`。
- `semantic_path` 由一个或多个 `lower_snake_case` 段组成，多段之间使用点号分隔；不得使用连字符。
- `state.activity_started` 与 `proactive.candidate.created` 均有效；`contracts.version_rejected`、`state.activity-started`、`State.activity_started` 无效。
- 名称本身不带版本号，版本只由信封中的 `schema_version` 表示。

## 3. 统一消息信封

以下是语义示意，不承诺具体 Python 类型或序列化格式：

```json
{
  "message_id": "全局唯一标识",
  "message_type": "state.activity_started",
  "message_kind": "event",
  "schema_version": "1.0",
  "occurred_at": "带时区的事件发生时间",
  "emitted_at": "带时区的消息发布时间",
  "producer": "state",
  "correlation_id": "一次业务交互的关联标识",
  "causation_id": "直接导致本消息的上游 message_id，可为空",
  "trace_id": "端到端链路标识",
  "privacy": "ai_life | public_source | session_scoped | operational",
  "payload": {}
}
```

### 3.1 必填规则

- 所有消息必须有 `message_id`、`message_type`、`message_kind`、`schema_version`、`occurred_at`、`emitted_at`、`producer`、`correlation_id`、`trace_id`、`privacy` 和 `payload`。
- 只有没有直接上游消息的根消息可以省略 `causation_id`。
- 时间必须带明确时区；内部比较统一按绝对时间，展示时再转换。
- `producer` 必须是十一种运行时事件族之一；`contracts` 不是合法 producer，也不接受任意插件名作为业务生产者。
- `producer` 表示实际发布消息的运行时模块。对于事实 `Event`、领域 `Response` 和领域 `Failure`，producer 必须与消息名的 family 相同；对于 `Command` 和 `Query`，消息名的 family 表示目标领域，producer 可以是发起请求的另一个运行时模块。
- payload 不得复制完整信封字段，也不得藏入无法验证的任意对象。

### 3.2 隐私分类

| 分类 | 允许内容 | 传播限制 |
|---|---|---|
| `ai_life` | AI 身份、日程、状态、生活事件和生活记忆 | 可在插件全局流转 |
| `public_source` | 天气、新闻、搜索、B 站公开观察及来源 | 可全局流转，必须保留来源与采集时间 |
| `session_scoped` | 某一 AstrBot 会话的消息内容、摘要、由其推断的信息，以及本次生成使用的临时 `ContextBundle` | 仅可沿该目标的临时生成链路使用，不得持久化到插件共享存储或进入全局生活记忆 |
| `operational` | 持久化所必需的 opaque target reference、免打扰/退出/权重、路由结果、脱敏投递审计，以及健康、错误、耗时和幂等元数据 | 其中目标相关数据仅供 `audience`、`proactive`、`admin` 按最小必要原则使用；所有运营数据均受访问、导出和删除控制，不得进入生活记忆或携带跨目标内容 |

`session_scoped` 消息必须绑定不可混淆的会话作用域；用于绑定的 opaque handle 在脱离消息内容并确需持久化时属于受限 `operational` 数据。处理器不得把任何 `session_scoped` payload 转发、摘要、改写或提升为 `ai_life`；1.0 不提供此类提升流程。

## 4. 因果、幂等与投递语义

- 同一业务链路共享 `trace_id`；请求与响应共享 `correlation_id`。
- 衍生消息的 `causation_id` 指向直接上游消息，形成可审计因果链。
- 消费者以 `message_id` 或契约指定的业务幂等键识别重复消息。
- 总线和外部适配器不得假设“绝不会重复”；所有有副作用处理器必须可安全重复消费。
- 不依赖跨生产者的全局顺序。需要顺序的领域由其唯一所有者使用版本、时间和前置状态校验。
- 重试间隔与次数不在本文件锁定；重试不得生成新的业务身份来绕过防重。

主动投递至少关联：原生活事件、主动候选、目标作用域和投递幂等身份。目标引用、路由结果和脱敏投递审计只保留在受限 `operational` 数据中；失败重试复用同一投递身份，成功后再次收到相同请求不得重复发送。

主动行为完成后，`proactive` 可以另行发布自己 family 的隐私断开 `ai_life Event`，固定示例为 `proactive.life_action_completed`（`producer=proactive`）；`memory` 只订阅该事件并在隐私准入校验通过后记入生活账本。主动行为生活回写这一路径不得使用 `memory.*` 事实请求。一般规则同样是源模块发布自身 family 的 `Event`，`memory` 订阅并准入；`memory.* Command/Query` 仅用于由 `memory` 所有者负责的管理或查询用例，不用于其他模块注入生活事实。该消息必须是新的根事件：不设置 `causation_id`，使用不沿用 `session_scoped` 链路的新 `correlation_id` 与 `trace_id`；payload 只包含动作类型、结果、发生时间和源生活事件引用，不得包含目标/会话 ID、消息文本、摘要、推断、投递幂等键或任何可反查目标的标识。该事实由主动行为自身产生，不读取或变换会话内容，因此不是 `session_scoped` 内容提升；目标标识只保留在受限 `operational` 审计中。

每个 `proactive` 候选最多形成一条目标无关的聚合 AI 行为事实。首次创建该事实时，`proactive` 分配并持久化稳定幂等身份；该身份不得由目标、会话或逐目标 delivery key 推导，也不得与受限投递审计建立可反查映射。重试或重放必须复用首次创建的同一 `message_id`、幂等身份，以及当时新建的隐私断开 `correlation_id`/`trace_id`，使 `memory` 准入去重后只记一条。事实不得包含目标数量、逐目标结果明细或逐目标键；具体字段和身份生成/持久化算法由后续 ADR 决定。

## 5. 查询与响应

查询也必须经过消息总线：

1. 请求方发布 `Query`，生成新的 `message_id` 与 `correlation_id`。
2. 数据所有者验证请求和隐私作用域。
3. 数据所有者发布一个成功 `Response` 或一个 `Failure`。
4. 响应沿用查询的 `correlation_id`，以查询的 `message_id` 作为 `causation_id`。
5. 超时由请求方按内核策略处理，不得回退为直接读取所有者存储。

响应只返回完成用例所需的最小只读投影，不暴露仓储对象、数据库键或可变引用。

## 6. 十一个运行时事件族

十二个模块中，`contracts` 只定义并验证契约，因此没有 `contracts.*` 运行时消息。下表定义其余十一个运行时事件族的职责和第一批需覆盖的语义；示例名称不是完整字段承诺。

| 事件族 | 所有者 | 典型消息语义 |
|---|---|---|
| `kernel` | P1 | 模块就绪/停止、时钟触发、任务失败、恢复完成、能力状态 |
| `world` | P2 | 身份/世界设定已更新、世界快照查询与响应 |
| `planner` | P2 | 日程已生成/调整、计划项到期、计划查询与响应 |
| `state` | P2 | 活动开始/结束/中断、当前状态查询与响应 |
| `memory` | P3 | AI 生活条目已记录、生活记忆查询与响应；不接收用户聊天正文作为全局记忆 |
| `conversation` | P3 | 最小会话目录查询/响应为受限 `operational`；单目标历史或 `ContextBundle` 的请求/响应始终为 `session_scoped` |
| `environment` | P4 | 天气已更新/陈旧/获取失败、环境快照查询与响应 |
| `exploration` | P5 | 新闻/搜索/B站观察已发现、来源不可用、观察查询与响应 |
| `audience` | P6 | 目标资格变化、排序请求、前 N 目标响应、免打扰拒绝 |
| `proactive` | P6 | 候选已创建/抑制、内容已生成、投递成功/失败、审计查询响应 |
| `admin` | P7 | 管理操作开始/完成/失败、审计关联、只读管理投影更新；不承载其他领域的业务命令 |

模块只能发布自己拥有的事实。例：`environment` 发布天气观察，`state` 决定该天气是否改变 AI 的实际活动；`environment` 不能直接宣布 AI 已取消活动。

### 6.1 管理命令路由

1. `admin` 受理 Plugin Page 或管理命令，完成权限校验并建立操作关联。
2. 需要改变领域状态时，`admin` 发布目标领域 family 的 `Command`，例如 `world.identity.update_requested`，此时消息 family 为 `world`、`producer` 为 `admin`。
3. 目标领域消费命令后，以自己的 family 和 producer 发布事实或失败，例如 `world.identity.updated` 或 `world.identity.update_failed`。
4. `admin` 观察结果，更新只读投影，并可发布 `admin.operation.completed` 或 `admin.operation.failed` 来结束管理操作生命周期。
5. 其他领域不得直接消费 `admin.*` 作为业务命令；`admin.*` 只用于管理操作生命周期、审计关联和只读投影。

## 7. 来源与现实信息

`public_source` 的具体 payload 后续定义，但必须能表达：

- 来源类型和稳定来源标识。
- 采集时间，以及来源自身发布时间（若有）。
- 数据新鲜度或是否已陈旧。
- 可供审计的引用，但不得保存超出许可或业务需要的完整第三方内容。
- 适配失败、能力缺失或结果不确定状态。

世界设定属于 `ai_life`，现实天气与公开内容属于 `public_source`。合并上下文时必须保持两者的来源层级，不能让虚构设定覆盖实时事实标签。

## 8. 版本兼容

`schema_version` 使用 `主版本.次版本`：

- 次版本只允许向后兼容扩展，例如新增可选字段或增加消费者可忽略的枚举值。
- 删除字段、改变既有字段含义/类型、收紧必填条件或改变隐私语义必须提升主版本。
- 消费者必须忽略它明确允许的未知可选字段；不得静默接受未知主版本。
- 同一主版本的迁移期允许新旧生产者并存，适配位置由 `contracts` 与 `kernel` 明确管理。
- 契约弃用要写明替代项、迁移窗口和移除版本，并由 P0 批准。

每个公共契约变更均执行同一门禁，不能因“只改字段”而省略：

1. 在修改契约定义或实现前，先补充现有契约 ADR 的变更记录；若改变已接受结论，则新增或替代 ADR，并由 P0 接受。
2. 按已接受 ADR 先新增或修改契约测试，覆盖兼容行为、拟议行为和拒绝条件。
3. 最后更新示例、版本说明、契约定义与实现；契约测试通过后才允许总线联调。

模块边界、隐私边界或共享策略变化始终属于结论变化，必须新增或替代 ADR，不能通过改写既有记录处理。

## 9. 错误与可观测性

- `Failure` 描述领域失败类别、是否可重试和安全诊断信息，不携带密钥、完整提示词或聊天正文。
- 外部能力失败必须能区分：未配置、未授权、超时、限流、无结果、响应无效和数据陈旧；具体错误码由模块契约决定。
- 诊断日志使用 `trace_id`、`correlation_id` 和 `message_id` 关联，不以用户消息正文作为检索键。
- 处理器崩溃由 `kernel` 监管并隔离；是否重放由所有者策略决定，不能破坏幂等性。

## 10. 契约验收

每个公共消息至少有以下测试：

1. 有效样例通过信封与 payload 校验。
2. 只接受十一个运行时 family；`contracts.*`、未知 family、连字符、非小写或空 `semantic_path` 被拒绝，多段 `lower_snake_case` 路径可通过。
3. `producer` 只接受十一个运行时模块；事实/响应/失败的 producer 与 family 一致，跨模块命令/查询允许 producer 为发起模块。
4. 缺失必填字段、未知主版本和非法隐私提升被拒绝。
5. 请求与响应的 `correlation_id`、`causation_id` 正确配对。
6. 管理写操作使用目标领域 family 的 `Command`；领域返回自己 family 的事实/失败，其他领域不消费 `admin.*` 业务命令。
7. 重复消息不会重复写入生活事件或重复投递主动消息。
8. 乱序消息不会使领域状态回退为无效状态。
9. `session_scoped` 内容不会进入 `memory` 全局生活账本或其他目标上下文；opaque target reference 与偏好/路由/脱敏审计仅按 `operational` 权限访问、导出和删除。
10. 主动行为生活回写固定为 `proactive` family 的新根 `ai_life Event`（如 `proactive.life_action_completed`，`producer=proactive`）；`memory` 订阅并做隐私准入后记账，此回写路径不使用 `memory.*` 事实请求。一般源模块发布自身 family 的 Event，`memory.* Command/Query` 仅服务于 memory 所有者的管理或查询用例。事件仅含动作类型、结果、时间与源生活事件，不含任何目标、会话、内容或可反查标识，也不复用会话链路的 correlation/causation/trace。
11. 每个候选最多产生一条聚合生活回写；首次创建的稳定幂等身份不可由目标/会话/逐目标键推导，且不可与受限审计反查关联。重试复用同一 message/correlation/trace，`memory` 不重复入账；事件不含目标数量、逐目标明细或逐目标键。
12. 外部来源事件保留来源和时间，并能表达不可用与陈旧。
