"""Evidence-based, scope-preserving journals and reading notes."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, date, datetime

from .life import character_timezone, scope_allowed
from .prompts import PROMPTS


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
            if self.runtime.enabled("memory"):
                memories = [
                    entry
                    for entry in self.runtime.memory.recall(query="", scope=scope, limit=8)
                    if entry.get("scope", "global") in {"global", scope}
                    and (entry.get("profile") or entry.get("kind") in {"knowledge", "emotional"})
                    and not str(entry.get("source", "")).startswith("journal")
                ]
            template = PROMPTS["journal.write"]
            data = {
                "date": day,
                "kind": kind,
                "scope": scope,
                "events": events,
                "memories": memories,
            }
            if hasattr(self.runtime, "complete"):
                text = (
                    await self.runtime.complete(kind + ".write", kind, template, data, scope)
                ).strip()
            else:
                text = (
                    await self.runtime.generate(
                        kind, template + json.dumps(data, ensure_ascii=False), scope=scope
                    )
                ).strip()
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
                "sources": sources,
                "created_at": datetime.now(UTC).timestamp(),
            }
            if not self.runtime.store.claim("journals", key, entry):
                return self.runtime.store.get("journals", key)
            if self.runtime.enabled("memory"):
                label = "角色日记" if kind == "journal" else "见闻笔记"
                fiction = (
                    "（含明确标记的虚构角色经历）"
                    if any(source["fiction"] for source in sources)
                    else ""
                )
                self.runtime.memory.remember(
                    f"{label}{fiction} {day}：{text}",
                    kind="emotional" if kind == "journal" else "knowledge",
                    scope=scope,
                    source=kind,
                    key=f"journal:{key}",
                    sources=sources,
                )
            return entry
