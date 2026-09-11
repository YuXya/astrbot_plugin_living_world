"""Independent context allowances, with a one-time legacy configuration conversion."""

import copy

from .context_catalog import BRIEF_IDS, DEFAULT_LIMITS, MEMORY_DEFAULTS

DEFAULT_USAGE = {
    "version": 1,
    "limits": DEFAULT_LIMITS,
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


def validate_usage(value):
    if (
        not isinstance(value, dict)
        or set(value) != set(DEFAULT_USAGE)
        or type(value["version"]) is not int
        or value["version"] != 1
    ):
        raise ValueError("上下文用量配置格式无效")
    result = {"version": 1}
    for field, defaults in (
        ("limits", DEFAULT_LIMITS),
        ("brief_max_chars", DEFAULT_USAGE["brief_max_chars"]),
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


def legacy_usage(settings):
    result = copy.deepcopy(DEFAULT_USAGE)
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


def usage_for(settings, selection=None):
    result = copy.deepcopy(settings.get("context_usage") or legacy_usage(settings))
    if selection is not None:
        for key in result["limits"]:
            if key not in selection:
                result["limits"][key] = 0
    return result


def brief_limit(settings, kind):
    return usage_for(settings)["brief_max_chars"][BRIEF_IDS[kind]]


def archive_conversion(store, settings):
    if "context_usage" not in settings or settings.get("context_layout", {}).get("version", 1) < 3:
        import hashlib
        import json

        payload = copy.deepcopy(settings)
        key = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
        store.claim("context_settings_history", key, {"id": key, "settings": payload})
