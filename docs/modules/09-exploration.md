# Exploration 模块职责卡

> **English Summary:** Exploration uses AstrBot's official LLM capability to read clipped, attributed, untrusted news/search/Bilibili results under control and emit sourced observations. It does not decide schedules, memory admission, proactive delivery, or freeze model/prompt/budget policy before a later ADR.

## 目标

在日程或显式命令触发时获取新闻、AstrBot 搜索和 B站公开结果；先裁剪、去重、附加来源并按不可信输入隔离，再通过 AstrBot 正式 LLM 能力进行受控阅读、摘要和 AI 观察，最终仍以带来源的 `exploration.*` observation 供下游使用。

## 非目标

- 不绕过站点权限、登录限制、robots、速率限制或付费墙。
- 不调用 `astrbot_plugin_bilibili_ai_bot` 的私有方法或读取其私有存储。
- 不下载、转载或长期保存完整新闻、视频、字幕和受版权保护内容。
- 不决定观察如何改变日程、是否成为长期记忆、发送给谁或如何措辞。
- 不负责主动发送、日程决策或 memory 准入；使用 LLM 阅读内容不授予这些权限。
- 不实现 AI 与其他 LLM 的自主持续聊天。

## 所有权

- 探索任务、来源游标、去重指纹、短期结果缓存和来源健康状态。
- 规范化观察的出处元数据：来源类型、标题/摘要、公开 URL、发布时间、获取时间与可信度提示。
- 不拥有原文、完整字幕、用户会话历史或长期 AI 记忆。
- 模型路由、阅读提示、LLM 预算/成本与具体摘要 payload 字段不在本卡锁定，由后续 ADR 和契约评审定义。

## 消费/发布的事件族

具体事件名、字段和版本以 `docs/03-event-contracts.md` 为唯一真源。

- 消费：`kernel.*` 生命周期、`planner.*` 探索触发，以及 admin 校验后以目标领域 family 发布的 `exploration.*` 显式探索/来源控制 Command；不消费 `admin.*` 作为业务命令。
- 发布：`exploration.*` 任务开始/完成、观察已发现、观察已去重、来源不可用/恢复和能力缺失事件。
- 典型示例：`exploration.discovery_requested`、`exploration.observation_discovered`、`exploration.source_unavailable`。
- 输出内容一律按外部不可信数据处理，不得让网页或字幕中的指令改变系统行为。
- LLM 输出只能形成带来源的 `exploration.*` observation 或显式失败，不能直接形成日程、memory 事实或主动发送命令。

## 允许修改范围

- 未来实现目录：`living_world/exploration/`。
- 对应测试：`tests/exploration/`。
- 本模块职责卡，以及经 P0 批准的外部能力 ADR/契约提案。
- 不得修改 B站目标插件、AstrBot 搜索实现或其他生活模块内部代码。

## 模拟依赖

- 新闻源、AstrBot 搜索能力与 B站公开能力的成功/缺失/限流/异常适配器。
- 固定时间、重复结果、恶意提示注入和超长内容 fixture。
- AstrBot 正式 LLM 能力的成功、拒绝、超时、不可用、超预算和恶意输出模拟器。
- 事件总线、能力发现端口与网络/LLM 预算钩子；具体路由、提示、预算和成本策略留待 ADR。

## 交付物

- 三类来源的只读适配器、裁剪/来源/不可信隔离管线、AstrBot 正式 LLM 阅读适配器及统一 observation 模型。
- 来源归属、内容裁剪、去重、超时、并发和降级策略。
- 缺少 B站插件时仍可启动的能力探测与诊断。
- 版权边界、提示注入、URL 安全和来源故障测试。

## 联调对象

- `planner`：提供有预算、有频率限制的探索触发。
- AstrBot 正式 LLM 能力：只接收已裁剪、带来源、标记为不可信的输入，只返回受控摘要/AI 观察或失败。
- `state` / `memory`：消费规范化观察，不读取来源私有缓存。
- `proactive`：消费观察事件生成候选，但不得要求 exploration 直接发送。
- `admin`：启停来源、触发一次探索并查看健康投影。

## 验收门槛

- 三类来源均能通过同一观察契约输出，且每条观察可追溯到公开来源。
- 同一内容的多次抓取不会重复形成观察；去重键不依赖保存完整正文。
- B站插件未安装、接口不兼容或单一来源故障时，插件整体继续工作并发布明确的降级事件。
- 恶意网页指令只能作为引用内容，不能触发工具、改写配置或改变事件路由。
- 调用顺序测试证明 LLM 只接收已完成裁剪、来源标注和不可信隔离的内容；其输出始终包装为带来源 observation。
- LLM 阅读不会直接发布 planner/memory/proactive 业务命令；模型不可用时发布可诊断降级，不伪造观察。
- fixture 和日志不含受版权保护内容的大段复制，且不调用目标插件私有 API。
