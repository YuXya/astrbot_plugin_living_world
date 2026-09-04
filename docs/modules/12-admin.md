# Admin 模块职责卡

> **English Summary:** The Admin module is an event-driven control plane and read-model owner. Its future Plugin Page issues versioned domain commands, displays redacted diagnostics and audit projections, and performs import/export without directly mutating another module's storage.

## 目标

为 1.0 提供完整管理页面与命令面：查看健康、生活状态、来源状态和主动审计；通过版本化事件命令管理各模块；提供带 schema 版本、预检、脱敏和审计的配置导入导出。

## 非目标

- 不直接读写其他模块数据库、缓存或内存对象。
- 不成为所有领域配置的事实来源；领域模块仍验证并拥有自己的状态。
- 不自行实现身份认证或绕过 AstrBot 管理权限。
- 不在浏览器、事件、日志或导出文件中暴露 API 密钥、会话正文或生成上下文。
- `0.0.1` 不包含空管理页、占位前端资源或业务配置 schema。

## 所有权

- 从领域事件构建的只读管理投影、投影游标和重建状态。
- 管理命令状态、操作者审计记录、导入/导出作业与脱敏清单。
- 页面自身的非领域展示偏好；不拥有天气、日程、记忆、对象池或主动策略的最终状态。

## 消费/发布的事件族

具体事件名、字段和版本以 `docs/03-event-contracts.md` 为唯一真源。

- 消费：`kernel.*`、`world.*`、`planner.*`、`state.*`、`memory.*`、`conversation.*`、`environment.*`、`exploration.*`、`audience.*`、`proactive.*` 的可投影状态与结果，以及 `admin.*` 页面管理操作请求。
- 发布：`admin.*` 仅承载管理操作受理/拒绝/完成、投影重建、导入/导出和审计生命周期；通过权限与格式校验后，另行发布目标领域自身 family 的 Command。
- 典型示例：页面发布 `admin.operation_requested`，admin 受理后发布 `environment.location_change_requested`，最终再发布 `admin.operation_completed` 或 `admin.operation_rejected`。
- 领域模块不消费 `admin.*` 作为业务命令；页面也不得把“操作已受理”当成领域变更已成功。

## 允许修改范围

- 未来实现目录：`living_world/admin/`、经 ADR 确认的 Plugin Page 前端目录。
- 对应测试：`tests/admin/`。
- 本模块职责卡，以及经 P0 批准的管理 API、导入导出和权限 ADR。
- 不得修改其他领域模块的存储层、私有接口或 AstrBot 权限实现。

## 模拟依赖

- 各事件族的固定状态流、乱序/重复事件与投影重建 fixture。
- AstrBot 管理员、非管理员、会话过期和权限撤销身份 fixture。
- 导入文件 schema 版本、损坏数据、未知字段、敏感值和回滚场景。

## 交付物

- 后端命令端口、只读投影、审计查询与健康聚合。
- 1.0 Plugin Page：总览、世界/日程、天气/探索、对象池/主动、诊断、导入导出。
- dry-run、二次确认、命令进度/失败显示和敏感字段遮蔽。
- 管理 API 黑盒、权限、投影重建、导入预检与审计测试。

## 联调对象

- 全部领域模块：只通过事件命令和事实事件交互。
- `kernel`：健康、任务状态、投影重建与安全关停。
- `proactive`：总开关、dry-run、投递审计和失败诊断。
- P8：权限、敏感信息、浏览器黑盒、备份恢复与发布验收。

## 验收门槛

- 页面无法通过任何路径直接修改领域存储；每个变更均有命令、领域结果和操作者审计。
- 非管理员访问和过期会话被拒绝；前端不缓存或展示密钥、消息正文和完整上下文。
- 投影可从事件重新构建，重复/乱序事件不会产生不可解释状态。
- 导入先做版本与完整性预检，失败不产生部分写入；导出默认脱敏且带版本清单。
- 管理页黑盒测试覆盖成功、拒绝、超时、模块离线、dry-run 与恢复路径。
