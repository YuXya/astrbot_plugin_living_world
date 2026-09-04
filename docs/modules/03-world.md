# 模块卡：`world`

## English Summary

`world` owns the plugin-managed AI identity and narrative world setting: stable facts, fictional or narrative places, rules, and constraints. Real weather lookup locations belong to `environment`, while the AI's current narrative location state belongs to `state`.

## 目标

提供 AI 在哪个叙事世界、是谁、有哪些稳定规则与叙事地点的唯一事实源，为日程、状态、记忆和表达提供一致的设定基线。人格由插件管理，但只描述 AI，不创建跨会话用户人格。

## 非目标

- 不生成每日/每周日程，不决定 AI 当前正在做什么。
- 不拥有现实天气查询位置，包括经纬度、和风天气城市 ID 与现实位置—时区关联；这些数据唯一属于 `environment`。
- 不保存 AI 当前活动中的叙事位置状态；该状态唯一属于 `state`。
- 不存储 AstrBot 消息、用户画像、关系档案或会话摘要。
- 不把世界设定直接写入每个会话历史。
- 不读取天气、新闻、搜索或 B 站，也不发送主动消息。

## 所有权

P2 世界模拟工程师拥有世界模型、校验和迁移；P0 会签人格边界与公共契约；P7 只能通过管理命令编辑。

## 消费/发布的事件族

- 消费：由 `admin` 作为 `producer` 发布的 `world.*` Command，以及必要的 `kernel.*` 恢复通知；不消费 `admin.*` 业务命令。
- 发布：`world.*` 中的身份、设定和叙事地点目录创建/变更事实、失败与只读查询响应，例如 `world.setting_changed`。
- `admin.*` 只承载管理生命周期和投影更新，不承载要求 `world` 执行业务写入的命令。
- 不消费：任何含原始会话内容或用户消息的 `conversation.*` 事件。

## 允许修改范围

- 允许：AI 身份、世界规则、叙事地点目录、设定版本、校验、导入迁移和只读投影。
- 需要 ADR：多世界、多人格、动态改写核心身份或外部内容自动改变世界规则。
- 禁止：日程算法、当前叙事位置状态、现实天气查询位置、聊天历史、对象评分和发送逻辑。

## 模拟依赖

使用内存总线、固定世界配置、虚拟存储和管理命令夹具。无需真实 AstrBot 会话、LLM 或外部服务。

## 交付物

- 版本化 AI 身份与世界设定模型；
- 地点、规则和约束的校验与只读查询；
- 导入、迁移、回滚及变更事件；
- 可供日程与上下文使用的最小投影；
- 非法设定、旧版本和并发编辑测试。

## 联调对象

`planner` 使用设定和约束生成计划，`state` 使用叙事地点与规则验证实际状态，`environment` 独立拥有现实天气查询位置且不从世界地点目录推断经纬度/城市 ID，`conversation` 获取只读身份上下文，`admin` 管理配置，`memory` 记录世界变更这一 AI 生活事实。

## 验收门槛

- 同一配置可确定性生成同一规范化世界投影。
- 无效地点引用、循环规则、未知版本和并发覆盖被明确拒绝。
- 世界变更只经管理命令写入，并发布可重放的版本化事件。
- 投影只包含 AI 与世界信息，不含用户或会话数据。
- `world` 不直接导入 `planner`、`state`、`memory`、`conversation` 或 `admin` 实现。
