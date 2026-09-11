"""Configuration defaults and validation shared by the page and runtime."""

import copy
import math
import re
from zoneinfo import ZoneInfo

from .drives import DRIVE_DEFAULTS, validate_drive
from .memory_config import DEFAULT_MEMORY, memory_settings
from .context_usage import DEFAULT_USAGE, integer, validate_usage
from .schedule_time import SCHEDULE_DEFAULTS, schedule_range
from .layout import (
    DEFAULT_SETTINGS as LAYOUT_DEFAULTS,
    validate_settings as validate_layout_settings,
)

DEFAULT_GROUP_REPLY_PROMPT = (
    "本轮只回应当前发言最重要的一点，通常用一句自然的短句；不分段、不列清单，不连续追问或罗列多个建议。\n"
    "保持人格设定的语言、称呼和语气。生活、日程、状态、记忆及群消息用于理解当前话题，"
    "不要逐项复述、总结或扩写，也不要添加无必要的动作描写。\n"
    "工具调用的说明、结果和失败提示也要简短收住，不重复背景，不展开内部处理过程。"
)

MODULES = (
    "state",
    "drives",
    "life",
    "memory",
    "reply",
    "interjection",
    "proactive",
    "news",
    "search",
    "weather",
    "bilibili",
    "journal",
    "notes",
    "daily_digest",
    "debug",
)
NEWS_SOURCES = [
    {"id": key, "name": name, "url": url, "enabled": True}
    for key, name, url in (
        ("bbc", "BBC 中文", "https://feeds.bbci.co.uk/zhongwen/simp/rss.xml"),
        ("google", "Google 新闻中文", "https://news.google.com/rss?hl=zh-CN&gl=CN&ceid=CN:zh-Hans"),
        ("solidot", "Solidot", "https://www.solidot.org/index.rss"),
        ("hn", "Hacker News", "https://hnrss.org/frontpage"),
        ("mit", "MIT Technology Review", "https://www.technologyreview.com/feed/"),
        ("ars", "Ars Technica", "https://feeds.arstechnica.com/arstechnica/index"),
    )
]
DIGEST_SOURCES = [
    {
        "id": "heya",
        "name": "黑鸦 Heya",
        "uid": "3706929260006322",
        "keywords": "早报 日报",
        "time": "12:00",
        "enabled": True,
    },
    {
        "id": "juya",
        "name": "橘鸦 Juya",
        "uid": "285286947",
        "keywords": "日报 早报",
        "time": "23:00",
        "enabled": True,
    },
]
DEFAULTS = {
    "persona_id": "",
    "sessions": [],
    "modules": {
        name: name in {"state", "drives", "life", "memory", "reply", "journal", "notes", "debug"}
        for name in MODULES
    },
    "models": {
        name: "" for name in ("default", "life", "memory", "social", "exploration", "journal")
    },
    "character": {
        "profile": "",
        "world": "",
        "timezone": "Asia/Shanghai",
        "mood": "平静",
        "location": "",
        "sleep_state": "未知",
    },
    "reply": {
        "group_prompt": DEFAULT_GROUP_REPLY_PROMPT,
        "private_prompt": DEFAULT_GROUP_REPLY_PROMPT,
        "proactive_prompt": DEFAULT_GROUP_REPLY_PROMPT,
    },
    "context_layout": copy.deepcopy(LAYOUT_DEFAULTS),
    "context_usage": copy.deepcopy(DEFAULT_USAGE),
    "life": {
        "tick_seconds": 60,
        "detail_minutes": 10,
        "daily_plan_time": "06:00",
        **SCHEDULE_DEFAULTS,
        "activity_count": 10,
        "stale_action_minutes": 10,
    },
    "social": {
        "target_count": 1,
        "cooldown_minutes": 60,
        "interjection_interval_minutes": 30,
    },
    "drives": copy.deepcopy(DRIVE_DEFAULTS),
    "news": {"sources": NEWS_SOURCES, "limit": 5},
    "search": {},
    "weather": {"location": "", "api_host": "", "auth_mode": "api_key", "credential": ""},
    "bilibili": {"plugin_name": "astrbot_plugin_bilibili_ai_bot", "recent_limit": 5},
    "daily_digest": {"sources": DIGEST_SOURCES},
    "debug": {"retain_per_category": 10},
    "memory": copy.deepcopy(DEFAULT_MEMORY),
    "journal": {"hour": 23},
    "model_timeout_seconds": 90,
}


