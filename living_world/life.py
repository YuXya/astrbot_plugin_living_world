"""Daily outlines, activity details, durable action starts and scoped refinements."""

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
from .life_actions import ActionLedger
from .context import (
    FICTION_NOTICE,
    activity_material,
    activity_text,
    clean_life_text,
    is_role_experience,
    prepare_life_records,
    record_text,
)

ACTION_ORDER = ("news", "search", "social")
FINAL_STATUSES = frozenset({"completed", "failed", "skipped", "cancelled"})
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


class LifeService(ActionLedger):
    def __init__(self, runtime):
        self.runtime = runtime
        self._tick_lock = asyncio.Lock()
        self._plan_lock = asyncio.Lock()
        self._detail_lock = asyncio.Lock()
        self._first_tick = True
        self.regenerating = False
        self._detailing = set()

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
            "daily_plan_time": self.runtime.settings.get("life", {}).get(
                "daily_plan_time", "06:00"
            ),
            "activity_count": int(self._setting("activity_count", 10)),
        }
        time.fromisoformat(result["daily_plan_time"])
        if not 1 <= result["activity_count"] <= 48:
            raise ValueError("每天活动数必须在 1 到 48 之间")
        return result

    async def _complete(self, task, template, context, scope="global", *, frozen_template=False):
        complete = getattr(self.runtime, "complete", None)
        if complete:
            if frozen_template:
                return await complete(
                    task, "life", template, context, scope=scope, frozen_template=True
                )
            return await complete(task, "life", template, context, scope=scope)
        return await self.runtime.generate(
            "life", template + "\n资料：" + json.dumps(context, ensure_ascii=False), scope=scope
        )

    def state(self) -> dict:
        character = self.runtime.settings.get("character", {})
        state = {
            "mood": character.get("mood", "平静"),
            "routine": character.get("routine", ""),
            "updated_at": None,
        }
        state.update(self.runtime.store.get("life_state", "current", {}) or {})
        state.pop("energy", None)
        current = self.current()
        for field in ("location", "sleep_state"):
            state[field] = (current or {}).get(field) or character.get(field) or "未知"
        return copy.deepcopy(state)

    def update_state(self, changes: dict) -> dict:
        if not isinstance(changes, dict) or set(changes) - {"mood", "routine"}:
            raise ValueError("Only mood and routine can be adjusted here.")
        state = self.state()
        for field in ("mood", "routine"):
            if field in changes:
                if not isinstance(changes[field], str):
                    raise ValueError(f"{field} must be text.")
                state[field] = changes[field]
        state["updated_at"] = self._now().isoformat()
        self.runtime.store.put("life_state", "current", state)
        return copy.deepcopy(state)

    def list_activities(self) -> list[dict]:
        retired = {row["id"] for row in self.runtime.store.list("life_retired_activities")}
        return sorted(
            (row for row in self.runtime.store.list("activities") if row["id"] not in retired),
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

    def schedule_context(self, scope: str = "global") -> dict:
        """Project today's visible schedule without exposing other scopes' overrides."""
        day = str(self._now().date())
        fields = (
            "id",
            "start",
            "end",
            "title",
            "description",
            "location",
            "sleep_state",
            "status",
            "kind",
        )
        rows = [
            self._view(row, scope)
            for row in self.list_activities()
            if row.get("date") == day and row.get("scope") in {"global", scope}
        ]
        return {
            "date": day,
            "status": "available" if rows else "missing",
            "notice": "这是角色今天的生活日程；计划不代表已发生。"
            if rows
            else "今天尚未生成可用日程，不代表角色没有日程能力。",
            "activities": [{key: row[key] for key in fields if key in row} for row in rows],
        }

    def _parse_time(self, value: str, day: date) -> datetime:
        if not isinstance(value, str):
            raise TypeError("Activity time must be an ISO datetime or HH:MM.")
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            parsed = datetime.combine(day, time.fromisoformat(value))
        return self._now(parsed)

    def _make_activity(self, data: dict, day: date, scope: str, key: str) -> dict:
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
        row = {
            "id": key,
            "date": str(day),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "title": title.strip(),
            "kind": "fiction",
            "scope": scope,
            "status": "planned",
            "detailed": False,
            "schema_version": 3,
            "drive_schema_version": 1,
            "created_at": self._now().isoformat(),
            "actions": self._empty_actions(),
        }
        for field in ("content", "location", "sleep_state"):
            value = data.get(field, "未知" if field != "content" else "")
            if not isinstance(value, str):
                raise TypeError(f"{field} must be text.")
            row[field] = value
        return row

    def _parse_actions(self, raw, activity):
        if not isinstance(raw, dict) or set(raw) != set(ACTION_ORDER):
            raise ValueError("细化结果必须包含 actions.news/search/social 三项决定")
        actions, previous = {}, None
        day = date.fromisoformat(activity["date"])
        start, end = (
            self._parse_time(activity["start"], day),
            self._parse_time(activity["end"], day),
        )
        for kind in ACTION_ORDER:
            item = raw[kind]
            if not isinstance(item, dict) or type(item.get("enabled")) is not bool:
                raise ValueError("行动 enabled 必须为布尔值")
            intent, reason = item.get("intent", ""), item.get("reason", "")
            if not isinstance(intent, str) or not isinstance(reason, str):
                raise ValueError("行动意图与理由必须为文本")
            at = None
            if item["enabled"]:
                if not intent.strip() or not reason.strip():
                    raise ValueError("启用行动必须提供执行意图与理由")
                at = self._parse_time(item.get("at"), day)
                if at < start and end.date() > start.date() and "T" not in item["at"]:
                    at += timedelta(days=1)
                if not start <= at < end or (previous and at < previous):
                    raise ValueError("行动时间必须位于活动内，并按新闻、搜索、聊天排序")
                previous = at
            elif item.get("at") is not None:
                raise ValueError("未安排的行动时间必须为空")
            actions[kind] = {
                "enabled": item["enabled"],
                "intent": intent.strip(),
                "reason": reason.strip(),
                "at": at.isoformat() if at else None,
                "execution": {"status": "pending" if item["enabled"] else "disabled"},
            }
        return actions

    def _memories(self, scope: str, *, reinforce=False, now=None) -> list[dict]:
        if not self.runtime.enabled("memory"):
            return []
        now = self._now(now)
        records = prepare_life_records(
            [
                e
                for e in self.runtime.memory.recall(
                    query="",
                    scope=scope,
                    limit=100 if scope != "global" else 12,
                    reinforce=reinforce,
                    context_now=now,
                )
                if e.get("scope", "global") in {"global", scope}
            ],
            now,
            memory=True,
            event_lookup=lambda key: self.runtime.store.get("events", key),
        )
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
        ordered = sorted(activities, key=lambda a: a["start"])
        if any(left["end"] > right["start"] for left, right in pairwise(ordered)):
            raise ValueError("日程活动不能重叠；同一活动内可包含多类行动")

    def freeze_parameters(self, now: datetime | None = None) -> dict:
        """Freeze only the daily outline settings before a configuration change."""
        now = self._now(now)
        key = f"{now.date()}:global"
        marker = self.runtime.store.get("life_days", key, {}) or {}
        if not marker and not self._day_activities(now.date()):
            marker = {
                "date": str(now.date()),
                "scope": "global",
                "schema_version": 3,
                "drive_schema_version": 1,
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
            "memories": [
                record_text(row, now, memory=True)
                for row in self._memories("global", reinforce=False, now=now)
            ],
            "经历说明": FICTION_NOTICE,
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
        if self.regenerating:
            return self._day_activities(now.date())
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
                            "schema_version": 3,
                            "drive_schema_version": 1,
                            "parameters": self.parameters(),
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
                "schema_version": 3,
                "drive_schema_version": 1,
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
                    )
                    for i, item in enumerate(raw)
                ]
                self._validate_day(rows, parameters)
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

    async def regenerate_day(self, day: str | None = None, scope: str = "global") -> list[dict]:
        """Replace today's plan atomically after generation, retaining the retired version."""
        if self.regenerating:
            raise ValueError("正在重新生成日程，请等待本次完成")
        now = self._now()
        if day and day != str(now.date()):
            raise ValueError("只能重新生成今天的日程；其他日期请使用调试试跑")
        if scope != "global":
            raise ValueError("正式日程只能在全局场合重新生成")
        self.regenerating = True
        try:
            parameters = copy.deepcopy(self.parameters())
            template = PLAN_TEMPLATE
            debug = getattr(self.runtime, "debug", None)
            if debug:
                template = debug.template("life.plan", template)
            binding = self.runtime.settings.get("persona_id")
            timezone_name = self.runtime.settings.get("character", {}).get("timezone")
            # Tick already takes plan/detail locks in this order. Wait for its work to finish.
            async with self._tick_lock, self._plan_lock, self._detail_lock:
                self._check_regeneration(now, binding, timezone_name)
                if not await scope_allowed(self.runtime, "global"):
                    raise ValueError("当前人格未接入，旧日程保持不变")
                request = self.plan_request(self._now())
                request["template"] = template
                request["context"]["parameters"] = parameters
                raw_json = await self._complete(
                    "life.plan", template, request["context"], frozen_template=True
                )
                data = parse_json(raw_json)
                raw = data.get("activities") if isinstance(data, dict) else None
                if not isinstance(raw, list):
                    raise TypeError("日程响应必须是包含 activities 数组的 JSON 对象")
                version = uuid.uuid4().hex
                rows = [
                    self._make_activity(
                        item,
                        now.date(),
                        "global",
                        uuid.uuid5(uuid.NAMESPACE_URL, f"living-world:plan:{version}:{i}").hex,
                    )
                    for i, item in enumerate(raw)
                ]
                self._validate_day(rows, parameters)
                if not await scope_allowed(self.runtime, "global"):
                    raise ValueError("人格接入已变化，旧日程保持不变")
                adopted_at = self._check_regeneration(now, binding, timezone_name)
                for row in rows:
                    row["plan_version"] = version
                marker = {
                    "date": str(now.date()),
                    "scope": "global",
                    "schema_version": 3,
                    "drive_schema_version": 1,
                    "status": "completed",
                    "version_id": version,
                    "origin": "manual_regeneration",
                    "parameters": parameters,
                    "request": request,
                    "raw_json": raw_json,
                    "full_request": copy.deepcopy(
                        getattr(self.runtime, "last_requests", {}).get(
                            ("life.plan", "global"), request
                        )
                    ),
                    "created_at": now.isoformat(),
                    "adopted_at": adopted_at.isoformat(),
                    "adopted_activities": copy.deepcopy(rows),
                }
                self._replace_day(marker, rows)
                return rows
        finally:
            self.regenerating = False

    def _check_regeneration(self, started, binding, timezone_name):
        if not self.runtime.enabled("life") or getattr(self.runtime, "stopped", False):
            raise ValueError("日程生活已关闭，旧日程保持不变")
        if (
            self.runtime.settings.get("persona_id") != binding
            or self.runtime.settings.get("character", {}).get("timezone") != timezone_name
        ):
            raise ValueError("人格或时区设置已变化，旧日程保持不变")
        current = self._now()
        if current.date() != started.date():
            raise ValueError("生成期间日期已变化，旧日程保持不变，请重新生成今天的日程")
        return current

    def _replace_day(self, marker, rows):
        day, store = marker["date"], self.runtime.store
        all_rows = self.list_activities()
        retired = {row["id"]: row for row in all_rows if row.get("date") == day}
        while True:
            children = {row["id"]: row for row in all_rows if row.get("parent_id") in retired}
            if children.keys() <= retired.keys():
                break
            retired.update(children)
        old = store.get("life_days", f"{day}:global", {}) or {}
        writes = [("activities", row["id"], row) for row in rows]
        if retired or old.get("status") in {"completed", "prepared"}:
            history_id = old.get("version_id") or uuid.uuid4().hex
            history = {
                **old,
                "id": history_id,
                "date": day,
                "scope": "global",
                "archived_at": marker["adopted_at"],
                "activities": list(retired.values()),
            }
            writes.append(("life_day_history", history_id, history))
        writes.append(("life_days", f"{day}:global", marker))
        # Backup merges can reintroduce old rows; retirement also survives those restores.
        writes.extend(
            ("life_retired_activities", key, {"id": key, "replaced_by": marker["version_id"]})
            for key in retired
        )
        store.apply_batch(writes, [("activities", key) for key in retired])

    def _editable(self, activity, now):
        return (
            activity.get("status") == "planned"
            and self._parse_time(activity["start"], date.fromisoformat(activity["date"])) > now
        )

    def _invalidate_detail(self, row):
        for field in (
            "description",
            "incident",
            "energy_delta",
            "mood",
            "detail_version",
            "detail_error",
            "detail_retry_after",
            "detail_request",
            "detail_raw",
        ):
            row.pop(field, None)
        row.update(detailed=False, detail_attempts=0, actions=self._empty_actions())

    def update_activities(self, updates: list[dict]) -> list[dict]:
        """Apply future-only edits atomically, invalidating decisions tied to changed outlines."""
        if self.regenerating:
            raise ValueError("正在重新生成日程，暂时不能编辑活动")
        if not isinstance(updates, list) or not updates:
            raise ValueError("需要待调整的活动列表")
        store, now = self.runtime.store, self._now()
        outlines = {"title", "start", "end", "content", "location", "sleep_state"}
        with store.transaction():
            proposed, writes, days = {}, [], set()
            for update in updates:
                if not isinstance(update, dict):
                    raise TypeError("日程调整必须为对象")
                key, changes = update.get("id"), update.get("changes", {})
                if key in self._detailing:
                    raise ValueError("该活动正在细化，请稍后编辑")
                if (
                    key in proposed
                    or not isinstance(changes, dict)
                    or set(changes) - outlines - {"actions"}
                ):
                    raise ValueError("活动调整包含不支持或重复的字段")
                old = store.get("activities", key)
                if not old or not self._editable(old, now) or old.get("schema_version") != 3:
                    raise ValueError("只能修改尚未开始的活动")
                outline = self._make_activity(
                    {**old, **changes}, date.fromisoformat(old["date"]), old["scope"], key
                )
                if not self._editable(outline, now):
                    raise ValueError("调整后的开始时间必须在现在之后")
                row = copy.deepcopy(old)
                row.update({f: outline[f] for f in outlines})
                changed = any(row[f] != old.get(f) for f in outlines)
                if changed:
                    writes.append(self._detail_archive(old, "outline_changed"))
                    self._invalidate_detail(row)
                elif "actions" in changes:
                    if not old.get("detailed"):
                        raise ValueError("请先细化活动，再调整行动")
                    actions = self._parse_actions(changes["actions"], row)
                    for kind in ACTION_ORDER:
                        previous = old["actions"][kind]
                        if previous.get("execution", {}).get("status") not in {
                            "pending",
                            "disabled",
                        }:
                            if any(
                                actions[kind][f] != previous.get(f)
                                for f in ("enabled", "at", "intent")
                            ):
                                raise ValueError("已经处理过的行动不能重新安排")
                            actions[kind]["execution"] = copy.deepcopy(previous["execution"])
                    writes.append(self._detail_archive(old, "actions_edited"))
                    row["actions"] = actions
                row["updated_at"] = now.isoformat()
                proposed[key] = row
                if row["scope"] == "global":
                    days.add(row["date"])
            for day in days:
                rows = [
                    proposed.get(row["id"], row)
                    for row in self._day_activities(date.fromisoformat(day))
                ]
                marker = store.get("life_days", f"{day}:global", {}) or {}
                self._validate_day(rows, marker.get("parameters") or {"activity_count": len(rows)})
            writes.extend(("activities", key, row) for key, row in proposed.items())
            store.apply_batch(writes)
        return list(proposed.values())

    def update_activity(self, activity_id: str, changes: dict) -> dict:
        return self.update_activities([{"id": activity_id, "changes": changes}])[0]

    async def revise(self, scope: str = "global", reason: str = "") -> dict:
        result = {"updated": [], "added": [], "cancelled": []}
        if (
            self.regenerating
            or not self.runtime.enabled("life")
            or not await scope_allowed(self.runtime, scope)
        ):
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
                "memories": [
                    record_text(row, now, memory=True) for row in self._memories(scope, now=now)
                ],
                "经历说明": FICTION_NOTICE,
                "editable": [activity_material(self._view(a, scope)) for a in editable.values()],
                "parameters": (
                    self.runtime.store.get("life_days", f"{now.date()}:global", {}) or {}
                ).get("parameters"),
            }
            data = parse_json(await self._complete("life.revise", REVISE_TEMPLATE, context, scope))
            if (
                self.regenerating
                or not self.runtime.enabled("life")
                or not await scope_allowed(self.runtime, scope)
            ):
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
                prepared, archives = [], []
                for update in updates:
                    changes = update.get("changes", {})
                    if (
                        not isinstance(changes, dict)
                        or set(changes) - allowed
                        or any(not isinstance(v, str) for v in changes.values())
                    ):
                        raise ValueError("私人场合调整只能修改活动说明，不改变公共时间或行动")
                    row = editable[update["id"]]
                    if not self._editable(row, self._now()):
                        continue
                    if row["id"] in self._detailing:
                        continue
                    if row.get("scope") == scope:
                        if any(row.get(f) != v for f, v in changes.items()):
                            archives.append(self._detail_archive(row, "scoped_outline_changed"))
                            self._invalidate_detail(row)
                        row.update(changes)
                    else:
                        override = row.setdefault("scope_overrides", {}).setdefault(scope, {})
                        if any(override.get(f, row.get(f)) != v for f, v in changes.items()):
                            override.update(description="", incident="")
                        override.update(changes)
                    prepared.append(row)
                self.runtime.store.apply_batch(
                    archives + [("activities", row["id"], row) for row in prepared]
                )
                result["updated"].extend(row["id"] for row in prepared)
            return result

    def detail_request(self, activity, instruction="", now=None):
        """Build the same read-only, scope-filtered material for production and dry runs."""
        now, scope = self._now(now), activity["scope"]
        day = date.fromisoformat(activity["date"])
        names = {"news": "新闻", "search": "搜索", "social": "主动聊天（轮）"}
        schedule = [
            activity_text(self._view(row, scope))
            for row in self.list_activities()
            if row["id"] != activity["id"]
            and row.get("date") == str(day)
            and row.get("scope") in {"global", scope}
        ]
        seen = set()
        event_rows = prepare_life_records(
            [
                row
                for row in self.runtime.store.list("events")
                if row.get("scope", "global") in {"global", scope}
                and row.get("source") in {"news", "search", "action"}
            ],
            now,
        )[:12]
        event_rows = prepare_life_records(event_rows, now, seen=seen)
        memory_rows = prepare_life_records(
            self._memories(scope, now=now), now, memory=True, seen=seen
        )
        memories = [record_text(row, now, memory=True) for row in memory_rows]
        events = [record_text(row, now) for row in event_rows]
        outcome_names = {"news": "新闻", "search": "搜索", "social": "主动聊天"}
        for result in self.runtime.store.list("actions"):
            if (
                result.get("scope", "global") in {"global", scope}
                and result.get("kind") in outcome_names
                and result.get("status") in {"failed", "skipped", "interrupted"}
            ):
                label = "失败" if result["status"] == "failed" else "跳过或中断，未确认成功"
                events.append(f"近期{outcome_names[result['kind']]}行动：{label}。")
                if len(events) >= 16:
                    break
        social = self.runtime.settings.get("social", {})
        context = {
            "当前时间": now.isoformat(),
            "待细化活动": activity_text(self._view(activity, scope)),
            "活动时间范围": f"{activity['start']} 至 {activity['end']}，结束时间不包含在执行范围内。",
            "当天其他安排": "\n".join(schedule) or "没有其他安排。",
            "相关记忆": "\n".join(memories) or "没有相关记忆。",
            "近期实际行动": "\n".join(events) or "没有可见的实际行动结果，不代表已执行计划。",
            "能力与限制": "；".join(
                f"{names[k]}{'已启用' if self.runtime.enabled('proactive' if k == 'social' else k) else '已关闭，不得安排'}"
                for k in ACTION_ORDER
            )
            + f"。聊天免打扰 {social.get('quiet_start', '23:00')}—{social.get('quiet_end', '08:00')}；"
            "对象在实际执行时由白名单抽取，并再次检查冷却及发送限制。",
        }
        if any(is_role_experience(row) for row in memory_rows):
            context["经历说明"] = FICTION_NOTICE
        if self.runtime.enabled("state"):
            state = self.state()
            context["角色状态与作息"] = f"心情：{state['mood']}；作息：{state['routine']}"
        thoughts = self.runtime.drives.thoughts()
        if thoughts:
            context["当前想法"] = thoughts
        if instruction:
            context["管理员本次要求"] = instruction
        return {"template": DETAIL_TEMPLATE, "context": context, "scope": scope}

    async def detail(self, activity_id: str, instruction="", regenerate=False) -> dict:
        if not isinstance(instruction, str) or len(instruction) > 4000:
            raise ValueError("本次要求须为不超过 4000 字的文本")
        if type(regenerate) is not bool:
            raise ValueError("重新细化开关必须为布尔值")
        if self.regenerating or activity_id in self._detailing:
            raise ValueError("正在生成或细化日程，请等待本次完成")
        activity = self.runtime.store.get("activities", activity_id)
        if not activity or not self._editable(activity, self._now()):
            raise ValueError("只能细化尚未开始的活动")
        return await self._detail(
            activity_id, self._now(), instruction=instruction, regenerate=regenerate
        )

    async def _detail(self, activity_id, now, *, instruction="", regenerate=False):
        if activity_id in self._detailing:
            raise ValueError("该活动正在细化，请等待本次完成")
        self._detailing.add(activity_id)
        current_time = elapsed_clock(now)
        try:
            async with self._detail_lock:
                activity = self.runtime.store.get("activities", activity_id)
                if not activity:
                    raise KeyError(activity_id)
                if (
                    self.regenerating
                    or not self.runtime.enabled("life")
                    or activity.get("schema_version") != 3
                    or (activity.get("detailed") and not regenerate)
                    or activity.get("status") != "planned"
                    or not await scope_allowed(self.runtime, activity["scope"])
                ):
                    return activity
                start = self._parse_time(activity["start"], date.fromisoformat(activity["date"]))
                if current_time() >= start:
                    raise ValueError("活动已经开始，不能采用新的细化结果")
                request = self.detail_request(activity, instruction, current_time())
                template = request["template"]
                if getattr(self.runtime, "debug", None):
                    template = self.runtime.debug.template("life.detail", template)
                request["template"] = template
                raw = await self._complete(
                    "life.detail",
                    template,
                    request["context"],
                    activity["scope"],
                    frozen_template=True,
                )
                data = parse_json(raw)
                if not isinstance(data, dict) or set(data) - {
                    "description",
                    "incident",
                    "mood",
                    "actions",
                }:
                    raise ValueError("细化只能返回活动细节、状态变化和三类行动决定")
                row = copy.deepcopy(activity)
                for field in ("description", "incident", "mood"):
                    if not isinstance(data.get(field, ""), str):
                        raise ValueError("活动细节必须为文本")
                    row[field] = data.get(field, "")
                row["actions"] = self._parse_actions(data.get("actions"), row)
                if not self.runtime.enabled("life") or not await scope_allowed(
                    self.runtime, activity["scope"]
                ):
                    raise ValueError("生活模块或接入场合已变化，保留原细化")
                adopted = max(current_time(), self._now())
                if adopted >= start:
                    raise ValueError("活动已经开始，保留原细化结果")
                store = self.runtime.store
                with store.transaction():
                    if self.regenerating or store.get("activities", activity_id) != activity:
                        raise ValueError("活动已变化，未采用过期细化结果")
                    for kind, action in row["actions"].items():
                        if action["enabled"] and not self.runtime.enabled(
                            "proactive" if kind == "social" else kind
                        ):
                            raise ValueError("行动模块已关闭，未采用细化结果")
                    row.update(
                        detailed=True,
                        detail_version=uuid.uuid4().hex,
                        detailed_at=adopted.isoformat(),
                        detail_request=request,
                        detail_raw=raw,
                    )
                    for field in ("detail_error", "detail_retry_after"):
                        row.pop(field, None)
                    writes = [("activities", activity_id, row)]
                    if activity.get("detailed"):
                        writes.append(self._detail_archive(activity, "regenerated"))
                    store.apply_batch(writes)
                return row
        finally:
            self._detailing.discard(activity_id)

    def _finish(self, activity, status, reason="", now=None):
        latest = self.runtime.store.get("activities", activity["id"])
        if latest is None:
            return
        activity.update(latest)
        activity.update(status=status, finished_at=self._now(now).isoformat())
        if reason:
            activity["reason"] = reason
        self.runtime.store.put("activities", activity["id"], activity)

    def _action_finish(self, activity, kind, status, reason, now, result=None):
        latest = self.runtime.store.get("activities", activity["id"])
        if latest is None:
            return
        activity.update(latest)
        execution = {"status": status, "reason": reason, "finished_at": now.isoformat()}
        if activity["actions"][kind].get("execution", {}).get("started_at"):
            execution["started_at"] = activity["actions"][kind]["execution"]["started_at"]
        if result is not None:
            execution["result"] = result
        activity["actions"][kind]["execution"] = execution
        self.runtime.store.put("activities", activity["id"], activity)

    async def _run_flag(self, activity, kind, now):
        current_time = elapsed_clock(now)
        activity = self.runtime.store.get("activities", activity["id"]) or activity
        action, key = activity["actions"][kind], self._action_key(activity, kind)
        if action.get("execution", {}).get("status") != "pending":
            return
        if not self.runtime.enabled("proactive" if kind == "social" else kind):
            self._action_finish(activity, kind, "skipped", "module_disabled", now)
            return
        deadline = min(
            self._parse_time(activity["end"], date.fromisoformat(activity["date"])),
            self._parse_time(action["at"], date.fromisoformat(activity["date"]))
            + timedelta(minutes=max(0, self._setting("stale_action_minutes", 10))),
        )
        remaining = (deadline - current_time()).total_seconds()
        if remaining <= 0:
            self._action_finish(
                activity, kind, "skipped", "execution_deadline_exceeded", current_time()
            )
            return
        if not self.runtime.store.claim(
            "life_action_claims", key, {"started_at": now.isoformat(), "schema_version": 3}
        ):
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

    async def _advance(self, activity, now, *, restart=False, restart_at=None):
        current_time = elapsed_clock(now)
        activity = self.runtime.store.get("activities", activity["id"]) or activity
        if (
            activity.get("schema_version") != 3
            or activity.get("needs_review")
            or activity.get("status") in FINAL_STATUSES
            or self.runtime.store.get("life_retired_activities", activity["id"])
            or not await scope_allowed(self.runtime, activity["scope"])
        ):
            return
        activity = self.runtime.store.get("activities", activity["id"])
        if not activity or activity.get("status") in FINAL_STATUSES or activity.get("needs_review"):
            return
        day = date.fromisoformat(activity["date"])
        start, end = (
            self._parse_time(activity["start"], day),
            self._parse_time(activity["end"], day),
        )
        restart_at = restart_at or now
        for kind in ACTION_ORDER:
            action = activity["actions"][kind]
            status = action.get("execution", {}).get("status")
            if not action["enabled"] or status not in {"pending", "running"}:
                continue
            at = self._parse_time(action["at"], day)
            if status == "running":
                self._action_finish(
                    activity,
                    kind,
                    "skipped",
                    "execution_interrupted_outcome_unknown",
                    current_time(),
                )
            elif (
                (restart and at < restart_at)
                or current_time() >= end
                or current_time() - at
                > timedelta(minutes=max(0, self._setting("stale_action_minutes", 10)))
            ):
                self._action_finish(
                    activity,
                    kind,
                    "skipped",
                    "overdue_after_restart" if restart else "expired_action_window",
                    current_time(),
                )
        if current_time() >= end:
            self._finish(
                activity,
                "completed" if activity.get("status") == "running" else "skipped",
                "expired",
                current_time(),
            )
            return
        if current_time() < start - timedelta(
            seconds=max(self._setting("detail_minutes", 10) * 60, self._setting("tick_seconds", 60))
        ):
            return
        if activity["id"] in self._detailing:
            return
        if (
            not activity.get("detailed")
            and activity.get("status") == "planned"
            and current_time() < start
            and activity.get("detail_attempts", 0) < 2
            and current_time().timestamp() >= activity.get("detail_retry_after", 0)
        ):
            activity["detail_attempts"] = activity.get("detail_attempts", 0) + 1
            self.runtime.store.put("activities", activity["id"], activity)
            try:
                activity = await self._detail(activity["id"], current_time())
            except Exception as exc:  # noqa: BLE001 - A failed detail does not cancel the outline.
                latest = self.runtime.store.get("activities", activity["id"])
                if latest == activity:
                    activity["detail_error"] = str(exc)
                    activity["detail_retry_after"] = current_time().timestamp() + 60
                    self.runtime.store.put("activities", activity["id"], activity)
        activity = self.runtime.store.get("activities", activity["id"]) or activity
        if (
            not self.runtime.enabled("life")
            or self.regenerating
            or not await scope_allowed(self.runtime, activity["scope"])
        ):
            return
        activity = self.runtime.store.get("activities", activity["id"])
        if not activity or activity.get("status") in FINAL_STATUSES or activity.get("needs_review"):
            return
        day = date.fromisoformat(activity["date"])
        start, end = (
            self._parse_time(activity["start"], day),
            self._parse_time(activity["end"], day),
        )
        now = current_time()
        if now >= end:
            for kind, action in activity["actions"].items():
                if action["enabled"] and action.get("execution", {}).get("status") == "pending":
                    self._action_finish(activity, kind, "skipped", "expired_after_detail", now)
            self._finish(activity, "skipped", "expired_after_detail", now)
            return
        if now < start:
            return
        if activity.get("status") == "planned":
            activity["status"] = "running"
            self.runtime.store.put("activities", activity["id"], activity)
            if self.runtime.store.claim(
                "life_fiction_claims", activity["id"], {"at": now.isoformat()}
            ):
                self.runtime.record_event(
                    clean_life_text(f"{activity['title']}。{activity.get('incident', '')}"),
                    scope=activity["scope"],
                    kind="event",
                    source="fiction",
                    key=f"life:{activity['id']}",
                    occurred_at=now.isoformat(),
                )
                if activity["scope"] == "global" and self.runtime.enabled("state"):
                    changes = {}
                    if activity.get("mood"):
                        changes["mood"] = activity["mood"]
                    self.update_state(changes)
        for kind in ACTION_ORDER:
            activity = self.runtime.store.get("activities", activity["id"]) or activity
            if not self.runtime.enabled("life") or not await scope_allowed(
                self.runtime, activity["scope"]
            ):
                return
            activity = self.runtime.store.get("activities", activity["id"]) or activity
            now, action = current_time(), activity["actions"][kind]
            if action["enabled"] and action.get("execution", {}).get("status") == "pending":
                at = self._parse_time(action["at"], day)
                if now >= end or now - at > timedelta(
                    minutes=max(0, self._setting("stale_action_minutes", 10))
                ):
                    self._action_finish(activity, kind, "skipped", "expired_before_action", now)
                    continue
                if at > now:
                    break
                await self._run_flag(activity, kind, now)
        if current_time() >= end:
            self._finish(activity, "completed", now=current_time())

    def day_summary(self, day: date | str | None = None) -> dict:
        day = date.fromisoformat(day) if isinstance(day, str) else day or self._now().date()
        rows, counts, next_social, now = self._day_activities(day), {}, None, self._now()
        current_activities = self.list_activities()
        starts = self.runtime.store.list("life_action_usage")
        for kind in ACTION_ORDER:
            enabled = [
                a["actions"][kind]
                for a in current_activities
                if a.get("actions", {}).get(kind, {}).get("enabled")
                and a["actions"][kind].get("at")
                and self._parse_time(a["actions"][kind]["at"], day).date() == day
            ]
            counts[kind] = {
                "arranged": len(enabled),
                "started": sum(
                    item.get("kind") == kind and item.get("date") == str(day) for item in starts
                ),
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
        for kind, values in self.day_results(day).items():
            counts[kind].update(values)
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
            self.regenerating
            or marker.get("schema_version") != 3
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
            if marker.get("version_id"):
                key += f":{marker['version_id']}"
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
        if self.regenerating or not self.runtime.enabled("life") or self._tick_lock.locked():
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
