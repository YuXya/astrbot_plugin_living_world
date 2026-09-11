"""Readable prompt projections and provenance from one scoped material snapshot."""

from __future__ import annotations

import json
import copy
import re
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .context_catalog import (
    BLOCK_NAMES,
    MEMORY_DEFAULTS,
    SOURCE_NAMES,
    V3_BLOCK_NAMES,
    memory_category,
)

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


def record_keys(row, now, *, memory=False, legacy=False):
    """Deduplicate explicit lineage; preserve old exact-copy rules only for old snapshots."""
    scope, identity = row.get("scope", "global"), event_id(row, memory=memory)
    keys = {(scope, "event", identity)} if identity else set()
    moment = material_time(row.get("occurred_at"), now) or material_time(row.get("created_at"), now)
    if legacy and moment and not identity:
        keys.add((scope, "body", moment.isoformat(), clean_life_text(row.get("text"))))
    elif not identity and row.get("id"):
        keys.add((scope, "memory" if memory else "record", str(row["id"])))
    return keys


def prepare_life_records(records, now, *, memory=False, event_lookup=None, seen=None, legacy=False):
    seen = set() if seen is None else seen
    prepared = []
    for row in records:
        item = prepare_life_record(row, now, memory=memory, event_lookup=event_lookup)
        if item is None:
            continue
        keys = record_keys(item, now, memory=memory, legacy=legacy)
        if keys & seen:
            continue
        seen.update(keys)
        moment = material_time(item.get("occurred_at"), now)
        if legacy and moment:
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


def recent_schedule_rows(schedule, now):
    """Select nearby activities from a single already scoped, character-day snapshot."""
    if schedule.get("status") == "disabled":
        return []
    day = now.date()
    if schedule.get("date") and schedule["date"] != day.isoformat():
        return []

    def moment(value):
        try:
            return material_time(datetime.combine(day, time.fromisoformat(value)).isoformat(), now)
        except (TypeError, ValueError):
            return material_time(value, now)

    rows = []
    for row in schedule.get("activities", []):
        if (
            not isinstance(row, dict)
            or row.get("status") in {"cancelled", "canceled", "retired"}
            or row.get("retired")
            or (row.get("date") and row["date"] != day.isoformat())
        ):
            continue
        start, end = moment(row.get("start")), moment(row.get("end"))
        if start is None or end is None or start.date() != day:
            continue
        if end <= start:
            try:
                time.fromisoformat(row.get("end"))
            except (TypeError, ValueError):
                continue
            end += timedelta(days=1)
        rows.append((start, end, row))
    rows.sort(key=lambda item: (item[0], item[1]))
    current = [index for index, (start, end, _) in enumerate(rows) if start <= now < end]
    if current:
        index = current[-1]
        return [row for _, _, row in rows[max(0, index - 1) : index + 2]]
    past = [row for _, end, row in rows if end <= now]
    future = [row for start, _, row in rows if start > now]
    return past[-2:] + future[:1]


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


def unified_memory_text(row, now):
    """Project a conclusion and its factual provenance without retention mechanics."""
    judgment = clean_life_text(row.get("judgment") or row.get("text"))
    if not judgment:
        return ""
    owner = clean_life_text(row.get("owner_name") or row.get("persona_name"))
    if row.get("owner") == "person" and owner == row.get("person_id"):
        owner = "当前人物"
    attribute = clean_life_text(row.get("attribute"))
    moment = material_time(row.get("occurred_at"), now)
    heading = " · ".join(
        filter(None, (owner, attribute, moment.strftime("%Y-%m-%d %H：%M") if moment else ""))
    )
    label = "有依据的推断" if row.get("inferred") else "记录"
    if row.get("stable"):
        label = "稳定画像，" + label
    parts = [f"{heading + '：' if heading else ''}{label}：{judgment}"]
    if basis := BASIS.get(row.get("reading_basis")):
        parts.append(basis)
    if reasoning := clean_life_text(row.get("reasoning")):
        parts.append("事实依据：" + reasoning)
    return "；".join(parts)


