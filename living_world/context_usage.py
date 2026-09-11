"""Independent context allowances, with a one-time legacy configuration conversion."""

import copy

from .context_catalog import BRIEF_IDS, DEFAULT_LIMITS, LEGACY_DEFAULT_LIMITS, MEMORY_DEFAULTS

DEFAULT_USAGE = {
    "version": 2,
    "limits": copy.deepcopy(DEFAULT_LIMITS),
    "people_limit": 3,
}
LEGACY_DEFAULT_USAGE = {
    "version": 1,
    "limits": copy.deepcopy(LEGACY_DEFAULT_LIMITS),
    "brief_max_chars": {identifier: 200 for identifier in BRIEF_IDS.values()},
}


def integer(value, low, high, label):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not low <= value <= high
        or int(value) != value
    ):
        raise ValueError(f"{label} 必须是 {low}—{high} 的整数")
    return int(value)


def validate_legacy_usage(value):
    if (
        not isinstance(value, dict)
        or set(value) != set(LEGACY_DEFAULT_USAGE)
        or type(value["version"]) is not int
        or value["version"] != 1
    ):
        raise ValueError("上下文用量配置格式无效")
    result = {"version": 1}
    for field, defaults in (
        ("limits", LEGACY_DEFAULT_LIMITS),
        ("brief_max_chars", LEGACY_DEFAULT_USAGE["brief_max_chars"]),
    ):
        if not isinstance(value[field], dict) or set(value[field]) != set(defaults):
            raise ValueError("上下文用量包含未知或缺失的资料类别")
        result[field] = {
            key: integer(
                item,
                50 if field == "brief_max_chars" else 0,
                1000 if field == "brief_max_chars" else 1 if key == "weather" else 50,
                key,
            )
            for key, item in value[field].items()
        }
    return result


def validate_usage(value):
    if isinstance(value, dict) and value.get("version") == 1:
        old = validate_legacy_usage(value)
        result = copy.deepcopy(DEFAULT_USAGE)
        result["limits"]["weather"] = old["limits"]["weather"]
        return result
    if (
        not isinstance(value, dict)
        or set(value) != set(DEFAULT_USAGE)
        or type(value["version"]) is not int
        or value["version"] != 2
        or not isinstance(value["limits"], dict)
        or set(value["limits"]) != set(DEFAULT_LIMITS)
    ):
        raise ValueError("上下文用量配置格式无效")
    return {
        "version": 2,
        "limits": {
            key: integer(item, 0, 1 if key == "weather" else 50, key)
            for key, item in value["limits"].items()
        },
        "people_limit": integer(value["people_limit"], 0, 20, "最多人物数"),
    }


def historical_usage(settings):
    """Interpret pre-v2 quotas only for archived request snapshots."""
    result = copy.deepcopy(LEGACY_DEFAULT_USAGE)
    old = settings.get("memory", {})
    total = integer(old.get("context_limit", 10), 0, 50, "旧记忆条数")
    briefs = min(total, integer(old.get("journal_limit", 2), 0, 50, "旧简报条数"))
    weights = dict(list(MEMORY_DEFAULTS.items())[:5])
    assigned = {key: total * weight // 10 for key, weight in weights.items()}
    order = sorted(weights, key=lambda key: -(total * weights[key] % 10))
    for key in order[: total - sum(assigned.values())]:
        assigned[key] += 1
    result["limits"].update(assigned)
    result["limits"].update({"memory.journal": (briefs + 1) // 2, "memory.notes": briefs // 2})
    chars = integer(old.get("brief_max_chars", 200), 50, 1000, "旧简报字符数")
    result["brief_max_chars"] = dict.fromkeys(BRIEF_IDS.values(), chars)
    return result


def legacy_usage(settings):
    """Initialize the unified allowances while preserving the independent weather switch."""
    old = settings.get("context_usage")
    return validate_usage(old) if old else copy.deepcopy(DEFAULT_USAGE)


def usage_for(settings, selection=None):
    result = legacy_usage(settings)
    if selection is not None:
        for key in result["limits"]:
            block = "memory" if key in {"memory.self", "memory.people", "memory.related"} else key
            if block not in selection:
                result["limits"][key] = 0
        if "memory" not in selection:
            result["people_limit"] = 0
    return result


def brief_limit(settings, kind):
    usage = settings.get("context_usage", {})
    return usage.get("brief_max_chars", historical_usage(settings)["brief_max_chars"])[
        BRIEF_IDS[kind]
    ]


def archive_conversion(store, settings):
    if (
        settings.get("context_usage", {}).get("version", 0) < 2
        or settings.get("context_layout", {}).get("version", 1) < 4
    ):
        import hashlib
        import json

        payload = copy.deepcopy(settings)
        key = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
        store.claim("context_settings_history", key, {"id": key, "settings": payload})
