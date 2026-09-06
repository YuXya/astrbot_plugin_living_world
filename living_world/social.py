"""Scoped proactive messages with persistent claims and shared rate controls."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import random
import re
from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .prompts import PROMPTS


def destination(scope: str) -> str:
    """Canonicalize a OneBot group conversation to its actual destination."""
    parts = scope.split(":", 2)
    if len(parts) != 3 or parts[1] not in {"GroupMessage", "FriendMessage"} or not all(parts):
        raise ValueError("A social destination must be an AstrBot OneBot UMO")
    target = parts[2].split("_")[-1] if parts[1] == "GroupMessage" else parts[2]
    if not target:
        raise ValueError("A social destination requires a target ID")
    return f"{parts[0]}:{parts[1]}:{target}"


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _parsed_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


class SocialService:
    """Coordinate all outgoing social activity through one asynchronous lock."""

    def __init__(self, runtime):
        self.runtime = runtime
        self._lock = asyncio.Lock()
        self.rng = random.Random()

    def _now(self) -> datetime:
        name = str(self.runtime.settings.get("character", {}).get("timezone", "Asia/Shanghai"))
        try:
            zone = ZoneInfo(name)
        except ZoneInfoNotFoundError:
            zone = (
                timezone(timedelta(hours=8))
                if name in {"Asia/Shanghai", "Asia/Hong_Kong", "Asia/Taipei"}
                else UTC
            )
        return datetime.now(zone)

    def _number(self, key: str, default: float, minimum: float = 0) -> float:
        try:
            value = float(self.runtime.settings.get("social", {}).get(key, default))
            return max(minimum, value) if math.isfinite(value) else default
        except (TypeError, ValueError):
            return default

    def _sessions(self) -> list[dict]:
        result = []
        for item in self.runtime.settings.get("sessions", []):
            if not isinstance(item, dict) or not item.get("enabled", True):
                continue
            try:
                scope = item["umo"]
                target = destination(scope)
                # A simple target entry uses the latest observed real conversation.
                if scope == target:
                    scope = self.runtime.store.get("session_contexts", target, {}).get(
                        "scope", scope
                    )
                weight = float(item.get("weight", 1))
            except (KeyError, TypeError, ValueError, AttributeError):
                continue
            if weight > 0 and math.isfinite(weight):
                result.append({"scope": scope, "destination": target, "weight": weight})
        return result

    def _quiet(self, now: datetime) -> bool:
        config = self.runtime.settings.get("social", {})
        start, end = str(config.get("quiet_start", "23:00")), str(config.get("quiet_end", "08:00"))
        if start == end:
            return False
        value = now.strftime("%H:%M")
        return start <= value < end if start < end else value >= start or value < end

    def _rate_reason(self, scope: str, now: datetime) -> str:
        target = destination(scope)
        attempts = [
            row
            for row in self.runtime.store.list("deliveries")
            if row.get("destination") == target
            and row.get("status") in {"pending", "sent", "unknown"}
            and row.get("attempted", True)
        ]
        times = [_parsed_time(row["created_at"]).astimezone(now.tzinfo) for row in attempts]
        if sum(at.date() == now.date() for at in times) >= self._number("daily_limit", 5):
            return "daily_limit"
        if times and (now - max(times)).total_seconds() < self._number("cooldown_minutes", 60) * 60:
            return "cooldown"
        return ""

    async def _control_reason(self, scope: str, interjection: bool) -> str:
        module = "interjection" if interjection else "proactive"
        if not self.runtime.enabled(module):
            return "module_disabled"
        if not any(row["destination"] == destination(scope) for row in self._sessions()):
            return "not_whitelisted"
        if not await self.runtime.scope_allowed(scope):
            return "persona_or_session_mismatch"
        # Settings can change while the host resolves the current persona.
        if not self.runtime.enabled(module):
            return "module_disabled"
        if not any(row["destination"] == destination(scope) for row in self._sessions()):
            return "not_whitelisted"
        if (
            ":GroupMessage:" in scope
            and interjection
            and self.runtime.host.host_interjection_enabled(scope)
        ):
            return "host_interjection_enabled"
        now = self._now()
        if self._quiet(now):
            return "quiet_hours"
        return self._rate_reason(scope, now)

    def _draw(self, candidates: list[dict], count: int) -> list[dict]:
        """Draw weighted entries, removing all aliases of each chosen destination."""
        candidates = list(candidates)
        selected = []
        while candidates and len(selected) < count:
            # Rescaling keeps finite configured weights from overflowing their sum.
            scale = max(item["weight"] for item in candidates)
            weights = [item["weight"] / scale for item in candidates]
            point = self.rng.random() * sum(weights)
            cumulative = 0.0
            chosen = candidates[-1]
            for item, weight in zip(candidates, weights):
                cumulative += weight
                if point < cumulative:
                    chosen = item
                    break
            selected.append(chosen)
            candidates = [
                item for item in candidates if item["destination"] != chosen["destination"]
            ]
        return selected

    @staticmethod
    def _result(
        status: str = "skipped", reason: str = "", deliveries: list[dict] | None = None
    ) -> dict:
        rows = deliveries or []
        sent = sum(item.get("status") == "sent" for item in rows)
        unknown = sum(item.get("status") == "unknown" for item in rows)
        if unknown:
            text = f"已确认发送 {sent} 条消息，另有 {unknown} 条发送结果未确认；不会自动重试。"
        else:
            text = (
                f"已向 {sent} 个会话发出主动消息；尚未收到回应。" if sent else "本次没有新增发送。"
            )
        # Detailed generated text stays in each target's scoped delivery record.
        return {
            "status": status,
            "text": text,
            "reason": reason,
            "deliveries": [
                {key: item.get(key) for key in ("id", "scope", "destination", "status", "reason")}
                for item in rows
            ],
        }

    @staticmethod
    def _person_id(scope: str) -> str:
        session = scope.split(":", 2)[-1]
        if ":FriendMessage:" in scope:
            return f"qq:{session}"
        return f"qq:{session.split('_', 1)[0]}" if "_" in session else ""

    def _duplicate_content(self, target: str, content_hash: str, now: datetime) -> bool:
        return any(
            row.get("destination") == target
            and row.get("content_hash") == content_hash
            and row.get("status") in {"pending", "sent", "unknown"}
            and row.get("attempted", True)
            and _parsed_time(row["created_at"]).astimezone(now.tzinfo).date() == now.date()
            for row in self.runtime.store.list("deliveries")
        )

    async def _deliver(
        self, target: dict, reason: str, action_id: str, interjection: bool, person_id: str = ""
    ) -> dict:
        scope = target["scope"]
        key = _digest(f"{action_id}\0{target['destination']}")
        previous = self.runtime.store.get("deliveries", key)
        if previous:
            return {**previous, "status": "skipped", "reason": "already_attempted"}
        now = self._now()
        record = {
            "id": key,
            "action_id": action_id,
            "scope": scope,
            "destination": target["destination"],
            "status": "skipped",
            "text": "",
            "content_hash": "",
            "attempted": False,
            "interjection": interjection,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "reason": "",
        }
        try:
            blocked = await self._control_reason(scope, interjection)
            if blocked:
                record["reason"] = blocked
                self.runtime.store.put("deliveries", key, record)
                return record
            history = (
                (await self.runtime.chat.history(scope, initialize=True))["text"]
                if hasattr(self.runtime, "chat")
                else await self.runtime.host.history(scope)
            )
            context = await self.runtime.context_text(
                scope, person_id=person_id or self._person_id(scope), query=reason
            )
            blocked = await self._control_reason(scope, interjection)
            if blocked:
                record["reason"] = blocked
                self.runtime.store.put("deliveries", key, record)
                return record
            template = PROMPTS["social.message"]
            data = {
                "reason": reason[:4000],
                "recent_messages": str(history)[-12000:],
                "context": context,
                "interjection": interjection,
            }
            if hasattr(self.runtime, "complete"):
                text = await self.runtime.complete(
                    "social.message", "social", template, data, scope
                )
            else:
                text = await self.runtime.generate(
                    "social", template + json.dumps(data, ensure_ascii=False), scope=scope
                )
            if not isinstance(text, str) or not text.strip():
                raise ValueError("Social generation returned no text")
            record["text"] = text.strip()[:2000]
            record["content_hash"] = _digest(" ".join(record["text"].casefold().split()))
            blocked = await self._control_reason(scope, interjection)
            now = self._now()
            if blocked or self._duplicate_content(
                target["destination"], record["content_hash"], now
            ):
                record["reason"] = blocked or "duplicate_content"
                record["updated_at"] = now.isoformat()
                self.runtime.store.put("deliveries", key, record)
                return record
            record.update(
                {
                    "status": "pending",
                    "attempted": True,
                    "created_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                }
            )
            if not self.runtime.store.claim("deliveries", key, record):
                return {**record, "status": "skipped", "reason": "already_attempted"}
            # The persistent claim precedes the transport await, so reload cannot replay it.
            if hasattr(self.runtime, "send_message"):
                sent = await self.runtime.send_message(
                    scope, record["text"], interjection=interjection
                )
            else:
                sent = await self.runtime.host.send(scope, record["text"])
            record["status"] = "sent" if sent else "failed"
            record["reason"] = "" if sent else "transport_rejected"
        except asyncio.CancelledError:
            record["status"] = "unknown" if record["attempted"] else "failed"
            record["reason"] = (
                "cancelled_during_send" if record["attempted"] else "cancelled_before_send"
            )
            record["updated_at"] = self._now().isoformat()
            self.runtime.store.put("deliveries", key, record)
            raise
        except Exception as exc:  # noqa: BLE001 - A failed transport has an uncertain delivery outcome.
            record["status"] = "unknown" if record["attempted"] else "failed"
            record["reason"] = (
                "transport_outcome_unknown"
                if record["attempted"]
                else "generation_or_context_failed"
            )
            record["error"] = type(exc).__name__
        record["updated_at"] = self._now().isoformat()
        self.runtime.store.put("deliveries", key, record)
        return record

    async def _send_locked(
        self,
        reason: str,
        scope: str,
        action_id: str,
        interjection: bool,
        target_scope: str | None,
        person_id: str = "",
    ) -> dict:
        module = "interjection" if interjection else "proactive"
        if not self.runtime.enabled(module):
            return self._result(reason="module_disabled")
        if scope != "global" and target_scope and target_scope != scope:
            return self._result(reason="source_scope_mismatch")
        target_scope = scope if scope != "global" else target_scope
        action_id = action_id or uuid4().hex
        action_key = _digest(action_id)
        existing = self.runtime.store.get("social_actions", action_key)
        if existing:
            return self._result(reason="already_attempted")
        candidates, blocked_reasons = [], []
        for item in self._sessions():
            if target_scope and item["destination"] != destination(target_scope):
                continue
            if target_scope:
                item = {**item, "scope": target_scope}
            try:
                blocked = await self._control_reason(item["scope"], interjection)
            except Exception:  # noqa: BLE001 - Isolate unavailable social destinations.
                blocked = "target_unavailable"
            if blocked:
                blocked_reasons.append(blocked)
            else:
                candidates.append(item)
        if not candidates:
            return self._result(
                reason=blocked_reasons[0] if blocked_reasons else "no_eligible_targets"
            )
        selected = self._draw(
            candidates, 1 if target_scope else min(20, int(self._number("target_count", 1, 1)))
        )
        action = {
            "id": action_key,
            "action_id": action_id,
            "scope": scope,
            "status": "pending",
            "targets": [item["scope"] for item in selected],
            "created_at": self._now().isoformat(),
        }
        if not self.runtime.store.claim("social_actions", action_key, action):
            return self._result(reason="already_attempted")
        deliveries = []
        try:
            for item in selected:
                deliveries.append(
                    await self._deliver(item, reason, action_id, interjection, person_id)
                )
        except asyncio.CancelledError:
            action.update({"status": "unknown", "updated_at": self._now().isoformat()})
            self.runtime.store.put("social_actions", action_key, action)
            raise
        sent = any(item["status"] == "sent" for item in deliveries)
        failed = any(item["status"] in {"failed", "unknown"} for item in deliveries)
        status = "success" if sent else "failed" if failed else "skipped"
        result = self._result(
            status,
            "" if sent else deliveries[0].get("reason", "") if deliveries else "no_delivery",
            deliveries,
        )
        action.update({"status": status, "result": result, "updated_at": self._now().isoformat()})
        self.runtime.store.put("social_actions", action_key, action)
        return result

    async def send(
        self,
        reason: str = "",
        scope: str = "global",
        action_id: str = "",
        interjection: bool = False,
        target_scope: str | None = None,
    ) -> dict:
        """Initiate social contact without widening the caller's trusted scope."""
        async with self._lock:
            return await self._send_locked(
                str(reason), scope, action_id, interjection, target_scope
            )

    async def interject(
        self, scope: str, message: str, person_id: str = "", event_id: str = ""
    ) -> dict:
        """Rate-limit group decisions before consulting the language model."""
        if ":GroupMessage:" not in scope:
            return self._result(reason="group_only")
        if self._lock.locked():
            return self._result(reason="social_busy")
        async with self._lock:
            try:
                blocked = await self._control_reason(scope, True)
                if blocked:
                    return self._result(reason=blocked)
                target = destination(scope)
                now = self._now()
                previous = self.runtime.store.get("social_interjections", target)
                if (
                    previous
                    and (now - _parsed_time(previous["at"])).total_seconds()
                    < self._number("interjection_interval_minutes", 30, 1) * 60
                ):
                    return self._result(reason="interjection_interval")
                event_id = event_id or uuid4().hex
                decision_key = _digest(f"{scope}\0{event_id}")
                decision = {
                    "id": decision_key,
                    "scope": scope,
                    "destination": target,
                    "at": now.isoformat(),
                    "event_id": event_id,
                }
                if not self.runtime.store.claim(
                    "social_interjection_events", decision_key, decision
                ):
                    return self._result(reason="event_already_considered")
                self.runtime.store.put("social_interjections", target, decision)
                history = (
                    (await self.runtime.chat.history(scope, initialize=True))["text"]
                    if hasattr(self.runtime, "chat")
                    else await self.runtime.host.history(scope)
                )
                context = await self.runtime.context_text(scope, person_id=person_id, query=message)
                blocked = await self._control_reason(scope, True)
                if blocked:
                    return self._result(reason=blocked)
                template = PROMPTS["social.interject"]
                data = {
                    "message": str(message)[-4000:],
                    "recent_messages": str(history)[-12000:],
                    "context": context,
                }
                if hasattr(self.runtime, "complete"):
                    response = await self.runtime.complete(
                        "social.interject", "social", template, data, scope
                    )
                else:
                    response = await self.runtime.generate(
                        "social", template + json.dumps(data, ensure_ascii=False), scope=scope
                    )
                raw = response.strip()
                if raw.startswith("```"):
                    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE).strip()
                data = json.loads(raw)
                if not isinstance(data, dict) or data.get("should_reply") is not True:
                    return self._result(reason="no_relevant_contribution")
                result = await self._send_locked(
                    f"自然加入当前群聊：{str(message)[-4000:]}",
                    scope,
                    f"interject:{decision_key}",
                    True,
                    scope,
                    person_id,
                )
                if result["status"] == "success":
                    for summary in result["deliveries"]:
                        if summary["status"] != "sent":
                            continue
                        delivery = self.runtime.store.get("deliveries", summary["id"])
                        self.runtime.record_event(
                            f"已向当前群聊发送：{delivery['text']}。发送成功，尚未收到回应。",
                            scope=scope,
                            source="action",
                            kind="social",
                            key=f"social:{delivery['id']}",
                        )
                return result
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - Keep interjection decisions independent.
                result = self._result("failed", "interjection_decision_failed")
                result["error"] = type(exc).__name__
                return result
