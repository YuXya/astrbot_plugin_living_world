"""Persistent, scope-aware daily life and action scheduling."""

from __future__ import annotations

import asyncio
import copy
import json
import math
import uuid
from datetime import UTC, date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ACTION_KINDS = frozenset(
    {"social", "news", "search", "weather", "bilibili", "bilibili_watch", "bilibili_recent"}
)
ACTIVITY_KINDS = ACTION_KINDS | {"fiction"}
FINAL_STATUSES = frozenset({"completed", "failed", "skipped"})


def character_timezone(settings: dict):
    """Use the configured civil time, including on Windows without tzdata."""
    name = str(settings.get("character", {}).get("timezone", "Asia/Shanghai"))
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        if name in {"Asia/Shanghai", "Asia/Hong_Kong", "Asia/Taipei"}:
            return timezone(timedelta(hours=8))
        return UTC


def parse_json(text: str) -> Any:
    """Accept plain JSON or a fenced JSON response, without evaluating code."""
    value = text.strip()
    if value.startswith("```"):
        lines = value.splitlines()
        if len(lines) > 2 and lines[-1].strip() == "```":
            value = "\n".join(lines[1:-1]).strip()
    return json.loads(value)


async def scope_allowed(runtime, scope: str) -> bool:
    """Recheck host bindings at asynchronous boundaries; fail closed on lookup errors."""
    checker = getattr(runtime, "scope_allowed", None)
    if checker is None:
        return True
    try:
        return bool(await checker(scope))
    except Exception:  # noqa: BLE001 - A binding lookup failure must not authorize an action.
        return False


