"""Canonical administration names and typed context categories."""

import copy

PAGES = {
    "overview": ("今日概览", {"current": "当前概览", "recent": "近期动态"}),
    "context": (
        "上下文与提示词",
        {
            "layout": "上下文排序",
            "usage": "上下文用量",
            "templates": "提示词模板",
            "trial": "模型试跑",
            "calls": "调用记录",
        },
    ),
    "character": (
        "角色与状态",
        {"profile": "角色与世界", "state": "生活状态", "drives": "内在状态", "events": "经历记录"},
    ),
    "schedule": (
        "日程与行动",
        {"timeline": "日程与执行", "settings": "生成与细化设置", "archives": "日程档案"},
    ),
    "chat": (
        "聊天与对象",
        {
            "targets": "聊天白名单",
            "reply": "回复与插话",
            "limits": "发送限制",
            "deliveries": "发送记录",
        },
    ),
    "sources": (
        "见闻与来源",
        {"settings": "来源设置", "records": "近期见闻", "manual": "手动读取", "runs": "日报执行"},
    ),
    "memory": ("记忆与日记", {"records": "记忆与人物", "journals": "日记与笔记"}),
    "system": (
        "系统与数据",
        {
            "models": "模型分配",
            "modules": "模块开关",
            "backup": "备份恢复",
            "maintenance": "维护与诊断",
        },
    ),
}
MEMORY_DEFAULTS = {
    "memory.knowledge": 4,
    "memory.event": 2,
    "memory.skill": 1,
    "memory.emotional": 1,
    "memory.profile": 2,
    "memory.journal": 1,
    "memory.notes": 1,
}
DEFAULT_LIMITS = {**MEMORY_DEFAULTS, "experiences": 10, "observations": 5, "weather": 1}
BRIEF_IDS = {"journal": "memory.journal", "notes": "memory.notes"}
SOURCE_NAMES = {"news": "新闻", "search": "搜索", "bilibili": "B站", "daily_digest": "AI日报"}
OWNERS = {
    "profile": ("character", "profile", "角色补充资料"),
    "world": ("character", "profile", "世界设定"),
    "speaker": ("chat", "targets", "交谈对象与场合"),
    "time": ("character", "profile", "当前时间"),
    "state": ("character", "state", "心情、地点与作息"),
    "activity": ("schedule", "timeline", "当前活动"),
    "schedule": ("schedule", "timeline", "今日日程（完整）"),
    "schedule.recent": ("schedule", "timeline", "今日日程（简版）"),
    "memory.knowledge": ("memory", "records", "知识记忆"),
    "memory.event": ("memory", "records", "事件与约定记忆"),
    "memory.skill": ("memory", "records", "技能记忆"),
    "memory.emotional": ("memory", "records", "情感记忆"),
    "memory.profile": ("memory", "records", "人物认知"),
    "memory.journal": ("memory", "journals", "日记简报"),
    "memory.notes": ("memory", "journals", "笔记简报"),
    "experiences": ("character", "events", "近期经历"),
    "weather": ("sources", "settings", "天气"),
    "observations": ("sources", "records", "综合见闻"),
    "group_history": ("chat", "targets", "近期会话消息"),
    "task.date": ("context", "trial", "任务日期"),
    "task.parameters": ("context", "trial", "任务参数"),
    "task.reason": ("context", "trial", "任务原因与聊天意图"),
    "task.activity": ("schedule", "timeline", "待细化活动"),
    "task.range": ("schedule", "timeline", "活动时间范围"),
    "task.actions": ("schedule", "timeline", "近期实际行动"),
    "task.limits": ("schedule", "settings", "能力与限制"),
    "task.thoughts": ("character", "drives", "当前阶段想法"),
    "task.instruction": ("schedule", "timeline", "管理员本次要求"),
    "task.editable": ("schedule", "timeline", "可调整活动"),
    "task.candidates": ("sources", "settings", "新闻候选"),
    "task.question": ("context", "trial", "本次问题与群消息"),
    "task.evidence": ("sources", "records", "来源原始证据"),
    "task.events": ("memory", "journals", "回顾经历资料"),
    "task.document": ("memory", "journals", "日记与笔记原文"),
    "task.brief_limit": ("context", "usage", "简报长度要求"),
    "task.material": ("context", "trial", "本次任务材料"),
    "task.other": ("context", "trial", "其他任务资料"),
    "group_reply": ("chat", "reply", "本轮群聊回复要求"),
    "private_reply": ("chat", "reply", "本轮私聊回复要求"),
    "proactive_reply": ("chat", "reply", "本轮主动聊天要求"),
}
BLOCK_NAMES = {
    "anchor.system": "宿主：原有系统提示词与人格",
    "anchor.user": "宿主消息／任务模板：本轮原始内容",
    **{key: f"{PAGES[page][1][tab]}：{name}" for key, (page, tab, name) in OWNERS.items()},
}


def memory_category(record):
    """Use stored types and lineage, never infer a category from prose."""
    source = str(record.get("source", "")).split(":", 1)[0]
    if record.get("source") in {"journal:brief", "notes:brief"}:
        return BRIEF_IDS[source]
    if source in BRIEF_IDS or str(record.get("id", "")).startswith("journal:"):
        return None  # Legacy full-text copies are not eligible briefs.
    if record.get("profile"):
        return "memory.profile"
    key = "memory." + str(record.get("kind", ""))
    return key if key in MEMORY_DEFAULTS else None


def menu_catalog():
    return {
        key: {"label": label, "tabs": copy.deepcopy(tabs)} for key, (label, tabs) in PAGES.items()
    }


def navigation_path(page, tab):
    label, tabs = PAGES[page]
    return f"{list(PAGES).index(page) + 1:02d} {label} → {tabs[tab]}"
