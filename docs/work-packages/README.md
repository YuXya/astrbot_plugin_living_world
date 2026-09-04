# P0–P8 工位与执行波次

> **English Summary:** Living World is planned for nine ownership roles, executed with no more than four concurrent workers. Wave 0 freezes contracts and infrastructure, Wave 1 builds life/perception modules against mocks, Wave 2 integrates proactive delivery and the admin UI, and Wave 3 stabilizes the release with one rotating module owner.

## 编制结论

完整 1.0 需要 **9 个明确工位**，不是要求同时雇佣 9 名全职人员。一个人可以顺序承担多个工位，但同一时刻不能混淆所有权、评审身份或验收责任；使用 AI 工位时最多并发 4 个。

| 工位 | 主责 | 任务卡 |
|---|---|---|
| P0 | 主程、公共契约、架构、集成与发布决策 | [P0](P0-lead-integration.md) |
| P1 | 总线、时钟、生命周期、任务与存储基础设施 | [P1](P1-kernel.md) |
| P2 | 世界、日程与实际生活状态 | [P2](P2-world-simulation.md) |
| P3 | AI 生活记忆与 AstrBot 原生会话上下文 | [P3](P3-memory-context.md) |
| P4 | 和风天气、位置与环境事件 | [P4](P4-environment.md) |
| P5 | 新闻、搜索和 B站只读探索 | [P5](P5-exploration.md) |
| P6 | 对象池、主动编排与 OneBot 投递 | [P6](P6-audience-proactive.md) |
| P7 | 管理命令、投影和完整 Plugin Page | [P7](P7-admin-ui.md) |
| P8 | QA、安全、隐私、许可证、CI 与发布验收 | [P8](P8-qa-release.md) |

## 通用所有权规则

- 未来业务包统一预留为 `living_world/<module>/`，测试为 `tests/<module>/`；首批 `0.0.1` 不创建这些空代码目录。
- 每个工位只修改任务卡列出的模块、对应测试与自己的任务卡。根文件、`main.py`、`metadata.yaml`、公共契约和 ADR 由 P0 串行合并。
- `tests/contracts/` 的内容所有权与测试判定权唯一属于 P8；P0 负责审核并串行合并契约测试，不在该目录声明所有权。
- 任何跨模块合作都经版本化消息总线；业务模块不得导入其他业务模块实现、读取其表或修改其状态。
- 事件卡只说明 family 和语义。envelope、字段、版本、兼容窗口以 `docs/03-event-contracts.md` 为唯一真源。
- 契约变更顺序固定为：提案 → ADR（含兼容性说明）→ P8 契约测试 → P0 审核与合并 → 消费者升级；禁止先改实现再补契约。

## 最多四并发的执行波次

| 波次 | 并发工位 | 入口条件 | 退出条件 |
|---|---|---|---|
| Wave 0：基础冻结 | P0、P1、P8（3） | `0.0.1` 骨架与计划获批 | envelope、事件族、总线端口、虚拟时钟、架构守卫和契约测试框架可供模拟开发 |
| Wave 1：生活与感知 | P2、P3、P4、P5（4） | Wave 0 契约冻结 | 世界/日程/状态/记忆/天气/探索均可只靠模拟事件完成模块测试，且无真实主动发送 |
| Wave 2：行动与运营 | P6、P7、P0、P8（4） | Wave 1 发布稳定事件与只读投影 | 对象池、逐目标生成、OneBot dry-run/投递、完整管理页和端到端验收链路完成 |
| Wave 3：稳定化 | P0、P1、P8 + 1 名轮值模块负责人（4） | 功能冻结 | 重启、故障、隐私、迁移、许可证、远端全新安装与发布清单全部通过 |

轮值负责人由当前失败用例所属模块的工位承担；问题关闭后释放名额，再轮换下一模块。Wave 之间的 P0 合并评审是串行门禁，不额外开启第五个实现工位。

## 统一交接包

每个工位交接时必须同时提供：

1. 已实现/未实现清单和明确非目标。
2. 消费/发布事件族、契约版本与兼容性结论。
3. 可复用 mock、fixture、虚拟时钟用例和失败注入方法。
4. 数据所有权、迁移/恢复方式与敏感信息检查结果。
5. 单元/契约/集成测试结果及仍存在的风险。
6. 给下一工位的最小运行说明；不得要求对方读取私有表或调用私有方法。

## 全局完成定义

- 模块仅通过事件交互，导入守卫与契约测试通过。
- 重复、乱序、超时、取消、重启和外部能力缺失均有确定结果。
- AstrBot 原始消息正文未被插件持久化，AI 生活记忆不提升跨会话用户信息。
- OneBot 主动发送具备总开关、免打扰、逐目标上下文、幂等、审计和 dry-run。
- 管理页不直接修改领域存储，不泄漏凭据/上下文，并能显示命令最终结果。
- P8 提交证据，P0 最终签署门禁；未签署不得提升版本或发布市场。
