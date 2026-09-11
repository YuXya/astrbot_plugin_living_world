"""Write scoped journals from unified memories, then enqueue their original text."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, date, datetime

from .life import character_timezone, scope_allowed
from .context_usage import usage_for
from .layout import resolve_selection
from .prompts import PROMPTS


class JournalService:
    def __init__(self, runtime):
        self.runtime = runtime
        self._lock = asyncio.Lock()

    def list_entries(self, scope: str | None = None) -> list[dict]:
        entries = self.runtime.store.list("journals")
        if scope is not None:
            entries = [e for e in entries if e.get("scope", "global") in {"global", scope}]
        return sorted(
            entries, key=lambda e: (e.get("day", ""), e.get("created_at", 0)), reverse=True
        )

    def delete(self, entry_id: str) -> None:
        self.runtime.store.delete("journals", entry_id)
        self.runtime.memory.delete_source("journal:" + entry_id)

    def brief_request(self, entry: dict) -> dict:
        """Interpret historical brief drafts without reviving their generation pipeline."""
        return {
            "date": entry.get("day", ""),
            "kind": entry.get("kind", "journal"),
            "max_chars": 200,
            "document": entry.get("text", ""),
            "sources": entry.get("sources", []),
        }

    def _enqueue(self, entry):
        job = self.runtime.memory.enqueue_material(
            entry["text"],
            scope=entry.get("scope", "global"),
            source=entry["kind"],
            key="journal:" + entry["id"],
            occurred_at=entry.get("created_at"),
            persona_name=entry.get("persona_name"),
            sources=entry.get("sources", []),
            journal_day=entry["day"],
        )
        self.runtime.kick_memory()
        return job

    async def summarize(self, entry_id: str, *, regenerate=False) -> dict:
        """Legacy management action now feeds the shared extraction queue."""
        entry = self.runtime.store.get("journals", entry_id)
        if not entry:
            raise ValueError("日记或笔记已不存在")
        if not self.runtime.enabled(entry["kind"]) or not self.runtime.enabled("memory"):
            return {"status": "skipped", "reason": "module_disabled"}
        if not await scope_allowed(self.runtime, entry.get("scope", "global")):
            return {"status": "skipped", "reason": "scope_disabled"}
        return self._enqueue(entry)

    async def _complete(self, task, kind, data, scope):
        template = PROMPTS[task]
        if hasattr(self.runtime, "complete"):
            return (await self.runtime.complete(task, kind, template, data, scope)).strip()
        return (
            await self.runtime.generate(
                kind,
                template + json.dumps(data, ensure_ascii=False),
                scope=scope,
            )
        ).strip()

    async def generate(
        self, day: str = "", scope: str = "global", kind: str = "journal", topic: str = ""
    ) -> dict:
        kind = "notes" if kind == "note" else kind
        if kind not in {"journal", "notes"}:
            raise ValueError("Journal kind must be journal or notes.")
        if not self.runtime.enabled(kind) or not await scope_allowed(self.runtime, scope):
            return {"status": "skipped", "reason": "module_disabled"}
        if not self.runtime.enabled("memory"):
            return {"status": "skipped", "reason": "memory_disabled"}
        now = datetime.now(character_timezone(self.runtime.settings))
        day = date.fromisoformat(day or str(now.date())).isoformat()
        persona = self.runtime.settings.get("persona_id", "")
        key = uuid.uuid5(
            uuid.NAMESPACE_URL, f"living-world:journal-v2:{persona}:{kind}:{day}:{scope}"
        ).hex
        async with self._lock:
            existing = self.runtime.store.get("journals", key)
            if existing:
                return existing
            task = kind + ".write"
            selection = resolve_selection(self.runtime.settings, task)
            usage = usage_for(self.runtime.settings, selection)
            memories = await self.runtime.memory.select_context(
                scope=scope,
                query=topic
                or (
                    f"回顾{day}经历和交流"
                    if kind == "journal"
                    else "整理阅读所得的知识方法与学习体会"
                ),
                task=task,
                selection=selection,
                usage=usage,
                context_now=now,
                date=day if kind == "journal" else None,
                self_only=True,
            )
            rows = [*memories.get("recent_memories", []), *memories.get("memories", [])]
            if not rows:
                return {"status": "skipped", "reason": "no_memories"}
            data = {
                "date": day,
                "current_time": now.isoformat(),
                "kind": kind,
                "context_usage": usage,
                "context_selection": selection,
                **memories,
            }
            text = await self._complete(task, kind, data, scope)
            if (
                not self.runtime.enabled(kind)
                or not self.runtime.enabled("memory")
                or self.runtime.settings.get("persona_id") != persona
                or not await scope_allowed(self.runtime, scope)
            ):
                return {"status": "skipped", "reason": "configuration_changed"}
            if not text:
                raise ValueError("The journal response was empty.")
            entry = {
                "id": key,
                "day": day,
                "scope": scope,
                "kind": kind,
                "text": text,
                "persona_name": persona,
                "memory_status": "queued",
                "sources": [
                    {
                        "id": row["id"],
                        "source": row.get("source", ""),
                        "scope": row.get("scope", "global"),
                        "occurred_at": row.get("occurred_at"),
                        "sources": row.get("sources", []),
                    }
                    for row in rows
                ],
                "created_at": datetime.now(UTC).timestamp(),
            }
            with self.runtime.store.transaction():
                if not self.runtime.store.claim("journals", key, entry):
                    return self.runtime.store.get("journals", key)
                self._enqueue(entry)
            return entry
