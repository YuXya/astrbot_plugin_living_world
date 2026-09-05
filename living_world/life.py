"""Daily plans with fixed action quotas, persistent claims and scoped refinements."""

from __future__ import annotations

import asyncio
import copy
import json
import math
import uuid
from datetime import UTC, date, datetime, time, timedelta, timezone
from itertools import pairwise
from time import monotonic
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .prompts import PROMPTS

ACTION_ORDER = ("news", "search", "social")
ACTION_KINDS = frozenset(
    {"social", "news", "search", "weather", "bilibili", "bilibili_watch", "bilibili_recent"}
)
ACTIVITY_KINDS = ACTION_KINDS | {"fiction"}
FINAL_STATUSES = frozenset({"completed", "failed", "skipped"})
PLAN_TEMPLATE = PROMPTS["life.plan"]
DETAIL_TEMPLATE = PROMPTS["life.detail"]
REVISE_TEMPLATE = PROMPTS["life.revise"]


def character_timezone(settings: dict):
    name = str(settings.get("character", {}).get("timezone", "Asia/Shanghai"))
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        if name in {"Asia/Shanghai", "Asia/Hong_Kong", "Asia/Taipei"}:
            return timezone(timedelta(hours=8))
        return UTC


def parse_json(text: str) -> Any:
    value = text.strip()
    if value.startswith("```"):
        lines = value.splitlines()
        if len(lines) > 2 and lines[-1].strip() == "```":
            value = "\n".join(lines[1:-1]).strip()
    return json.loads(value)


async def scope_allowed(runtime, scope: str) -> bool:
    checker = getattr(runtime, "scope_allowed", None)
    if checker is None:
        return True
    try:
        return bool(await checker(scope))
    except Exception:  # noqa: BLE001 - Lookup failures must fail closed.
        return False


def elapsed_clock(now):
    """Advance an injected civil time by elapsed work without depending on wall-clock jumps."""
    started = monotonic()
    return lambda: now + timedelta(seconds=max(0, monotonic() - started))


