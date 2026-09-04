# P4 环境工程师任务卡

> **English Summary:** P4 owns explicit AI location configuration, QWeather integration, normalized provider-neutral weather events, freshness, cache behavior, and source health. Credentials and provider-specific types must never cross the module boundary.

## 目标

- 以和风天气作为首个供应商，设计可替换的天气端口与响应适配器。
- 管理 AI 明确配置的位置、单位、时区关联、天气缓存和数据新鲜度。
- 发布供应商无关的现实环境事件，并在限流、断网或无数据时安全降级。
- 提供脱敏健康诊断、可控 fixture 和天气边界测试。

## 非目标

- 不获取或推断用户位置，只处理管理员明确配置的 AI 所在位置。
- 不安排日程、推进生活状态、生成文本或发送天气消息。
- 不支持未经 ADR 批准的第二供应商，也不把供应商类型变成公共契约。

## 所有权

- 未来实现：`living_world/environment/`。
- 对应测试与供应商 fixture：`tests/environment/`。
- Environment 职责卡、P4 任务卡及经 P0 批准的天气字段 ADR。

## 消费/发布的事件族

- 消费：`kernel.*` 时钟/生命周期，以及 admin 校验后以目标领域 family 发布的 `environment.*` 刷新、位置变更等 Command；不消费 `admin.*` 作为业务命令。
- 发布：`environment.*` 位置事实、天气观察、缓存/刷新结果、来源不可用与恢复。
- 任何输出均遵循 `docs/03-event-contracts.md` 的信封与来源语义；具体规范化天气 payload 由后续天气 ADR 与契约评审定义，不输出和风天气 SDK 类型或明文凭据。

## 允许修改范围

- 可修改 `living_world/environment/`、`tests/environment/`、Environment 模块卡和本任务卡。
- 天气字段/供应商 ADR 与公共契约变更只提交 P0/P8 评审。
- 不得修改 planner/state/proactive/admin 实现或读取其私有数据。

## 模拟依赖

- 和风天气成功、空响应、超时、限流、认证失败和 schema 异常 fixture。
- P1 虚拟时钟、总线、受控缓存、任务取消和凭据提供器。
- 多地点、时区、单位、陈旧缓存与断网恢复场景。

## 交付物

- 和风天气端口/适配器、供应商无关模型和显式 AI 位置配置。
- 缓存、刷新、退避、来源健康、陈旧/不可用降级和脱敏诊断。
- 面向 P2/P6/P7 的天气事件 fixture 与完整边界测试。

## 阶段任务

- **Wave 0 后准备：** 用 fixture 验证契约能表达来源、地点、观测/获取时间和有效期。
- **Wave 1：** 完成位置、和风天气适配、缓存、刷新、退避、降级和健康投影；向 P2 提供稳定模拟事件。
- **Wave 2 交接：** 为 P6/P7 提供天气触发与诊断场景，不参与主动策略或页面直写。
- **Wave 3：稳定化轮值：** 处理限流、时区、缓存污染、供应商 schema 变化和凭据泄漏问题。

## 禁止越界

- 不获取或推断用户位置；仅使用管理员明确配置的 AI 所在位置。
- 不安排日程、不推进活动状态、不生成或发送天气消息。
- 不让其他模块直接调用天气客户端或读取环境私有缓存。
- 不在事件、日志、错误、fixture、导出或管理投影中写入真实 API 密钥。

## 联调对象

- 从 P1 接收时钟、任务与存储端口，从 P0 接收规范化契约。
- 向 P2 交付成功、陈旧、不可用、恢复和地点切换 fixture。
- 向 P6 提供只读天气触发事件，向 P7 提供位置变更命令与脱敏健康事件。
- 向 P8 提供限流/超时注入、单位/时区矩阵及密钥扫描规则。

## 验收门槛

- 固定响应稳定映射到带来源、地点、时间与有效期的规范化天气观察。
- 新鲜缓存避免重复请求；过期、限流、断网、空响应与恢复均有确定事件。
- 切换地点后不复用旧地点数据，跨时区时间解释正确。
- 和风天气不可用不会阻止插件或世界模拟启动。
- 自动扫描确认凭据和供应商私有响应没有越过模块边界。