class LifeService:
    """Keep plans separate from fiction and confirmed external actions."""

    def __init__(self, runtime):
        self.runtime = runtime
        self._tick_lock = asyncio.Lock()
        self._plan_lock = asyncio.Lock()
        self._detail_lock = asyncio.Lock()

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

    def state(self) -> dict:
        character = self.runtime.settings.get("character", {})
        defaults = {
            "energy": character.get("energy", 80),
            "mood": character.get("mood", "平静"),
            "routine": character.get("routine", ""),
            "updated_at": None,
        }
        defaults.update(self.runtime.store.get("life_state", "current", {}) or {})
        return copy.deepcopy(defaults)

    def update_state(self, changes: dict) -> dict:
        """Administrative adjustments never modify the bound persona."""
        allowed = {"energy", "mood", "routine"}
        if not isinstance(changes, dict) or set(changes) - allowed:
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
            key=lambda activity: (activity.get("start", ""), activity.get("id", "")),
        )

    def current(self, scope: str = "global") -> dict | None:
        now = self._now()
        matching = [
            activity
            for activity in self.list_activities()
            if activity.get("scope") in {"global", scope}
            and activity.get("status") in {"planned", "running"}
            and self._parse_time(activity["start"], now.date())
            <= now
            < self._parse_time(activity["end"], now.date())
        ]
        return matching[-1] if matching else None

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
        kind = data.get("kind", "fiction")
        if kind not in ACTIVITY_KINDS:
            raise ValueError("Unsupported activity kind.")
        title = str(data.get("title", "")).strip()
        if not title:
            raise ValueError("An activity requires a title.")
        start = self._parse_time(data.get("start", ""), day)
        end = self._parse_time(data.get("end", ""), day)
        if end <= start and "T" not in data.get("end", ""):
            end += timedelta(days=1)
        if start.date() != day or end <= start or end - start > timedelta(days=1):
            raise ValueError("An activity must start on its planned day and last at most a day.")
        payload = data.get("payload", {})
        if not isinstance(payload, dict):
            raise TypeError("Activity payload must be an object.")
        return {
            "id": key,
            "date": day.isoformat(),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "title": title,
            "kind": kind,
            "payload": copy.deepcopy(payload),
            "scope": scope,
            "status": "planned",
            "detailed": False,
            "created_at": self._now().isoformat(),
        }

    def _memories(self, scope: str) -> list[dict]:
        if not self.runtime.enabled("memory"):
            return []
        entries = self.runtime.memory.recall(query="", scope=scope, limit=12)
        return [entry for entry in entries if entry.get("scope", "global") in {"global", scope}]

    async def plan_day(self, now: datetime | None = None, scope: str = "global") -> list[dict]:
        """Generate one global outline or scoped additions based on commitments."""
        if not self.runtime.enabled("life") or not await scope_allowed(self.runtime, scope):
            return []
        now = self._now(now)
        day = now.date()
        day_key = f"{day.isoformat()}:{scope}"
        async with self._plan_lock:
            marker = self.runtime.store.get("life_days", day_key)
            if marker and marker.get("status") == "completed":
                return [
                    a
                    for a in self.list_activities()
                    if a.get("date") == day.isoformat() and a.get("scope") == scope
                ]
            memories = self._memories(scope)
            if scope != "global":
                memories = [
                    entry
                    for entry in memories
                    if entry.get("scope") == scope and not entry.get("profile")
                ]
            if scope != "global" and not memories:
                self.runtime.store.put(
                    "life_days",
                    day_key,
                    {"status": "completed", "date": day.isoformat(), "scope": scope},
                )
                return []
            count = max(
                1,
                min(
                    24,
                    int(
                        self._setting(
                            "max_activities" if scope == "global" else "scoped_max_activities",
                            12 if scope == "global" else 3,
                        )
                    ),
                ),
            )
            prompt = (
                "制定角色今天的轻量生活日程，只输出JSON对象。"
                '格式：{"activities":[{"start":"HH:MM","end":"HH:MM","title":"活动",'
                '"kind":"fiction","payload":{}}]}。'
                f"最多{count}项。日期{day.isoformat()}，现在{now.isoformat()}。"
                "kind仅限fiction/social/news/search/weather/bilibili/bilibili_watch/bilibili_recent。bilibili是搜索；bilibili_watch的query必须为已知视频BV号，不编造BV号；bilibili_recent读取公开旧见闻。fiction是角色虚构日常，真实阅读、搜索、社交必须用相应kind。"
                "可以安排上数学课觉得无聊、稍后找群聊聊天等自然活动；不要为了填满日程反复安排社交。"
                "social的payload使用reason/topic，search和bilibili使用query。不要指定其他会话目标；目标由社交能力处理。"
                "计划不等于发生，不编造真实搜索结果、观看内容或聊天回应。考虑精力、情绪、作息及与角色有关的约定。"
            )
            if scope != "global":
                prompt += "这是单独场合的附加安排，只处理下述记忆中今天需要兑现的明确约定，不复制整天日常；没有约定就返回空数组。不得把该场合的内容分享去其他场合。"
            context = {"scope": scope, "memories": memories}
            if self.runtime.enabled("state"):
                context["state"] = self.state()
            response = await self.runtime.generate(
                "life",
                prompt + "\n资料：" + json.dumps(context, ensure_ascii=False),
                scope=scope,
            )
            if not self.runtime.enabled("life") or not await scope_allowed(self.runtime, scope):
                return []
            data = parse_json(response)
            raw = data.get("activities") if isinstance(data, dict) else data
            if not isinstance(raw, list):
                raise TypeError("The daily plan must contain an activities array.")
            activities = []
            for index, item in enumerate(raw[:count]):
                key = uuid.uuid5(uuid.NAMESPACE_URL, f"living-world:plan:{day_key}:{index}").hex
                activities.append(self._make_activity(item, day, scope, key))
            for activity in activities:
                self.runtime.store.claim("activities", activity["id"], activity)
            self.runtime.store.put(
                "life_days",
                day_key,
                {"status": "completed", "date": day.isoformat(), "scope": scope},
            )
            return [
                a
                for a in self.list_activities()
                if a.get("date") == day.isoformat() and a.get("scope") == scope
            ]

    async def revise(self, scope: str = "global", reason: str = "") -> dict:
        """Adapt only future plans from fresh memories, preserving execution claims."""
        result = {"updated": [], "added": [], "cancelled": []}
        if not self.runtime.enabled("life") or not await scope_allowed(self.runtime, scope):
            return result
        async with self._plan_lock:
            now = self._now()
            editable = {
                activity["id"]: activity
                for activity in self.list_activities()
                if activity.get("scope") == scope
                and activity.get("date") == now.date().isoformat()
                and activity.get("status") == "planned"
                and self._parse_time(activity["start"], now.date()) > now
            }
            context = {
                "reason": reason,
                "now": now.isoformat(),
                "scope": scope,
                "memories": self._memories(scope),
                "editable": list(editable.values()),
            }
            prompt = (
                "根据新的聊天约定、见闻和当前有效记忆，必要时微调今天尚未执行的未来日程。不要重新生成整天日程。"
                '只输出JSON：{"updates":[{"id":"已有ID","changes":{"title":"活动","start":"HH:MM","end":"HH:MM","kind":"fiction","payload":{}}}],'
                '"additions":[{"start":"HH:MM","end":"HH:MM","title":"新增活动","kind":"fiction","payload":{}}],"cancel":[]}。'
                "没有必要就返回三个空数组。updates只能引用editable中的ID；cancel也只能包含这些ID，仅在新约定明确冲突时取消。"
                "新增和修改的开始时间必须在现在之后，kind只用fiction/social/news/search/weather/bilibili/bilibili_watch/bilibili_recent；观看的query只用已知BV号，不编造；"
                "新增项不要重复已有安排，不编造真实行动结果，不指定或更改场合。\n"
                + json.dumps(context, ensure_ascii=False)
            )
            data = parse_json(await self.runtime.generate("life", prompt, scope=scope))
            if not self.runtime.enabled("life") or not await scope_allowed(self.runtime, scope):
                return result
            if not isinstance(data, dict):
                raise TypeError("A plan revision must be an object.")
            if any(
                not isinstance(data.get(field, []), list)
                for field in ("updates", "additions", "cancel")
            ):
                raise TypeError("Revision updates, additions and cancel must be arrays.")
            allowed = {
                "title",
                "start",
                "end",
                "kind",
                "payload",
                "description",
                "incident",
            }
            updates = []
            for update in data.get("updates", [])[:12]:
                if not isinstance(update, dict) or update.get("id") not in editable:
                    continue
                activity = editable[update["id"]]
                changes = update.get("changes", {})
                if not isinstance(changes, dict) or set(changes) - allowed:
                    continue
                merged = self._make_activity(
                    {**activity, **changes}, now.date(), scope, activity["id"]
                )
                if self._parse_time(merged["start"], now.date()) <= now:
                    continue
                updates.append((activity, changes))
            additions = []
            for addition in data.get("additions", [])[
                : max(1, int(self._setting("scoped_max_activities", 3)))
            ]:
                key = uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"living-world:revision:{now.date()}:{scope}:{json.dumps(addition, sort_keys=True, ensure_ascii=False)}",
                ).hex
                activity = self._make_activity(addition, now.date(), scope, key)
                if self._parse_time(activity["start"], now.date()) > now:
                    additions.append(activity)
            for activity, changes in updates:
                if self.runtime.store.get("activities", activity["id"]) == activity:
                    self.update_activity(activity["id"], changes)
                    result["updated"].append(activity["id"])
            for activity in additions:
                if self.runtime.store.claim("activities", activity["id"], activity):
                    result["added"].append(activity["id"])
            for key in data.get("cancel", [])[:12]:
                if (
                    isinstance(key, str)
                    and key in editable
                    and key not in result["updated"]
                    and self.runtime.store.get("activities", key) == editable[key]
                ):
                    self._finish(editable[key], "skipped", "replaced_by_new_plan", now)
                    self._cancel_children(key, now)
                    result["cancelled"].append(key)
            return result

    def update_activity(self, activity_id: str, changes: dict) -> dict:
        """Only unexecuted plans can be edited; scope and execution state are immutable."""
        allowed = {
            "title",
            "start",
            "end",
            "kind",
            "payload",
            "description",
            "incident",
        }
        if not isinstance(changes, dict) or set(changes) - allowed:
            raise ValueError("Unsupported activity changes.")
        activity = self.runtime.store.get("activities", activity_id)
        if activity is None:
            raise KeyError(activity_id)
        if activity.get("status") != "planned":
            raise ValueError("Only planned activities can be edited.")
        merged = {**activity, **copy.deepcopy(changes)}
        validated = self._make_activity(
            merged, date.fromisoformat(activity["date"]), activity["scope"], activity_id
        )
        for field in ("title", "start", "end", "kind", "payload"):
            merged[field] = validated[field]
        for field in ("description", "incident"):
            if field in changes and not isinstance(changes[field], str):
                raise ValueError(f"{field} must be text.")
        merged["updated_at"] = self._now().isoformat()
        if set(changes) & {"title", "start", "end", "kind", "payload"}:
            merged["detailed"] = False
            for field in ("description", "incident", "mood", "energy_delta"):
                if field not in changes:
                    merged.pop(field, None)
            self._cancel_children(activity_id, self._now())
        self.runtime.store.put("activities", activity_id, merged)
        return merged

    def _cancel_children(self, parent_id: str, now: datetime) -> None:
        for child in self.list_activities():
            if child.get("parent_id") == parent_id and child.get("status") == "planned":
                self._finish(child, "skipped", "parent_plan_changed", now)

    async def detail(self, activity_id: str) -> dict:
        return await self._detail(activity_id, self._now())

    async def _detail(self, activity_id: str, now: datetime) -> dict:
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
            prompt = (
                "细化即将开始的一个生活活动，只输出JSON："
                '{"description":"简短细节","incident":"可选的小插曲或空串","energy_delta":0,"mood":"",'
                '"action":null}。可选action格式为{"kind":"social/search/news/weather/bilibili/bilibili_watch/bilibili_recent","payload":{},"title":"行动"}。观看视频必须使用资料中已知BV号作为payload.query。'
                "角色日常可有忘带笔等适量虚构小插曲，不能编造现实新闻、搜索结果或已发生的聊天。"
                "只有fiction活动可附加一个真实行动，例如上数学课无聊时找群聊；当前活动本来就是真实行动时不再附加。"
                "energy_delta范围-10到10。只使用给定场合资料，action不指定其他场合。"
            )
            context = {
                "activity": activity,
                "memories": self._memories(activity["scope"]),
            }
            if self.runtime.enabled("state"):
                context["state"] = self.state()
            response = await self.runtime.generate(
                "life",
                prompt + "\n" + json.dumps(context, ensure_ascii=False),
                scope=activity["scope"],
            )
            if not self.runtime.enabled("life") or not await scope_allowed(
                self.runtime, activity["scope"]
            ):
                return activity
            latest = self.runtime.store.get("activities", activity_id)
            if latest != activity:
                return latest or activity
            data = parse_json(response)
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
            activity["mood"] = str(data.get("mood", ""))
            activity["detailed"] = True
            self.runtime.store.put("activities", activity_id, activity)
            if activity["kind"] == "fiction":
                self._add_action(activity, data.get("action"), now, "detail")
            return activity

    def _add_action(self, parent: dict, action: Any, now: datetime, origin: str) -> dict | None:
        if not isinstance(action, dict) or action.get("kind") not in ACTION_KINDS:
            return None
        start = max(now, self._parse_time(parent["start"], now.date()))
        end = min(self._parse_time(parent["end"], now.date()), start + timedelta(minutes=5))
        if end <= start:
            return None
        key = f"{parent['id']}:{origin}"
        child = self._make_activity(
            {
                "title": action.get("title") or action["kind"],
                "kind": action["kind"],
                "payload": action.get("payload", {}),
                "start": start.isoformat(),
                "end": end.isoformat(),
            },
            start.date(),
            parent["scope"],
            key,
        )
        child.update({"parent_id": parent["id"], "detailed": True, "origin": origin})
        self.runtime.store.claim("activities", key, child)
        return child

    async def _consider(self, activity: dict, now: datetime) -> None:
        if not await scope_allowed(self.runtime, activity["scope"]):
            return
        delay = self._setting("spontaneous_minutes", 30)
        if delay <= 0 or now < self._parse_time(activity["start"], now.date()) + timedelta(
            minutes=delay
        ):
            return
        if self.runtime.store.get("activities", f"{activity['id']}:detail"):
            return
        if not self.runtime.store.claim(
            "life_considerations", activity["id"], {"at": now.isoformat()}
        ):
            return
        prompt = (
            "角色正在进行以下虚构生活活动。根据当前感受和已有记忆，决定此刻是否临时搜索、阅读或找人聊天。"
            '只输出JSON {"action":null}，确有自然理由才返回{"action":{"kind":"social/search/news/weather/bilibili/bilibili_watch/bilibili_recent",'
            '"title":"行动","payload":{}}}。不要为了行动而行动，不指定其他场合，不声称行动已完成。\n'
            + json.dumps(
                {
                    "activity": activity,
                    "now": now.isoformat(),
                    "memories": self._memories(activity["scope"]),
                },
                ensure_ascii=False,
            )
        )
        data = parse_json(await self.runtime.generate("life", prompt, scope=activity["scope"]))
        if (
            self.runtime.enabled("life")
            and isinstance(data, dict)
            and await scope_allowed(self.runtime, activity["scope"])
        ):
            self._add_action(activity, data.get("action"), now, "spontaneous")

    def _finish(
        self, activity: dict, status: str, reason: str = "", now: datetime | None = None
    ) -> None:
        activity["status"] = status
        activity["finished_at"] = self._now(now).isoformat()
        if reason:
            activity["reason"] = reason
        self.runtime.store.put("activities", activity["id"], activity)

    async def _run_action(self, activity: dict, now: datetime) -> None:
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
                copy.deepcopy(activity["payload"]),
                activity["scope"],
                activity["id"],
            )
            if not isinstance(result, dict):
                raise TypeError("Action result must be an object.")
            activity["result"] = result
            status = {
                "success": "completed",
                "failed": "failed",
                "skipped": "skipped",
            }.get(result.get("status"), "failed")
            self._finish(activity, status, str(result.get("reason", "")), now)
        except asyncio.CancelledError:
            self._finish(activity, "skipped", "execution_interrupted_outcome_unknown", now)
            raise
        except Exception as exc:  # noqa: BLE001 - External action failures must not stop the scheduler.
            activity["result"] = {"status": "failed", "text": "", "reason": str(exc)}
            self._finish(activity, "failed", "action_failed", now)

    async def _advance(self, activity: dict, now: datetime) -> None:
        if activity.get("needs_review"):
            return
        if activity.get("status") in FINAL_STATUSES or not await scope_allowed(
            self.runtime, activity["scope"]
        ):
            return
        start = self._parse_time(activity["start"], now.date())
        end = self._parse_time(activity["end"], now.date())
        if activity["kind"] in ACTION_KINDS and activity.get("status") == "running":
            self._finish(activity, "skipped", "execution_interrupted_outcome_unknown", now)
            return
        if now >= end:
            if activity["kind"] == "fiction" and activity.get("status") == "running":
                self._finish(activity, "completed", now=now)
            else:
                self._finish(activity, "skipped", "expired", now)
            return
        if activity["kind"] in ACTION_KINDS and now - start > timedelta(
            minutes=max(0, self._setting("stale_action_minutes", 10))
        ):
            self._finish(activity, "skipped", "expired_action_window", now)
            return
        if now < start - timedelta(minutes=max(0, self._setting("detail_minutes", 15))):
            return
        if not activity.get("detailed") and activity.get("status") == "planned":
            try:
                activity = await self._detail(activity["id"], now)
            except Exception as exc:  # noqa: BLE001 - A detail-provider failure must not stop the activity.
                activity["detail_error"] = str(exc)
                activity["detailed"] = True
                self.runtime.store.put("activities", activity["id"], activity)
        if (
            not self.runtime.enabled("life")
            or now < start
            or not await scope_allowed(self.runtime, activity["scope"])
        ):
            return
        if activity["kind"] in ACTION_KINDS:
            await self._run_action(activity, now)
            return
        if activity.get("status") == "planned":
            activity["status"] = "running"
            self.runtime.store.put("activities", activity["id"], activity)
            if self.runtime.store.claim(
                "life_fiction_claims", activity["id"], {"at": now.isoformat()}
            ):
                text = f"角色虚构生活：{activity['title']}。"
                if activity.get("incident"):
                    text += activity["incident"]
                self.runtime.record_event(
                    text,
                    scope=activity["scope"],
                    kind="event",
                    source="fiction",
                    key=f"life:{activity['id']}",
                )
                if activity["scope"] == "global" and self.runtime.enabled("state"):
                    state = self.state()
                    changes = {"energy": float(state["energy"]) + activity.get("energy_delta", 0)}
                    if activity.get("mood"):
                        changes["mood"] = activity["mood"]
                    self.update_state(changes)
        await self._consider(activity, now)

    async def tick(self, now: datetime | None = None) -> None:
        if not self.runtime.enabled("life") or self._tick_lock.locked():
            return
        async with self._tick_lock:
            now = self._now(now)
            scopes = {"global"}
            for session in self.runtime.settings.get("sessions", []):
                if (
                    isinstance(session, dict)
                    and session.get("enabled", True)
                    and session.get("umo")
                ):
                    scopes.add(str(session["umo"]))
            for scope in sorted(scopes, key=lambda value: (value != "global", value)):
                if not await scope_allowed(self.runtime, scope):
                    continue
                day_key = f"{now.date().isoformat()}:{scope}"
                marker = self.runtime.store.get("life_days", day_key, {}) or {}
                if marker.get("retry_after", 0) > now.timestamp():
                    continue
                try:
                    await self.plan_day(now, scope)
                except Exception as exc:  # noqa: BLE001 - Isolate daily planning failures by scope.
                    self.runtime.store.put(
                        "life_days",
                        day_key,
                        {
                            "status": "failed",
                            "error": str(exc),
                            "retry_after": (now + timedelta(minutes=15)).timestamp(),
                        },
                    )
            visited = set()
            # A second pass includes real actions proposed during activity details.
            for _ in range(2):
                for activity in self.list_activities():
                    if activity["id"] in visited or not self.runtime.enabled("life"):
                        continue
                    visited.add(activity["id"])
                    try:
                        await self._advance(activity, now)
                    except Exception as exc:  # noqa: BLE001 - One broken activity must not stop other activities.
                        self.runtime.store.put(
                            "life_errors",
                            activity["id"],
                            {
                                "id": activity["id"],
                                "error": str(exc),
                                "at": now.isoformat(),
                            },
                        )
