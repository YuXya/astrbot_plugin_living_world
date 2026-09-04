# ADR-0006：MIT 发布与 clean-room 参考边界

> **English Summary:** Living World is independently authored and released under MIT. Reference projects may inform behavior, but code, prompts, documents, assets, and private APIs are not copied when licenses are incompatible, restrictive, or unclear.

- 状态：Accepted
- 决策日期：项目初始化
- 决策人：P0

## 背景

项目会研究“我会永远陪着你”“天使之心”、LivingMemory、angel_memory 和 Bilibili AI Bot。它们的许可证分别存在不明确、AGPL、GPL 或 MIT 等差异。Living World 计划以 MIT 公开发布，必须避免把受限表达、代码或资源带入仓库。

## 决策

1. Living World 的代码、提示词、文档、测试数据和资源全部独立创作，以 MIT 发布。
2. 对许可证不明确的 `astrbot_plugin_private_companion`，只观察公开行为和高层产品思想，不复制任何代码、提示词、文档或资源。
3. 对 AGPL 的 `astrbot_plugin_angel_heart`、`astrbot_plugin_livingmemory` 和 GPL 的 `astrbot_plugin_angel_memory`，只进行行为层与架构思想研究，不复制受版权保护的具体表达或实现。
4. 对 MIT 的 `astrbot_plugin_bilibili_ai_bot`，首期仍优先通过其公开工具或正式能力契约联动；不调用私有方法，不把其内部实现作为耦合接口。
5. 所有新增第三方依赖、代码片段、提示词模板、图像和数据集在合并前登记来源与许可证，由 P8 检查兼容性。
6. 无法确认权利或来源时默认不纳入，并独立重写需求与实现。

## Clean-room 流程

1. **行为描述：** 研究者只记录输入、可观察输出、用户价值和限制，不抄录实现文本。
2. **独立规格：** P0/模块负责人把行为需求改写为本项目术语、事件边界和验收测试。
3. **独立实现：** 实现者只依据本项目规格与公开官方接口编写代码。
4. **来源审计：** P8 检查提交历史、依赖清单、资源来源和显著相似内容。
5. **许可记录：** 可纳入的第三方内容保留版权声明和许可证要求；与 MIT 不兼容则移除或隔离在项目之外。

## 后果

### 正面

- 公共仓库的许可边界清晰，可供用户和贡献者安全复用。
- 与参考项目的关系可以公开说明而不形成私有实现依赖。
- B 站集成通过公开契约，升级和替换成本更低。

### 代价

- 即使参考项目已有相似实现，也需要重新设计和编码。
- 贡献评审需要额外来源与许可证检查。
- 无明确许可证的有用资源不能直接采用。

## 验证

- README 和参考文档明确列出参考范围与“不复制”规则。
- CI/发布清单检查依赖许可证，并由 P8 人工检查提示词、文档和资源来源。
- B 站适配测试只依赖公开能力或正式契约，不导入/调用目标插件私有实现。
