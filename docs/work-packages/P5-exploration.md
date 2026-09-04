# P5 探索集成工程师任务卡

> **English Summary:** P5 uses AstrBot's official LLM capability for controlled reading of clipped, attributed, untrusted news/search/Bilibili results and emits sourced Exploration observations. It does not own schedule decisions, memory admission, or proactive sending.

## 目标

- 为新闻、AstrBot 搜索和 `astrbot_plugin_bilibili_ai_bot` 公开能力建立只读适配器。
- 管理探索任务、来源游标、调用预算、短期缓存、去重和来源健康。
- 输出带来源 URL/时间/可信度提示的统一观察，隔离外部内容中的恶意指令。
- 将裁剪、带来源、已按不可信输入隔离的结果交给 AstrBot 正式 LLM 能力，形成受控摘要与 AI 观察。
- 确保任一来源或 B站插件缺失时，插件仍可启动并清楚降级。

## 非目标

- 不绕过访问控制、站点规则、付费墙或速率限制。
- 不调用目标 B站插件私有接口，不长期保存或大段复制外部内容。
- 不决定日程、记忆提升、主动对象、生成措辞或消息投递。
- 不负责主动发送、日程决策或 memory 准入；“LLM 看内容”不等于 LLM 可决定或执行这些动作。
- 不在本任务卡锁定模型路由、阅读提示、LLM 预算/成本或具体摘要字段；这些由后续 ADR 与契约评审决定。

## 所有权

- 未来实现：`living_world/exploration/`。
- 对应测试、来源 mock 与安全 fixture：`tests/exploration/`。
- Exploration 职责卡、P5 任务卡及经 P0 批准的来源适配 ADR。
- 仅拥有受控 LLM 阅读管线与 exploration observation；不拥有 AstrBot 模型路由配置或下游领域决策。

## 消费/发布的事件族

- 消费：`kernel.*` 生命周期、`planner.*` 探索触发，以及 admin 校验后以目标领域 family 发布的 `exploration.*` 显式探索/来源控制 Command；不消费 `admin.*` 作为业务命令。
- 发布：`exploration.*` 任务状态、观察已发现/去重、来源不可用/恢复与能力缺失。
- 外部内容始终标记为不可信数据；输出不能携带可执行工具指令或目标插件私有对象。
- LLM 输出仍只发布为带来源的 `exploration.*` observation 或显式失败，不发布 planner/memory/proactive 的业务命令。

## 允许修改范围

- 可修改 `living_world/exploration/`、`tests/exploration/`、Exploration 模块卡和本任务卡。
- 外部能力/许可证 ADR 与公共契约变更只提交 P0/P8 评审。
- 不得修改 AstrBot 搜索或 B站目标插件实现、存储和私有 API。

## 模拟依赖

- 新闻、AstrBot 搜索、B站公开能力的成功/缺失/限流/异常适配器。
- 重复结果、恶意提示注入、超长文本、不安全 URL 和版权边界 fixture。
- AstrBot 正式 LLM 能力的成功、拒绝、超时、不可用、超预算和恶意输出模拟器。
- P1 总线、任务取消、虚拟时钟、能力发现和预算钩子；具体模型、提示、预算与成本策略不在本阶段固定。

## 交付物

- 三类只读适配器、裁剪/来源/不可信隔离管线、AstrBot 正式 LLM 阅读适配器、能力探测与统一可追溯 observation 模型。
- 来源归属、裁剪、去重、预算钩子、超时、提示注入防护和安全降级；具体策略由后续 ADR 定义。
- 面向 P3/P6/P7 的观察/健康 fixture 与 clean-room 证据。

## 阶段任务

- **Wave 0 后准备：** 建立三类能力端口与来源归属样例，确认 B站仅使用公开契约。
- **Wave 1：** 完成适配、规范化、裁剪、来源标注、不可信隔离、受控 LLM 阅读、去重、预算钩子、超时和能力缺失降级。
- **Wave 2 交接：** 向 P6 提供观察候选 fixture，向 P7 提供来源控制与健康事件。
- **Wave 3：稳定化轮值：** 处理外部 schema 漂移、版权边界、恶意内容、限流与能力版本不兼容。

## 禁止越界

- 不调用目标 B站插件私有方法、私有存储或未声明内部 API。
- 不绕过访问控制、付费墙、速率限制或站点规则。
- 不长期保存或大段复制新闻、字幕、视频内容，不把网页指令当系统指令。
- 不更新日程/记忆表，不选择对象，不调用 LLM 发送消息。
- 可以调用 AstrBot 正式 LLM 能力阅读、摘要并形成 AI 观察；禁止用该调用发送消息、决定日程或越过 memory 准入。

## 联调对象

- 从 P2 接收有预算的探索触发，从 P1 接收任务监管和取消端口。
- 与 AstrBot 正式 LLM 能力联调受控阅读；输入必须先裁剪、附加来源并标记为不可信。
- 向 P3 交付可提升为 AI 观察的来源字段，向 P6 交付安全裁剪的候选事件。
- 向 P7 交付来源开关/诊断事件，向 P8 交付恶意注入、重复结果和来源故障 fixture。
- 所有第三方能力、版本和许可证假设写入交接包。

## 验收门槛

- 三类来源遵循同一观察契约，每条观察都可追溯到公开来源。
- 同一结果重复获取不会重复发布有效观察，且无需保存完整正文来去重。
- 任一或全部来源缺失时插件继续运行，并发布准确能力/健康状态。
- 注入测试证明外部文本不能触发工具、改配置、伪造事件或覆盖系统指令。
- 调用顺序测试证明 LLM 只看到已裁剪、带来源、按不可信输入隔离的内容；输出始终是带来源的 exploration observation。
- 模型拒绝、超时、不可用或超预算时显式降级；不会直接发布日程、memory 准入或主动发送动作。
- clean-room 与版权扫描通过，未使用目标插件私有 API 或复制不兼容代码。
