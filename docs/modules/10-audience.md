# Audience 模块职责卡

> **English Summary:** The Audience module owns minimal operational routing data—opaque target references, consent, quiet-time preferences, weights, ranking, and selection explanations. This data is restricted to Audience, Proactive, and redacted Admin views; it never enters Memory or cross-target content.

## 目标

经 `conversation` 的最小会话目录 Query，从 AstrBot 已存在的私聊和群聊会话中接收仅含 opaque `session_ref`、会话类型和可达性的 `operational` 目录并维护候选对象池；先执行授权、免打扰和可达性过滤，再用基础权重与本次事件评分排序，返回前 N 个目标及可审计理由。Audience 全程不读取历史正文。

## 非目标

- 不复制聊天记录，不建立自己的会话隔离或身份合并系统。
- 不读取或解释消息正文，不组装 LLM 上下文。
- 不直接枚举 AstrBot 会话、不调用历史读取端口，也不在选择阶段预取或批量读取正文。
- 不生成内容、不调用 LLM、不执行 OneBot 投递。
- 不用模型绕过用户退出、群禁用、免打扰或管理员策略。
- 不跨平台扩张首发范围；1.0 只保证 OneBot QQ / `aiocqhttp`。

## 所有权

- 可持久化的 `operational` 数据仅包括：最小 opaque `target_ref`、启用/退出状态、免打扰窗口、基础权重、最小路由可达信息、评分理由与短期选择记录；需受保留期、访问控制和脱敏导出约束。
- 上述 `operational` 数据只允许 `audience`、`proactive` 与脱敏的 `admin` 投影使用，禁止写入 `memory`、拼入 AI 生活事实或进入任何目标的生成内容。
- 会话正文、会话摘要和临时 `ContextBundle` 才属于 `session_scoped`；Audience 不读取、不接收、不保存这些数据。
- 不拥有用户画像推断、生成内容或发送结果。

## 消费/发布的事件族

具体事件名、字段和版本以 `docs/03-event-contracts.md` 为唯一真源。

- 消费：`audience.*` 偏好变更、`proactive.*` 选择请求、只含 opaque `session_ref`/会话类型/可达性的 `conversation.*` operational 会话目录响应、`kernel.*` 时间/生命周期。
- 发布：`audience.*` 选择完成/失败、目标已过滤、偏好已变更，以及不请求正文的 `conversation.*` 最小会话目录 Query。
- 典型示例：`audience.selection_requested`、`audience.selection_completed`、`audience.target_suppressed`。
- 选择响应只包含目标引用、得分、理由和关联 ID，不包含任何消息内容。
- 选择响应属于 `operational` 路由数据，只能交给当前 proactive 编排与脱敏 admin 投影；不得发布给 `memory`，也不得复制到其他目标的内容链。

## 允许修改范围

- 未来实现目录：`living_world/audience/`。
- 对应测试：`tests/audience/`。
- 本模块职责卡，以及经 P0 批准的同意/排序策略 ADR。
- 不得直接修改 AstrBot 会话、`conversation`、`proactive` 或 OneBot 适配器。

## 模拟依赖

- 由 conversation 返回且只含 opaque `session_ref`、会话类型和可达状态的目录 fixture；若出现正文/摘要字段测试立即失败。
- 虚拟时钟、固定候选事件、确定性评分器和总线。
- 私聊、群聊、退出、禁用、免打扰、不可达和并列得分场景。

## 交付物

- 目标偏好存储、资格过滤器、确定性排名器和前 N 选择服务。
- 每次选择的理由码、策略版本和不含内容的审计数据。
- 管理命令、并列排序、时区/跨午夜免打扰和退出优先级测试。
- 可供 `proactive` 独立开发的请求/响应模拟器。

## 联调对象

- `conversation`：仅查询最小 operational 会话目录；不直接访问 AstrBot，也不请求聊天内容。
- `proactive`：接收候选事件特征并返回排序后的 opaque 目标引用；只有 proactive 在选择完成后才能逐目标请求 session-scoped 上下文。
- `admin`：通过事件管理启用、退出、免打扰、权重与 top-N 上限。
- `kernel`：使用统一时钟解释目标所在时区和策略生效时间。

## 验收门槛

- 资格过滤先于评分，退出/禁用/不可达目标在任何分值下都不会被选中。
- 相同输入、策略版本和时间产生相同排序；并列规则明确且可测试。
- 每个入选或过滤结果都有不泄漏消息内容的理由码。
- 持久层与总线测试证明 opaque `target_ref`、权重、免打扰和路由信息仅进入 audience/proactive/admin 的 operational 路径，memory 与目标内容中均为零命中。
- 并发偏好更新与选择请求不会使用半更新状态；旧事件可按策略版本追溯。
- 测试和架构守卫证明模块既不读取消息正文，也不调用发送接口。
- 目录契约只允许 opaque `session_ref`、会话类型、可达性；Audience 的完整建池/排序测试中 AstrBot 历史读取次数严格为零。
