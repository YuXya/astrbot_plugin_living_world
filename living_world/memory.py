"""Evidence-backed, scope-aware memory without external search dependencies."""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import math
import re
import unicodedata
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from .prompts import PROMPTS
from .context import brief_text, is_journal_memory, prepare_life_record, record_keys
from .context_catalog import MEMORY_DEFAULTS, memory_category
from .context_usage import usage_for

logger = logging.getLogger(__name__)

KINDS = frozenset({"knowledge", "event", "skill", "emotional"})
PROFILE_LABELS = {"name": "称呼", "interest": "稳定兴趣", "relationship": "关系"}
RELATIONSHIPS = frozenset({"朋友", "好友", "同学", "同事", "网友", "邻居", "师生", "家人", "伙伴"})
PRIVATE_MARKERS = re.compile(
    r"密码|密钥|住址|地址|电话|身份证|银行卡|诊断|病|药|抑郁|创伤|怀孕|"
    r"性取向|同性恋|异性恋|双性恋|跨性别|政治|宗教|信仰|秘密|私密|保密|"
    r"别告诉|不要告诉|不能告诉|不要记|别记|不要分享|仅限|只告诉|"
    r"(?:别|不要|不能).{0,8}(?:说|讲|透露|提|分享)|"
    r"今天|明天|昨天|今晚|下周|下个月|约定|见面|\d{2,}"
)


def _now(value: Any = None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, (float, int)):
        result = datetime.fromtimestamp(value, UTC)
    else:
        result = datetime.fromisoformat(str(value))
    return result.replace(tzinfo=UTC) if result.tzinfo is None else result.astimezone(UTC)


def _stamp(value: datetime) -> str:
    return value.isoformat(timespec="microseconds")


def _normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _terms(value: str) -> set[str]:
    result: set[str] = set()
    for part in re.findall(r"[a-z0-9_]+|[\u3400-\u9fff]+", _normalize(value)):
        if re.fullmatch(r"[a-z0-9_]+", part) or len(part) == 1:
            result.add(part)
        else:
            for size in (2, 3):
                result.update(part[index : index + size] for index in range(len(part) - size + 1))
    return result


