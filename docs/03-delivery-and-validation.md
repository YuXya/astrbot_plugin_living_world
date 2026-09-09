# 0.2.3 交付与验收记录

日期：2026-09-06。交付为**可安装测试版本**，源码实现、自动化验证与真实环境验收分别记录。[总计划](01-lead-plan.md)的真实场景联调仍需后续完成。

## 2026-09-09 调试页面可读性优化

- 保留四按钮切换，①的来源改为默认展开的单列折叠卡片；分别收起后仍显示出处与注入位置，切换视图或请求保留状态。
- ②③默认格式化 JSON 缩进与字符串换行，支持切换原文；SSE 逐个完整事件格式化，错误文本及未结束事件回退原文。复制、下载继续保留原始正文，后端捕获与存储未改动。
- JavaScript 语法检查、`git diff --check` 和完整页面冒烟测试通过。新增 14 组 JSON/SSE 格式化样例，覆盖多段中文、反斜杠、重复字段、数字精度与写法、事件边界和异常内容；同时验证来源折叠、键盘操作、请求配对、原文切换与复制下载一致性。
- 使用 Python 3.12.14、Node.js 24.19.0 和无头 Edge，在 1600px 桌面与 390px 手机检查来源及 JSON 截图；手机 JSON 字号提高至 12px，无页面或阅读框横向溢出。页面桥接与模型仍用替身，后端视图沿用实际代码生成的 fixture；本次未重新执行 Python 全量测试，不增加真实在线联调验收结论。

## 0.2.3 本轮交付与最终验证

- 四项调试页面、请求选择器、直接 JSON/SSE 正文下载、中文生活上下文、同次来源清单及设置入口 02 已完成。升级保留配置、日程、记忆、历史和执行记录。
- **278 项自动化通过，无跳过**。其中 25 项 HTTP 专项使用真实 AstrBot Provider、OpenAI SDK 3.8.0 和 aiohttp 本地 HTTP 服务；直接对比服务端收到的请求正文与导出结果、服务端发出的正文与记录结果。未知字段和普通同名数据保留，只隐藏已知认证值。
- HTTP 专项覆盖 Chat Completions / DeepSeek 兼容配置、Responses、SDK 重试、工具后第二次请求、并发及第三方内部调用排除、JSON/SSE、非 JSON 错误、异常返回字段、取消、客户端替换和卸载。流式正文出现字面 `[DONE]` 不冒充结束；失败和不完整事件分别标记。阅读版解析失败不影响原文保存。流中关闭调试后立即保留部分原文，重新开启不补收或伪称旧流完整，宿主仍收到完整回复。
- 用“莉莉现在日程是什么？”贯通真实消息钩子、宿主 Runner、Provider、SDK 与本地 HTTP，检查实际请求含当前活动、当天日程及中文资料，不含新增 UUID/历史统计或其他私聊材料；原有历史恢复与场合隔离继续回归。
- 其余新增验证覆盖中文投影和来源一致性、后台完整调用保留与清理、直接原文导出、旧记录不冒充原文、正式日程采用结果按生成记录关联，以及试跑不写正式数据。
- **Ruff 与 JavaScript 语法检查通过**。无头 Edge 在 1600px 桌面和 390px 手机通过页面验证并检查截图；使用实际后端 `build_views` 生成的 fixture，覆盖四项视图、收发配对、下载逐字一致、SSE、旧版和未适配状态、工具/采用/发送展示、试跑与模板分离、导航、日程归档入口及 XSS。页面桥接仍使用替身，不等于真实 AstrBot WebUI 联调。

### 4.27.5 专项边界

继续验证官方 4.27.5 人格解析函数；本轮另加载该版本完整 Chat Completions 与 Responses Provider 类，移除全局注册装饰器，Responses 继承加载的 4.27.5 基类，分别通过 JSON 与 SSE 共 4 项本地 HTTP 测试。其余宿主依赖来自本地 4.28.0-beta.1、Python 3.12.14 和 SDK 3.8.0，**不能称为完整 4.27.5 安装环境或在线联调**。

缓存通过 `python scripts/fetch_compat_source.py` 获取，均校验固定 SHA-256；缓存缺失时相关测试明确跳过，不计为通过。缓存不进入安装包。

