"""Static administration links; these descriptions are never model input."""

from .context_catalog import (
    BLOCK_NAMES,
    BRIEF_IDS,
    DEFAULT_LIMITS,
    MEMORY_DEFAULTS,
    OWNERS,
    SOURCE_NAMES,
    navigation_path,
)


def target(label, page, tab, **params):
    return {"label": label, "page": page, "tab": tab, "params": params}


def entry(purpose, origin, *targets, templates=(), host_help=""):
    return {
        "purpose": purpose,
        "origin": origin,
        "targets": list(targets),
        "templates": list(templates),
        "host_help": host_help,
    }


PROFILE = target("角色资料", "character", "profile", field="character.profile")
STATE = target("生活状态", "character", "state", field="mood")
SCHEDULE = target("日程与执行", "schedule", "timeline")
LIFE_SETTINGS = target("生成与细化设置", "schedule", "settings")
MEMORY = target("记忆与人物", "memory", "records")
JOURNALS = target("日记与笔记", "memory", "journals")
USAGE = target("上下文用量", "context", "usage")
TRIAL = target("本次试跑材料", "context", "trial", current_task="1")
INDEX = {
    "anchor.system": entry(
        "保留宿主原有系统提示词与核心人格。",
        "AstrBot 为当前会话选择的人格及其他宿主系统内容。",
        target("查看人格绑定", "character", "profile", field="persona_id"),
        host_help="核心人格正文请在 AstrBot 的人格管理中编辑；插件只维护绑定与补充资料。",
    ),
    "anchor.user": entry(
        "标记本轮原消息或后台任务提示词的位置。",
        "普通聊天来自当前消息；后台任务来自该任务模板。",
        TRIAL,
        host_help="收到的 QQ 原消息不能在插件中改写；后台任务可从相关任务模板入口调整。",
    ),
    "profile": entry("补充核心人格之外的角色资料。", "角色与世界中已保存的角色补充资料。", PROFILE),
    "world": entry(
        "提供角色生活的背景设定。",
        "角色与世界中已保存的世界设定。",
        target("世界设定", "character", "profile", field="character.world"),
    ),
    "speaker": entry(
        "告诉角色本轮在和谁交谈。",
        "普通聊天来自当前会话与发送者；主动聊天来自抽中的唯一白名单目标。群聊面向整个群，私聊面向指定 QQ 对象。可选称呼明确提供给 AI。",
        target("聊天白名单", "chat", "targets"),
    ),
    "time": entry(
        "提供角色时区中的当前时间。",
        "请求组装时的时钟和已保存时区；时间由程序产生。",
        target("角色时区", "character", "profile", field="character.timezone"),
    ),
    "state": entry(
        "提供心情、作息、地点及睡眠状态。",
        "当前生活状态与本场合可见的活动；活动无信息时采用角色设置。",
        STATE,
        SCHEDULE,
    ),
    "activity": entry(
        "说明此刻正在进行的活动。",
        "当前时间命中的、所属场合可见的正式活动。",
        SCHEDULE,
        templates=("life.detail",),
    ),
    "schedule": entry(
        "提供今天的安排或本活动之外的安排。",
        "正式日程及当前场合调整；关闭或缺少日程时明确说明。",
        SCHEDULE,
        LIFE_SETTINGS,
        templates=("life.plan", "life.revise"),
    ),
    "schedule.recent": entry(
        "提供当前活动附近最多三条完整安排。",
        "与完整日程共用本轮场合快照；有当前活动取上一条、当前条、下一条，空档取过去两条和未来一条，不跨日补齐。",
        SCHEDULE,
    ),
    "memories": entry(
        "提供相关知识、约定、人物认知及日记简报。",
        "按查询、场合、日期、模块及上下文用量筛选后的记忆；只使用简报，不回退日记全文。",
        MEMORY,
        USAGE,
        templates=("memory.reflect", "journal.brief", "notes.brief"),
    ),
    "experiences": entry(
        "提供近期发生的角色生活经历。",
        "本场合可见的生活事件；角色虚构日常只取角色时区今天的记录，同事件去重。",
        target("经历记录", "character", "events"),
        SCHEDULE,
    ),
    "group_history": entry(
        "提供本场合近期对话和说话人信息。",
        "普通群聊使用有上限的群观察窗口；后台聊天任务使用本场合已有消息。",
        target("会话与历史检查", "chat", "targets"),
        host_help="原始聊天存档由 AstrBot 管理；此入口只能检查当前会话，不编辑原消息。",
    ),
    "group_reply": entry(
        "控制本轮普通群聊的表达方式与回复长度倾向。",
        "回复与插话中已保存的本轮群聊回复要求；独立于生活资料说明。",
        target("本轮群聊回复要求", "chat", "reply", field="reply.group_prompt"),
    ),
    "private_reply": entry(
        "控制本轮私聊的表达方式与回复长度倾向。",
        "回复与插话中独立保存的私聊文案；作为临时指令，不进入聊天历史。",
        target("本轮私聊回复要求", "chat", "reply", field="reply.private_prompt"),
    ),
    "proactive_reply": entry(
        "控制主动聊天及插话正文的表达方式。",
        "回复与插话中独立保存的主动聊天文案；可在各任务勾选，不影响发送目标。",
        target("本轮主动聊天要求", "chat", "reply", field="reply.proactive_prompt"),
    ),
    "task.date": entry(
        "限定本次生成或回顾的日期。",
        "日程、日记等任务启动时确定的日期。",
        SCHEDULE,
        JOURNALS,
        TRIAL,
    ),
    "task.parameters": entry(
        "提供任务需要的生成参数。",
        "本任务已保存的设置，或试跑中显式填写的参数。",
        LIFE_SETTINGS,
        TRIAL,
    ),
    "task.reason": entry(
        "说明本次调整、聊天或来源处理的原因。",
        "活动决定、触发本任务的业务过程或管理员本次输入。",
        SCHEDULE,
        TRIAL,
    ),
    "task.activity": entry(
        "提供这次需要细化的活动大纲。",
        "选中的未开始活动，使用其场合允许的视图。",
        SCHEDULE,
        templates=("life.detail",),
    ),
    "task.range": entry(
        "限定行动可以安排的时间范围。",
        "待细化活动的起止时间；结束时间不包含在可执行范围内。",
        SCHEDULE,
        templates=("life.detail",),
    ),
    "task.actions": entry(
        "提供可见的实际行动结果与失败边界。",
        "本场合可见的近期行动、生活事件及失败／跳过记录。",
        SCHEDULE,
        target("发送记录", "chat", "deliveries"),
    ),
    "task.limits": entry(
        "告知本次可用能力和执行限制。",
        "模块启停状态、白名单聊天的免打扰及发送限制。",
        target("模块开关", "system", "modules"),
        target("发送限制", "chat", "limits"),
    ),
    "task.thoughts": entry(
        "让活动细化参考当前阶段的自然语言想法。",
        "寂寞值和精力在细化开始时命中的阶段文案；不提供数值与增减机制。",
        target("内在状态阶段", "character", "drives"),
        templates=("life.detail",),
    ),
    "task.instruction": entry(
        "为这一次细化补充临时要求。",
        "管理员点击细化／重新细化时填写的内容，不保存到公共模板。",
        SCHEDULE,
        TRIAL,
    ),
    "task.editable": entry(
        "告诉模型哪些未来活动允许调整。",
        "当前角色日期内，尚未开始且本场合可见的活动。",
        SCHEDULE,
        templates=("life.revise",),
    ),
    "task.candidates": entry(
        "提供实际获取的候选新闻供选择。",
        "本次新闻来源读取的候选条目，数量受新闻设置限制。",
        target("新闻来源设置", "sources", "settings", source="news"),
        templates=("news.select",),
    ),
    "task.question": entry(
        "提供当前选题问题或需要判断的群消息。",
        "任务触发时的活动意图、群消息或试跑输入。",
        TRIAL,
        target("回复与插话", "chat", "reply"),
    ),
    "task.evidence": entry(
        "提供本次来源整理所必需的原始证据。",
        "本次实际获得的网页、搜索、视频、链接或日记关联材料；结构内部不拆散。",
        target("近期见闻", "sources", "records"),
        TRIAL,
    ),
    "task.events": entry(
        "提供生成日记或笔记所依据的经历。",
        "所选日期和场合允许回顾的事件及记忆，保留事实边界。",
        target("经历记录", "character", "events"),
        JOURNALS,
        templates=("journal.write", "notes.write"),
    ),
    "task.document": entry(
        "提供这一次简报任务需要阅读的完整原文。",
        "选中的日记或笔记正文；全文仅用于明确的简报任务。",
        JOURNALS,
        templates=("journal.brief", "notes.brief"),
    ),
    "task.brief_limit": entry(
        "要求模型生成指定长度的上下文简报。",
        "上下文用量中的简报最长字符设置，或本次显式试跑输入。",
        USAGE,
        templates=("journal.brief", "notes.brief"),
    ),
    "task.material": entry(
        "提供聊天记忆提炼或试跑的本次材料。",
        "本次任务直接提供的结构化输入，不是公共模板。",
        TRIAL,
        templates=("memory.reflect",),
    ),
    "task.other": entry(
        "保留任务提供的其他必要资料。", "不属于通用资料块的任务输入；含义由当前任务决定。", TRIAL
    ),
}
for source, name, template in (
    ("news", "新闻", "news.reflect"),
    ("search", "搜索", "search.reflect"),
    ("bilibili", "B站", "bilibili.reflect"),
    ("daily_digest", "AI日报", "daily_digest.reflect"),
    ("weather", "天气", None),
):
    INDEX[source] = entry(
        f"提供当前场合允许使用的{name}见闻。",
        f"已保存的{name}来源记录，沿用模块启停、场合及数量筛选；同一来源多条内容保持原顺序。",
        target(f"{name}设置", "sources", "settings", source=source),
        target(f"{name}记录", "sources", "records", source=source),
        templates=(template,) if template else (),
    )


