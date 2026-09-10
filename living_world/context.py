"""Readable prompt projections and provenance from one scoped material snapshot."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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
FICTION_NOTICE = "角色经历属于角色虚构日常，不是真实网络事实；计划不代表已经发生。"
_URL = re.compile(r"(?:https?://|www\.)[^\s<>\"'`【】\u3000-\u303f\uff00-\uffef]+", re.I)
_LINK_START = re.compile(r"!?\[([^\]\n]*)\]\(")
_PREFIX = re.compile(
    r"^(?:[-*•]\s*)?(?:(?:经历|记忆)[，,]\s*)?"
    r"(?:角色虚构经历|角色虚构生活|已记录经历|角色经历(?:[（(][^）)\n]*[）)])?)"
    r"\s*(?:[:：]\s*|$)"
)
_HEADER = re.compile(r"^[【\[]?(?:近期经历|相关记忆与人物认知)[】\]]?[:：]?$")


def clean_life_text(value):
    """Clean automatic material only; never apply this to chat history or raw evidence."""
    text = _text(value).replace("\r\n", "\n").replace("\r", "\n")
    # Consume balanced destinations so parentheses in URLs cannot leave broken markup.
    for match in reversed(list(_LINK_START.finditer(text))):
        start, end, depth = match.end(), match.end(), 1
        while end < len(text) and depth and text[end] != "\n":
            if text[end] == "(" and (end == 0 or text[end - 1] != "\\"):
                depth += 1
            elif text[end] == ")" and (end == 0 or text[end - 1] != "\\"):
                depth -= 1
            end += 1
        if not depth and re.match(r"<?(?:https?://|www\.)", text[start:end].lstrip(), re.I):
            text = text[: match.start()] + match[1] + text[end:]
    references = re.findall(r"(?mi)^\s*\[([^\]]+)\]:\s*(?:https?://|www\.).*$", text)
    for reference in references:
        text = re.sub(r"\[([^\]\n]+)\]\[" + re.escape(reference) + r"\]", r"\1", text)
    lines = []
    for line in text.splitlines():
        line = line.strip()
        while _PREFIX.match(line):
            line = _PREFIX.sub("", line, count=1).strip()
        if _HEADER.fullmatch(line):
            continue
        if re.match(r"^(?:[-*•]\s*)?(?:阅读依据|来源|出处)\s*[:：]", line):
            continue
        if re.match(r"^\[[^\]]+\]:\s*(?:https?://|www\.)", line, re.I):
            continue
        line = re.sub(r"([。；;])\s*(?:阅读依据|来源|出处)\s*[:：].*$", r"\1", line)
        line = _URL.sub("", line)
        line = re.sub(r"<\s*>", "", line).strip()
        if line and not line.strip("-*• :：、，。;；,.!?！？()（）[]"):
            continue
        if line or (lines and lines[-1]):
            lines.append(line)
    return "\n".join(lines).strip()


def material_time(value, now):
    """Interpret stored timestamps in the character timezone, without inventing a date."""
    try:
        if isinstance(value, bool) or value is None:
            return None
        moment = (
            datetime.fromtimestamp(value, UTC)
            if isinstance(value, (int, float))
            else datetime.fromisoformat(str(value))
        )
        return (moment.replace(tzinfo=now.tzinfo) if moment.tzinfo is None else moment).astimezone(
            now.tzinfo
        )
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def is_role_experience(row):
    return (
        not row.get("profile")
        and row.get("kind", "event") == "event"
        and (
            row.get("source") == "fiction"
            or (
                not row.get("source")
                and re.match(r"^(?:[-*•]\s*)?角色虚构", _text(row.get("text")))
            )
        )
    )


def is_journal_memory(row):
    return str(row.get("source", "")).split(":", 1)[0] in {"journal", "notes"} or str(
        row.get("id", "")
    ).startswith("journal:")


def is_fiction_journal(row):
    return row.get("source") == "journal:brief" and any(
        isinstance(source, dict) and (source.get("fiction") or source.get("source") == "fiction")
        for source in row.get("sources", [])
    )


def brief_text(value, limit=200):
    """Bound a reading projection without modifying the archived document."""
    text = " ".join(clean_life_text(value).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def event_id(row, *, memory=False):
    key = str(row.get("id", ""))
    return str(
        row.get("source_event_id")
        or (key.removeprefix("event:") if not memory or key.startswith("event:") else "")
    )


def prepare_life_record(row, now, *, memory=False, event_lookup=None):
    """Return a presentation copy; lineage lookups must stay within the original scope."""
    result = dict(row)
    if memory and is_journal_memory(row):
        if row.get("source") not in {"journal:brief", "notes:brief"}:
            return None
        if is_fiction_journal(row) and row.get("journal_day") != now.date().isoformat():
            return None
    identity = event_id(row, memory=memory)
    origin = event_lookup(identity) if identity and event_lookup else None
    moment = material_time(result.get("occurred_at"), now)
    if origin and origin.get("scope", "global") == row.get("scope", "global"):
        result["source_event_id"] = identity
        moment = (
            moment
            or material_time(origin.get("occurred_at"), now)
            or material_time(origin.get("created_at"), now)
        )
        if not result.get("source"):
            result["source"] = origin.get("source", "")
    moment = moment or material_time(result.get("created_at"), now)
    if is_role_experience(result):
        if moment is None or moment.date() != now.date():
            return None
        result["source"] = "fiction"
    if not result.get("reading_basis"):
        basis = re.search(r"(?m)^\s*阅读依据\s*[:：]\s*(\w+)\s*$", _text(row.get("text")))
        if basis and basis[1] in BASIS:
            result["reading_basis"] = basis[1]
    result["text"] = clean_life_text(result.get("text"))
    if not result["text"]:
        return None
    if moment:
        result["occurred_at"] = moment.isoformat()
    return result


def record_keys(row, now, *, memory=False):
    """Deduplicate known lineage, or exact legacy copies at the same instant and scope."""
    scope, identity = row.get("scope", "global"), event_id(row, memory=memory)
    keys = {(scope, "event", identity)} if identity else set()
    moment = material_time(row.get("occurred_at"), now) or material_time(row.get("created_at"), now)
    if moment and not identity:
        keys.add((scope, "body", moment.isoformat(), clean_life_text(row.get("text"))))
    return keys


def prepare_life_records(records, now, *, memory=False, event_lookup=None, seen=None):
    seen = set() if seen is None else seen
    prepared = []
    for row in records:
        item = prepare_life_record(row, now, memory=memory, event_lookup=event_lookup)
        if item is None:
            continue
        keys = record_keys(item, now, memory=memory)
        if keys & seen:
            continue
        seen.update(keys)
        moment = material_time(item.get("occurred_at"), now)
        if moment:
            seen.add((item.get("scope", "global"), "body", moment.isoformat(), item["text"]))
        prepared.append(item)
    if not memory:
        prepared.sort(
            key=lambda row: (
                material_time(row.get("occurred_at"), now)
                or datetime.min.replace(tzinfo=now.tzinfo)
            ),
            reverse=True,
        )
    return prepared


def record_text(row, now, *, memory=False):
    value = clean_life_text(row.get("text"))
    if memory and is_journal_memory(row):
        label = "日记简报" if str(row.get("source", "")).startswith("journal") else "笔记简报"
        day = row.get("journal_day", "")
        return f"{label}{'（' + day + '）' if day else ''}：{value}"
    if is_role_experience(row):
        moment = material_time(row.get("occurred_at"), now) or material_time(
            row.get("created_at"), now
        )
        return f"角色经历（{moment:%H：%M}）：{value}" if moment else ""
    label = (
        ("人物认知" if row.get("profile") else KINDS.get(row.get("kind"), "记忆"))
        if memory
        else "已记录经历"
    )
    if basis := BASIS.get(row.get("reading_basis")):
        value = basis + "；" + value
    return f"{label}：{value}"


def source_item(title, source, content, placement=PLACEMENT, *, block_id=None):
    row = {"title": title, "source": source, "content": content, "placement": placement}
    if block_id:
        row["block_id"] = block_id
    return row


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
    title = clean_life_text(row.get("title")) or clean_life_text(row.get("content")) or "未命名活动"
    status = STATUS.get(row.get("status"), "")
    lines = [" · ".join(filter(None, (when, title, status)))]
    seen = {title}
    for name in ("content", "description", "incident"):
        text = clean_life_text(row.get(name))
        if text and text not in seen:
            lines.append(("生活小插曲：" if name == "incident" else "") + text)
            seen.add(text)
    for name, label in (("location", "地点"), ("sleep_state", "睡眠")):
        if value := clean_life_text(row.get(name)):
            lines.append(f"{label}：{value}")
    return "；".join(lines)


def activity_material(row):
    """Keep editable IDs and controls while cleaning only projected outline prose."""
    return {
        key: clean_life_text(value)
        if key in {"title", "content", "description", "incident", "location", "sleep_state"}
        else value
        for key, value in row.items()
    }


def observation_text(row):
    basis = BASIS.get(row.get("reading_basis"), "")
    if row.get("from_memory"):
        basis = BASIS["public_video_memory"]
    lines = [basis] if basis else []
    for field, label in (
        ("title", "标题"),
        ("selection_reason", "选题理由"),
        ("factual_summary", "事实摘要"),
        ("impression", "角色感想"),
    ):
        value = clean_life_text(row.get(field))
        if value:
            lines.append(f"{label}：{value}")
    if not _text(row.get("factual_summary")) and _text(row.get("text")):
        lines.append(clean_life_text(row["text"]))
    return "\n".join(lines)


def context_from_data(data):
    """Create readable content and its source list without re-reading any business data."""
    if not isinstance(data, dict):
        raise TypeError("Living World context must be an object")
    sources = []
    try:
        now = datetime.fromisoformat(data.get("current_time", ""))
    except (ValueError, TypeError):
        now = datetime.now(UTC)
    try:
        tz = ZoneInfo(data["timezone"])
    except (KeyError, TypeError, ValueError, ZoneInfoNotFoundError):
        tz = now.tzinfo or UTC
    now = now.replace(tzinfo=tz) if now.tzinfo is None else now.astimezone(tz)

    identifiers = {
        "当前时间": "time",
        "生活状态": "state",
        "当前活动": "activity",
        "今日日程": "schedule",
        "相关记忆与人物认知": "memories",
        "近期经历": "experiences",
    }

    def add(title, source, content, identifier=None):
        sources.append(
            source_item(title, source, content, block_id=identifier or identifiers[title])
        )

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
    notice = clean_life_text(schedule.get("notice"))
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
    seen = set()
    events = {str(row.get("id", "")): row for row in data.get("experiences", [])}
    experiences = prepare_life_records(data.get("experiences", []), now, seen=seen)
    memories = prepare_life_records(
        data.get("memories", []), now, memory=True, event_lookup=events.get, seen=seen
    )
    memory_lines = [f"- {record_text(row, now, memory=True)}" for row in memories]
    add(
        "相关记忆与人物认知",
        "记忆库按当前场合与人物检索的结果",
        "\n".join(memory_lines) or "本轮没有可用的相关记忆。",
    )
    experience_lines = [f"- {record_text(row, now)}" for row in experiences]
    if experience_lines:
        add("近期经历", "生活记录中当前场合可见的经历", "\n".join(experience_lines))
    for identifier, rows in (("memories", memories), ("experiences", experiences)):
        if any(is_role_experience(row) or is_fiction_journal(row) for row in rows):
            for item in sources:
                if item["block_id"] == identifier:
                    item["notice"] = FICTION_NOTICE
    for row in data.get("observations", []):
        add(
            "天气" if row.get("module") == "weather" else "近期见闻",
            "见闻记录及其中注明的实际来源",
            observation_text(row),
            identifier=row.get("module")
            if row.get("module") in {"weather", "news", "search", "bilibili", "daily_digest"}
            else "task.other",
        )
    return {
        "text": "\n\n".join(
            f"【{row['title']}】\n"
            + (row["notice"] + "\n" if row.get("notice") else "")
            + row["content"]
            for row in sources
        ),
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


def is_life_snapshot(value):
    return (
        isinstance(value, dict)
        and {"current_time", "memories", "schedule", "observations"} <= value.keys()
        and isinstance(value["memories"], list)
        and isinstance(value["observations"], list)
        and isinstance(value["schedule"], dict)
        and value["schedule"].get("status") in {"disabled", "missing", "available"}
    )


def normalize_context(context):
    """Project only recognizable legacy life snapshots, preserving task JSON schemas."""
    sources = []

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
