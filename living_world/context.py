"""Readable prompt projections and provenance from one scoped material snapshot."""

from __future__ import annotations

import json
from datetime import datetime

PLACEMENT = "本轮动态资料（不写入聊天历史）"
STATUS = {
    "planned": "计划中，尚未发生",
    "running": "进行中",
    "completed": "已结束",
    "done": "已结束",
    "skipped": "已跳过",
    "failed": "执行失败",
    "cancelled": "已取消",
}
KINDS = {"knowledge": "知识", "event": "经历", "skill": "技能", "emotional": "感受"}
BASIS = {
    "feed_summary": "订阅源摘要，未确认已读网页全文",
    "title_only": "仅标题",
    "page_text": "网页正文",
    "search_results": "搜索结果，不代表看过视频或读过原文",
    "qweather_current": "和风实时天气接口",
    "public_video_memory": "已存在的公开视频记忆，不是本次新观看",
    "video_search_results": "视频搜索结果，不代表观看",
    "video_analysis": "依赖插件本次视频分析结果",
}


def source_item(title, source, content, placement=PLACEMENT):
    return {"title": title, "source": source, "content": content, "placement": placement}


def _text(value):
    return str(value).strip() if isinstance(value, (str, int, float)) else ""


def _clock(value):
    value = _text(value)
    try:
        return datetime.fromisoformat(value).strftime("%H:%M")
    except ValueError:
        return value


def activity_text(row):
    """Project semantic fields only; IDs, scopes and execution controls stay internal."""
    if not row:
        return "此刻没有正在进行的活动。"
    when = "—".join(filter(None, (_clock(row.get("start")), _clock(row.get("end")))))
    title = _text(row.get("title")) or _text(row.get("content")) or "未命名活动"
    status = STATUS.get(row.get("status"), "")
    lines = [" · ".join(filter(None, (when, title, status)))]
    seen = {title}
    for name in ("content", "description", "incident"):
        text = _text(row.get(name))
        if text and text not in seen:
            lines.append(("生活小插曲：" if name == "incident" else "") + text)
            seen.add(text)
    for name, label in (("location", "地点"), ("sleep_state", "睡眠")):
        if _text(row.get(name)):
            lines.append(f"{label}：{row[name]}")
    return "；".join(lines)


def observation_text(row):
    lines = []
    for field, label in (
        ("title", "标题"),
        ("selection_reason", "选题理由"),
        ("factual_summary", "事实摘要"),
        ("impression", "角色感想"),
    ):
        value = _text(row.get(field))
        if value:
            lines.append(f"{label}：{value}")
    if not _text(row.get("factual_summary")) and _text(row.get("text")):
        lines.append(_text(row["text"]))
    basis = BASIS.get(row.get("reading_basis"), "阅读依据未注明")
    if row.get("from_memory"):
        basis = BASIS["public_video_memory"]
    lines.append("阅读依据：" + basis)
    links = row.get("sources", [])
    if not isinstance(links, list):
        links = [links]
    links = [_text(link) for link in links if _text(link)]
    if links:
        lines.append("出处：" + "、".join(links))
    return "\n".join(lines)


def context_from_data(data):
    """Create readable content and its source list without re-reading any business data."""
    if not isinstance(data, dict):
        raise TypeError("Living World context must be an object")
    sources = []

    def add(title, source, content):
        sources.append(source_item(title, source, content))

    add("当前时间", "角色设置中的时区与当前时钟", _text(data.get("current_time")))
    state = data.get("state")
    if state is not None:
        fields = (
            ("mood", "心情"),
            ("location", "地点"),
            ("sleep_state", "睡眠"),
            ("routine", "作息"),
        )
        content = "\n".join(
            f"{label}：{_text(state.get(field)) or '未知'}" for field, label in fields
        )
        add("生活状态", "角色状态与当前场合可见的日程", content)
    else:
        add("生活状态", "模块设置", "生活状态模块已关闭，本轮未读取状态。")
    schedule = data.get("schedule", {})
    notice = _text(schedule.get("notice"))
    if schedule.get("status") == "disabled":
        add("当前活动", "日程模块设置", "日程生活模块已关闭，本轮未读取当前活动。")
    else:
        add("当前活动", "当前场合可见的今日活动", activity_text(data.get("activity")))
    rows = schedule.get("activities", [])
    add(
        "今日日程",
        "今日正式日程及当前场合的调整",
        "\n".join(filter(None, [notice, *(f"- {activity_text(row)}" for row in rows)]))
        or "今天尚未生成可用日程，不代表角色没有日程能力。",
    )
    memories = data.get("memories", [])
    memory_lines = []
    for row in memories:
        label = "人物认知" if row.get("profile") else KINDS.get(row.get("kind"), "记忆")
        if row.get("source") == "fiction":
            label += "，角色虚构经历"
        value = _text(row.get("text"))
        if value:
            memory_lines.append(f"- {label}：{value}")
    add(
        "相关记忆与人物认知",
        "记忆库按当前场合与人物检索的结果",
        "\n".join(memory_lines) or "本轮没有可用的相关记忆。",
    )
    experience_lines = []
    for row in data.get("experiences", []):
        value = _text(row.get("text"))
        if value:
            label = "角色虚构经历" if row.get("source") == "fiction" else "已记录经历"
            experience_lines.append(f"- {label}：{value}")
    if experience_lines:
        add("近期经历", "生活记录中当前场合可见的经历", "\n".join(experience_lines))
    for row in data.get("observations", []):
        add(
            "天气" if row.get("module") == "weather" else "近期见闻",
            "见闻记录及其中注明的实际来源",
            observation_text(row),
        )
    return {
        "text": "\n\n".join(f"【{row['title']}】\n{row['content']}" for row in sources),
        "sources": sources,
    }


def group_messages_text(messages):
    """Keep speaker attribution and quoted text, excluding storage/session metadata."""
    lines = []
    for row in messages:
        # QQ IDs are only needed as a last-resort speaker label for unnamed members.
        name = _text(row.get("sender_name")) or _text(row.get("sender_id")) or "未命名成员"
        text = _text(row.get("text"))
        if text:
            lines.append(f"{name}：{text}")
    return "\n".join(lines) or "首次对话，暂无近期群消息。"


def normalize_context(context):
    """Project only recognizable legacy life snapshots, preserving task JSON schemas."""
    sources = []

    def is_life_snapshot(value):
        return (
            isinstance(value, dict)
            and {"current_time", "memories", "schedule", "observations"} <= value.keys()
            and isinstance(value["memories"], list)
            and isinstance(value["observations"], list)
            and isinstance(value["schedule"], dict)
            and value["schedule"].get("status") in {"disabled", "missing", "available"}
        )

    def visit(value, path):
        candidate = value
        if isinstance(value, str) and value.lstrip().startswith("{"):
            try:
                candidate = json.loads(value)
            except (ValueError, TypeError):
                pass
        if is_life_snapshot(candidate):
            bundle = context_from_data(candidate)
            sources.extend(
                {**row, "placement": f"本轮动态资料：{path}"} for row in bundle["sources"]
            )
            return bundle["text"]
        if isinstance(value, dict):
            return {key: visit(item, f"{path}.{key}") for key, item in value.items()}
        if isinstance(value, list):
            return [visit(item, f"{path}[{index}]") for index, item in enumerate(value)]
        return value

    return visit(context, "context"), sources
