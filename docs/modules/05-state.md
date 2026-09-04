# 模块卡：`state`

## English Summary

`state` is the authoritative record of what the AI is actually doing, including its current narrative location state. It does not own real weather lookup coordinates, city IDs, or timezone mappings, which belong to `environment`.

## 目标

维护 AI 当前活动、活动中的叙事位置及后续 ADR 批准的生活状态维度；依据计划、时钟和环境证据执行合法转换，并发布“开始、继续、完成、跳过或中断”的实际事实。

## 非目标

- 不生成或改写计划，不定义核心世界设定。
- 不创建或修改 `world` 的叙事地点目录，也不保存 `environment` 唯一拥有的经纬度、城市 ID 或时区关联等现实天气查询位置。
- 不保存聊天历史、用户情绪或跨会话画像。
- 不把瞬时状态自动当成长久记忆；由 `memory` 依据准入规则消费事实。
- 不进行对象选择、文案生成或 OneBot 投递。

## 所有权

P2 世界模拟工程师拥有状态机、转换校验和恢复；P1 会签时钟/任务语义；P3 会签可进入生活账本的数据边界。

## 消费/发布的事件族

- 消费：`kernel.*` 中的时钟 tick，`planner.*` 计划活动，`world.*` 地点/规则变更，`environment.*` 环境变化，以及由 `admin` 作为 `producer` 发布的 `state.*` Command；不消费 `admin.*` 业务命令。
- 发布：`state.*` 中的活动开始/完成/跳过/中断、当前叙事位置状态变更、快照更新、失败、查询响应和非法转换诊断，例如 `state.activity_started`。
- `admin.*` 只承载管理生命周期和投影更新，不承载要求 `state` 执行业务写入的命令。
- `memory`、`conversation`、`proactive` 只能消费状态事件或查询投影，不能写状态表。

## 允许修改范围

- 允许：状态模型、转换表、活动执行协调、当前叙事位置状态、快照、恢复和状态诊断。
- 需要 ADR：具体状态数值、自然变化公式、冲突优先级、手动覆盖与过期语义。
- 禁止：叙事地点目录、现实天气查询位置配置、计划生成、记忆压缩、天气获取、聊天上下文和发送。

## 模拟依赖

使用虚拟时钟、计划事件夹具、世界/环境投影、内存状态存储和可注入的重启点。所有长时活动都必须能瞬时推进测试。

## 交付物

- 明确的状态与活动转换模型；
- 当前快照和带原因的转换事件；
- 重复 tick、漏 tick、跨日、计划取消、环境中断和重启恢复处理；
- 只读查询与诊断投影；
- 属性测试或状态转换矩阵测试。

## 联调对象

`planner` 提供意图并接收实际结果，`world` 提供合法叙事地点/规则，`environment` 以标准化事件提供现实天气影响并独立管理天气查询位置，`memory` 记录 AI 已发生事件，`conversation` 获取当前状态，`proactive` 使用状态判断可发送时机。

## 验收门槛

- 同一 tick 或计划事件重复到达不重复启动/完成活动。
- 任一时刻的当前活动、位置和状态满足已批准不变量；非法转换被拒绝并可诊断。
- 漏 tick 或重启恢复有确定补算规则，不伪造未经证实的细节。
- 计划取消、天气变化和管理覆盖均留下带原因的实际结果。
- 模块不读取其他模块数据表，也不把用户或会话内容写入状态。