def unified_memory_rows(records, *, seen=None):
    """Accept current-format records; repeated selection of the same version is harmless."""
    seen = set() if seen is None else seen
    rows = []
    for row in records:
        if (
            not isinstance(row, dict)
            or row.get("schema_version") != 2
            or not row.get("active", True)
        ):
            continue
        identity = str(row.get("id", ""))
        if identity and identity in seen:
            continue
        if not clean_life_text(row.get("judgment") or row.get("text")):
            continue
        if identity:
            seen.add(identity)
        rows.append(row)
    return rows


def memory_blocks(
    records, now=None, usage=None, *, include_identifiers=False, version=4, identifier="memory"
):
    """Render already selected, typed memories without rereading storage."""
    now = now or datetime.now(UTC)
    if version >= 4:
        rows = unified_memory_rows(records)
        if not rows:
            return []
        content = "\n".join(f"- {unified_memory_text(row, now)}" for row in rows)
        if include_identifiers:
            content = json.dumps(
                {
                    "known": [
                        {
                            key: row.get(key)
                            for key in (
                                "id",
                                "version",
                                "judgment",
                                "reasoning",
                                "attribute",
                                "tags",
                                "owner",
                                "persona_name",
                                "person_id",
                                "scope",
                                "stable",
                                "inferred",
                            )
                        }
                        for row in rows
                    ]
                },
                ensure_ascii=False,
                indent=2,
            )
        item = source_item(
            BLOCK_NAMES[identifier],
            "统一记忆库在本轮按人格、人物、场合及模块选择的同次资料",
            content,
            block_id=identifier,
        )
        item.update(
            count=len(rows),
            memory_ids=[str(row["id"]) for row in rows if row.get("id")],
            memory_versions={
                str(row["id"]): row.get("version", 1) for row in rows if row.get("id")
            },
            memory_snapshots=copy.deepcopy(rows),
            memory_projection_time=now.isoformat(),
            include_memory_identifiers=include_identifiers,
        )
        if usage and identifier == "memory.recent":
            item["limit"] = usage.get("limits", {}).get(identifier, 5)
        return [item]
    grouped = {identifier: [] for identifier in MEMORY_DEFAULTS}
    for row in records:
        identifier = memory_category(row)
        if identifier and (
            not is_journal_memory(row) or row.get("source") in {"journal:brief", "notes:brief"}
        ):
            grouped[identifier].append(row)
    blocks = []
    for identifier, rows in grouped.items():
        if not rows:
            continue
        content = "\n".join(f"- {record_text(row, now, memory=True)}" for row in rows)
        if include_identifiers:
            content = json.dumps(
                {
                    "known": [
                        {key: row.get(key) for key in ("id", "text", "kind", "profile")}
                        for row in rows
                    ]
                },
                ensure_ascii=False,
                indent=2,
            )
        item = source_item(
            V3_BLOCK_NAMES[identifier],
            "记忆库按结构化类型、人物与场合筛选的同次资料",
            content,
            block_id=identifier,
        )
        item["count"] = len(rows)
        item["memory_ids"] = [str(row["id"]) for row in rows if row.get("id")]
        if usage:
            item["limit"] = usage["limits"][identifier]
        blocks.append(item)
    return blocks