for identifier in MEMORY_DEFAULTS:
    page, tab, detail = OWNERS[identifier]
    kind = identifier.split(".", 1)[1]
    INDEX[identifier] = entry(
        f"提供当前任务可用的{detail}。",
        "按结构化类型、场合、人物、日期与模块筛选的资料；独立限量，不占用其他类别额度。日记和笔记只取已生成的简报，不回退全文。",
        target(
            BLOCK_NAMES[identifier],
            page,
            tab,
            **(
                {"kind": "journal" if kind == "journal" else "notes"}
                if identifier in BRIEF_IDS.values()
                else {"category": identifier}
            ),
        ),
        templates=(
            ("journal.brief",)
            if kind == "journal"
            else ("notes.brief",)
            if kind == "notes"
            else ("memory.reflect",)
        ),
    )
INDEX["observations"] = entry(
    "提供新闻、搜索、B站和AI日报的近期来源记录。",
    "四类已保存见闻按当前场合和模块过滤后合计取最新记录；天气独立处理。每次读取数量与上下文最多条数分别设置。",
    target(BLOCK_NAMES["observations"], "sources", "records"),
    *(
        item
        for source, name in SOURCE_NAMES.items()
        for item in (
            target(f"近期见闻：{name}", "sources", "records", source=source),
            target(f"来源设置：{name}", "sources", "settings", source=source),
        )
    ),
    templates=("news.reflect", "search.reflect", "bilibili.reflect", "daily_digest.reflect"),
)
INDEX = {identifier: INDEX[identifier] for identifier in BLOCK_NAMES}
for identifier, metadata in INDEX.items():
    if identifier in DEFAULT_LIMITS:
        metadata["targets"].append(
            target(
                BLOCK_NAMES[identifier] + " · 最多条数",
                "context",
                "usage",
                field="context_usage.limits." + identifier,
            )
        )
    if identifier in BRIEF_IDS.values():
        metadata["targets"].append(
            target(
                BLOCK_NAMES[identifier] + " · 最长字符",
                "context",
                "usage",
                field="context_usage.brief_max_chars." + identifier,
            )
        )
    for link in metadata["targets"]:
        menu_path = navigation_path(link["page"], link["tab"])
        detail = link["label"].removeprefix(menu_path.split(" → ")[-1] + "：")
        link["path"] = (
            menu_path if detail == menu_path.split(" → ")[-1] else menu_path + " → " + detail
        )
