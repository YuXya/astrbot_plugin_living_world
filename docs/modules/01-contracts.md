# 模块卡：`contracts`

## English Summary

`contracts` is the sole source of truth for versioned message envelopes and public cross-module payloads. It contains no business behavior, runtime bus, storage, or AstrBot integration.

## 目标

为全部模块提供稳定、可序列化、可验证、可演进的消息信封，以及事件、命令、请求和响应载荷定义，使模块能够只依赖契约和模拟事件并行开发。

## 非目标

- 不实现消息路由、调度、重试、持久化或任务生命周期。
- 不包含日程、记忆、天气、对象评分等业务决策。
- 不提供“万能字典”绕过类型检查，也不暴露任何模块内部模型。
- 不直接依赖 AstrBot、OneBot 或第三方插件实现。

## 所有权

P0 主程拥有契约语义、版本冻结和合并权；P8 拥有兼容性测试。事件生产者和消费者必须共同会签相关变更，但不能自行修改已冻结契约。

## 消费/发布的事件族

`contracts` 不在运行时消费或发布消息，也不存在 `contracts.*` 运行时事件族。它只定义并校验 11 个运行时事件族：`kernel.*`、`world.*`、`planner.*`、`state.*`、`memory.*`、`conversation.*`、`environment.*`、`exploration.*`、`audience.*`、`proactive.*` 与 `admin.*`，以及其中成对的命令、查询、成功响应和失败响应。

具体 wire type、信封字段和主/次版本规则以 [`../03-event-contracts.md`](../03-event-contracts.md) 为准。

## 允许修改范围

- P0 可修改契约定义、序列化/校验辅助和兼容 fixture；任何公开字段、枚举、错误码、事件名或版本规则均由 P0 评审并串行合并。
- `tests/contracts/` 仅允许 P8 修改；P0 负责评审并串行合并这些契约测试，其他工位只能提交变更建议或测试需求。
- 对应 ADR 按 P0 主程流程维护；兼容 fixture 与 `tests/contracts/` 测试代码的所有权不得混用。
- 禁止：在契约包中添加业务服务、数据库访问、适配器或副作用。

## 模拟依赖

无运行时依赖。测试只需要固定时钟值、固定 UUID/标识符生成器和合法/非法载荷样例；所有夹具必须是原创、无真实用户数据。

## 交付物

- 统一消息信封和公共基础类型；
- 11 个运行时事件族的类型注册表；`contracts` 仅提供定义与校验，不注册自身运行时事件族；
- 请求/响应、错误和分页等公共模式；
- 向后兼容、未知版本拒绝和序列化往返测试；
- 面向模块开发者的事件示例与变更记录。

## 联调对象

所有模块。P1 使用契约实现总线校验；P8 以其建立契约门禁；P0 确保 `main.py` 只在装配层引用公共接口。

## 验收门槛

- 合法消息可无损序列化往返，缺少必填字段或非法类型会得到稳定错误。
- 同一主版本的兼容规则有测试；未知主版本不会被静默解析。
- 每个消息类型只有一个注册定义，事件生产者与消费者夹具一致。
- 契约层不导入任何业务模块、AstrBot、OneBot 或外部服务 SDK。
- 破坏性变更具备 ADR、迁移说明、受影响消费者清单和契约测试。