class MemoryService:
    """Store memories using the runtime's synchronous namespace store."""

    namespace = "memories"

    def __init__(self, runtime: Any):
        self.runtime = runtime

    def _setting(self, name: str, default: float, minimum: float = 0) -> float:
        config = self.runtime.settings.get("memory", {})
        if not isinstance(config, dict):
            return default
        try:
            value = float(config.get(name, default))
            return max(minimum, value) if math.isfinite(value) else default
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _validate(text: str, kind: str, scope: str, person_id: str, profile: bool) -> str:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Memory text must not be empty")
        if len(text) > 8000:
            raise ValueError("Memory text exceeds 8000 characters")
        if kind not in KINDS:
            raise ValueError("Unsupported memory kind")
        if not isinstance(scope, str) or not scope.strip():
            raise ValueError("Memory scope must not be empty")
        if not isinstance(person_id, str):
            raise TypeError("Person ID must be a string")
        if profile and not person_id:
            raise ValueError("A profile requires a trusted person ID")
        return text.strip()

    def _strength(self, record: dict, now: datetime) -> float:
        strength = max(0.0, float(record.get("strength", 1.0)))
        if record.get("important"):
            return max(1.0, strength)
        anchor = _now(record.get("last_decay_at") or record["updated_at"])
        days = max(0.0, (now - anchor).total_seconds() / 86400)
        return strength * (0.5 ** (days / self._setting("half_life_days", 30, 1)))

    def _reinforce(self, record: dict, now: datetime) -> dict:
        record = dict(record)
        record["strength"] = min(
            5.0, self._strength(record, now) + self._setting("recall_boost", 0.2)
        )
        record["last_accessed_at"] = _stamp(now)
        record["last_decay_at"] = _stamp(now)
        record["access_count"] = int(record.get("access_count", 0)) + 1
        self.runtime.store.put(self.namespace, record["id"], record)
        return record

    def remember(
        self,
        text: str,
        *,
        kind: str = "event",
        scope: str = "global",
        person_id: str = "",
        profile: bool = False,
        important: bool = False,
        source: str = "",
        key: str | None = None,
        sources: list | None = None,
        source_event_id: str | None = None,
        occurred_at: str | None = None,
        journal_day: str | None = None,
    ) -> dict:
        """Remember a trusted caller's content without promoting its scope."""
        if not self.runtime.enabled("memory"):
            return {}
        if sources is not None and not isinstance(sources, list):
            raise ValueError("Memory source lineage must be a list")
        if profile and scope == "global":
            raise ValueError("Global profiles require evidence-backed reflect() extraction")
        metadata = {"sources": sources} if sources is not None else {}
        if source_event_id is not None:
            metadata["source_event_id"] = source_event_id
        if occurred_at is not None:
            metadata["occurred_at"] = occurred_at
        if journal_day is not None:
            metadata["journal_day"] = journal_day
        return self._remember(
            text,
            kind=kind,
            scope=scope,
            person_id=person_id,
            profile=profile,
            important=important,
            source=source,
            key=key,
            **metadata,
        )

    def _remember(
        self,
        text: str,
        *,
        kind: str,
        scope: str,
        person_id: str,
        profile: bool,
        important: bool,
        source: str,
        key: str | None = None,
        **metadata: Any,
    ) -> dict:
        text = self._validate(text, kind, scope, person_id, profile)
        now = _now()
        existing = self.runtime.store.get(self.namespace, str(key)) if key is not None else None
        if existing and any(
            existing.get(field, "") != value
            for field, value in (
                ("scope", scope),
                ("person_id", person_id),
                ("profile", profile),
            )
        ):
            raise ValueError("A memory key cannot replace another scope or identity")
        if key is None:
            existing = next(
                (
                    record
                    for record in self.runtime.store.list(self.namespace)
                    if record.get("scope") == scope
                    and record.get("person_id", "") == person_id
                    and record.get("profile", False) == profile
                    and record.get("kind") == kind
                    and _normalize(record["text"]) == _normalize(text)
                ),
                None,
            )
        record = dict(existing or {})
        record.update(
            {
                "id": existing["id"] if existing else str(key) if key is not None else uuid4().hex,
                "text": text,
                "kind": kind,
                "scope": scope,
                "person_id": person_id,
                "profile": bool(profile),
                "important": bool(important or record.get("important")),
                "source": str(source),
                "created_at": record.get("created_at", _stamp(now)),
                "updated_at": _stamp(now),
                "active": True,
                "last_decay_at": _stamp(now),
                "last_accessed_at": _stamp(now),
                "access_count": int(record.get("access_count", 0)),
                "strength": max(1.0, self._strength(record, now)) if existing else 1.0,
            }
        )
        record.pop("forgotten_at", None)
        record.update(metadata)
        if existing:
            record["strength"] = min(5.0, record["strength"] + self._setting("recall_boost", 0.2))
        self.runtime.store.put(self.namespace, record["id"], record)
        return dict(record)

    @staticmethod
    def _source_names(record: dict) -> list[str]:
        sources = [record.get("source", "")]
        lineage = record.get("sources", [])
        if isinstance(lineage, list):
            sources.extend(
                item.get("source", "") if isinstance(item, dict) else item for item in lineage
            )
        return list(dict.fromkeys(str(source) for source in sources if source))

    def _sources_enabled(self, record: dict) -> bool:
        checker = getattr(self.runtime, "_source_enabled", None)
        if not callable(checker):
            return True
        return all(checker(source) for source in self._source_names(record))

    def _visible(self, scope: str, person_id: str) -> list[dict]:
        records = []
        for record in self.runtime.store.list(self.namespace):
            if not record.get("active", True) or record.get("scope") not in {scope, "global"}:
                continue
            if not self._sources_enabled(record):
                continue
            owner = record.get("person_id", "")
            if record.get("profile") and (not person_id or owner != person_id):
                continue
            if record.get("scope") == "global" and owner and owner != person_id:
                continue
            if person_id and owner and owner != person_id:
                continue
            records.append(record)
        return records

    def recall(
        self,
        query: str = "",
        *,
        scope: str = "global",
        person_id: str = "",
        limit: int | None = None,
        reinforce: bool = True,
        context_now: datetime | None = None,
        include_journals: bool = True,
        usage: dict | None = None,
        exclude_keys: set | None = None,
    ) -> list[dict]:
        """Return and reinforce relevant memories visible in this exact context."""
        usage = copy.deepcopy(usage) if usage is not None else usage_for(self.runtime.settings)
        if not self.runtime.enabled("memory") or (limit is not None and limit <= 0):
            return []
        now = _now()
        local_now = context_now or now.astimezone(
            ZoneInfo(self.runtime.settings.get("character", {}).get("timezone", "Asia/Shanghai"))
        )
        query = _normalize(query)
        query_terms = _terms(query)
        scored = []
        for record in self._visible(scope, person_id):
            journal = is_journal_memory(record)
            category = memory_category(record)
            if not category or not usage["limits"][category] or (journal and not include_journals):
                continue
            projected = prepare_life_record(
                record,
                local_now,
                memory=True,
                event_lookup=lambda key: self.runtime.store.get("events", key),
            )
            if projected is None:
                continue
            if journal:
                projected["text"] = brief_text(
                    projected["text"], usage["brief_max_chars"][category]
                )
            normalized = _normalize(projected["text"])
            overlap = query_terms & _terms(normalized)
            phrase = bool(query and query in normalized)
            if query and not overlap and not phrase:
                continue
            relevance = sum(1 + min(len(term), 3) * 0.2 for term in overlap)
            freshness = 1 / (1 + max(0, (now - _now(record["updated_at"])).total_seconds()) / 86400)
            score = relevance + 3 * phrase + self._strength(record, now) * 0.15 + freshness * 0.1
            score += 0.15 if record.get("important") else 0
            scored.append((score, record["updated_at"], record, projected))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        results, seen = [], set(exclude_keys or ())
        counts = dict.fromkeys(MEMORY_DEFAULTS, 0)
        for _, _, record, projected in scored:
            category = memory_category(record)
            if counts[category] >= usage["limits"][category]:
                continue
            keys = record_keys(projected, local_now, memory=True)
            if keys & seen:
                continue
            seen.update(keys)
            result = self._reinforce(record, now) if reinforce else copy.deepcopy(record)
            for field in ("text", "occurred_at", "source_event_id", "source", "reading_basis"):
                if field in projected:
                    result[field] = projected[field]
            results.append(result)
            counts[category] += 1
            if limit is not None and len(results) >= int(limit):
                break
        return results

    def update(self, id: str, changes: dict) -> dict:
        """Allow administrative edits while preventing accidental scope widening."""
        existing = self.runtime.store.get(self.namespace, id)
        if not existing:
            raise KeyError(id)
        if not isinstance(changes, dict):
            raise TypeError("Memory changes must be an object")
        for field in (
            "id",
            "scope",
            "person_id",
            "profile",
            "profile_attribute",
            "profile_evidence",
        ):
            if field in changes and changes[field] != existing.get(field):
                raise ValueError(f"Memory {field} cannot be changed")
        allowed = {"text", "kind", "important", "source", "active"}
        record = {**existing, **{key: value for key, value in changes.items() if key in allowed}}
        record["text"] = self._validate(
            record["text"],
            record["kind"],
            record["scope"],
            record.get("person_id", ""),
            record.get("profile", False),
        )
        if (
            record.get("profile")
            and record["scope"] == "global"
            and record["text"] != existing["text"]
        ):
            raise ValueError("Global profile text requires new evidence-backed extraction")
        for flag in ("important", "active"):
            if not isinstance(record.get(flag, True), bool):
                raise TypeError(f"Memory {flag} must be a boolean")
        now = _now()
        record["updated_at"] = _stamp(now)
        record["last_decay_at"] = _stamp(now)
        record["strength"] = max(1.0, float(record.get("strength", 1.0)))
        if record.get("active", True):
            record.pop("forgotten_at", None)
            record["last_accessed_at"] = _stamp(now)
        self.runtime.store.put(self.namespace, id, record)
        return dict(record)

    def delete(self, id: str) -> None:
        """Delete a stored memory even when business use is disabled."""
        self.runtime.store.delete(self.namespace, id)

    def merge(self, ids: list[str], text: str) -> dict:
        """Combine memories into their common or more restrictive scope."""
        if not self.runtime.enabled("memory"):
            return {}
        ids = list(dict.fromkeys(ids))
        if len(ids) < 2:
            raise ValueError("At least two different memories are required")
        records = [self.runtime.store.get(self.namespace, id) for id in ids]
        if any(record is None for record in records):
            raise KeyError("A memory to merge was not found")
        restricted = {record["scope"] for record in records if record["scope"] != "global"}
        if len(restricted) > 1:
            raise ValueError("Memories from unrelated scopes cannot be merged")
        people = {record.get("person_id", "") for record in records if record.get("person_id")}
        if len(people) > 1:
            raise ValueError("Memories about different people cannot be merged")
        scope = next(iter(restricted), "global")
        if scope == "global" and any(record.get("profile") for record in records):
            raise ValueError("Global profiles cannot be replaced by unverified merged prose")
        record = self._remember(
            text,
            kind=records[0]["kind"],
            scope=scope,
            person_id=next(iter(people), ""),
            profile=False,
            important=any(item.get("important") for item in records),
            source="merged",
            key=uuid4().hex,
            merged_from=ids,
            sources=list(
                dict.fromkeys(source for item in records for source in self._source_names(item))
            ),
        )
        for id in ids:
            self.runtime.store.delete(self.namespace, id)
        return record

    def maintain(self, now: Any = None) -> dict:
        """Decay ordinary memories and archive stale ones without destroying them."""
        result = {"decayed": 0, "forgotten": 0, "retained": 0}
        if not self.runtime.enabled("memory"):
            return result
        now = _now(now)
        for existing in self.runtime.store.list(self.namespace):
            if not existing.get("active", True):
                continue
            record = dict(existing)
            record["strength"] = self._strength(record, now)
            record["last_decay_at"] = _stamp(now)
            if record.get("important"):
                result["retained"] += 1
            else:
                result["decayed"] += 1
                last_used = _now(record.get("last_accessed_at") or record["updated_at"])
                age = max(0, (now - last_used).total_seconds()) / 86400
                if age >= self._setting("forget_after_days", 180, 1) and record[
                    "strength"
                ] < self._setting("forget_threshold", 0.15):
                    record["active"] = False
                    record["forgotten_at"] = _stamp(now)
                    result["forgotten"] += 1
            self.runtime.store.put(self.namespace, record["id"], record)
        return result

    @staticmethod
    def _parse_response(raw: str) -> list:
        raw = raw.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE).strip()
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError):
            return []
        return (
            payload
            if isinstance(payload, list)
            else payload.get("memories", [])
            if isinstance(payload, dict) and isinstance(payload.get("memories"), list)
            else []
        )

    @staticmethod
    def _profile(candidate: dict, original: str, person_id: str) -> dict | None:
        attribute, value, evidence = (
            candidate.get(key) for key in ("profile_attribute", "value", "evidence")
        )
        if (
            not person_id
            or not isinstance(attribute, str)
            or attribute not in PROFILE_LABELS
            or not isinstance(value, str)
            or not isinstance(evidence, str)
        ):
            return None
        value, evidence = value.strip(), evidence.strip()
        if (
            not value
            or len(value) > 32
            or not evidence
            or evidence not in original
            or value not in evidence
        ):
            return None
        if PRIVATE_MARKERS.search(original) or re.search(r"[\n\r:：,，。！？!?;；]", value):
            return None
        escaped = re.escape(value)
        if attribute == "name":
            valid = re.search(
                r"(?:我叫|我的名字(?:叫|是)|叫我|称呼我)\s*[‘’“”\"']?" + escaped, evidence
            )
        elif attribute == "interest":
            valid = re.search(
                r"(?:我|本人)(?:平时|一直|长期|通常|很|非常|特别|最|比较|挺|都)*(?:喜欢|爱好|热爱|爱)\s*"
                + escaped,
                evidence,
            )
        else:
            valid = value in RELATIONSHIPS and re.search(
                r"(?:(?:我们|咱们|你和我|我和你)(?:一直|现在|已经|算)?(?:是|算是)|我是你的)\s*"
                + escaped,
                evidence,
            )
        if not valid:
            return None
        return {
            "text": f"{PROFILE_LABELS[attribute]}：{value}",
            "attribute": attribute,
            "value": value,
            "evidence": valid.group(0),
        }

    async def reflect(
        self, text: str, *, scope: str, person_id: str = "", source: str = ""
    ) -> list[dict]:
        """Extract grounded facts; all scope and identity decisions remain local."""
        if not self.runtime.enabled("memory") or not isinstance(text, str) or not text.strip():
            return []
        original = text[:16000]
        usage = usage_for(self.runtime.settings)
        now = _now().astimezone(
            ZoneInfo(self.runtime.settings.get("character", {}).get("timezone", "Asia/Shanghai"))
        )
        known = self.recall(
            scope=scope, person_id=person_id, reinforce=False, usage=usage, context_now=now
        )
        template = PROMPTS["memory.reflect"]
        data = {
            "current_time": now.isoformat(),
            "context_usage": usage,
            "known": known,
            "material": original,
        }
        try:
            if hasattr(self.runtime, "complete"):
                raw = await self.runtime.complete("memory.reflect", "memory", template, data, scope)
            else:
                raw = await self.runtime.generate(
                    "memory", template + json.dumps(data, ensure_ascii=False), scope=scope
                )
        except Exception:
            logger.warning("Memory extraction failed", exc_info=True)
            return []
        if not self.runtime.enabled("memory") or not isinstance(raw, str):
            return []
        results = []
        known_by_id = {record["id"]: record for record in known}
        limit = int(self._setting("reflection_limit", 8, 1))
        for candidate in self._parse_response(raw)[: min(limit, 20)]:
            if not isinstance(candidate, dict):
                continue
            evidence = candidate.get("evidence")
            if (
                not isinstance(evidence, str)
                or not evidence.strip()
                or evidence.strip() not in original
            ):
                continue
            profile = self._profile(candidate, original, person_id)
            if profile:
                identity = f"{person_id}\0{profile['attribute']}"
                if profile["attribute"] == "interest":
                    identity += "\0" + _normalize(profile["value"])
                key = "profile_" + hashlib.sha256(identity.encode()).hexdigest()[:32]
                record = self._remember(
                    profile["text"],
                    kind="knowledge",
                    scope="global",
                    person_id=person_id,
                    profile=True,
                    important=True,
                    source=source,
                    key=key,
                    profile_attribute=profile["attribute"],
                    profile_evidence=profile["evidence"],
                    origin_scope=scope,
                )
            else:
                content = candidate.get("text")
                if not isinstance(content, str) or not content.strip() or len(content) > 8000:
                    continue
                kind = candidate.get("kind", "event")
                kind = kind if isinstance(kind, str) and kind in KINDS else "event"
                important = candidate.get("important") is True
                previous = (
                    known_by_id.get(candidate.get("replace_id"))
                    if isinstance(candidate.get("replace_id"), str)
                    else None
                )
                if (
                    previous
                    and previous["scope"] == scope
                    and previous.get("person_id", "") == person_id
                    and not previous.get("profile")
                ):
                    current = self.runtime.store.get(self.namespace, previous["id"])
                    if not current or any(
                        current.get(field) != previous.get(field)
                        for field in ("updated_at", "text", "active")
                    ):
                        # A correction made while extraction awaited the model wins.
                        continue
                    record = self.update(
                        previous["id"],
                        {"text": content, "kind": kind, "important": important, "source": source},
                    )
                else:
                    record = self.remember(
                        content,
                        kind=kind,
                        scope=scope,
                        person_id=person_id,
                        important=important,
                        source=source,
                    )
            if record and record["id"] not in {item["id"] for item in results}:
                results.append(record)
        return results