class LifeService:
    def __init__(self, runtime):
        self.runtime = runtime
        self._tick_lock = asyncio.Lock()
        self._plan_lock = asyncio.Lock()
        self._detail_lock = asyncio.Lock()
        self._first_tick = True

    def _now(self, now: datetime | None = None) -> datetime:
        tz = character_timezone(self.runtime.settings)
        if now is None:
            return datetime.now(tz)
        return now.replace(tzinfo=tz) if now.tzinfo is None else now.astimezone(tz)

    def _setting(self, key: str, default: float) -> float:
        try:
            value = float(self.runtime.settings.get("life", {}).get(key, default))
            return value if math.isfinite(value) else default
        except (TypeError, ValueError):
            return default

    def parameters(self) -> dict:
        result = {
            "daily_plan_time": self.runtime.settings.get("life", {}).get("daily_plan_time", "06:00")
        }
        for key, default in (
            ("activity_count", 10),
            ("news_count", 2),
            ("search_count", 2),
            ("social_count", 3),
        ):
            result[key] = int(self._setting(key, default))
        time.fromisoformat(result["daily_plan_time"])
        if not 1 <= result["activity_count"] <= 48 or any(
            not 0 <= result[f"{k}_count"] <= result["activity_count"] for k in ACTION_ORDER
        ):
            raise ValueError("日程数量或行动数量无效，每类行动数量不能超过活动数量")
        return result

    async def _complete(self, task, template, context, scope="global"):
        complete = getattr(self.runtime, "complete", None)
        if complete:
            return await complete(task, "life", template, context, scope=scope)
        return await self.runtime.generate(
            "life", template + "\n资料：" + json.dumps(context, ensure_ascii=False), scope=scope
        )

    def state(self) -> dict:
        character = self.runtime.settings.get("character", {})
        state = {
            "energy": character.get("energy", 80),
            "mood": character.get("mood", "平静"),
            "routine": character.get("routine", ""),
            "updated_at": None,
        }
        state.update(self.runtime.store.get("life_state", "current", {}) or {})
        current = self.current()
        for field in ("location", "sleep_state"):
            state[field] = (current or {}).get(field) or character.get(field) or "未知"
        return copy.deepcopy(state)

    def update_state(self, changes: dict) -> dict:
        if not isinstance(changes, dict) or set(changes) - {"energy", "mood", "routine"}:
            raise ValueError("Only energy, mood and routine can be adjusted.")
        state = self.state()
        if "energy" in changes:
            energy = float(changes["energy"])
            if not math.isfinite(energy):
                raise ValueError("Energy must be finite.")
            state["energy"] = max(0.0, min(100.0, energy))
        for field in ("mood", "routine"):
            if field in changes:
                if not isinstance(changes[field], str):
                    raise ValueError(f"{field} must be text.")
                state[field] = changes[field]
        state["updated_at"] = self._now().isoformat()
        self.runtime.store.put("life_state", "current", state)
        return copy.deepcopy(state)

    def list_activities(self) -> list[dict]:
        return sorted(
            self.runtime.store.list("activities"),
            key=lambda a: (a.get("start", ""), a.get("id", "")),
        )

    def _view(self, activity: dict, scope: str) -> dict:
        result = copy.deepcopy(activity)
        overrides = result.pop("scope_overrides", {})
        if scope != "global":
            result.update(overrides.get(scope, {}))
        return result

    def current(self, scope: str = "global") -> dict | None:
        now = self._now()
        matches = [
            a
            for a in self.list_activities()
            if a.get("scope") in {"global", scope}
            and a.get("status") in {"planned", "running"}
            and self._parse_time(a["start"], now.date())
            <= now
            < self._parse_time(a["end"], now.date())
        ]
        return self._view(matches[-1], scope) if matches else None

    def _parse_time(self, value: str, day: date) -> datetime:
        if not isinstance(value, str):
            raise TypeError("Activity time must be an ISO datetime or HH:MM.")
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            parsed = datetime.combine(day, time.fromisoformat(value))
        return self._now(parsed)

    def _make_activity(self, data: dict, day: date, scope: str, key: str, *, modern=False) -> dict:
        if not isinstance(data, dict):
            raise TypeError("An activity must be an object.")
        title = data.get("title", "")
        if not isinstance(title, str) or not title.strip():
            raise ValueError("An activity requires a title.")
        start, end = (
            self._parse_time(data.get("start", ""), day),
            self._parse_time(data.get("end", ""), day),
        )
        if end <= start and "T" not in data.get("end", ""):
            end += timedelta(days=1)
        if start.date() != day or end <= start or end - start > timedelta(days=1):
            raise ValueError("An activity must start on its planned day and last at most a day.")
        kind = "fiction" if modern else data.get("kind", "fiction")
        if kind not in ACTIVITY_KINDS or not isinstance(data.get("payload", {}), dict):
            raise ValueError("Unsupported activity kind or payload.")
        row = {
            "id": key,
            "date": str(day),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "title": title.strip(),
            "kind": kind,
            "payload": copy.deepcopy(data.get("payload", {})),
            "scope": scope,
            "status": "planned",
            "detailed": False,
            "created_at": self._now().isoformat(),
        }
        for field in ("content", "location", "sleep_state", "description", "incident"):
            value = data.get(field, "未知" if field in {"location", "sleep_state"} else "")
            if not isinstance(value, str):
                raise TypeError(f"{field} must be text.")
            row[field] = value
        if modern:
            raw = data.get("actions")
            if not isinstance(raw, dict) or set(raw) != set(ACTION_ORDER):
                raise ValueError("每个活动必须包含 actions.news/search/social")
            actions, last_at = {}, None
            for action_kind in ACTION_ORDER:
                action = raw[action_kind]
                if not isinstance(action, dict) or type(action.get("enabled")) is not bool:
                    raise ValueError("行动 enabled 必须为布尔值")
                intent = action.get("intent", action.get("reason", ""))
                if not isinstance(intent, str) or (action["enabled"] and not intent.strip()):
                    raise ValueError("启用行动必须提供执行意图")
                at = None
                if action["enabled"]:
                    at = self._parse_time(action.get("at"), day)
                    if at < start and end.date() > start.date() and "T" not in action["at"]:
                        at += timedelta(days=1)
                    if not start <= at < end or (last_at and at < last_at):
                        raise ValueError("行动时间必须位于活动内，并按新闻、搜索、聊天排序")
                    last_at = at
                actions[action_kind] = {
                    "enabled": action["enabled"],
                    "intent": intent.strip(),
                    "at": at.isoformat() if at else None,
                    "execution": {"status": "pending" if action["enabled"] else "disabled"},
                }
            row.update(schema_version=2, actions=actions)
        return row

    def _memories(self, scope: str, *, reinforce=False) -> list[dict]:
        if not self.runtime.enabled("memory"):
            return []
        records = [
            e
            for e in self.runtime.memory.recall(
                query="", scope=scope, limit=100 if scope != "global" else 12, reinforce=reinforce
            )
            if e.get("scope", "global") in {"global", scope}
        ]
        if scope != "global":
            records = [e for e in records if e.get("scope") == scope][:6] + [
                e for e in records if e.get("scope", "global") == "global"
            ][:6]
        return records[:12]

    def _day_activities(self, day: date) -> list[dict]:
        return [
            a
            for a in self.list_activities()
            if a.get("date") == str(day) and a.get("scope") == "global"
        ]

    def _validate_day(self, activities, parameters):
        if len(activities) != parameters["activity_count"]:
            raise ValueError(f"日程需要恰好 {parameters['activity_count']} 个活动")
        for kind in ACTION_ORDER:
            if (
                sum(bool(a.get("actions", {}).get(kind, {}).get("enabled")) for a in activities)
                != parameters[f"{kind}_count"]
            ):
                raise ValueError(f"{kind} 标记数量必须为 {parameters[f'{kind}_count']}")
        ordered = sorted(activities, key=lambda a: a["start"])
        if any(left["end"] > right["start"] for left, right in pairwise(ordered)):
            raise ValueError("日程活动不能重叠；同一活动内可包含多类行动")

    def freeze_parameters(self, now: datetime | None = None) -> dict:
        """Freeze today's budget before configuration changes can affect tomorrow."""
        now = self._now(now)
        key = f"{now.date()}:global"
        marker = self.runtime.store.get("life_days", key, {}) or {}
        if not marker and not self._day_activities(now.date()):
            marker = {
                "date": str(now.date()),
                "scope": "global",
                "schema_version": 2,
                "status": "waiting",
                "parameters": self.parameters(),
            }
            self.runtime.store.put("life_days", key, marker)
        return marker

    def plan_request(self, now: datetime | None = None, *, formal=False) -> dict:
        now = self._now(now)
        marker = self.runtime.store.get("life_days", f"{now.date()}:global", {}) or {}
        context = {
            "date": str(now.date()),
            "now": now.isoformat(),
            "parameters": (marker.get("parameters") if formal else None) or self.parameters(),
            "memories": self._memories("global", reinforce=False),
        }
        if self.runtime.enabled("state"):
            context["state"] = self.state()
        return {"template": PLAN_TEMPLATE, "context": context, "scope": "global"}

    async def plan_day(self, now: datetime | None = None, scope: str = "global") -> list[dict]:
        """Publish one validated outline; a prepared marker recovers interrupted publication."""
        if (
            scope != "global"
            or not self.runtime.enabled("life")
            or not await scope_allowed(self.runtime, scope)
        ):
            return []
        now = self._now(now)
        day, key = now.date(), f"{now.date()}:global"
        async with self._plan_lock:
            marker, existing = (
                self.runtime.store.get("life_days", key, {}) or {},
                self._day_activities(day),
            )
            if marker.get("status") == "prepared":
                for row in marker["adopted_activities"]:
                    self.runtime.store.claim("activities", row["id"], row)
                marker["status"] = "completed"
                self.runtime.store.put("life_days", key, marker)
                return self._day_activities(day)
            if marker.get("status") == "completed" or existing:
                if not marker:
                    self.runtime.store.put(
                        "life_days",
                        key,
                        {
                            "date": str(day),
                            "scope": scope,
                            "status": "completed",
                            "schema_version": 1,
                            "legacy": True,
                        },
                    )
                return existing
            parameters = marker.get("parameters") or self.parameters()
            due = datetime.combine(
                day, time.fromisoformat(parameters["daily_plan_time"]), tzinfo=now.tzinfo
            )
            if now < due:
                self.freeze_parameters(now)
                return []
            request = self.plan_request(now, formal=True)
            marker = {
                "date": str(day),
                "scope": scope,
                "schema_version": 2,
                "status": "generating",
                "parameters": parameters,
                "request": copy.deepcopy(request),
                "created_at": now.isoformat(),
            }
            self.runtime.store.put("life_days", key, marker)
            try:
                raw_json = await self._complete(
                    "life.plan", request["template"], request["context"], scope
                )
                marker["raw_json"] = raw_json
                marker["full_request"] = copy.deepcopy(
                    getattr(self.runtime, "last_requests", {}).get(("life.plan", scope), request)
                )
                if not self.runtime.enabled("life") or not await scope_allowed(self.runtime, scope):
                    marker.update(status="failed", error="生活模块或人格绑定已变化")
                    self.runtime.store.put("life_days", key, marker)
                    return []
                data = parse_json(raw_json)
                raw = data.get("activities") if isinstance(data, dict) else None
                if not isinstance(raw, list):
                    raise TypeError("日程响应必须是包含 activities 数组的 JSON 对象")
                rows = [
                    self._make_activity(
                        item,
                        day,
                        scope,
                        uuid.uuid5(uuid.NAMESPACE_URL, f"living-world:plan:{key}:{i}").hex,
                        modern=True,
                    )
                    for i, item in enumerate(raw)
                ]
                self._validate_day(rows, parameters)
                for row in rows:
                    for action in row["actions"].values():
                        if action["enabled"] and self._parse_time(action["at"], day) < now:
                            action["execution"] = {
                                "status": "skipped",
                                "reason": "overdue_at_generation",
                                "finished_at": now.isoformat(),
                            }
                marker.update(status="prepared", adopted_activities=copy.deepcopy(rows))
                self.runtime.store.put("life_days", key, marker)
                for row in rows:
                    self.runtime.store.claim("activities", row["id"], row)
                marker["status"] = "completed"
                self.runtime.store.put("life_days", key, marker)
                return self._day_activities(day)
            except BaseException as exc:
                if marker.get("status") != "prepared":
                    marker.update(
                        status="failed",
                        error=str(exc) or type(exc).__name__,
                        retry_after=(now + timedelta(minutes=15)).timestamp(),
                    )
                marker["full_request"] = copy.deepcopy(
                    getattr(self.runtime, "last_requests", {}).get(("life.plan", scope), request)
                )
                self.runtime.store.put("life_days", key, marker)
                raise

    def _editable(self, activity, now):
        return (
            activity.get("status") == "planned"
            and self._parse_time(activity["start"], date.fromisoformat(activity["date"])) > now
        )

    def update_activities(self, updates: list[dict]) -> list[dict]:
        """Validate a complete future-only batch before writing any changes."""
        if not isinstance(updates, list) or not updates:
            raise ValueError("需要待调整的活动列表")
        now, proposed, days = self._now(), {}, set()
        allowed = {
            "title",
            "start",
            "end",
            "content",
            "location",
            "sleep_state",
            "kind",
            "payload",
            "description",
            "incident",
            "actions",
        }
        for update in updates:
            if not isinstance(update, dict):
                raise TypeError("日程调整必须为对象")
            key, changes = update.get("id"), update.get("changes", {})
            if key in proposed or not isinstance(changes, dict) or set(changes) - allowed:
                raise ValueError("Unsupported or duplicate activity changes.")
            old = self.runtime.store.get("activities", key)
            if old is None:
                raise KeyError(key)
            if not self._editable(old, now):
                raise ValueError("只能修改尚未开始的活动")
            modern = old.get("schema_version") == 2
            if modern and (
                ("kind" in changes and changes["kind"] != "fiction") or "payload" in changes
            ):
                raise ValueError("新日程的行动只能通过 actions 配置")
            if not modern and "actions" in changes:
                raise ValueError("旧日程不能追加新的行动配额，新规则用于下一份正式日程")
            validated = self._make_activity(
                {**old, **changes},
                date.fromisoformat(old["date"]),
                old["scope"],
                key,
                modern=modern,
            )
            if not self._editable(validated, now):
                raise ValueError("调整后的开始时间必须在现在之后")
            merged = {
                **old,
                **validated,
                "created_at": old.get("created_at"),
                "updated_at": now.isoformat(),
            }
            if modern:
                for kind in ACTION_ORDER:
                    previous, current = old["actions"][kind], merged["actions"][kind]
                    status = previous.get("execution", {}).get("status", "pending")
                    if status not in {"pending", "disabled"} and any(
                        previous.get(f) != current.get(f) for f in ("enabled", "intent", "at")
                    ):
                        raise ValueError("已经处理过的行动记录不能修改")
                    current["execution"] = copy.deepcopy(
                        previous.get("execution", {"status": "pending"})
                    )
                    if status in {"pending", "disabled"}:
                        current["execution"] = {
                            "status": "pending" if current["enabled"] else "disabled"
                        }
                days.add(old["date"])
            for field in ("description", "incident", "energy_delta", "mood"):
                if field not in changes:
                    merged.pop(field, None)
            proposed[key] = merged
        for day in days:
            rows = [
                proposed.get(row["id"], row)
                for row in self._day_activities(date.fromisoformat(day))
            ]
            parameters = (self.runtime.store.get("life_days", f"{day}:global", {}) or {}).get(
                "parameters"
            )
            if parameters is None:
                raise ValueError("缺少该日正式生成参数，不能修改行动配额")
            self._validate_day(rows, parameters)
        batch_write = getattr(self.runtime.store, "put_many", None)
        if batch_write:
            batch_write("activities", list(proposed.items()))
        else:
            for key, row in proposed.items():
                self.runtime.store.put("activities", key, row)
        for key in proposed:
            self._cancel_children(key, now)
        return list(proposed.values())

    def update_activity(self, activity_id: str, changes: dict) -> dict:
        return self.update_activities([{"id": activity_id, "changes": changes}])[0]

    async def revise(self, scope: str = "global", reason: str = "") -> dict:
        result = {"updated": [], "added": [], "cancelled": []}
        if not self.runtime.enabled("life") or not await scope_allowed(self.runtime, scope):
            return result
        async with self._plan_lock:
            now = self._now()
            editable = {
                a["id"]: a
                for a in self.list_activities()
                if a.get("date") == str(now.date())
                and a.get("scope") in {"global", scope}
                and self._editable(a, now)
            }
            if not editable:
                return result
            context = {
                "reason": reason,
                "now": now.isoformat(),
                "scope": scope,
                "memories": self._memories(scope),
                "editable": [self._view(a, scope) for a in editable.values()],
                "parameters": (
                    self.runtime.store.get("life_days", f"{now.date()}:global", {}) or {}
                ).get("parameters"),
            }
            data = parse_json(await self._complete("life.revise", REVISE_TEMPLATE, context, scope))
            if not self.runtime.enabled("life") or not await scope_allowed(self.runtime, scope):
                return result
            if not isinstance(data, dict) or not isinstance(data.get("updates", []), list):
                raise TypeError("日程调整响应必须包含 updates 数组")
            if data.get("additions") or data.get("cancel"):
                raise ValueError("局部调整不能新增或取消活动")
            updates = [
                u
                for u in data.get("updates", [])
                if isinstance(u, dict)
                and u.get("id") in editable
                and self.runtime.store.get("activities", u["id"]) == editable[u["id"]]
            ]
            if scope == "global":
                if updates:
                    result["updated"] = [a["id"] for a in self.update_activities(updates)]
            else:
                allowed = {"title", "content", "description", "incident", "location", "sleep_state"}
                prepared = []
                for update in updates:
                    changes = update.get("changes", {})
                    if (
                        not isinstance(changes, dict)
                        or set(changes) - allowed
                        or any(not isinstance(v, str) for v in changes.values())
                    ):
                        raise ValueError("私人场合调整只能修改活动说明，不改变公共时间或行动配额")
                    row = editable[update["id"]]
                    if not self._editable(row, self._now()):
                        continue
                    if row.get("scope") == scope:
                        row.update(changes)
                    else:
                        row.setdefault("scope_overrides", {}).setdefault(scope, {}).update(changes)
                    prepared.append(row)
                for row in prepared:
                    self.runtime.store.put("activities", row["id"], row)
                    result["updated"].append(row["id"])
            return result

    def _cancel_children(self, parent_id, now):
        for child in self.list_activities():
            if child.get("parent_id") == parent_id and child.get("status") == "planned":
                self._finish(child, "skipped", "parent_plan_changed", now)

    async def detail(self, activity_id: str) -> dict:
        return await self._detail(activity_id, self._now())

    async def _detail(self, activity_id, now):
        async with self._detail_lock:
            activity = self.runtime.store.get("activities", activity_id)
            if activity is None:
                raise KeyError(activity_id)
            if (
                not self.runtime.enabled("life")
                or activity.get("detailed")
                or activity.get("status") != "planned"
                or not await scope_allowed(self.runtime, activity["scope"])
            ):
                return activity
            context = {
                "activity": self._view(activity, activity["scope"]),
                "memories": self._memories(activity["scope"]),
            }
            if self.runtime.enabled("state"):
                context["state"] = self.state()
            data = parse_json(
                await self._complete("life.detail", DETAIL_TEMPLATE, context, activity["scope"])
            )
            if not self.runtime.enabled("life") or not await scope_allowed(
                self.runtime, activity["scope"]
            ):
                return activity
            latest = self.runtime.store.get("activities", activity_id)
            if latest != activity:
                return latest or activity
            if not isinstance(data, dict):
                raise TypeError("Activity detail must be an object.")
            activity["description"] = str(data.get("description", ""))
            activity["incident"] = (
                str(data.get("incident", "")) if activity["kind"] == "fiction" else ""
            )
            delta = data.get("energy_delta", 0)
            activity["energy_delta"] = (
                max(-10.0, min(10.0, float(delta)))
                if isinstance(delta, (int, float)) and math.isfinite(delta)
                else 0
            )
            activity.update(mood=str(data.get("mood", "")), detailed=True)
            self.runtime.store.put("activities", activity_id, activity)
            return activity

    def _finish(self, activity, status, reason="", now=None):
        activity.update(status=status, finished_at=self._now(now).isoformat())
        if reason:
            activity["reason"] = reason
        self.runtime.store.put("activities", activity["id"], activity)

    def _action_finish(self, activity, kind, status, reason, now, result=None):
        execution = {"status": status, "reason": reason, "finished_at": now.isoformat()}
        if result is not None:
            execution["result"] = result
        activity["actions"][kind]["execution"] = execution
        self.runtime.store.put("activities", activity["id"], activity)

    async def _run_flag(self, activity, kind, now):
        current_time = elapsed_clock(now)
        action, key = activity["actions"][kind], f"{activity['id']}:{kind}"
        if not self.runtime.enabled("proactive" if kind == "social" else kind):
            self._action_finish(activity, kind, "skipped", "module_disabled", now)
            return
        deadline = min(
            self._parse_time(activity["end"], now.date()),
            self._parse_time(action["at"], now.date())
            + timedelta(minutes=max(0, self._setting("stale_action_minutes", 10))),
        )
        remaining = (deadline - current_time()).total_seconds()
        if remaining <= 0:
            self._action_finish(
                activity, kind, "skipped", "execution_deadline_exceeded", current_time()
            )
            return
        if not self.runtime.store.claim("life_action_claims", key, {"started_at": now.isoformat()}):
            self._action_finish(
                activity, kind, "skipped", "previous_execution_may_have_started", now
            )
            return
        action["execution"] = {"status": "running", "started_at": now.isoformat()}
        self.runtime.store.put("activities", activity["id"], activity)
        payload = {
            "reason": action["intent"],
            "query": action["intent"],
            "intent": action["intent"],
            "activity_id": activity["id"],
            "planned": True,
            "activity": {
                f: activity.get(f, "")
                for f in ("title", "content", "description", "incident", "start", "end")
            },
        }
        try:
            result = await asyncio.wait_for(
                self.runtime.execute_action(kind, payload, activity["scope"], key),
                timeout=remaining,
            )
            if not isinstance(result, dict):
                raise TypeError("Action result must be an object.")
            status = (
                result.get("status")
                if result.get("status") in {"success", "failed", "skipped"}
                else "failed"
            )
            reason = result.get("reason") or (result.get("text") if status != "success" else "")
            if status == "skipped" and current_time() >= deadline:
                reason = "execution_deadline_exceeded"
            self._action_finish(activity, kind, status, str(reason or ""), current_time(), result)
        except TimeoutError:
            self._action_finish(
                activity, kind, "skipped", "execution_deadline_exceeded", current_time()
            )
        except asyncio.CancelledError:
            self._action_finish(
                activity, kind, "skipped", "execution_interrupted_outcome_unknown", current_time()
            )
            raise
        except Exception as exc:  # noqa: BLE001 - Subsequent action types remain independent.
            self._action_finish(activity, kind, "failed", str(exc), current_time())

    async def _run_action(self, activity, now):
        """Retain existing version-one actions without creating new legacy plans."""
        if not self.runtime.store.claim(
            "life_action_claims", activity["id"], {"started_at": now.isoformat()}
        ):
            self._finish(activity, "skipped", "previous_execution_may_have_started", now)
            return
        activity["status"] = "running"
        self.runtime.store.put("activities", activity["id"], activity)
        try:
            result = await self.runtime.execute_action(
                activity["kind"],
                copy.deepcopy(activity.get("payload", {})),
                activity["scope"],
                activity["id"],
            )
            if not isinstance(result, dict):
                raise TypeError("Action result must be an object.")
            activity["result"] = result
            self._finish(
                activity,
                {"success": "completed", "failed": "failed", "skipped": "skipped"}.get(
                    result.get("status"), "failed"
                ),
                str(result.get("reason", "")),
                now,
            )
        except asyncio.CancelledError:
            self._finish(activity, "skipped", "execution_interrupted_outcome_unknown", now)
            raise
        except Exception as exc:  # noqa: BLE001 - Legacy source failures remain isolated.
            self._finish(activity, "failed", str(exc), now)

    async def _advance(self, activity, now, *, restart=False, restart_at=None):
        current_time = elapsed_clock(now)
        restart_at = restart_at or now
        if (
            activity.get("needs_review")
            or activity.get("status") in FINAL_STATUSES
            or not await scope_allowed(self.runtime, activity["scope"])
        ):
            return
        now = current_time()
        start, end = (
            self._parse_time(activity["start"], now.date()),
            self._parse_time(activity["end"], now.date()),
        )
        modern = activity.get("schema_version") == 2
        if modern:
            for kind in ACTION_ORDER:
                action = activity["actions"][kind]
                status = action.get("execution", {}).get("status", "pending")
                if not action["enabled"] or status not in {"pending", "running"}:
                    continue
                at, reason = self._parse_time(action["at"], now.date()), ""
                if status == "running":
                    reason = "execution_interrupted_outcome_unknown"
                elif (
                    (restart and at < restart_at)
                    or now >= end
                    or now - at
                    > timedelta(minutes=max(0, self._setting("stale_action_minutes", 10)))
                ):
                    reason = "overdue_after_restart" if restart else "expired_action_window"
                if reason:
                    self._action_finish(activity, kind, "skipped", reason, now)
        elif activity["kind"] in ACTION_KINDS and activity.get("status") == "running":
            self._finish(activity, "skipped", "execution_interrupted_outcome_unknown", now)
            return
        if now >= end:
            completed = activity.get("status") == "running" and activity["kind"] == "fiction"
            self._finish(
                activity,
                "completed" if completed else "skipped",
                "" if completed else "expired",
                now,
            )
            return
        if (
            not modern
            and activity["kind"] in ACTION_KINDS
            and now - start > timedelta(minutes=max(0, self._setting("stale_action_minutes", 10)))
        ):
            self._finish(activity, "skipped", "expired_action_window", now)
            return
        if now < start - timedelta(minutes=max(0, self._setting("detail_minutes", 10))):
            return
        if not activity.get("detailed") and activity.get("status") == "planned":
            try:
                activity = await self._detail(activity["id"], now)
            except Exception as exc:  # noqa: BLE001 - Detail failures cannot block a validated plan.
                activity.update(detail_error=str(exc), detailed=True)
                self.runtime.store.put("activities", activity["id"], activity)
        if not self.runtime.enabled("life") or not await scope_allowed(
            self.runtime, activity["scope"]
        ):
            return
        now = current_time()
        if now < start:
            return
        if now >= end:
            if modern:
                for kind in ACTION_ORDER:
                    action = activity["actions"][kind]
                    if (
                        action["enabled"]
                        and action.get("execution", {}).get("status", "pending") == "pending"
                    ):
                        self._action_finish(activity, kind, "skipped", "expired_after_detail", now)
            self._finish(activity, "skipped", "expired_after_detail", now)
            return
        if not modern and activity["kind"] in ACTION_KINDS:
            if now - start > timedelta(minutes=max(0, self._setting("stale_action_minutes", 10))):
                self._finish(activity, "skipped", "expired_after_detail", now)
                return
            await self._run_action(activity, now)
            return
        if activity.get("status") == "planned":
            activity["status"] = "running"
            self.runtime.store.put("activities", activity["id"], activity)
            if self.runtime.store.claim(
                "life_fiction_claims", activity["id"], {"at": now.isoformat()}
            ):
                self.runtime.record_event(
                    f"角色虚构生活：{activity['title']}。{activity.get('incident', '')}",
                    scope=activity["scope"],
                    kind="event",
                    source="fiction",
                    key=f"life:{activity['id']}",
                )
                if activity["scope"] == "global" and self.runtime.enabled("state"):
                    changes = {
                        "energy": float(self.state()["energy"]) + activity.get("energy_delta", 0)
                    }
                    if activity.get("mood"):
                        changes["mood"] = activity["mood"]
                    self.update_state(changes)
        if modern:
            previous_action = False
            for kind in ACTION_ORDER:
                if not self.runtime.enabled("life") or not await scope_allowed(
                    self.runtime, activity["scope"]
                ):
                    return
                now = current_time()
                action = activity["actions"][kind]
                if (
                    action["enabled"]
                    and action.get("execution", {}).get("status", "pending") == "pending"
                ):
                    at = self._parse_time(action["at"], now.date())
                    if now >= end or now - at > timedelta(
                        minutes=max(0, self._setting("stale_action_minutes", 10))
                    ):
                        self._action_finish(
                            activity,
                            kind,
                            "skipped",
                            "expired_after_previous_action"
                            if previous_action
                            else "expired_before_action",
                            now,
                        )
                        continue
                    if at > now:
                        break
                    await self._run_flag(activity, kind, now)
                    previous_action = True
            now = current_time()
            if now >= end and activity.get("status") == "running":
                self._finish(activity, "completed", now=now)

    def day_summary(self, day: date | str | None = None) -> dict:
        day = date.fromisoformat(day) if isinstance(day, str) else day or self._now().date()
        rows, counts, next_social, now = self._day_activities(day), {}, None, self._now()
        for kind in ACTION_ORDER:
            enabled = [
                a["actions"][kind]
                for a in rows
                if a.get("actions", {}).get(kind, {}).get("enabled")
            ]
            counts[kind] = {
                "planned": len(enabled),
                "success": sum(a.get("execution", {}).get("status") == "success" for a in enabled),
                "skipped": sum(a.get("execution", {}).get("status") == "skipped" for a in enabled),
                "failed": sum(a.get("execution", {}).get("status") == "failed" for a in enabled),
                "reasons": [
                    a["execution"].get("reason", "")
                    for a in enabled
                    if a.get("execution", {}).get("status") in {"failed", "skipped"}
                ],
            }
        for row in rows:
            action = row.get("actions", {}).get("social", {})
            if (
                action.get("enabled")
                and action.get("execution", {}).get("status", "pending") == "pending"
                and self._parse_time(action["at"], day) >= now
            ):
                candidate = {
                    "activity_id": row["id"],
                    "title": row["title"],
                    "at": action["at"],
                    "intent": action["intent"],
                }
                if next_social is None or candidate["at"] < next_social["at"]:
                    next_social = candidate
        return {
            "date": str(day),
            "activity_count": len(rows),
            "counts": counts,
            "next_social": next_social,
            "generation": self.runtime.store.get("life_days", f"{day}:global", {}),
            "next_parameters": self.parameters(),
        }

    async def _revise_daily_scopes(self, now):
        """Apply remembered commitments to existing slots once per day and exact scope."""
        current_time, day = elapsed_clock(now), now.date()
        marker = self.runtime.store.get("life_days", f"{now.date()}:global", {}) or {}
        if (
            marker.get("schema_version") != 2
            or marker.get("status") != "completed"
            or not self.runtime.enabled("memory")
        ):
            return
        if not any(self._editable(a, now) for a in self._day_activities(now.date())):
            return
        scopes = {str(row.get("scope", "global")) for row in self.runtime.store.list("memories")}
        scopes.update(
            str(row.get("umo", ""))
            for row in self.runtime.settings.get("sessions", [])
            if row.get("enabled", True)
        )
        for scope in sorted(scopes - {"", "global"}):
            if not self.runtime.enabled("life") or not self.runtime.enabled("memory"):
                return
            if not await scope_allowed(self.runtime, scope):
                continue
            now = current_time()
            if now.date() != day or not any(
                self._editable(a, now) for a in self._day_activities(day)
            ):
                return
            key = f"{now.date()}:{scope}"
            record = self.runtime.store.get("life_scoped_revisions", key, {}) or {}
            if (
                record.get("status") == "completed"
                or record.get("retry_after", 0) > now.timestamp()
            ):
                continue
            if not any(
                m.get("scope") == scope and not m.get("profile") for m in self._memories(scope)
            ):
                continue
            record = {
                "id": key,
                "date": str(now.date()),
                "scope": scope,
                "status": "running",
                "started_at": now.isoformat(),
            }
            self.runtime.store.put("life_scoped_revisions", key, record)
            try:
                result = await self.revise(
                    scope, "检查先前记住的约定是否需要调整今天尚未开始的活动"
                )
                now = current_time()
                if (
                    not self.runtime.enabled("life")
                    or not self.runtime.enabled("memory")
                    or not await scope_allowed(self.runtime, scope)
                ):
                    record.update(
                        status="skipped",
                        reason="module_or_binding_changed",
                        retry_after=(now + timedelta(minutes=15)).timestamp(),
                    )
                else:
                    record.update(status="completed", result=result)
            except asyncio.CancelledError:
                now = current_time()
                record.update(
                    status="interrupted", retry_after=(now + timedelta(minutes=15)).timestamp()
                )
                self.runtime.store.put("life_scoped_revisions", key, record)
                raise
            except Exception as exc:  # noqa: BLE001 - One private model failure must not stop other scopes.
                now = current_time()
                record.update(
                    status="failed",
                    error=str(exc),
                    retry_after=(now + timedelta(minutes=15)).timestamp(),
                )
            self.runtime.store.put("life_scoped_revisions", key, record)

    async def tick(self, now: datetime | None = None) -> None:
        if not self.runtime.enabled("life") or self._tick_lock.locked():
            return
        async with self._tick_lock:
            now, restart = self._now(now), self._first_tick
            current_time = elapsed_clock(now)
            self._first_tick = False
            key = f"{now.date()}:global"
            marker = self.runtime.store.get("life_days", key, {}) or {}
            if marker.get("retry_after", 0) <= now.timestamp():
                try:
                    await self.plan_day(now)
                except Exception as exc:  # noqa: BLE001 - Invalid JSON cannot stop existing activities.
                    marker = self.runtime.store.get("life_days", key, {}) or {}
                    if marker.get("status") != "prepared":
                        marker.update(
                            status="failed",
                            error=str(exc),
                            retry_after=(now + timedelta(minutes=15)).timestamp(),
                        )
                    self.runtime.store.put("life_days", key, marker)
            for activity in self.list_activities():
                if not self.runtime.enabled("life"):
                    break
                try:
                    await self._advance(activity, current_time(), restart=restart, restart_at=now)
                except Exception as exc:  # noqa: BLE001 - Isolate malformed legacy records.
                    self.runtime.store.put(
                        "life_errors",
                        activity["id"],
                        {"id": activity["id"], "error": str(exc), "at": now.isoformat()},
                    )
            await self._revise_daily_scopes(current_time())
