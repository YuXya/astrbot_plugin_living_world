# 我也在这个世界生活 / Living World

> **Pre-alpha / planning scaffold:** `0.0.1` 目前只有可加载骨架、`/living_world` 健康检查和主程规划文档；尚未实现日程、天气、记忆、探索或主动消息。

Living World 计划为 AstrBot 提供一套“AI 真的在生活”的模块化系统：AI 拥有自己的世界设定、现实锚定的日程与状态、可持续的生活记忆、新闻/搜索/B站观察能力，以及经过对象池筛选的个性化主动表达。

## 当前版本

- 插件 ID：`astrbot_plugin_living_world`
- 展示名：`我也在这个世界生活 / Living World`
- 许可证：MIT
- 首个验证平台：OneBot QQ（`aiocqhttp`）
- 最低规划基线：AstrBot `>=4.24,<5`
- 唯一命令：`/living_world`，用于确认骨架加载成功

当前版本不会创建数据库、后台任务、配置页面、聊天记录副本或主动消息。

## 已锁定边界

- 单仓库、单插件、模块化单体。
- 跨模块通信全部经过版本化消息总线。
- 插件拥有自己的人格与世界设定；使用者为相关 AstrBot 会话选择空白人格壳。
- 只有 AI 自己的生活、观察和事件进入全局共享记忆。
- 群聊和私聊的原始历史、隔离与持久化继续完全由 AstrBot 管理。
- 首发只声明 OneBot QQ 支持；其他平台验证后再加入。
- 和风天气是第一个天气适配器。
- B站首先只读联动 [`astrbot_plugin_bilibili_ai_bot`](https://github.com/chenluQwQ/astrbot_plugin_bilibili_ai_bot) 的公开能力。
- AI 自主与其他 LLM 持续聊天不属于 1.0。

## 文档入口

完整主程文档、架构边界、事件契约、九人工位和模块任务卡见 [`docs/README.md`](docs/README.md)。

## 开发状态

本仓库尚未进入业务实现阶段。`0.0.1` 的目标是让未来的开发者或 AI 工位在不互相踩代码的前提下，依据同一套模块职责、事件契约和质量门禁开始工作。

实现阶段将遵循 AstrBot 的[插件开发指南](https://docs.astrbot.app/dev/star/plugin-new.html)、[最小实例](https://docs.astrbot.app/dev/star/guides/simple.html)和[发布规范](https://docs.astrbot.app/dev/star/plugin-publish.html)。

## Clean-room 声明

项目会研究“我会永远陪着你”、天使之心、Angel Memory、LivingMemory 等公开项目的产品行为与架构思想，但不会复制其代码、提示词、文档、资源或私有数据结构。详细边界见 [`docs/08-references-and-license-boundaries.md`](docs/08-references-and-license-boundaries.md)。

## License

[MIT](LICENSE)
