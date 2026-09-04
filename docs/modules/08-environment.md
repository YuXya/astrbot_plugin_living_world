# Environment 模块职责卡

> **English Summary:** The Environment module is the sole owner of location profiles, weather-source access, normalized weather snapshots, freshness, and source health. QWeather is the first provider, while downstream modules consume provider-neutral events only.

## 目标

从和风天气获取 AI 所在现实地点的天气，将供应商字段转换成稳定的环境观察事件，并为生活模拟提供带地点、观测时间、有效期和来源的现实环境信号。

## 非目标

- 不安排日程、不推导 AI 当前活动，也不决定是否主动发消息。
- 不把和风天气专有字段泄漏为跨模块契约。
- 不抓取用户位置；位置只能来自明确的管理员配置。
- 不由管理页直接写天气缓存或位置表。
- 不承诺首期支持第二天气源；只保留可替换端口。

## 所有权

- AI 的位置配置及其变更版本。
- 当前与近期规范化天气快照、缓存有效期和最后成功时间。
- 天气供应商健康状态、限流状态和最近错误的脱敏摘要。
- API 凭据只通过 AstrBot 安全配置注入；事件、日志、导出和管理投影不得包含明文凭据。

## 消费/发布的事件族

信封、事件命名、来源语义与 `schema_version` 以 `docs/03-event-contracts.md` 为唯一真源；具体规范化天气 payload 由后续天气 ADR 与契约评审定义。

- 消费：`kernel.*` 时钟/生命周期，以及 admin 校验后以目标领域 family 发布的 `environment.*` 刷新、位置变更等 Command；不消费 `admin.*` 作为业务命令。
- 发布：`environment.*` 天气观测、缓存命中、位置已变更、刷新完成、来源不可用和恢复事件。
- 典型示例：`environment.weather_refresh_requested`、`environment.weather_observed`、`environment.source_unavailable`。
- 其他模块只能消费规范化事件；不得调用和风天气客户端。

## 允许修改范围

- 未来实现目录：`living_world/environment/`。
- 对应测试：`tests/environment/`。
- 本模块职责卡，以及经 P0 审核的天气字段 ADR/契约提案。
- 不得修改 `planner`、`state`、`proactive` 或 `admin` 的实现与存储。

## 模拟依赖

- 和风天气成功、超时、限流、无数据和异常响应 fixture。
- 虚拟时钟、事件总线、受控缓存和凭据提供器。
- 可配置的经纬度、城市标识与时区样例。

## 交付物

- 和风天气端口、供应商适配器和供应商无关的规范化模型。
- 位置配置、缓存、刷新、退避和来源健康投影。
- 时间新鲜度、单位、地点切换、限流和离线降级测试。
- 面向世界模拟与管理模块的模拟天气事件生成器。

## 联调对象

- `kernel`：周期刷新、关停和任务监管。
- `planner` / `state`：消费天气观察以影响计划和实际状态。
- `admin`：通过命令修改位置、触发刷新并读取脱敏健康投影。
- `proactive`：只消费已规范化的环境事件，不反向控制天气模块。

## 验收门槛

- 固定供应商响应能稳定生成包含来源、地点、观测时间、获取时间和有效期的规范化事件。
- 缓存未过期时不重复请求；过期、限流和断网时按策略发布可识别的陈旧或不可用状态。
- 地点切换会使旧地点缓存失效，且不会混用不同地点的观测。
- 任意事件、日志、错误和导出均不出现 API 密钥。
- 消费方测试不需要导入和风天气 SDK 或供应商响应类型。
