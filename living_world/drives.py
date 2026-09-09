"""Program-owned motivations with online growth and transactional action debits."""

from __future__ import annotations

import copy
import math
import time
import uuid


DRIVE_DEFAULTS = {
    "loneliness": {
        "growth_per_hour": 10,
        "costs": {"social": 10},
        "stages": [
            {"max": 40, "text": "不是很想聊天。"},
            {"max": 80, "text": "想偷偷看一眼 QQ 聊天。"},
            {"max": 100, "text": "必须聊天。"},
        ],
    },
    "energy": {
        "growth_per_hour": 10,
        "costs": {"news": 10, "search": 10},
        "stages": [
            {"max": 40, "text": "暂时不太想阅读新闻或主动搜索。"},
            {"max": 80, "text": "想了解新鲜事，或查查感兴趣的问题。"},
            {"max": 100, "text": "很想阅读新闻或主动搜索，了解些新东西。"},
        ],
    },
}
INITIAL_VALUES = {"loneliness": 0, "energy": 70}


def number(value, *, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("数值必须是有限非负数")
    if not math.isfinite(value) or value < 0 or maximum is not None and value > maximum:
        raise ValueError("数值必须在 0—100 之间" if maximum == 100 else "数值必须是有限非负数")
    return float(value)


def validate_drive(identifier, config):
    if identifier not in DRIVE_DEFAULTS:
        raise ValueError("未知内在状态")
    if not isinstance(config, dict) or set(config) != {"growth_per_hour", "costs", "stages"}:
        raise ValueError("请提供增长、行动消耗和完整阶段设置")
    result = copy.deepcopy(config)
    result["growth_per_hour"] = number(config["growth_per_hour"])
    if not isinstance(config["costs"], dict) or set(config["costs"]) != set(
        DRIVE_DEFAULTS[identifier]["costs"]
    ):
        raise ValueError("行动消耗配置不完整")
    result["costs"] = {kind: number(value) for kind, value in config["costs"].items()}
    stages = config["stages"]
    if not isinstance(stages, list) or not 1 <= len(stages) <= 101:
        raise ValueError("至少保留一个阶段，完整覆盖 0—100")
    previous = -1
    for stage in stages:
        if not isinstance(stage, dict) or set(stage) != {"max", "text"}:
            raise ValueError("阶段需包含上界与注入文案")
        bound = stage["max"]
        if type(bound) is not int or not previous < bound <= 100:
            raise ValueError("阶段上界须为递增整数，不允许空段或重叠")
        if not isinstance(stage["text"], str) or not stage["text"].strip():
            raise ValueError("阶段注入文案不能为空")
        previous = bound
    if previous != 100:
        raise ValueError("最后一个阶段必须覆盖到 100")
    return result


class DriveService:
    def __init__(self, runtime, clock=None):
        self.runtime = runtime
        self.clock = clock or time.monotonic
        self.session = uuid.uuid4().hex
        # A new process starts at the saved value, without interpreting an old clock.
        self.rebase()

    def _row(self, now):
        row = self.runtime.store.get("drive_state", "current", {})
        values = {**INITIAL_VALUES, **row.get("values", {})}
        elapsed = (
            max(0.0, now - row.get("anchor", now))
            if row.get("session") == self.session and row.get("accruing")
            else 0.0
        )
        for identifier, config in self.runtime.settings["drives"].items():
            value = number(values[identifier], maximum=100)
            # Dividing first also avoids overflowing a large, valid hourly rate.
            growth = config["growth_per_hour"] * (elapsed / 3600)
            values[identifier] = min(100.0, value + growth)
        return {
            "values": values,
            "session": self.session,
            "anchor": now,
            "accruing": self.runtime.enabled("drives"),
        }

    def rebase(self):
        """Resume a saved value with a fresh anchor; never accumulate paused time."""
        store = self.runtime.store
        with store.transaction():
            row = store.get("drive_state", "current", {})
            row.update(
                values={**INITIAL_VALUES, **row.get("values", {})},
                session=self.session,
                anchor=self.clock(),
                accruing=self.runtime.enabled("drives"),
            )
            store.put("drive_state", "current", row)

    def settle(self):
        with self.runtime.store.transaction():
            row = self._row(self.clock())
            self.runtime.store.put("drive_state", "current", row)
        return row

    def snapshot(self):
        with self.runtime.store.lock:
            row = self._row(self.clock())
            configs = copy.deepcopy(self.runtime.settings["drives"])
        meters = {}
        for identifier, config in configs.items():
            value = row["values"][identifier]
            displayed, lower = math.floor(value), 0
            for stage in config["stages"]:
                if displayed <= stage["max"]:
                    selected = {"min": lower, **stage}
                    break
                lower = stage["max"] + 1
            meters[identifier] = {
                "value": value,
                "display_value": displayed,
                "stage": selected,
                "thought": selected["text"],
                "config": config,
            }
        return {"enabled": self.runtime.enabled("drives"), "meters": meters}

    def thoughts(self):
        if not self.runtime.enabled("drives"):
            return ""
        return "\n".join(meter["thought"] for meter in self.snapshot()["meters"].values())

    def set_value(self, identifier, value):
        if identifier not in DRIVE_DEFAULTS:
            raise ValueError("未知内在状态")
        value = number(value, maximum=100)
        with self.runtime.store.transaction():
            row = self.settle()
            before = row["values"][identifier]
            row["values"][identifier] = value
            self.runtime.store.put("drive_state", "current", row)
            key = uuid.uuid4().hex
            self.runtime.store.put(
                "drive_adjustments",
                key,
                {
                    "id": key,
                    "meter": identifier,
                    "before": before,
                    "after": value,
                    "at": time.time(),
                },
            )
        return self.snapshot()

    def save_settings(self, identifier, config):
        config = validate_drive(identifier, config)
        with self.runtime.store.lock:
            old = self.runtime.settings
            proposed = copy.deepcopy(old)
            proposed["drives"][identifier] = config
            try:
                with self.runtime.store.transaction():
                    self.settle()
                    self.runtime.store.put("settings", "current", proposed)
                    self.runtime.settings = proposed
                    self.rebase()
            except BaseException:
                self.runtime.settings = old
                raise
        return self.snapshot()

    def debit(self, action_id, kind):
        """Called within the same transaction as the scheduled action start receipt."""
        store = self.runtime.store
        with store.transaction():
            if store.get("drive_debits", action_id):
                return False
            row = self.settle()
            deductions = {}
            if self.runtime.enabled("drives"):
                for identifier, config in self.runtime.settings["drives"].items():
                    if kind not in config["costs"]:
                        continue
                    before = row["values"][identifier]
                    after = max(0.0, before - config["costs"][kind])
                    row["values"][identifier] = after
                    deductions[identifier] = {
                        "before": before,
                        "after": after,
                        "cost": config["costs"][kind],
                    }
            store.put("drive_state", "current", row)
            store.put(
                "drive_debits",
                action_id,
                {
                    "id": action_id,
                    "kind": kind,
                    "at": time.time(),
                    "enabled": self.runtime.enabled("drives"),
                    "deductions": deductions,
                },
            )
        return True
