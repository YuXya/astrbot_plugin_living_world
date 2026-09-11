"""Validated settings for unified memory extraction and optional forgetting."""

import copy
import math


DEFAULT_MEMORY = {
    "version": 1,
    "chat_batch_rounds": 5,
    "chat_idle_seconds": 600,
    "reflection_limit": 8,
    "query_timeout_seconds": 15.0,
    "forgetting_enabled": False,
    "initial_strength": 10.0,
    "low_decay_seconds": 259200,
    "low_decay_amount": 1.0,
    "useful_strength": 1.0,
    "useful_score": 2.5,
    "medium_threshold": 3.0,
    "long_threshold": 10.0,
    "useless_strength": 1.0,
}

_RANGES = {
    "chat_batch_rounds": (1, 100),
    "chat_idle_seconds": (1, 86400),
    "reflection_limit": (1, 100),
    "query_timeout_seconds": (0.1, 15),
    "initial_strength": (0.01, 100000),
    "low_decay_seconds": (1, 31536000),
    "low_decay_amount": (0.01, 100000),
    "useful_strength": (0, 100000),
    "useful_score": (0, 100000),
    "medium_threshold": (0, 100000),
    "long_threshold": (0, 100000),
    "useless_strength": (0, 100000),
}


def memory_settings(value=None):
    """Validate current memory settings without converting historical options."""
    result = copy.deepcopy(DEFAULT_MEMORY)
    if not isinstance(value, dict):
        return result
    if set(value) - set(DEFAULT_MEMORY):
        raise ValueError("记忆配置含未知或旧版本字段，不再自动转换")
    if "forgetting_enabled" in value:
        if not isinstance(value["forgetting_enabled"], bool):
            raise ValueError("遗忘开关必须是布尔值")
        result["forgetting_enabled"] = value["forgetting_enabled"]
    for key, (minimum, maximum) in _RANGES.items():
        if key not in value:
            continue
        raw = value[key]
        if isinstance(raw, bool):
            raise ValueError(f"记忆设置 {key} 必须是数字")
        try:
            number = float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"记忆设置 {key} 必须是数字") from exc
        if not math.isfinite(number) or not minimum <= number <= maximum:
            raise ValueError(f"记忆设置 {key} 超出允许范围")
        if isinstance(DEFAULT_MEMORY[key], int):
            if number != int(number):
                raise ValueError(f"记忆设置 {key} 必须是整数")
            number = int(number)
        result[key] = number
    if result["long_threshold"] < result["medium_threshold"]:
        raise ValueError("长期保留阈值不能低于中档阈值")
    return result
