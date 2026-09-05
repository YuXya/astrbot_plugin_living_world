# 0.2.0 交付与验收记录

日期：2026-09-05。交付为**可安装测试版本**，源码实现、自动化验证与真实环境验收分别记录。[总计划](01-lead-plan.md)的真实场景联调仍需后续完成。

## 本轮交付

| 分工 | 已交付内容 |
|---|---|
| 主程 | 配置迁移、调试与隔离试跑、模板、宿主接入、白名单匹配、统一调用、集成审查与打包 |
| 日程 | 唯一正式日程、10/2/2/3 精确校验、标记重叠、顺序执行、未来活动调整、原始生成档案和恢复 |
| 来源 | 六个默认新闻源、新闻选择与正文读取、宿主搜索、和风天气、固定 B 站、独立日报及失败审计 |
| 页面 | 角色状态首页、独立白名单/来源/调试入口、明确按钮、完整 JSON、试跑与模板、桌面和手机布局 |

一个主会话统筹，最多三个子智能体并行；独立文件范围开发后由主程审查集成，不直接拼接未经验证的结果。

## 自动化结果

- 环境：独立 **Python 3.12.14**，本地 **AstrBot 4.28.0-beta.1 / 4.28.0b1**。
- 最终集成测试：`python -m pytest -q` **207 项通过**。包括真实宿主类初始化、Plugin Pages HTTP 适配和工具执行器；模型、消息传输和来源使用替身。
- Ruff、Python 语法和 JavaScript 语法检查通过。
- 无头 Edge 页面测试通过，覆盖桌面及 **390px 手机宽度**：导航与白名单、配置、时间线、JSON 明细、调试试跑、模板、记录管理及页面适配。页面桥接使用替身，不能等同真实宿主 WebUI 联调。
- 来源专项覆盖 RSS 失败隔离与正文提取、搜索空结果、天气认证/刷新/退避、B 站搜索与观看区分、公开视频记忆、日报作者日期、防重和重启。
- 回归覆盖默认配额、重叠、局部修改、已执行记录保留、模块热关闭、旧日程迁移、场合隔离、加权抽选、调试保留数量和试跑无业务副作用。
- 完整流程替身测试覆盖新闻候选→新闻事实→搜索事实→QQ 消息，验证新见闻参与表达、私人资料隔离、重启防重与关闭模块；首次打开页面即可获得完整默认任务模板。
- 延迟回归覆盖模型细化跨活动结束、新闻耗时后后续行动过期、执行窗口超时取消与防重记录保留。

宿主 `audioop` 弃用提醒属于 Python 3.12 宿主依赖提示，不影响当前测试。主程已完成整合检查、桌面与手机页面检查及源码安装包构建检查。

## AstrBot 4.27.5 兼容核对

安装声明为 `>=4.27.5,<5`。已只读核对官方 **v4.27.5 发布标签源码**：

| 接口 | 结论与依据 |
|---|---|
| Plugin Pages 请求与响应 | `request.body()`、`request.json(default)`、`json_response`、`error_response` 均存在；见 [api/web.py](https://raw.githubusercontent.com/AstrBotDevs/AstrBot/v4.27.5/astrbot/api/web.py)。 |
| 路由、模型与提供商 | `register_web_api`、`registered_web_apis`、同步 `get_provider_by_id`、`llm_generate` 的上下文/系统提示词/附加参数匹配；见 [Context](https://raw.githubusercontent.com/AstrBotDevs/AstrBot/v4.27.5/astrbot/core/star/context.py)。 |
| 模型名称覆盖 | OpenAI Provider 支持 `model` 参数；见 [openai_source.py](https://raw.githubusercontent.com/AstrBotDevs/AstrBot/v4.27.5/astrbot/core/provider/sources/openai_source.py)。 |
| 发送后记录 | `after_message_sent` 已导出并注册对应事件；见 [filter 导出](https://raw.githubusercontent.com/AstrBotDevs/AstrBot/v4.27.5/astrbot/api/event/filter/__init__.py)和[注册实现](https://raw.githubusercontent.com/AstrBotDevs/AstrBot/v4.27.5/astrbot/core/star/register/star_handler.py)。 |
| 网页搜索 | 宿主搜索设置、六种服务选择和内置工具查找匹配；见 [主 Agent](https://raw.githubusercontent.com/AstrBotDevs/AstrBot/v4.27.5/astrbot/core/astr_main_agent.py)及[工具管理器](https://raw.githubusercontent.com/AstrBotDevs/AstrBot/v4.27.5/astrbot/core/provider/func_tool_manager.py)。 |

这些是源码兼容核对，**没有安装 4.27.5 实机完成在线模型、QQ 或来源验收**。不能把 4.28 自动化结果写成 4.27.5 实测。

## 真实联调待办

本轮没有用于实际联调的模型、QQ、和风认证或 B 站账号凭据，下列项目不记为已通过：

- [ ] 在目标 AstrBot 4.27.5 安装/升级、打开 Plugin Pages、保存配置并重载。
- [ ] 用真实模型生成一次合格 10/2/2/3 日程，验证临近细化、未来活动调整及前一日约定影响。
- [ ] 使用明确测试白名单完成群聊/私聊主动消息、上下文读取、群聊插话、免打扰和频率控制。
- [ ] 在不同群与私聊确认人物识别及具体谈话、约定、日程衍生内容不串场合。
- [ ] 获取真实 RSS 正文、宿主搜索、和风实时天气、B 站搜索与观看结果。
- [ ] 在各自定时点读取两路 AI 日报，核对作者、当日发布日期、缓存标记和独立次数。
- [ ] 下载真实请求 JSON、编辑后隔离试跑，确认只有模型调用，没有工具执行、QQ 发送或正式数据变更。
- [ ] 在真实运行中关闭模块、改变配置、模拟来源失败、重载和重启，验证不重复或集中补发。
- [ ] 恢复备份，确认旧 ID、防重记录保留，所有业务模块关闭，检查后再启用。

## 实际边界

- 轻量中文文本检索，无向量数据库；日程、提炼和感想的质量取决于真实模型，长期自然程度待使用验证。
- 新闻源可逐项修改；天气固定和风当前接口，搜索使用宿主配置，B 站依赖固定插件及公开 API v3。天气成功刷新间隔 90 分钟，失败 15 分钟退避，不调用 AI。
- 新闻全文不可读时只使用实际标题或订阅摘要，并标明依据；模型感想失败保留实际证据且标记部分完成，不编造成功。
- 日报无法核对作者或日期时跳过，旧视频记忆不标为新观看；独立日报不占 10/2/2/3 配额。
- 调试是 AstrBot 调用边界或宿主钩子快照，认证字段隐藏；不是提供商最终 HTTP 报文，不包含第三方内部调用。特殊媒体/工具上下文未支持直接试跑的字段会明确拒绝，不能静默丢弃。
- 升级不重建当天日程，恢复采用缺失记录合并并关闭全部模块；清理调试不删正式档案或防重记录。
- 管理员视图允许看全部场合，实际聊天仍过滤；核心人格不自动改写，不含世界居民、梦境或复杂技能成长。

## 安装包

运行 `python scripts/build_test_package.py` 生成 `dist/astrbot_plugin_living_world-0.2.0.zip` 及 SHA-256 文件。脚本检查入口文件、ZIP 完整性及包内 Python 语法；仅包含源码、页面、依赖声明、许可证和文档，不包含环境、数据库、缓存及凭据。

公开发布和插件市场上架另行安排。
