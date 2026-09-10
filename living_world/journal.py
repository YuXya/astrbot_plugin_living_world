"""Evidence-based, scope-preserving journals and reading notes."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import UTC, date, datetime

from .life import character_timezone, scope_allowed
from .context import brief_text
from .context_usage import brief_limit, usage_for
from .prompts import PROMPTS

logger = logging.getLogger(__name__)


class JournalService:
    def __init__(self, runtime):
        self.runtime = runtime
        self._lock = asyncio.Lock()

    def list_entries(self, scope: str | None = None) -> list[dict]:
        entries = self.runtime.store.list("journals")
        if scope is not None:
            entries = [
                entry for entry in entries if entry.get("scope", "global") in {"global", scope}
            ]
        return sorted(
            entries,
            key=lambda entry: (entry.get("day", ""), entry.get("created_at", 0)),
            reverse=True,
        )

    def delete(self, entry_id: str) -> None:
        self.runtime.store.delete("journals", entry_id)
        # Remove the derived copy as well so a deleted diary is not recalled later.
        self.runtime.memory.delete(f"journal:{entry_id}")

    def _event_day(self, event: dict) -> str:
        value = event.get("created_at", event.get("timestamp", event.get("time")))
        try:
            if isinstance(value, (int, float)):
                moment = datetime.fromtimestamp(value, tz=UTC)
            else:
                moment = datetime.fromisoformat(str(value))
                if moment.tzinfo is None:
                    moment = moment.replace(tzinfo=character_timezone(self.runtime.settings))
            return moment.astimezone(character_timezone(self.runtime.settings)).date().isoformat()
        except (TypeError, ValueError, OverflowError, OSError):
            return ""

    def brief_request(self, entry: dict) -> dict:
        return {
            "date": entry.get("day", ""),
            "kind": entry.get("kind", "journal"),
            "max_chars": brief_limit(self.runtime.settings, entry.get("kind", "journal")),
            "document": entry.get("text", ""),
            "sources": entry.get("sources", []),
        }

    async def _complete(self, task, kind, data, scope):
        template = PROMPTS[task]
        if hasattr(self.runtime, "complete"):
            return (await self.runtime.complete(task, kind, template, data, scope)).strip()
        return (
            await self.runtime.generate(
                kind, template + json.dumps(data, ensure_ascii=False), scope=scope
            )
        ).strip()

    async def summarize(self, entry_id: str, *, regenerate=False) -> dict:
        """Generate an explicit brief for an archived document, without rewriting its body."""
        async with self._lock:
            entry = self.runtime.store.get("journals", entry_id)
            if not entry:
                raise ValueError("日记或笔记已不存在")
            if entry.get("summary") and not regenerate:
                return entry
            return await self._summarize(entry)

    async def _summarize(self, entry: dict) -> dict:
        kind, scope, key = entry["kind"], entry.get("scope", "global"), entry["id"]
        if not self.runtime.enabled(kind) or not await scope_allowed(self.runtime, scope):
            return {"status": "skipped", "reason": "module_disabled"}
        data = self.brief_request(entry)
        try:
            summary = brief_text(
                await self._complete(kind + ".brief", kind, data, scope), data["max_chars"]
            )
            if not summary:
                raise ValueError("模型未生成有效简报")
        except Exception:
            logger.warning(
                "Journal brief generation failed; archived text is retained", exc_info=True
            )
            # A failed regeneration must keep the previous usable brief.
            current = self.runtime.store.get("journals", key)
            if current == entry and not current.get("summary"):
                current.update(summary_status="failed")
                self.runtime.store.put("journals", key, current)
            return {**(current or {}), "status": "partial", "reason": "brief_failed"}
        if not self.runtime.enabled(kind) or not await scope_allowed(self.runtime, scope):
            return {"status": "skipped", "reason": "module_disabled"}
        with self.runtime.store.transaction():
            if self.runtime.store.get("journals", key) != entry:
                return {"status": "skipped", "reason": "entry_changed"}
            updated = {
                **entry,
                "summary": summary,
                "summary_status": "ready",
                "summary_at": datetime.now(UTC).timestamp(),
            }
            self.runtime.store.put("journals", key, updated)
            if self.runtime.enabled("memory"):
                self.runtime.memory.remember(
                    summary,
                    kind="emotional" if kind == "journal" else "knowledge",
                    scope=scope,
                    source=kind + ":brief",
                    key=f"journal:{key}",
                    sources=entry.get("sources", []),
                    journal_day=entry["day"],
                )
        return updated

    async def generate(self, day: str = "", scope: str = "global", kind: str = "journal") -> dict:
        if kind == "note":
            kind = "notes"
        if kind not in {"journal", "notes"}:
            raise ValueError("Journal kind must be journal or notes.")
        if not self.runtime.enabled(kind) or not await scope_allowed(self.runtime, scope):
            return {"status": "skipped", "reason": "module_disabled"}
        day = day or datetime.now(character_timezone(self.runtime.settings)).date().isoformat()
        day = date.fromisoformat(day).isoformat()
        key = uuid.uuid5(uuid.NAMESPACE_URL, f"living-world:{kind}:{day}:{scope}").hex
        async with self._lock:
            existing = self.runtime.store.get("journals", key)
            if existing:
                return existing
            events = [
                event
                for event in self.runtime.store.list("events")
                if self._event_day(event) == day
                and event.get("scope", "global") in {"global", scope}
                and event.get("kind") not in {"plan", "planned", "schedule", "activity_plan"}
                and event.get("status", "success")
                not in {"planned", "running", "failed", "skipped"}
                and (event.get("text") or event.get("content"))
            ]
            enabled_source = getattr(self.runtime, "_source_enabled", lambda _: True)
            events = [event for event in events if enabled_source(event.get("source", ""))]
            if kind == "journal" and self.runtime.enabled("memory"):
                # Render current extracted facts instead of duplicating stale chat summaries.
                events.extend(
                    {
                        "id": "memory:" + row["id"],
                        "text": "交流中记下（约定不代表已兑现）：" + row["text"],
                        "scope": scope,
                        "kind": "interaction",
                        "source": "chat",
                        "created_at": row["created_at"],
                    }
                    for row in self.runtime.store.list("memories")
                    if row.get("active", True)
                    and not row.get("profile")
                    and row.get("scope") == scope
                    and row.get("source") in {"chat", "explicit"}
                    and self._event_day(row) == day
                )
            if kind == "notes":
                events = [event for event in events if event.get("source") != "fiction"]
            if not events:
                return {"status": "skipped", "reason": "no_events"}
            events = events[:80]
            memories = []
            usage = usage_for(self.runtime.settings)
            now = datetime.now(character_timezone(self.runtime.settings))
            if self.runtime.enabled("memory"):
                selected_usage = usage_for(self.runtime.settings)
                for category in selected_usage["limits"]:
                    if category not in {"memory.knowledge", "memory.emotional", "memory.profile"}:
                        selected_usage["limits"][category] = 0
                memories = [
                    entry
                    for entry in self.runtime.memory.recall(
                        query="",
                        scope=scope,
                        include_journals=False,
                        usage=selected_usage,
                        context_now=now,
                    )
                    if entry.get("scope", "global") in {"global", scope}
                    and (entry.get("profile") or entry.get("kind") in {"knowledge", "emotional"})
                    and not str(entry.get("source", "")).startswith("journal")
                ]
            data = {
                "date": day,
                "current_time": now.isoformat(),
                "context_usage": usage,
                "kind": kind,
                "scope": scope,
                "events": events,
                "memories": memories,
            }
            text = await self._complete(kind + ".write", kind, data, scope)
            if not self.runtime.enabled(kind) or not await scope_allowed(self.runtime, scope):
                return {"status": "skipped", "reason": "module_disabled"}
            if not text:
                raise ValueError("The journal response was empty.")
            sources = [
                {
                    "id": event.get("id", ""),
                    "source": event.get("source", ""),
                    "scope": event.get("scope", "global"),
                    "kind": event.get("kind", "event"),
                    "fiction": event.get("source") == "fiction",
                }
                for event in events
            ]
            entry = {
                "id": key,
                "day": day,
                "scope": scope,
                "kind": kind,
                "text": text,
                "summary_status": "pending",
                "sources": sources,
                "created_at": datetime.now(UTC).timestamp(),
            }
            if not self.runtime.store.claim("journals", key, entry):
                return self.runtime.store.get("journals", key)
            return await self._summarize(entry)
