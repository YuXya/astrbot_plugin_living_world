"""Configuration defaults and validation shared by the page and runtime."""

import copy
import math
import re
from zoneinfo import ZoneInfo

MODULES = (
    "state",
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
)
DEFAULTS = {
    "persona_id": "",
    "sessions": [],
    "modules": {
        name: name in {"state", "life", "memory", "reply", "journal", "notes"} for name in MODULES
    },
    "models": {
        name: "" for name in ("default", "life", "memory", "social", "exploration", "journal")
    },
    "character": {
        "profile": "",
        "world": "",
        "timezone": "Asia/Shanghai",
        "energy": 70,
        "mood": "平静",
    },
    "life": {
        "tick_seconds": 60,
        "detail_minutes": 10,
        "max_activities": 12,
        "stale_action_minutes": 10,
        "spontaneous_minutes": 30,
    },
    "social": {
        "target_count": 1,
        "cooldown_minutes": 60,
        "daily_limit": 5,
        "quiet_start": "23:00",
        "quiet_end": "08:00",
        "interjection_interval_minutes": 30,
    },
    "news": {"feeds": [], "limit": 5},
    "search": {"tool_name": "", "query_argument": "query"},
    "weather": {"url": "", "location": ""},
    "bilibili": {"plugin_name": "astrbot_plugin_bilibili_ai_bot", "recent_limit": 5},
    "memory": {
        "half_life_days": 30,
        "forget_after_days": 180,
        "forget_threshold": 0.15,
        "recall_boost": 0.2,
        "reflection_limit": 8,
    },
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
        "models",
        "character",
        "life",
        "social",
        "news",
        "search",
        "weather",
        "bilibili",
        "memory",
        "journal",
    ):
        if not isinstance(result[section], dict):
            raise TypeError(f"{section} 必须是对象")
    ZoneInfo(str(result["character"]["timezone"]))
    if not isinstance(result["persona_id"], str):
        raise TypeError("人格 ID 必须是字符串")
    if not isinstance(result["sessions"], list):
        raise TypeError("会话白名单必须是数组")
    seen = set()
    for session in result["sessions"]:
        umo = session.get("umo", "")
        if not isinstance(umo, str) or not re.fullmatch(
            r"[^:]+:(GroupMessage|FriendMessage):[^:]+", umo
        ):
            raise ValueError("会话需使用完整 AstrBot UMO")
        if umo in seen:
            raise ValueError("会话白名单不能重复")
        seen.add(umo)
        session["enabled"] = bool(session.get("enabled", True))
        weight = float(session.get("weight", 1))
        if not math.isfinite(weight) or weight < 0:
            raise ValueError("白名单权重必须为有限非负数")
        session["weight"] = weight
    for name in MODULES:
        if not isinstance(result["modules"][name], bool):
            raise TypeError("模块开关必须为布尔值")
    for name in ("quiet_start", "quiet_end"):
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", str(result["social"][name])):
            raise ValueError("免打扰时间格式必须为 HH:MM")
    bounds = {
        ("social", "target_count"): (1, 20),
        ("social", "daily_limit"): (0, 1000),
        ("social", "cooldown_minutes"): (0, 10080),
        ("social", "interjection_interval_minutes"): (1, 1440),
        ("life", "tick_seconds"): (10, 3600),
        ("life", "detail_minutes"): (0, 120),
        ("life", "max_activities"): (1, 48),
        ("life", "stale_action_minutes"): (1, 60),
        ("news", "limit"): (1, 30),
        ("journal", "hour"): (0, 23),
        ("character", "energy"): (0, 100),
    }
    for (section, key), (low, high) in bounds.items():
        value = float(result[section][key])
        if not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"{section}.{key} 必须在 {low} 到 {high} 之间")
    if not isinstance(result["news"]["feeds"], list):
        raise TypeError("新闻来源必须是 URL 数组")
    for section, keys in {
        "character": ("profile", "world", "mood"),
        "models": tuple(DEFAULTS["models"]),
        "search": ("tool_name", "query_argument"),
        "weather": ("url", "location"),
        "bilibili": ("plugin_name",),
    }.items():
        if any(not isinstance(result[section].get(key), str) for key in keys):
            raise TypeError(f"{section} 的文本字段无效")
    if any(not isinstance(feed, str) for feed in result["news"]["feeds"]):
        raise TypeError("新闻来源必须为 URL 字符串")
    timeout = float(result["model_timeout_seconds"])
    if not math.isfinite(timeout) or not 5 <= timeout <= 300:
        raise ValueError("模型超时必须在 5 到 300 秒之间")
    return result
