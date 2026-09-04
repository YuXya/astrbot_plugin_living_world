# 参考来源与许可证边界

## English Summary

Living World is an independent MIT implementation. Reference projects are studied only at the behavior and public-interface level unless their license and provenance are explicitly approved. No code, prompts, documentation, assets, tests, or private APIs are copied. License claims in this document are a dated inventory and must be rechecked before integration or release.

## 项目许可立场

`astrbot_plugin_living_world` 以 MIT 发布，但 MIT 只覆盖本项目原创内容和已确认可兼容的贡献。为维持这一边界，设计与实现采用 clean-room 流程：先把用户需求和可观察行为写成中立规格，再由未复制参考实现的开发者独立编码和测试。

本文件是工程准入规则，不是法律意见。外部仓库可能随时间改变许可证或公开接口；每次引入依赖、代码、素材或跨插件适配前都要针对固定提交重新核验。

## 来源清单

核验快照日期：**2026-09-04**。

| 来源 | 核验时的许可/状态 | 允许的参考方式 | 禁止或需额外批准 |
|---|---|---|---|
| [AstrBot 插件开发指南](https://docs.astrbot.app/dev/star/plugin-new.html)、[最小实例](https://docs.astrbot.app/dev/star/guides/simple.html)、[市场发布规范](https://docs.astrbot.app/dev/star/plugin-publish.html) | 官方公开文档 | 使用公开插件 API、元数据格式和发布流程 | 依赖废弃入口；假设未声明的内部 API 稳定 |
| [我会永远陪着你](https://github.com/menglimi/astrbot_plugin_private_companion) | 仓库页面未发现明确许可证声明或 `LICENSE` 文件 | 观察产品行为、公开功能描述与用户体验方向 | 复制/改写代码、提示词、文档、测试、页面或资源；以“公开仓库”等同开源许可 |
| [天使之心](https://github.com/kawayiYokami/astrbot_plugin_angel_heart) | AGPL-3.0 | 研究抽象的交互问题与行为目标 | 复制、翻译或移植实现及表达性内容；引入其代码前必须单独评估 AGPL 义务并改变本计划 |
| [LivingMemory](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory) | AGPL-3.0 | 研究长期记忆领域问题、公开用户能力与可观察行为 | 复制数据结构、算法实现、页面、测试、提示词或文档表达 |
| [Angel Memory](https://github.com/kawayiYokami/astrbot_plugin_angel_memory) | GPL-3.0（2026-09-04 快照核验） | 研究记忆领域问题、公开用户能力与可观察行为 | 复制、翻译或移植代码、数据结构、算法实现、提示词、页面、测试或文档表达；并入 MIT 主体 |
| [Bilibili AI Bot](https://github.com/chenluQwQ/astrbot_plugin_bilibili_ai_bot) | MIT | 首期通过作者明确公开、只读、可协商版本的能力联动；参考公开接口说明 | 调用私有方法、扫描运行时对象、直读其数据库；若复制代码则必须另行记录来源并保留 MIT 声明 |
| [和风天气开发服务](https://dev.qweather.com/) | 外部服务，受其服务条款与配额约束 | 按官方文档调用、保护用户自行提供的凭据 | 提交密钥、绕过配额、缓存或再分发超出条款允许范围的数据 |

“未发现许可证”不代表作者放弃权利。对 `private_companion` 的默认处理始终是不可复制，直到仓库所有者提供可验证、适用于目标提交的许可。

## Clean-room 工作流

1. **需求记录**：只记录用户目标、公开文档事实和可观察的输入/输出，不摘录实现细节。
2. **中立规格**：将需求改写为本项目术语、事件契约和验收场景；避免沿用参考项目独特命名、提示词或页面布局。
3. **独立实现**：实现者依据本项目规格编码，不复制、逐行翻译或机械改写参考仓库内容。
4. **来源审查**：评审者检查提交历史、相似命名、注释、测试数据、素材和依赖来源。
5. **证据留存**：在依赖/素材台账中记录 URL、固定提交或版本、许可证文件哈希、用途和必要声明。

如果贡献者曾查看参考实现，也不能凭记忆复写其具体结构。应由 P0 重新给出行为规格，贡献者只按规格工作；高度相似的代码需重写或移除。

## 跨插件与外部服务边界

- 首选 AstrBot 正式公共 API、能力注册表或对方明确发布的版本化接口。
- 能力发现必须校验精确插件身份、接口版本、生命周期和只读权限；缺失、歧义、未知高版本或已卸载时 fail closed。
- 不使用私有属性、内部模块路径、GC 扫描、猴子补丁、数据库直读或未经声明的 HTTP 端点建立联动。
- 首期 B 站集成只接收公开观察数据，不点赞、投币、评论、关注、私信或修改对方状态。
- 外部插件或服务不可用时，只关闭对应观察来源，不阻止世界、日程、状态和既有记忆运行。
- API 密钥、Cookie 和用户令牌由各适配器按最小权限保存，禁止进入事件载荷、普通日志、导出或测试夹具。

## 贡献准入检查

每个外部依赖、代码片段、提示词、图片、字体、测试语料或生成素材必须回答：

- 来源 URL 与固定版本是什么？
- 作者和许可证是什么，许可证文件是否覆盖该具体内容？
- 是否允许修改、再分发和与 MIT 项目组合？需要保留哪些声明？
- 是否包含商标、人物形象、用户数据或服务条款之外的权利？
- 能否用标准库、AstrBot 公共 API 或原创内容替代？
- 移除它时，核心插件能否安全降级？

信息不全时默认不合入。P8 负责形成检查记录，P0 对引入作最终决定；必要时请求权利人书面许可。

## 发布前许可证门禁

- 仓库根目录 MIT 文本、README 声明和 `metadata.yaml` 信息一致。
- 依赖锁定清单与实际发布包一致，没有未声明的 vendored 代码或二进制。
- 所有第三方 NOTICE、版权声明和源代码提供义务均已满足。
- 秘密扫描、相似代码复查和资源来源清单无阻断项。
- 重新访问本表所有实际使用的来源，核验目标提交的许可证和公共接口未变化。
- 无法确认来源的内容从发布包移除，而不是仅在文档中注明风险。

## 明确排除

1.0 不从任何参考插件复制聊天历史系统、用户画像、记忆库、页面、提示词或主动发送实现；不通过私有方法“兼容”B 站插件；不把 AGPL/GPL 项目实现并入 MIT 主体；不使用无明确授权的品牌素材。未来若要改变任一边界，必须先提交独立 ADR、许可证评估和迁移计划。