def context_from_data(data, *, legacy=False, version=4):
    """Create readable content and its source list without re-reading any business data."""
    if not isinstance(data, dict):
        raise TypeError("Living World context must be an object")
    sources = []
    version = 1 if legacy else version
    legacy = version == 1
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
        identifier = identifier or identifiers[title]
        display_title = (
            title if legacy else (BLOCK_NAMES if version >= 4 else V3_BLOCK_NAMES)[identifier]
        )
        if version == 2 and identifier == "schedule":
            display_title = "日程与执行：今日日程"
        sources.append(source_item(display_title, source, content, block_id=identifier))

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
    if version >= 3:
        recent = recent_schedule_rows(schedule, now)
        add(
            "今日日程（简版）",
            "同次今日正式日程快照中当前场合可见的附近活动",
            "\n".join(filter(None, [notice, *(f"- {activity_text(row)}" for row in recent)]))
            or "今天尚未生成可用日程，不代表角色没有日程能力。",
            identifier="schedule.recent",
        )
    if version >= 4:
        selection = data.get("context_selection")
        selection = set(selection) if isinstance(selection, list) else None
        seen = set()
        for identifier, field in (("memory.recent", "recent_memories"), ("memory", "memories")):
            if selection is not None and identifier not in selection:
                continue
            sources.extend(
                memory_blocks(
                    unified_memory_rows(data.get(field, []), seen=seen),
                    now,
                    data.get("context_usage"),
                    version=version,
                    identifier=identifier,
                )
            )
        if selection is None or "weather" in selection:
            for row in data.get("observations", []):
                if row.get("module") == "weather":
                    add(
                        "天气",
                        "本轮场合可见的已保存天气记录",
                        observation_text(row),
                        identifier="weather",
                    )
                    sources[-1]["count"] = 1
        if selection is not None:
            sources = [row for row in sources if row["block_id"] in selection]
        return {
            "text": "\n\n".join(
                f"【{row['title']}】\n"
                + (row["notice"] + "\n" if row.get("notice") else "")
                + row["content"]
                for row in sources
            ),
            "sources": sources,
        }
    seen = set()
    selection = data.get("context_selection") if version >= 3 else None
    selection = set(selection) if isinstance(selection, list) else None
    events = {str(row.get("id", "")): row for row in data.get("experiences", [])}
    experiences = prepare_life_records(
        data.get("experiences", []),
        now,
        seen=seen if selection is None or "experiences" in selection else None,
        legacy=legacy,
    )
    selected_memories = [
        row
        for row in data.get("memories", [])
        if selection is None or memory_category(row) in selection
    ]
    memories = prepare_life_records(
        selected_memories,
        now,
        memory=True,
        event_lookup=events.get,
        seen=seen,
        legacy=legacy,
    )
    if selection is not None:
        # Unselected sources remain available to the caller without affecting selected deduplication.
        memories.extend(
            prepare_life_records(
                [row for row in data.get("memories", []) if memory_category(row) not in selection],
                now,
                memory=True,
                event_lookup=events.get,
            )
        )
    memory_lines = [f"- {record_text(row, now, memory=True)}" for row in memories]
    if legacy:
        add(
            "相关记忆与人物认知",
            "记忆库按当前场合与人物检索的结果",
            "\n".join(memory_lines) or "本轮没有可用的相关记忆。",
        )
    else:
        sources.extend(memory_blocks(memories, now, data.get("context_usage"), version=version))
    experience_lines = [f"- {record_text(row, now)}" for row in experiences]
    if experience_lines:
        add("近期经历", "生活记录中当前场合可见的经历", "\n".join(experience_lines))
        if not legacy:
            sources[-1]["count"] = len(experiences)
            sources[-1]["record_keys"] = [
                list(key) for row in experiences for key in record_keys(row, now)
            ]
    for row in data.get("observations", []):
        module = row.get("module")
        identifier = (
            module
            if legacy and module in {"weather", *SOURCE_NAMES}
            else "weather"
            if module == "weather"
            else "observations"
            if module in SOURCE_NAMES
            else "task.other"
        )
        text = observation_text(row)
        if not legacy and module in SOURCE_NAMES:
            text = f"近期见闻：{SOURCE_NAMES[module]}\n{text}"
        add(
            "天气" if row.get("module") == "weather" else "近期见闻",
            "见闻记录及其中注明的实际来源",
            text,
            identifier=identifier,
        )
        if not legacy:
            sources[-1]["count"] = 1
    if not legacy:
        merged = {}
        for item in sources:
            key = item["block_id"]
            if key in merged:
                merged[key]["content"] += "\n\n" + item["content"]
                merged[key]["count"] = merged[key].get("count", 0) + item.get("count", 0)
            else:
                merged[key] = item
            if key in data.get("context_usage", {}).get("limits", {}):
                merged[key]["limit"] = data["context_usage"]["limits"][key]
        sources = list(merged.values())
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
        and {"current_time", "memories", "schedule"} <= value.keys()
        and isinstance(value["memories"], list)
        and isinstance(value.get("observations", []), list)
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
            bundle = context_from_data(candidate, legacy=True)
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
