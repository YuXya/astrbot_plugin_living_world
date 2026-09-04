# Living World engineering rules

## English Summary

This repository is a contract-first modular monolith. Cross-module implementation imports are forbidden. P0 owns public contracts, root files, integration, and release decisions. Read the lead plan, architecture, event-contract, ownership, and relevant module/work-package documents before changing code.

## 必读文档

开始任何实现前，必须阅读：

1. `docs/01-lead-plan.md`
2. `docs/02-architecture.md`
3. `docs/03-event-contracts.md`
4. `docs/05-team-and-ownership.md`
5. 对应的 `docs/modules/*.md` 与 `docs/work-packages/*.md`

## 强制边界

- 业务模块不得导入其他业务模块的实现，只能依赖公共契约并通过消息总线协作。
- 每类状态只有一个写入所有者；禁止读取或修改其他模块的数据表和私有文件。
- `main.py` 保持为薄适配与装配入口，不承载领域业务。
- 原始聊天记录、群聊/私聊隔离和会话持久化归 AstrBot；本插件不得复制消息总账。
- 全局共享的只有 AI 自己的生活、观察和事件，不得把会话内用户信息自动提升为跨会话记忆。
- 外部天气、新闻、搜索、B站和消息发送必须通过可替换适配器；测试不得依赖真实外部副作用。
- 所有公共契约变更必须先更新 ADR、契约文档和契约测试，并由 P0 合并。
- 代码注释、docstring 和日志使用英文；用户文档以中文正文配 English Summary。

## 所有权与修改规则

- 每个工位只修改任务卡列出的目录、测试和文档。
- 根文件、公共契约、元数据和跨模块装配由 P0 串行修改。
- 公共配置新增项由模块负责人提出，P0 统一合入，避免多人同时编辑共享文件。
- 不复制参考插件的代码、提示词、文档、资源或私有接口。

## 提交与质量

- 使用 Conventional Commits。
- 每次提交保持单一职责，先通过所属模块测试和公共契约测试。
- 禁止提交密钥、Cookie、完整私聊、原始提示词、运行数据库、缓存或构建产物。
- 真实主动发送只允许在明确的测试白名单中进行。