| 官方来源 | SHA-256 |
|---|---|
| [4.27.5 人格解析](https://raw.githubusercontent.com/AstrBotDevs/AstrBot/v4.27.5/astrbot/core/persona_mgr.py) | `14bfc9413f37ce3ab09e1a11233525874929b6707dcd41ee4c554f3b83633606` |
| [4.27.5 Chat Completions](https://raw.githubusercontent.com/AstrBotDevs/AstrBot/v4.27.5/astrbot/core/provider/sources/openai_source.py) | `cd648baf5ab92357e3cb08326dcf4313662906f1e2d04959934762a299b72d13` |
| [4.27.5 Responses](https://raw.githubusercontent.com/AstrBotDevs/AstrBot/v4.27.5/astrbot/core/provider/sources/openai_responses_source.py) | `78a0a2e6af3fb3d8c0e5c075bae9a578cc74733421da9e5886112fdf4dfc8a6d` |

真实 DeepSeek、QQ、天气和 B 站认证信息未提供，本轮没有向在线模型或真实 QQ 发送测试。下方真实联调待办继续保留。

## 0.2.2 历史交付

0.2.2 修复用户报告的“已有 24 条私聊历史，但人格未解析而无法接入”问题：传入真实 UMO 的宿主模型设置，统一配置检查和实际执行的判断；新增明确错误原因、人格来源和实际注入记录。聊天携带当前时间、当前活动及按场合过滤的当天日程摘要。

此前 0.2.1 的人格测试替身固定返回可用人格，源码兼容检查也没有验证默认人格参数的语义，因此漏掉了这个错误。本版已替换该测试方式，不能将此前的 223 项通过解释为已经验证了此场景。

以下为继续保留的 0.2.1 能力：

0.2.1 由主会话完成聊天上下文、会话只读检查、Provider 调用适配及整轮页面记录；未另开并行子智能体。本轮新增：

- 私聊当前对话接续、首次联系、成功发送后的宿主会话存档、动态资料不写入历史。
- 独立群观察、成员和引用信息、24 条／12,000 字符限制、人格隔离、重复群历史移除与保存前恢复，接管失败回退。
- 白名单首次对话／找到历史／读取失败显示；真实会话与人格诊断，不创建空会话。
- 按轮次捕获模型→工具→模型→发送，流式、失败、取消、并发、发送部分完成及卸载恢复；调试写入失败不影响业务。
- 聊天整轮保留、旧日志兼容、消息正文修复、单步和整轮 JSON、原始返回与解析字段分开。

以下为 0.2.0 已交付并继续保留的能力：

| 分工 | 已交付内容 |
|---|---|
| 主程 | 配置迁移、调试与隔离试跑、模板、宿主接入、白名单匹配、统一调用、集成审查与打包 |
| 日程 | 唯一正式日程、10/2/2/3 精确校验、标记重叠、顺序执行、未来活动调整、原始生成档案和恢复 |
| 来源 | 六个默认新闻源、新闻选择与正文读取、宿主搜索、和风天气、固定 B 站、独立日报及失败审计 |
| 页面 | 角色状态首页、独立白名单/来源/调试入口、明确按钮、完整 JSON、试跑与模板、桌面和手机布局 |

原有开发安排允许主会话统筹最多三个子智能体，并由主程审查集成。

## 0.2.2 历史自动化结果

- 环境：独立 **Python 3.12.14**，本地 **AstrBot 4.28.0-beta.1 / 4.28.0b1**。
- 最终集成测试：`python -m pytest -q` **244 项通过、无跳过**（0.2.1 为 223 项）。使用真实宿主请求、`ToolLoopAgentRunner`、消息类型和工具执行结构；模型、会话持久化服务、消息传输与来源使用替身。
- 新增 21 项验证：4.27.5 官方人格解析函数和本地 4.28 解析函数分别覆盖默认、当前对话、会话规则、禁用、未配置、缺失及不匹配；并分别贯通真实插件消息钩子→当前历史→日程组装→Provider 参数。另测连接、平台、读取、模块错误和缺失／关闭日程。
- 精确重现 4.27.5 漏传 `provider_settings` 返回空人格；同一已有 24 条历史的会话，修复后的检查与执行均通过。实际 Provider 请求包含测试日程、当前活动和当前时间，不含其他私聊的日程与调整；未改写已有历史。
- 本轮覆盖首次私聊、当前对话切换、首次主动消息存档、群观察和历史恢复、人格隔离、工具后模型调用、第三方内部调用排除、并发隔离、流式结果、取消、部分发送、关闭调试与卸载、完整轮次保留及调试写入失败隔离。
- 新增后台 Provider 参数校验、历史被其他链路修改时的回退，以及生成前／生成中卸载不丢失历史的回归。修正日程重试冷却测试的时钟控制，消除真实时钟产生的毫秒偏差。
- Ruff、Python 语法和 JavaScript 语法检查通过。
- 无头 Edge 页面测试通过，覆盖 **1600px 桌面及 390px 手机宽度**：白名单首次／历史／读取错误和只读检查，完整聊天轮次、两次模型及工具步骤、真实发送正文、整轮 JSON 和类别筛选，以及原有导航、配置、时间线、试跑、模板和记录管理。已检查白名单和调试页面截图。页面桥接使用替身，不能等同真实宿主 WebUI 联调。
- 页面新增验证配置允许与实际注入区分、默认人格来源、实际注入时间、跳转对应轮次，以及导航后检查状态保持；检查不调用模型或发送 QQ。
- 来源专项覆盖 RSS 失败隔离与正文提取、搜索空结果、天气认证/刷新/退避、B 站搜索与观看区分、公开视频记忆、日报作者日期、防重和重启。
- 回归覆盖默认配额、重叠、局部修改、已执行记录保留、模块热关闭、旧日程迁移、场合隔离、加权抽选、调试保留数量和试跑无业务副作用。
- 完整流程替身测试覆盖新闻候选→新闻事实→搜索事实→QQ 消息，验证新见闻参与表达、私人资料隔离、重启防重与关闭模块；首次打开页面即可获得完整默认任务模板。
- 延迟回归覆盖模型细化跨活动结束、新闻耗时后后续行动过期、执行窗口超时取消与防重记录保留。

宿主 `audioop` 弃用提醒属于 Python 3.12 宿主依赖提示，不影响当前测试。主程已完成整合检查、桌面与手机页面检查及源码安装包构建检查。

## 0.2.2 历史兼容核对

本轮除源码核对外，**实际执行了 v4.27.5 官方 `resolve_selected_persona` 函数**。源码只缓存于 `dist/compat`，不进入代码提交或安装包，校验 SHA-256 为 `14bfc9413f37ce3ab09e1a11233525874929b6707dcd41ee4c554f3b83633606`。测试只提取该函数进入隔离命名空间，用测试会话与设置驱动；这验证了真实解析逻辑，不等于安装完整 4.27.5 宿主后完成 QQ 联调。

复现命令：先运行 `python scripts/fetch_compat_source.py` 缓存固定来源，再运行 `python -m pytest -q`。没有缓存时 4.27.5 的 8 项测试会明确跳过，不能记为通过。已缓存后测试不需要网络。

安装声明为 `>=4.27.5,<5`。已只读核对官方 **v4.27.5 发布标签源码**：

| 接口 | 结论与依据 |
|---|---|
| Plugin Pages 请求与响应 | `request.body()`、`request.json(default)`、`json_response`、`error_response` 均存在；见 [api/web.py](https://raw.githubusercontent.com/AstrBotDevs/AstrBot/v4.27.5/astrbot/api/web.py)。 |
| 路由、模型与提供商 | `register_web_api`、`registered_web_apis`、同步 `get_provider_by_id`、`llm_generate` 的上下文/系统提示词/附加参数匹配；见 [Context](https://raw.githubusercontent.com/AstrBotDevs/AstrBot/v4.27.5/astrbot/core/star/context.py)。 |
| 模型名称覆盖 | OpenAI Provider 支持 `model` 参数；见 [openai_source.py](https://raw.githubusercontent.com/AstrBotDevs/AstrBot/v4.27.5/astrbot/core/provider/sources/openai_source.py)。 |
| 发送后记录 | `after_message_sent` 已导出并注册对应事件；见 [filter 导出](https://raw.githubusercontent.com/AstrBotDevs/AstrBot/v4.27.5/astrbot/api/event/filter/__init__.py)和[注册实现](https://raw.githubusercontent.com/AstrBotDevs/AstrBot/v4.27.5/astrbot/core/star/register/star_handler.py)。 |
| 网页搜索 | 宿主搜索设置、六种服务选择和内置工具查找匹配；见 [主 Agent](https://raw.githubusercontent.com/AstrBotDevs/AstrBot/v4.27.5/astrbot/core/astr_main_agent.py)及[工具管理器](https://raw.githubusercontent.com/AstrBotDevs/AstrBot/v4.27.5/astrbot/core/provider/func_tool_manager.py)。 |
| Agent 生命周期及工具钩子 | `on_agent_begin`、`on_agent_done`、工具前后事件可用；见 [astr_agent_hooks.py](https://raw.githubusercontent.com/AstrBotDevs/AstrBot/v4.27.5/astrbot/core/astr_agent_hooks.py)。 |
| 会话默认人格 | `provider_settings.default_personality` 必须传给解析器；已执行官方函数并验证优先级和失败分支，见 [persona_mgr.py](https://raw.githubusercontent.com/AstrBotDevs/AstrBot/v4.27.5/astrbot/core/persona_mgr.py)。 |
| 逐次模型捕获 | `step`、`_iter_llm_responses`、Provider 调用及 Agent 开始／结束入口存在；见 [tool_loop_agent_runner.py](https://raw.githubusercontent.com/AstrBotDevs/AstrBot/v4.27.5/astrbot/core/agent/runners/tool_loop_agent_runner.py)。 |
| 动态资料不入存档 | `TextPart`、`_no_save`、`dump_messages_with_checkpoints` 和 `bind_checkpoint_messages` 存在；见 [message.py](https://raw.githubusercontent.com/AstrBotDevs/AstrBot/v4.27.5/astrbot/core/agent/message.py)。 |
| 主动消息接续会话 | `session_lock_manager.acquire_lock`、`new_conversation` 的人格参数及 `update_conversation` 存在；见 [会话锁](https://raw.githubusercontent.com/AstrBotDevs/AstrBot/v4.27.5/astrbot/core/utils/session_lock.py)及[会话管理器](https://raw.githubusercontent.com/AstrBotDevs/AstrBot/v4.27.5/astrbot/core/conversation_mgr.py)。 |

这些是源码兼容核对，**没有安装 4.27.5 实机完成在线模型、QQ 或来源验收**。不能把 4.28 自动化结果写成 4.27.5 实测。

## 真实联调待办

本轮没有用于实际联调的模型、QQ、和风认证或 B 站账号凭据，下列项目不记为已通过：

- [ ] 在目标 AstrBot 4.27.5 安装/升级、打开 Plugin Pages、保存配置并重载。
- [ ] 用真实模型生成一次合格 10/2/2/3 日程，验证临近细化、未来活动调整及前一日约定影响。
- [ ] 使用明确测试白名单完成群聊/私聊主动消息、上下文读取、群聊插话、免打扰和频率控制。
- [ ] 真实账号首次联系及后续回复接续、切换对话、群成员近期消息与引用；核对原始宿主历史保留。
- [ ] 真实一轮工具调用后的第二次模型、流式发送、并发和传输失败，在调试页四项视图对照；确认未接入原因显示。
- [ ] 在不同群与私聊确认人物识别及具体谈话、约定、日程衍生内容不串场合。
- [ ] 获取真实 RSS 正文、宿主搜索、和风实时天气、B 站搜索与观看结果。
- [ ] 在各自定时点读取两路 AI 日报，核对作者、当日发布日期、缓存标记和独立次数。
- [ ] 下载真实请求 JSON 核对，再在任务试跑编辑器调整输入，确认只有模型调用，没有工具执行、QQ 发送或正式数据变更。
- [ ] 在真实运行中关闭模块、改变配置、模拟来源失败、重载和重启，验证不重复或集中补发。
- [ ] 恢复备份，确认旧 ID、防重记录保留，所有业务模块关闭，检查后再启用。

## 实际边界

- 轻量中文文本检索，无向量数据库；日程、提炼和感想的质量取决于真实模型，长期自然程度待使用验证。
- 新闻源可逐项修改；天气固定和风当前接口，搜索使用宿主配置，B 站依赖固定插件及公开 API v3。天气成功刷新间隔 90 分钟，失败 15 分钟退避，不调用 AI。
- 新闻全文不可读时只使用实际标题或订阅摘要，并标明依据；模型感想失败保留实际证据且标记部分完成，不编造成功。
- 日报无法核对作者或日期时跳过，旧视频记忆不标为新观看；独立日报不占 10/2/2/3 配额。
- 调试②③保存 DeepSeek/OpenAI 兼容 Chat Completions 与 Responses 的实际 HTTP 正文，包含 SDK 的实际 HTTP 重试；SSE 保存原始事件流，阅读版另行合并。未适配或没有原文明确标注，旧 Provider 参数不能替代原文；第三方内部调用不捕获。只隐藏已知认证凭据。特殊媒体/工具上下文未支持直接试跑的字段明确拒绝。
- 发送成功表示传输调用返回，不代表对方已读；传输异常可能已发出部分内容，明确标为未知／部分完成。主动私聊存档失败另行记录，不自动重发。
- 升级不重建当天日程，恢复采用缺失记录合并并关闭全部模块；清理调试不删正式档案或防重记录。
- 管理员视图允许看全部场合，实际聊天仍过滤；核心人格不自动改写，不含世界居民、梦境或复杂技能成长。

## 安装包

运行 `python scripts/build_test_package.py` 生成 `dist/astrbot_plugin_living_world-0.2.3.zip` 及 SHA-256 文件。脚本检查入口文件、ZIP 完整性及包内 Python 语法；仅包含源码、页面、依赖声明、许可证和文档，不包含环境、数据库、兼容测试源码缓存、页面截图及凭据。

公开发布和插件市场上架另行安排。
