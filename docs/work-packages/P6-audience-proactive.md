# P6 对象池与主动能力工程师任务卡

> **English Summary:** P6 separates minimal operational routing/audit data from ephemeral session-scoped content and emits only a privacy-disconnected, target-free AI-life action fact to Memory after delivery. It begins in Wave 2 after upstream contracts and mocks are stable.

## 目标

- 管理不含消息正文的私聊/群聊对象池、退出/启用、免打扰、基础权重与事件评分。
- 将生活/天气/探索事件转成有期限、有去重键的主动候选并选取前 N 个目标。
- 为每个目标分别请求 AstrBot 会话上下文、调用 LLM 个性化生成并执行最终发送检查。
- Audience 只通过 conversation 获取最小 operational 会话目录；Proactive 必须在选择完成后才逐目标请求 session-scoped 上下文。
- 通过 OneBot QQ / `aiocqhttp` 投递，管理幂等、有限重试、速率限制、dry-run 与审计。

## 非目标

- 不持久化聊天正文、不管理 AI 生活账本、不直接读取其他模块数据表。
- 不绕过退出、免打扰、总开关、速率预算、候选过期或 dry-run。
- 不实现其他平台或 AI 与其他 LLM 的自主持续聊天。
- 不直接枚举 AstrBot 会话、不在对象池阶段读取正文、不预取或批量读取多个目标历史。

## 所有权

- 未来实现：`living_world/audience/`、`living_world/proactive/`。
- 对应测试：`tests/audience/`、`tests/proactive/`；OneBot fixture 与 P8 共同评审。
- 两张模块职责卡、P6 任务卡及经 P0 批准的主动策略/投递 ADR。
- 数据分类所有权：opaque `target_ref`、免打扰、权重、最小路由和脱敏投递审计属于可受控持久化的 `operational`；会话正文/摘要、临时 `ContextBundle` 和逐目标生成内容属于不可持久化的 `session_scoped`。

## 消费/发布的事件族

- 消费：`planner.*`、`state.*`、`environment.*`、`exploration.*` 候选触发；`conversation.*` 上下文响应；`kernel.*` 生命周期；`audience.*`/`proactive.*` 管理请求。
- 发布：`audience.*` 选择/过滤/偏好事实；`proactive.*` 候选、生成、抑制、投递结果；Audience 发布不含正文的 `conversation.*` 最小目录 Query，Proactive 在选定目标后发布逐目标上下文 Query；投递处理后可在自身 family 另发隐私断开的 `proactive.life_action_completed` 最小 ai_life Event，供 memory 订阅。
- 同一候选对每个目标建立独立 correlation 链；重试沿用稳定幂等键。
- `proactive.life_action_completed` 只含动作类型、结果、时间和源生活事件，不含目标、会话 ID、文本、opaque `target_ref` 或可反查标识，并使用全新的 correlation/causation/trace 链；该路径不向 `memory.*` 发布写入 Command。

## 允许修改范围

- 可修改 `living_world/audience/`、`living_world/proactive/` 及其对应测试、模块卡和本任务卡。
- OneBot 共享端到端 fixture 由 P8 唯一纳入测试门禁，P6 仅共同评审领域预期。
- 主动策略/投递 ADR 与公共契约变更只提交 P0/P8 评审；不得修改 memory/conversation 私有实现。

## 模拟依赖

- P2/P4/P5 的候选触发、P3 的逐会话 context 和确定性 LLM 生成器。
- P3 conversation 返回的最小目录响应，以及可断言“选择前正文读取为零、选择后每次只读一个会话”的 context/历史端口 fixture。
- OneBot 成功、限流、超时、永久失败、重复回执和部分成功适配器。
- P1 虚拟时钟、重启点、总开关、免打扰、速率预算与取消 fixture。
- operational/session_scoped/ai_life 分类 canary，以及 session 链 ID 与新 ai_life 链 ID 必须完全不同的断言 fixture。

## 交付物

- 对象偏好、资格过滤、确定性排名和带理由的前 N 选择服务。
- 最小 operational 会话目录 Query/建池流程，以及选择完成后逐目标启动的 session-scoped 上下文流程。
- 主动候选状态机、逐目标上下文/生成、最终发送闸门与 OneBot 适配器。
- 幂等、去重、有限重试、dry-run、脱敏审计及 P7/P8 联调 fixture。
- 三类数据隔离规则、发送后最小 AI 行为事实构造器和隐私断链测试。

## 阶段任务

- **Wave 0/1：** 不并发实现；只接收 P2–P5 发布的契约与 mock，记录阻塞问题。
- **Wave 2：** 完成资格过滤、确定性排名、候选状态机、逐目标生成、最终闸门、OneBot dry-run/真实投递和审计。
- **Wave 2 联调：** 与 P7/P0/P8 跑通管理启用 → 候选 → 选人 → 上下文 → 生成 → 投递/抑制 → 投影。
- **Wave 3：稳定化轮值：** 解决重复发送、隐私串话、免打扰、重启恢复和平台错误映射问题。

## 禁止越界

- 不读取其他模块私有表，不持久化聊天正文，不复用不同目标上下文。
- 不绕过退出、免打扰、总开关、速率预算、候选过期或 dry-run。
- 不把 operational 或 session_scoped 发送数据写入 memory；只可在 proactive 自身 family 发布字段最小化且与 session 链彻底断开的 `proactive.life_action_completed`，由 memory 自主订阅/准入，这不构成会话内容提升。
- 不实现其他平台或 AI 与其他 LLM 持续聊天，除非新版本另有 ADR。

## 联调对象

- 从 P2/P4/P5 接收候选 fixture；从 P3 接收最小会话目录与严格逐目标 context mock；从 P1 接收任务/恢复端口。
- 向 P7 提供对象偏好命令、主动总开关、dry-run 和审计投影事件。
- 向 P8 提供 OneBot mock、重复回执、限流/失败、跨会话 canary 和重启点。
- 向 P0 提交主动策略版本、失败矩阵和真实发送启用前检查表。

## 验收门槛

- 退出、禁用、不可达和免打扰过滤先于排名，任何分值都不能绕过。
- N 个目标严格产生 N 份独立上下文与生成输入，无跨会话 canary 泄漏。
- Audience 建池与选择期间历史正文读取为零；Proactive 只在目标选择完成后发出上下文 Query，且每个 Query 恰好读取一个会话，不存在批量正文读取。
- 重复事件、重复回执和重启不会对同一候选/目标重复发送。
- opaque `target_ref`、免打扰、权重、路由与脱敏审计只出现在 audience/proactive/admin operational 路径，不进入 memory 或任何其他目标内容。
- 会话正文/摘要、临时 `ContextBundle` 与生成内容始终为 `session_scoped` 且不落盘；发送后 `ai_life` 事实只含动作类型、结果、时间、源生活事件并使用全新消息链。
- 每个主动候选最多产生一条目标无关的聚合 `proactive.life_action_completed`；目标数量、部分失败和目标级重试均不得扩大回写条数。
- 候选重试、总线重放和重启恢复复用稳定且非目标/会话派生的回写幂等身份；memory 对同一身份最多入账一次。
- 发送前最终闸门完整；dry-run 与关闭状态真实发送调用次数为零。
- 暂时/永久 OneBot 失败被正确区分、有限重试并可审计，不伪报成功。
