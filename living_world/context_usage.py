"""Independent context allowances for the current data format."""

import copy

from .context_catalog import DEFAULT_LIMITS

DEFAULT_USAGE = {
    "version": 2,
    "limits": copy.deepcopy(DEFAULT_LIMITS),
    "people_limit": 3,
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


def usage_for(settings, selection=None):
    result = validate_usage(settings.get("context_usage", copy.deepcopy(DEFAULT_USAGE)))
    if selection is not None:
        for key in result["limits"]:
            block = "memory" if key in {"memory.self", "memory.people", "memory.related"} else key
            if block not in selection:
                result["limits"][key] = 0
        if "memory" not in selection:
            result["people_limit"] = 0
    return result
