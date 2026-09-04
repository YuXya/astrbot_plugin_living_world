# Proactive 模块职责卡

> **English Summary:** The Proactive module keeps minimal routing and redacted delivery audit as operational data, treats per-target context and message content as ephemeral session-scoped data, and may emit only a privacy-disconnected AI-life action fact to Memory after delivery.

## 目标

编排主动能力完整链路：从生活、天气或探索事件生成候选，向 `audience` 请求前 N 个目标，对每个目标分别向 `conversation` 请求独立上下文，调用 LLM 生成个性化内容，经最终策略检查后通过 OneBot 投递，并发布可审计结果与 AI 生活回写请求。

## 非目标

- 不维护聊天历史、目标权重、共享生活记忆或天气/新闻数据。
- 不把一个目标的上下文或生成文本复用于另一个目标。
- 不绕过退出、免打扰、速率限制、人工总开关或平台可达性检查。
- 不实现 AI 与其他 LLM 的自主持续聊天；该能力属于 1.x 后路线图。
- `0.0.1` 不创建后台任务、不调用 LLM、不发送任何真实消息。

## 所有权

- 主动候选、触发依据、生命周期、过期时间和策略版本。
- `operational` 数据：最小 opaque `target_ref`、路由/频控状态、生成任务状态、去重/幂等键、投递尝试与不含文本的脱敏审计；只允许 `audience`、`proactive` 和脱敏 `admin` 投影使用，禁止进入 `memory` 或任何其他目标的内容。
- `session_scoped` 数据：当前目标的会话正文/摘要、临时 `ContextBundle`、生成输入与个性化消息文本；只在当前目标的一次生成/投递期间存在，禁止持久化或进入脱敏投递审计。
- OneBot 投递适配器及平台错误映射。
- 不拥有目标偏好、会话正文或 AI 长期生活账本。

## 消费/发布的事件族

具体事件名、字段和版本以 `docs/03-event-contracts.md` 为唯一真源。

- 消费：`planner.*`、`state.*`、`environment.*`、`exploration.*` 的候选触发；`audience.*` 选择响应；`conversation.*` 上下文响应；`kernel.*` 生命周期；`proactive.*` 管理触发/取消。
- 发布：`proactive.*` 候选、生成、抑制与投递结果；`audience.*` 选择请求；`conversation.*` 逐目标上下文请求；发送处理结束后可在自身 family 另发隐私断开的最小 `proactive.life_action_completed` ai_life Event，由 memory 订阅并按准入规则记账。
- 典型链路：`proactive.candidate_created` → `audience.selection_requested` → `audience.selection_completed` → 每目标 `conversation.context_requested` → `proactive.delivery_succeeded` 或 `proactive.delivery_failed`。
- 重试必须沿用同一幂等键；每个目标使用不同的 context correlation 链。
- `proactive.life_action_completed` 只含动作类型、结果、发生时间和源生活事件；不得含目标、会话 ID、消息文本、opaque `target_ref` 或任何可反查标识。该路径不向 `memory.*` 发布写入 Command。
- 该 `ai_life` 事实必须开启新的隐私断开消息链，不复用任何 `session_scoped` 请求的 `correlation_id`、`causation_id` 或 `trace_id`；它记录 AI 自身行为，不构成会话内容提升。

## 允许修改范围

- 未来实现目录：`living_world/proactive/`。
- 对应测试：`tests/proactive/` 与经 P8 共同维护的 OneBot 端到端 fixture。
- 本模块职责卡，以及经 P0 批准的主动策略/投递 ADR。
- 不得直接访问 `audience`、`conversation`、`memory` 的表或实现。

## 模拟依赖

- 确定性的 audience 选择响应、逐会话 context 响应和 LLM 生成器。
- OneBot 成功、超时、限流、重复回执、永久失败和部分成功适配器。
- 虚拟时钟、总开关、免打扰边界、速率预算和重启恢复 fixture。

## 交付物

- 候选规则、编排状态机、逐目标生成、最终策略闸门与 OneBot 适配器。
- 幂等、去重、过期、取消、重试、速率限制和审计实现。
- operational/session_scoped/ai_life 三类数据隔离器，以及发送后隐私断开的最小行为事实构造器。
- dry-run/禁发模式，允许完整演练但永不调用真实发送接口。
- 面向 `admin` 的状态投影事件，以及供 `memory` 订阅的 `proactive.life_action_completed` 最小 ai_life Event。

## 联调对象

- `planner` / `state` / `environment` / `exploration`：接收可追溯触发。
- `audience`：资格过滤与前 N 排序。
- `conversation`：每个目标单独组装临时上下文。
- `memory`：订阅隐私断开的 `proactive.life_action_completed` 并自行按准入规则记账；不接收目标、会话 ID、文本、opaque `target_ref` 或 session 链标识。
- `admin` / `kernel` / P8：总开关、诊断、任务监管和 OneBot 端到端测试。

## 验收门槛

- 同一触发被重复投递、总线重放或进程重启后，不会对同一目标重复发送。
- N 个目标产生 N 条独立上下文请求；测试可证明目标 A 的内容不进入目标 B 的生成输入。
- operational 路由/审计数据仅供 audience/proactive/admin，memory 和跨目标生成内容中不出现 opaque `target_ref`、路由、免打扰或权重。
- 发送后的 `ai_life` 行为事实字段严格限于动作类型、结果、时间、源生活事件，并使用与 session correlation/causation/trace 完全不同的新链。
- 每个主动候选最多发布一条目标无关的聚合 `proactive.life_action_completed`；无论选中、重试或成功多少目标，都不得按目标回写多条。
- 候选重试、总线重放和进程恢复必须复用稳定且不由目标/会话派生的回写幂等身份；memory 对该身份最多入账一次。
- 发送前再次检查总开关、目标资格、免打扰、候选过期和速率预算。
- OneBot 暂时/永久失败有不同结果与有限重试；失败不会伪装成成功生活事件。
- dry-run、禁用或 `0.0.1` 骨架路径下真实发送调用次数严格为零。