def merge(base, patch):
    result = copy.deepcopy(base)
    for key, value in patch.items():
        result[key] = (
            merge(result[key], value)
            if isinstance(result.get(key), dict) and isinstance(value, dict)
            else copy.deepcopy(value)
        )
    return result


def settings_from(patch=None):
    if patch is not None and not isinstance(patch, dict):
        raise ValueError("配置必须是对象")
    result = merge(DEFAULTS, patch or {})
    for section in (
        "modules",
        "drives",
        "models",
        "character",
        "reply",
        "life",
        "social",
        "news",
        "search",
        "weather",
        "bilibili",
        "memory",
        "journal",
        "daily_digest",
        "debug",
    ):
        if not isinstance(result[section], dict):
            raise TypeError(f"{section} 必须是对象")
    result["context_usage"] = validate_usage(result["context_usage"])
    result["context_layout"] = validate_layout_settings(result["context_layout"])
    result["social"] = {key: result["social"][key] for key in DEFAULTS["social"]}
    integer(result["social"]["target_count"], 1, 20, "每轮抽选目标数")
    result["social"]["target_count"] = 1
    result["memory"] = memory_settings(result["memory"])
    if set(result["drives"]) != set(DRIVE_DEFAULTS):
        raise ValueError("内在状态配置包含未知项目")
    result["drives"] = {
        identifier: validate_drive(identifier, config)
        for identifier, config in result["drives"].items()
    }
    ZoneInfo(str(result["character"]["timezone"]))
    if not isinstance(result["persona_id"], str):
        raise TypeError("人格 ID 必须是字符串")
    if not isinstance(result["sessions"], list):
        raise TypeError("会话白名单必须是数组")
    seen = set()
    for session in result["sessions"]:
        if not isinstance(session, dict):
            raise TypeError("白名单对象必须为表单记录")
        if not session.get("umo"):
            platform = str(session.get("platform_id", "")).strip()
            number = str(session.get("number", "")).strip()
            kind = {"group": "GroupMessage", "private": "FriendMessage"}.get(session.get("type"))
            if not platform or ":" in platform or not number.isdigit() or not kind:
                raise ValueError("请选择 QQ 连接、群聊／私聊，并填写数字号码")
            session["umo"] = f"{platform}:{kind}:{number}"
        umo = session.get("umo", "")
        if not isinstance(umo, str) or not re.fullmatch(
            r"[^:]+:(GroupMessage|FriendMessage):[^:]+", umo
        ):
            raise ValueError("会话需使用完整 AstrBot UMO")
        if umo in seen:
            raise ValueError("会话白名单不能重复")
        seen.add(umo)
        session["enabled"] = bool(session.get("enabled", True))
        name = session.get("display_name", "")
        if not isinstance(name, str) or len(name) > 120 or "\n" in name or "\r" in name:
            raise ValueError("对话称呼必须是最多 120 字符的单行文本")
        session["display_name"] = name.strip()
        weight = float(session.get("weight", 1))
        if not math.isfinite(weight) or weight < 0:
            raise ValueError("白名单权重必须为有限非负数")
        session["weight"] = weight
        platform, kind, number = umo.split(":", 2)
        session.update(
            platform_id=platform,
            type="group" if kind == "GroupMessage" else "private",
            number=number.split("_")[-1] if kind == "GroupMessage" else number,
        )
    for name in MODULES:
        if not isinstance(result["modules"][name], bool):
            raise TypeError("模块开关必须为布尔值")
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", str(result["life"]["daily_plan_time"])):
        raise ValueError("日程生成时间格式必须为 HH:MM")
    schedule_range(result["life"])
    bounds = {
        ("social", "target_count"): (1, 20),
        ("social", "cooldown_minutes"): (0, 10080),
        ("social", "interjection_interval_minutes"): (1, 1440),
        ("life", "tick_seconds"): (10, 3600),
        ("life", "detail_minutes"): (0, 120),
        ("life", "activity_count"): (1, 48),
        ("life", "stale_action_minutes"): (1, 60),
        ("news", "limit"): (1, 30),
        ("journal", "hour"): (0, 23),
        ("debug", "retain_per_category"): (1, 1000),
        ("bilibili", "recent_limit"): (1, 50),
    }
    for (section, key), (low, high) in bounds.items():
        if section == "memory" and isinstance(result[section][key], bool):
            raise ValueError(f"{section}.{key} 必须为整数")
        value = float(result[section][key])
        if not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"{section}.{key} 必须在 {low} 到 {high} 之间")
        if key.endswith("_count") or key in {
            "retain_per_category",
            "limit",
            "hour",
            "recent_limit",
            "context_limit",
            "journal_limit",
            "brief_max_chars",
        }:
            if not value.is_integer():
                raise ValueError(f"{section}.{key} 必须为整数")
            result[section][key] = int(value)
    for section in ("news", "daily_digest"):
        rows = result[section]["sources"]
        if not isinstance(rows, list) or len(rows) > 100:
            raise ValueError("来源必须是最多 100 项的数组")
        ids = set()
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                raise TypeError("来源记录必须为对象")
            if section == "daily_digest" and isinstance(row.get("keywords"), list):
                if any(not isinstance(word, str) for word in row["keywords"]):
                    raise TypeError("日报关键词必须为文本")
                row["keywords"] = " ".join(row["keywords"])
            row.setdefault("id", f"{section}-{i}")
            if not isinstance(row["id"], str) or not row["id"] or row["id"] in ids:
                raise ValueError("来源 ID 不能为空或重复")
            ids.add(row["id"])
            row.setdefault("enabled", True)
            if not isinstance(row["enabled"], bool) or not isinstance(row.get("name"), str):
                raise TypeError("来源名称或开关无效")
            if section == "news":
                if not isinstance(row.get("url"), str) or not row["url"].startswith(
                    ("https://", "http://")
                ):
                    raise ValueError("新闻来源需填写 HTTP(S) RSS/Atom 地址")
            elif (
                not str(row.get("uid", "")).isdigit()
                or not isinstance(row.get("keywords"), str)
                or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", str(row.get("time", "")))
            ):
                raise ValueError("日报需填写 UP 主 UID、搜索词和 HH:MM 时间")
    for section, keys in {
        "character": ("profile", "world", "mood", "location", "sleep_state"),
        "reply": ("group_prompt",),
        "models": tuple(DEFAULTS["models"]),
        "weather": ("location", "api_host", "auth_mode", "credential"),
        "bilibili": ("plugin_name",),
    }.items():
        if any(not isinstance(result[section].get(key), str) for key in keys):
            raise TypeError(f"{section} 的文本字段无效")
    for key in ("group_prompt", "private_prompt", "proactive_prompt"):
        if not isinstance(result["reply"].get(key), str):
            raise TypeError("聊天回复要求必须为文本")
        if len(result["reply"][key]) > 8000:
            raise ValueError("聊天回复要求不能超过 8000 字符")
    if result["weather"]["auth_mode"] not in {"api_key", "jwt"}:
        raise ValueError("和风认证方式必须为 API Key 或 JWT")
    result["bilibili"]["plugin_name"] = "astrbot_plugin_bilibili_ai_bot"
    timeout = float(result["model_timeout_seconds"])
    if not math.isfinite(timeout) or not 5 <= timeout <= 300:
        raise ValueError("模型超时必须在 5 到 300 秒之间")
    return result
