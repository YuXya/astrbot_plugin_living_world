# P0 主程、架构与集成任务卡

> **English Summary:** P0 owns architecture decisions, public contracts, root integration, merge serialization, release scope, and final acceptance. P0 does not absorb domain implementation; it keeps module boundaries enforceable and integrations releasable.

## 目标

- 维护产品边界、模块化单体架构、公共消息语义、ADR 和风险登记。
- 维护 AstrBot 插件入口与根文件，确保入口只做适配和装配。
- 串行评审公共契约/根文件变更，安排联调顺序，裁决跨工位冲突。
- 管理版本、变更日志、GitHub 发布和 AstrBot 市场前最终签署。

## 非目标

- 不接管 `kernel` 至 `admin` 的领域实现或成为其数据所有者。
- 不把业务规则、后台任务、数据库访问或供应商客户端塞入 `main.py`。
- 不代替 P8 生成独立质量证据，也不在门禁失败时强行发布。

## 所有权

- 根文件：`main.py`、`metadata.yaml`、`README.md`、`CHANGELOG.md`、`LICENSE`、`AGENTS.md` 及 Git 基础配置。
- 公共契约语义与实现：`living_world/contracts/`；P0 审核并串行合并由 P8 唯一拥有的契约测试。
- 架构决策与主程文档：`docs/adr/`、`docs/00-*.md` 至 `docs/08-*.md` 中的跨模块内容，以及本任务卡。
- 其他工位可提交提案，但无权直接合并上述范围。

## 消费/发布的事件族

- 消费：P0 不是运行时业务模块，不消费生产事件；AstrBot 入口只接收宿主生命周期回调并调用 P1 提供的 kernel 生命周期端口。
- 发布：P0 不发布任何运行时事件，尤其不发布 `kernel.*` 生命周期事实；这些事实唯一由 P1/kernel 发布。
- 契约治理覆盖 `kernel.*` 至 `admin.*` 的 schema、兼容规则和变更提案，但 P0 不拥有或代发任何领域事实。

## 允许修改范围

- 可修改上述根文件、`living_world/contracts/`、主程文档、ADR 与本任务卡。
- 可评审并串行合并 `tests/contracts/` 的变更，但该目录的内容所有权和测试判定权唯一属于 P8。
- 修改其他模块文件必须由对应负责人提交，P0 只做集成合并与冲突裁决。

## 模拟依赖

- AstrBot 加载、热重载、卸载和 OneBot 事件入口 fixture。
- 各事件族最小合法/非法 envelope、旧版本 envelope 与相关 ID 示例。
- 十二模块的空装配清单、健康响应器和失败/超时模拟器。

## 交付物

- 可加载根骨架、公共契约包、family 注册表、ADR 与兼容/弃用政策。
- 每波集成清单、冲突裁决记录、候选构建与版本/发布资料。
- 供 P1–P8 使用的契约示例、集成入口和最终发布签署记录。

## 阶段任务

- **Wave 0：** 校验 `0.0.1` 骨架；冻结 envelope v1、事件族、相关 ID、错误和兼容规则；批准内核端口与架构守卫。
- **Wave 1 门禁：** 串行评审 P2–P5 交接包，拒绝跨模块导入、供应商类型泄漏或用户聊天数据提升。
- **Wave 2：** 负责总装、AstrBot 适配、完整链路配置和管理/主动联调；维护唯一集成分支。
- **Wave 3：稳定化：** 主持缺陷分流、选择轮值模块负责人、冻结范围、签署 RC 与正式版。

## 禁止越界

- 不长期接管领域代码，不以“方便集成”为由直接读取模块私有表。
- 不在 `main.py` 放业务规则、后台循环、数据库逻辑或供应商客户端。
- 不跳过 ADR 与契约测试做破坏性 wire 变更。
- 不在 P8 未给出证据时自行宣布质量门禁通过。

## 联调对象

- 向 P1 交付冻结 envelope、路由语义、生命周期与时钟端口。
- 向 P2–P7 交付 family 注册表、模拟契约、目录所有权和 ADR 模板。
- 从各工位接收统一交接包；向 P8 交付集成构建、风险清单和候选发布说明。
- 对契约争议给出书面 ADR；口头约定不作为实现依据。

## 验收门槛

- 根入口可加载、热重载、卸载，且不包含领域实现。
- 所有公开事件均有所有者、版本、兼容测试和请求/响应超时语义。
- 集成构建无跨模块实现导入或跨模块表访问。
- 每波有明确入口/退出证据；并发实现工位从不超过 4 个。
- GitHub/市场发布版本、metadata、tag、变更日志和文档一致，并由 P8 共同签署。
