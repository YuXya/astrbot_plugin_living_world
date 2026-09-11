"""Unified, evidence-backed profiles and lexical retrieval."""

from __future__ import annotations
import asyncio
import copy
import hashlib
import json
import logging
import math
import re
import time
import unicodedata
from collections import Counter
from contextlib import nullcontext
from datetime import UTC, datetime
from zoneinfo import ZoneInfo
from .memory_config import memory_settings
from .prompts import PROMPTS

logger = logging.getLogger(__name__)
_UNSET = object()
ATTRIBUTES = ("用户别名", "事实属性", "技能树", "关系图谱", "活跃项目")
KINDS = frozenset({"knowledge", "event", "skill", "emotional"})
PROFILE_LABELS = {"name": "用户别名", "interest": "事实属性", "relationship": "关系图谱"}
PRIVATE_MARKERS = re.compile(
    r"密码|密钥|住址|地址|电话|身份证|银行卡|诊断|病|药|抑郁|创伤|怀孕|性取向|同性恋|异性恋|双性恋|跨性别|政治|宗教|信仰|秘密|私密|保密|别告诉|不要告诉|不能告诉|不要记|别记|不要分享|仅限|只告诉|(?:别|不要|不能).{0,8}(?:说|讲|透露|提|分享)|今天|明天|昨天|今晚|下周|下个月|约定|见面|\d{2,}"
)


def _now(value=None):
    if value is None:
        return datetime.now(UTC)
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, (float, int)):
        result = datetime.fromtimestamp(value, UTC)
    else:
        result = datetime.fromisoformat(str(value))
    return result.replace(tzinfo=UTC) if result.tzinfo is None else result.astimezone(UTC)


def _stamp(value):
    return value.isoformat(timespec="microseconds")


def _normalize(value):
    return " ".join(unicodedata.normalize("NFKC", str(value)).casefold().split())


def _tokens(value):
    result = []
    for part in re.findall(r"[a-z0-9_]+|[\u3400-\u9fff]+", _normalize(value)):
        if re.fullmatch(r"[a-z0-9_]+", part) or len(part) == 1:
            result.append(part)
        else:
            for size in (2, 3):
                result.extend(part[index : index + size] for index in range(len(part) - size + 1))
    return result


def _terms(value):
    return set(_tokens(value))


def _digest(*values):
    return hashlib.sha256(
        json.dumps(values, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def _payload(raw):
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        raise ValueError("Memory response must be JSON")
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.IGNORECASE)
    value = json.loads(raw)
    if isinstance(value, list):
        return {"memories": value}
    if not isinstance(value, dict):
        raise ValueError("Memory response must be an object")
    return value


def _person(value):
    value = str(value or "").strip()
    match = re.fullmatch(r"(?:qq:)?([0-9]+)", value, flags=re.IGNORECASE)
    return "qq:" + str(int(match.group(1))) if match else value


class MemoryService:
    """Reads are pure; only final-response feedback strengthens memory."""

    namespace = "memories"

    def __init__(self, runtime):
        self.runtime = runtime
        self._processing = False
        self._clock = time.monotonic()
        self._clock_enabled = self._decay_enabled()

    @property
    def config(self):
        return memory_settings(self.runtime.settings.get("memory"))

    def _transaction(self):
        factory = getattr(self.runtime.store, "transaction", None)
        return factory() if callable(factory) else nullcontext()

    def _persona(self, name=None):
        return str(self.runtime.settings.get("persona_id", "") if name is None else name).strip()

    @staticmethod
    def _identity(person_id, persona_name):
        return "person:" + _person(person_id) if person_id else "self:" + _digest(persona_name)[:32]

    def _archive_profile(self, person_id, persona_name, name=""):
        identity = self._identity(person_id, persona_name)
        row = {
            "id": identity,
            "owner": "person" if person_id else "self",
            "person_id": _person(person_id),
            "persona_name": "" if person_id else persona_name,
            "name": name or (person_id if person_id else persona_name),
        }
        self.runtime.store.put("memory_profiles", identity, row)
        return identity

    def profiles(self):
        result = self.runtime.store.list("memory_profiles")
        name = self._persona()
        identity = self._identity("", name)
        if name and not any(row.get("id") == identity for row in result):
            result.append(
                {
                    "id": identity,
                    "owner": "self",
                    "person_id": "",
                    "persona_name": name,
                    "name": name,
                }
            )
        counts = Counter(
            row.get("identity")
            for row in self.runtime.store.list(self.namespace)
            if row.get("schema_version") == 2 and row.get("active", True)
        )
        for row in result:
            row.update(
                count=counts[row["id"]],
                current=(
                    row.get("owner") == "person" or row.get("persona_name") == self._persona()
                ),
            )
        return result

    @staticmethod
    def _source_names(record):
        sources = [record.get("source", "")]
        pending = list(record.get("sources", []))
        while pending:
            item = pending.pop()
            if isinstance(item, dict):
                sources.append(item.get("source", item.get("module", "")))
                pending.extend(item.get("sources", []))
            else:
                sources.append(item)
        return list(dict.fromkeys(str(source) for source in sources if source))

    def _sources_enabled(self, record):
        checker = getattr(self.runtime, "_source_enabled", None)
        return not callable(checker) or all(
            checker(source) for source in self._source_names(record)
        )

    @staticmethod
    def _keys(row):
        return {
            "memory:" + str(row.get("id", "")),
            "judgment:"
            + _digest(
                row.get("identity"),
                row.get("scope"),
                _normalize(row.get("judgment", row.get("text", ""))),
            ),
        }

    def _visible(self, scope, people=None, persona_name=None, date=None, self_only=False):
        people = set(people or ())
        persona_name = self._persona(persona_name)
        timezone = ZoneInfo(
            self.runtime.settings.get("character", {}).get("timezone", "Asia/Shanghai")
        )
        result = []
        for row in self.runtime.store.list(self.namespace):
            if row.get("schema_version") != 2 or not row.get("active", True):
                continue
            if row.get("scope") not in {"global", scope} or not self._sources_enabled(row):
                continue
            if row.get("owner") == "self":
                if row.get("persona_name") != persona_name:
                    continue
            elif self_only:
                continue
            if date:
                try:
                    if _now(row.get("occurred_at", "")).astimezone(
                        timezone
                    ).date().isoformat() != str(date):
                        continue
                except (ValueError, TypeError):
                    continue
            result.append(copy.deepcopy(row))
        return result

    @staticmethod
    def _rank(rows, query):
        terms = _terms(query)
        if not terms:
            return sorted(rows, key=lambda row: row.get("occurred_at") or "", reverse=True)
        documents = [
            Counter(
                _tokens(
                    row.get("judgment", row.get("text", "")) + " " + " ".join(row.get("tags", []))
                )
            )
            for row in rows
        ]
        # An explicit one-character query such as "猫" must also match prose.
        # Do not split every multi-character query into common single characters.
        single_terms = {term for term in terms if re.fullmatch(r"[\u3400-\u9fff]", term)}
        for row, document in zip(rows, documents):
            content = _normalize(
                row.get("judgment", row.get("text", "")) + " " + " ".join(row.get("tags", []))
            )
            for term in single_terms:
                count = content.count(term)
                if count:
                    document[term] = count
        avg = sum(sum(doc.values()) for doc in documents) / max(1, len(documents)) or 1
        frequencies = Counter(term for doc in documents for term in doc)
        scored = []
        for row, doc in zip(rows, documents):
            score = 0.0
            length = sum(doc.values())
            for term in terms & doc.keys():
                inverse = math.log(
                    1 + (len(rows) - frequencies[term] + 0.5) / (frequencies[term] + 0.5)
                )
                score += (
                    inverse * doc[term] * 2.2 / (doc[term] + 1.2 * (0.25 + 0.75 * length / avg))
                )
            if score:
                scored.append((score, row.get("occurred_at") or "", row))
        return [
            row for _, _, row in sorted(scored, key=lambda item: (item[0], item[1]), reverse=True)
        ]

    def recall(
        self,
        query="",
        *,
        scope="global",
        person_id="",
        limit=None,
        reinforce=False,
        context_now=None,
        include_journals=True,
        usage=None,
        exclude_keys=None,
        date=None,
        self_only=False,
        persona_name=None,
        people=None,
        **ignored,
    ):
        if not self.runtime.enabled("memory") or limit is not None and limit <= 0:
            return []
        persons = [_person(person_id)] if person_id else []
        persons.extend(self._people(people))
        rows = self._rank(self._visible(scope, persons, persona_name, date, self_only), str(query))
        result, seen = [], set(exclude_keys or ())
        for row in rows:
            if self._keys(row) & seen:
                continue
            seen.update(self._keys(row))
            result.append(row)
            if limit is not None and len(result) >= limit:
                break
        return result

    @staticmethod
    def _people(people):
        result = []
        for item in people or []:
            value = item.get("person_id", item.get("id", "")) if isinstance(item, dict) else item
            value = _person(value)
            if value and value not in result:
                result.append(value)
        return result

    async def _complete(self, task, data, scope):
        if hasattr(self.runtime, "complete"):
            return await self.runtime.complete(task, "memory", PROMPTS[task], data, scope)
        return await self.runtime.generate(
            "memory", PROMPTS[task] + json.dumps(data, ensure_ascii=False), scope=scope
        )

    async def select_context(
        self,
        scope,
        query="",
        person_id="",
        people=None,
        task="",
        selection=None,
        usage=None,
        context_now=None,
        semantic=True,
        date=None,
        self_only=False,
        persona_name=None,
    ):
        result = {"memories": [], "recent_memories": []}
        if not self.runtime.enabled("memory"):
            return result
        selected = set(selection if selection is not None else ("memory", "memory.recent"))
        limits = {"memory.self": 5, "memory.people": 5, "memory.related": 10, "memory.recent": 5}
        if usage is None:
            usage = self.runtime.settings.get("context_usage", {})
        limits.update(
            {
                key: max(0, int(value))
                for key, value in usage.get("limits", {}).items()
                if key in limits
            }
        )
        persons = list(
            dict.fromkeys(([_person(person_id)] if person_id else []) + self._people(people))
        )
        persons = persons[: max(0, int(usage.get("people_limit", 3)))]
        rows = self._visible(scope, persons, persona_name, date, self_only)
        seen = set()

        def adopt(items, count, target):
            for row in items:
                if not count:
                    break
                if seen & self._keys(row):
                    continue
                seen.update(self._keys(row))
                result[target].append(copy.deepcopy(row))
                count -= 1

        if "memory.recent" in selected:
            recent = [row for row in rows if row.get("owner") == "self" and row.get("occurred_at")]
            recent.sort(key=lambda row: _now(row["occurred_at"]), reverse=True)
            adopt(recent, limits["memory.recent"], "recent_memories")
        if "memory" not in selected:
            return result
        if not date:
            stable = [row for row in rows if row.get("stable")]
            stable.sort(key=lambda row: row.get("occurred_at") or "", reverse=True)
            adopt(
                [row for row in stable if row.get("owner") == "self"],
                limits["memory.self"],
                "memories",
            )
            for person in persons:
                adopt(
                    [row for row in stable if row.get("person_id") == person],
                    limits["memory.people"],
                    "memories",
                )
        if not limits["memory.related"]:
            return result
        search = "" if date else str(query)
        if rows and semantic and search.strip() and not str(task).startswith("memory."):
            try:
                timeout = self.config["query_timeout_seconds"]
                raw = await asyncio.wait_for(
                    self._complete("memory.query", {"query": search, "task": task}, scope),
                    timeout=timeout,
                )
                keywords = _payload(raw).get("keywords", [])
                if isinstance(keywords, str):
                    keywords = [keywords]
                if isinstance(keywords, list):
                    search += " " + " ".join(str(word)[:100] for word in keywords[:16])
            except Exception:
                logger.debug(
                    "Memory query expansion unavailable; using lexical query", exc_info=True
                )
        adopt(self._rank(rows, search), limits["memory.related"], "memories")
        return result

    def reinforce_sources(self, sources, scope):
        """Legacy calls have no effect; usefulness requires a final response."""

    def remember(
        self,
        text,
        *,
        kind="event",
        scope="global",
        person_id="",
        profile=False,
        important=False,
        source="",
        key=None,
        sources=None,
        source_event_id=None,
        occurred_at=_UNSET,
        journal_day=None,
        persona_name=None,
        attribute="事实属性",
        reasoning="",
        tags=None,
        stable=False,
        inferred=False,
        protected=False,
        owner_name="",
        **metadata,
    ):
        if not self.runtime.enabled("memory"):
            return {}
        # Settle existing records first so a new record never inherits earlier online time.
        self.maintain()
        text = self._validate_text(text)
        person_id = _person(person_id)
        if not isinstance(reasoning, str) or len(reasoning) > 8000:
            raise ValueError("事实依据格式无效或超过 8000 字符")
        if inferred and not reasoning.strip():
            raise ValueError("推断记忆必须提供事实依据")
        if attribute not in ATTRIBUTES:
            raise ValueError("记忆属性无效")
        if not isinstance(scope, str) or not scope or not isinstance(person_id, str):
            raise ValueError("记忆归属或场合无效")
        if sources is not None and not isinstance(sources, list):
            raise ValueError("记忆来源必须为列表")
        name = self._persona(persona_name)
        identity = self._identity(person_id, name)
        key = key or "memory:" + _digest(identity, scope, _normalize(text))[:32]
        previous = self.runtime.store.get(self.namespace, key)
        if previous and (previous.get("scope") != scope or previous.get("identity") != identity):
            raise ValueError("不能改变已有记忆的归属或场合")
        if self.runtime.store.get("memory_tombstones", key):
            return {}
        source_keys = list(
            dict.fromkeys(
                (previous or {}).get("source_keys", []) + list(metadata.pop("source_keys", []))
            )
        )
        if source_event_id:
            source_keys.append("event:" + str(source_event_id))
        if occurred_at is _UNSET:
            occurred_at = (previous or {}).get("occurred_at") or _stamp(_now())
        timestamp = ""
        if occurred_at:
            try:
                timestamp = _stamp(_now(occurred_at))
            except (ValueError, TypeError):
                pass
        now = _stamp(_now())
        record = {
            **(previous or {}),
            "id": key,
            "schema_version": 2,
            "version": int((previous or {}).get("version", 0)) + 1,
            "text": text,
            "judgment": text,
            "reasoning": str(reasoning)[:8000],
            "attribute": attribute,
            "tags": self._tags(tags),
            "identity": identity,
            "owner": "person" if person_id else "self",
            "owner_name": owner_name or (person_id if person_id else name),
            "persona_name": "" if person_id else name,
            "person_id": _person(person_id),
            "stable": bool(stable or profile),
            "inferred": bool(inferred),
            "scope": scope,
            "source": str(source),
            "sources": (sources or [])
            + (previous or {}).get("sources", [])
            + (
                [previous["source"]]
                if previous and previous.get("source") and previous["source"] != source
                else []
            ),
            "source_keys": list(dict.fromkeys(source_keys)),
            "occurred_at": timestamp,
            "active": True,
            "important": bool(important or (previous or {}).get("important")),
            "protected": bool(
                protected or source == "explicit" or (previous or {}).get("protected")
            ),
            "strength": (previous or {}).get("strength", self.config["initial_strength"]),
            "useful_score": (previous or {}).get("useful_score", 0.0),
            "decay_seconds": (previous or {}).get("decay_seconds", 0.0),
            "created_at": (previous or {}).get("created_at", now),
            "updated_at": now,
            "access_count": (previous or {}).get("access_count", 0),
        }
        if source_event_id:
            record["source_event_id"] = source_event_id
        if journal_day:
            record["journal_day"] = journal_day
        if metadata.get("evidence_type"):
            record["evidence_type"] = metadata["evidence_type"]
        if metadata.get("reading_basis"):
            record["reading_basis"] = metadata["reading_basis"]
        with self._transaction():
            if previous:
                self._version(previous)
            self._archive_profile(person_id, name, owner_name)
            self.runtime.store.put(self.namespace, key, record)
        return copy.deepcopy(record)

    _remember = remember

    @staticmethod
    def _validate_text(text):
        if not isinstance(text, str) or not text.strip() or len(text) > 8000:
            raise ValueError("记忆结论必须为 1—8000 字符")
        return text.strip()

    @staticmethod
    def _tags(tags):
        if tags is None:
            return []
        if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
            raise ValueError("记忆标签必须为文字列表")
        return list(dict.fromkeys(tag.strip()[:100] for tag in tags[:32] if tag.strip()))

    def _version(self, row):
        key = str(row["id"]) + ":" + str(row.get("version", 1))
        self.runtime.store.put(
            "memory_versions",
            key,
            {
                "id": key,
                "memory_id": row["id"],
                "version": row.get("version", 1),
                "record": copy.deepcopy(row),
            },
        )

    def versions(self, id):
        return sorted(
            [
                row
                for row in self.runtime.store.list("memory_versions")
                if row.get("memory_id") == id
            ],
            key=lambda row: row.get("version", 0),
            reverse=True,
        )

    def update(self, id, changes):
        row = self.runtime.store.get(self.namespace, id)
        if not row:
            raise KeyError(id)
        if not isinstance(changes, dict):
            raise ValueError("记忆修改必须为对象")
        for field in ("id", "scope", "identity", "owner", "person_id", "persona_name"):
            if field in changes and changes[field] != row.get(field):
                raise ValueError("不能修改记忆归属或扩大场合")
        result = copy.deepcopy(row)
        for key in (
            "text",
            "judgment",
            "reasoning",
            "attribute",
            "tags",
            "important",
            "active",
            "stable",
            "inferred",
        ):
            if key in changes:
                result[key] = changes[key]
        result["text"] = result["judgment"] = self._validate_text(
            changes.get("judgment", changes.get("text", row["text"]))
        )
        if result.get("attribute") not in ATTRIBUTES:
            raise ValueError("记忆属性无效")
        result["tags"] = self._tags(result.get("tags"))
        for key in ("important", "active", "stable", "inferred"):
            if not isinstance(result.get(key, False), bool):
                raise ValueError("记忆标志必须是布尔值")
        if (
            not isinstance(result.get("reasoning", ""), str)
            or len(result.get("reasoning", "")) > 8000
        ):
            raise ValueError("事实依据过长或格式无效")
        if result.get("inferred") and not result.get("reasoning", "").strip():
            raise ValueError("推断记忆必须提供事实依据")
        result.update(version=int(row.get("version", 1)) + 1, updated_at=_stamp(_now()))
        with self._transaction():
            self._version(row)
            self.runtime.store.put(self.namespace, id, result)
        return result

    def delete(self, id, _visited=None):
        """Remove all versions while retaining non-content tombstones."""
        visited = set() if _visited is None else _visited
        if id in visited:
            return
        visited.add(id)
        row = self.runtime.store.get(self.namespace, id)
        with self._transaction():
            for merged in self.runtime.store.list(self.namespace):
                if merged.get("replaced_by") == id and merged.get("id") != id:
                    self.delete(merged["id"], visited)
            self.runtime.store.put(
                "memory_tombstones",
                id,
                {
                    "id": id,
                    "deleted_at": _stamp(_now()),
                    "source_keys": (row or {}).get("source_keys", []),
                },
            )
            for key in (row or {}).get("source_keys", []):
                self.runtime.store.put(
                    "memory_source_tombstones", key, {"id": key, "deleted_at": _stamp(_now())}
                )
                for job in self.runtime.store.list("memory_jobs"):
                    if key in job.get("source_keys", []):
                        self.runtime.store.delete("memory_jobs", job["id"])
                for archived in self.runtime.store.list("memory_legacy"):
                    if archived.get("namespace") == self.namespace and archived.get("key") == key:
                        self.runtime.store.delete("memory_legacy", archived["id"])
            self.runtime.store.delete(self.namespace, id)
            for version in self.versions(id):
                self.runtime.store.delete("memory_versions", version["id"])
            for feedback in self.runtime.store.list("memory_feedback"):
                if id in feedback.get("memory_ids", []):
                    feedback["memory_ids"] = [key for key in feedback["memory_ids"] if key != id]
                    feedback["memories"] = [
                        item for item in feedback.get("memories", []) if item.get("id") != id
                    ]
                    self.runtime.store.put("memory_feedback", feedback["id"], feedback)

    def merge(self, ids, text):
        ids = list(dict.fromkeys(ids))
        if len(ids) < 2:
            raise ValueError("至少选择两条不同记忆")
        rows = [self.runtime.store.get(self.namespace, key) for key in ids]
        if any(not row for row in rows):
            raise KeyError("记忆不存在")
        if (
            len({row.get("identity") for row in rows}) != 1
            or len({row.get("scope") for row in rows}) != 1
        ):
            raise ValueError("只能合并同一归属和场合的记忆")
        with self._transaction():
            result = self.update(
                ids[0], {"text": text, "important": any(row.get("important") for row in rows)}
            )
            result["source_keys"] = list(
                dict.fromkeys(key for row in rows for key in row.get("source_keys", []))
            )
            result["protected"] = any(row.get("protected") for row in rows)
            result["sources"] = [item for row in rows for item in self._source_names(row)]
            self.runtime.store.put(self.namespace, result["id"], result)
            for row in rows[1:]:
                self._version(row)
                row.update(active=False, replaced_by=result["id"])
                self.runtime.store.put(self.namespace, row["id"], row)
        return result

    def enqueue_material(
        self,
        text,
        *,
        scope="global",
        source="",
        key=None,
        occurred_at=None,
        person_id="",
        sources=None,
        persona_name=None,
        people=None,
        migration=False,
        **metadata,
    ):
        """Persist source evidence; completion keeps only its digest and output IDs."""
        if not isinstance(text, str) or not text.strip():
            return {}
        original_key = str(key or "material:" + _digest(scope, source, text))
        digest = _digest(original_key, text)
        job_id = "material:" + _digest(original_key)
        done = self.runtime.store.get("memory_materials", job_id)
        if done or self.runtime.store.get("memory_source_tombstones", original_key):
            return done or {}
        existing = self.runtime.store.get("memory_jobs", job_id)
        if existing:
            return existing
        name = self._persona(persona_name)
        job = {
            "id": job_id,
            "key": original_key,
            "text": text,
            "digest": digest,
            "kind": "material",
            "scope": scope,
            "source": source,
            "sources": sources or [],
            "source_keys": list(dict.fromkeys([original_key] + metadata.get("source_keys", []))),
            "person_id": _person(person_id),
            "people": people or [],
            "persona_name": name,
            "occurred_at": occurred_at or "",
            "migration": bool(migration),
            "created_at": _stamp(_now()),
            "status": "pending",
            "attempts": 0,
            "retry_at": 0,
            "important": bool(metadata.get("important", False)),
            "protected": bool(metadata.get("protected", False)),
            "evidence_type": metadata.get("evidence_type", ""),
            "reading_basis": metadata.get("reading_basis", ""),
            "journal_day": metadata.get("journal_day", ""),
        }
        self.runtime.store.put("memory_jobs", job_id, job)
        return copy.deepcopy(job)

    def enqueue_chat(
        self,
        user_text,
        final_reply,
        *,
        scope,
        person_id="",
        people=None,
        recent_history=None,
        round_id=None,
        memory_ids=None,
        persona_name=None,
        occurred_at=None,
        memory_sources=None,
    ):
        if not self.runtime.enabled("memory") or not str(final_reply).strip():
            return {}
        round_id = str(round_id or _digest(scope, user_text, final_reply, occurred_at))
        job_id = "chat:" + _digest(scope, round_id)
        if self.runtime.store.get("memory_materials", job_id):
            return {}
        existing = self.runtime.store.get("memory_jobs", job_id)
        if existing:
            return existing
        name = self._persona(persona_name)
        material = {"user": str(user_text), "reply": str(final_reply), "person_id": person_id}
        job = {
            "id": job_id,
            "key": job_id,
            "kind": "chat",
            "round_id": round_id,
            "text": json.dumps(material, ensure_ascii=False),
            "source": "chat",
            "scope": scope,
            "person_id": _person(person_id),
            "people": people or [],
            "persona_name": name,
            "occurred_at": occurred_at or _stamp(_now()),
            "background": copy.deepcopy(recent_history or []),
            "source_keys": [job_id],
            "created_at": _stamp(_now()),
            "status": "pending",
            "attempts": 0,
            "retry_at": 0,
            "migration": False,
            "sources": [],
        }
        self.runtime.store.put("memory_jobs", job_id, job)
        if memory_sources or memory_ids:
            sources = memory_sources or (
                memory_ids if isinstance(memory_ids[0], dict) else [{"memory_ids": memory_ids}]
            )
            self.record_feedback(
                round_id, sources, final_reply, scope=scope, task="chat", query=user_text
            )
        return copy.deepcopy(job)

    def record_feedback(self, round_id, sources, final_reply, *, task="", scope="global", query=""):
        if not self.runtime.enabled("memory") or str(task).startswith("memory.") or not final_reply:
            return {}
        round_id = str(round_id)
        identity = "feedback:" + _digest(scope, round_id)
        previous = self.runtime.store.get("memory_feedback", identity)
        if previous or self.runtime.store.get("memory_feedback_done", identity):
            return previous or {}
        selected = {}
        for source in sources or []:
            if not isinstance(source, dict):
                continue
            snapshots = {
                row["id"]: row
                for row in source.get("memories", source.get("memory_snapshots", []))
                if isinstance(row, dict) and row.get("id")
            }
            versions = source.get("memory_versions", {})
            for key in source.get("memory_ids", []):
                row = snapshots.get(key) or self.runtime.store.get(self.namespace, key)
                if (
                    not row
                    or row.get("schema_version") != 2
                    or row.get("scope") not in {scope, "global"}
                ):
                    continue
                if (
                    isinstance(versions, dict)
                    and key in versions
                    and row.get("version") != versions[key]
                ):
                    continue
                selected[key] = copy.deepcopy(row)
        if not selected:
            return {}
        row = {
            "id": identity,
            "round_id": round_id,
            "task": task,
            "scope": scope,
            "query": str(query),
            "final_reply": str(final_reply),
            "memory_ids": list(selected),
            "memories": list(selected.values()),
            "created_at": _stamp(_now()),
        }
        self.runtime.store.put("memory_feedback", identity, row)
        return row

    def _apply_feedback(self, payload, rounds):
        entries = payload.get("feedback", [])
        if not isinstance(entries, list):
            return
        indexed = {row["round_id"]: row for row in rounds}
        for item in entries:
            if not isinstance(item, dict) or not isinstance(item.get("useful"), bool):
                continue
            original = indexed.get(str(item.get("round_id", "")))
            key = item.get("memory_id", item.get("id"))
            if not original or key not in original["memory_ids"]:
                continue
            marker = _digest(original["id"], key)
            if self.runtime.store.get("memory_feedback_applied", marker):
                continue
            snapshot = next((row for row in original["memories"] if row["id"] == key), {})
            current = self.runtime.store.get(self.namespace, key)
            if (
                not current
                or not current.get("active", True)
                or current.get("version") != snapshot.get("version")
            ):
                continue
            config = self.config
            if item["useful"]:
                current["strength"] += config["useful_strength"]
                current["useful_score"] += config["useful_score"]
                current["access_count"] += 1
            elif (
                config["forgetting_enabled"]
                and not current.get("important")
                and not current.get("protected")
                and config["medium_threshold"] <= current["useful_score"] < config["long_threshold"]
            ):
                current["strength"] -= config["useless_strength"]
            self.runtime.store.put(
                "memory_feedback_applied",
                marker,
                {
                    "id": marker,
                    "memory_id": key,
                    "round_id": original["round_id"],
                    "useful": item["useful"],
                },
            )
            if current["strength"] <= 0 and self._decay_enabled():
                self.delete(key)
            else:
                self.runtime.store.put(self.namespace, key, current)

    def _finish_feedback(self, rounds):
        for row in rounds:
            self.runtime.store.put(
                "memory_feedback_done", row["id"], {"id": row["id"], "round_id": row["round_id"]}
            )
            self.runtime.store.delete("memory_feedback", row["id"])

    def _eligible_batches(self):
        jobs = sorted(
            self.runtime.store.list("memory_jobs"), key=lambda row: row.get("created_at", "")
        )
        chats, batches = {}, []
        status = self.migration_status()
        for job in jobs:
            if (
                job.get("retry_at", 0) > time.time()
                or job.get("migration")
                and status.get("paused")
            ):
                continue
            if not self._sources_enabled(job):
                continue
            if not job.get("persona_name"):
                name = status.get("persona_name") if job.get("migration") else self._persona()
                if not name:
                    continue
                job["persona_name"] = name
                self.runtime.store.put("memory_jobs", job["id"], job)
            if job.get("kind") == "chat":
                chats.setdefault((job["scope"], job["persona_name"]), []).append(job)
            else:
                batches.append([job])
        config = self.config
        for jobs in chats.values():
            idle = (_now() - _now(jobs[-1]["created_at"])).total_seconds() >= config[
                "chat_idle_seconds"
            ]
            while len(jobs) >= config["chat_batch_rounds"] or jobs and idle:
                size = min(len(jobs), config["chat_batch_rounds"])
                batches.append(jobs[:size])
                jobs = jobs[size:]
        return batches

    async def process_pending(self):
        result = {"processed": 0, "failed": 0, "memories": 0}
        if self._processing or not self.runtime.enabled("memory"):
            return result
        self._processing = True
        try:
            self.migrate()
            for batch in self._eligible_batches()[:20]:
                if not self.runtime.enabled("memory"):
                    break
                try:
                    rows = await self._extract_batch(batch)
                    if rows is None:
                        continue
                    result["processed"] += len(batch)
                    result["memories"] += len(rows)
                except Exception:
                    logger.warning(
                        "Memory extraction batch failed; preserving material", exc_info=True
                    )
                    result["failed"] += len(batch)
                    for job in batch:
                        current = self.runtime.store.get("memory_jobs", job["id"])
                        if current:
                            current.update(
                                status="failed",
                                attempts=int(current.get("attempts", 0)) + 1,
                                retry_at=time.time() + 60,
                            )
                            self.runtime.store.put("memory_jobs", current["id"], current)
            # Chat usefulness is judged with its summary. Background calls need no chat material.
            pending = [
                row
                for row in self.runtime.store.list("memory_feedback")
                if not str(row.get("task", "")).startswith("chat")
            ]
            for row in pending[:20]:
                try:
                    payload = _payload(
                        await self._complete("memory.feedback", {"rounds": [row]}, row["scope"])
                    )
                    if self.runtime.enabled("memory"):
                        with self._transaction():
                            self._apply_feedback(payload, [row])
                            self._finish_feedback([row])
                except Exception:
                    # Failure has no reward or penalty and is not blindly retried.
                    self._finish_feedback([row])
                    logger.debug("Memory usefulness feedback unavailable", exc_info=True)
        finally:
            self._processing = False
        return result

    async def _extract_batch(self, batch):
        scope, name = batch[0]["scope"], batch[0]["persona_name"]
        people = list(
            dict.fromkeys(
                person
                for job in batch
                for person in ([job["person_id"]] if job.get("person_id") else [])
                + self._people(job.get("people"))
            )
        )
        material = "\n".join(job["text"] for job in batch)
        known = self.recall(material, scope=scope, people=people, persona_name=name, limit=30)
        round_ids = {job.get("round_id") for job in batch if job.get("round_id")}
        feedback = [
            row
            for row in self.runtime.store.list("memory_feedback")
            if row.get("scope") == scope and row.get("round_id") in round_ids
        ]
        data = {
            "persona_name": name,
            "people": people,
            "materials": copy.deepcopy(batch),
            "material": material,
            "known": known,
            "rounds": feedback,
            "memories": known,
            "recent_memories": [],
            "memory_internal": True,
        }
        payload = _payload(await self._complete("memory.reflect", data, scope))
        if not self.runtime.enabled("memory") or any(
            not self._sources_enabled(job) for job in batch
        ):
            return None
        candidates = payload.get("memories")
        if not isinstance(candidates, list):
            raise ValueError("Memory extraction omitted memories array")
        plans = []
        for candidate in candidates[: self.config["reflection_limit"]]:
            if not isinstance(candidate, dict):
                raise ValueError("Invalid extracted memory")
            evidence = candidate.get("evidence", candidate.get("reasoning", ""))
            if not isinstance(evidence, str) or not evidence.strip() or evidence not in material:
                raise ValueError("Extracted memory is missing a verbatim source basis")
            content = self._validate_text(candidate.get("judgment", candidate.get("text")))
            attribute = candidate.get("attribute", "事实属性")
            if attribute not in ATTRIBUTES:
                raise ValueError("Invalid profile attribute")
            person = (
                _person(candidate.get("person_id", ""))
                if candidate.get("owner") == "person"
                else ""
            )
            if candidate.get("owner") == "person" and not person:
                raise ValueError("Person memory is missing a provided identity")
            if person and person not in people:
                raise ValueError("Extracted memory names an unprovided identity")
            supporting = [job for job in batch if evidence in job["text"]]
            source = supporting[0]
            if source.get("migration") and source.get("person_id"):
                if person and person != source["person_id"]:
                    raise ValueError("Migration cannot change a recorded QQ identity")
                person = source["person_id"]
            stable = candidate.get("stable") is True
            global_profile = (
                person
                and stable
                and source.get("person_id") == person
                and not candidate.get("inferred")
                and self._cross_scope_profile(candidate, evidence, material, person)
            )
            if global_profile:
                content = global_profile
            row_scope = "global" if global_profile else scope
            # Attribution and occurrence times come from evidence, never model inventions.
            occurred = source.get("occurred_at") or ""
            old_ids = candidate.get("merge_ids", [])
            if candidate.get("replace_id"):
                old_ids = [candidate["replace_id"]]
            if not isinstance(old_ids, list) or any(not isinstance(key, str) for key in old_ids):
                raise ValueError("Invalid replacement identifiers")
            old = {row["id"]: row for row in known}
            for key in old_ids:
                if (
                    key not in old
                    or old[key].get("scope") != row_scope
                    or old[key].get("person_id", "") != person
                ):
                    raise ValueError("Replacement changes ownership or scope")
                current = self.runtime.store.get(self.namespace, key)
                if not current or current.get("version") != old[key].get("version"):
                    raise ValueError("Memory changed during extraction")
            plans.append(
                {
                    "candidate": candidate,
                    "text": content,
                    "evidence": evidence,
                    "attribute": attribute,
                    "person": person,
                    "scope": row_scope,
                    "stable": stable,
                    "occurred": occurred,
                    "source": source,
                    "supporting": supporting,
                    "old_ids": old_ids,
                }
            )
        results = []
        with self._transaction():
            for job in batch:
                if not self.runtime.store.get("memory_jobs", job["id"]):
                    raise ValueError("Source was removed during extraction")
            for plan in plans:
                candidate, source = plan["candidate"], plan["source"]
                key = plan["old_ids"][0] if plan["old_ids"] else None
                row = self.remember(
                    plan["text"],
                    key=key,
                    scope=plan["scope"],
                    person_id=plan["person"],
                    persona_name=name,
                    source=source["source"],
                    sources=source.get("sources", []),
                    source_keys=list(
                        dict.fromkeys(
                            key for job in plan["supporting"] for key in job["source_keys"]
                        )
                    ),
                    occurred_at=plan["occurred"],
                    attribute=plan["attribute"],
                    reasoning=plan["evidence"],
                    tags=candidate.get("tags", []),
                    stable=plan["stable"],
                    inferred=candidate.get("inferred") is True,
                    important=source.get("important", False),
                    protected=source.get("protected", False),
                    evidence_type=source.get("evidence_type"),
                    reading_basis=source.get("reading_basis"),
                    journal_day=source.get("journal_day"),
                )
                if row:
                    if plan["old_ids"]:
                        originals = [
                            self.runtime.store.get(self.namespace, key) for key in plan["old_ids"]
                        ]
                        row["important"] = row["important"] or any(
                            item.get("important") for item in originals
                        )
                        row["protected"] = row["protected"] or any(
                            item.get("protected") for item in originals
                        )
                        row["sources"] += [
                            source for item in originals for source in self._source_names(item)
                        ]
                        row["source_keys"] = list(
                            dict.fromkeys(
                                row["source_keys"]
                                + [key for item in originals for key in item.get("source_keys", [])]
                            )
                        )
                        self.runtime.store.put(self.namespace, row["id"], row)
                    results.append(row)
                    for old_id in plan["old_ids"][1:]:
                        previous = self.runtime.store.get(self.namespace, old_id)
                        self._version(previous)
                        previous.update(active=False, replaced_by=row["id"])
                        self.runtime.store.put(self.namespace, old_id, previous)
            self._apply_feedback(payload, feedback)
            self._finish_feedback(feedback)
            for job in batch:
                self.runtime.store.put(
                    "memory_materials",
                    job["id"],
                    {
                        "id": job["id"],
                        "key": job["key"],
                        "digest": job.get("digest", _digest(job["text"])),
                        "memory_ids": [row["id"] for row in results],
                        "migration": job.get("migration", False),
                        "completed_at": _stamp(_now()),
                    },
                )
                self.runtime.store.delete("memory_jobs", job["id"])
        return results

    @staticmethod
    def _cross_scope_profile(candidate, evidence, original, person_id):
        scrubbed = re.sub(r'"person_id"\s*:\s*"[^"]*"', "", original)
        if PRIVATE_MARKERS.search(scrubbed):
            return False
        # Cross-context sharing remains limited to explicit self-described names,
        # lasting interests and relationships. Other stable facts remain local.
        attribute = candidate.get("attribute")
        match = None
        if attribute == "用户别名":
            match = re.search(
                r"(?:我叫|我的名字(?:叫|是)|叫我|称呼我)\s*([^\s，。！？、;；:：]{1,32})", evidence
            )
        if attribute == "事实属性":
            match = re.search(
                r"(?:我|本人)(?:平时|一直|长期|通常|很|非常|特别|最|比较|挺|都)*(?:喜欢|爱好|热爱|爱)\s*([^\s，。！？、;；:：]{1,32})",
                evidence,
            )
        if attribute == "关系图谱":
            match = re.search(
                r"(?:我们|咱们|你和我|我和你)(?:一直|现在|已经|算)?(?:是|算是)(朋友|好友|同学|同事|网友|邻居|师生|家人|伙伴)",
                evidence,
            )
        return attribute + "：" + match.group(1) if match else False

    async def reflect(self, text, *, scope, person_id="", source=""):
        """Compatibility explicit extraction route, bypassing chat batching."""
        job = self.enqueue_material(text, scope=scope, person_id=person_id, source=source)
        if not job or not job.get("text") or not job.get("persona_name"):
            return []
        try:
            return await self._extract_batch([job]) or []
        except Exception:
            logger.warning("Memory extraction failed", exc_info=True)
            return []

    history = versions

    def delete_source(self, key):
        """Deleting a source removes its dependent memory and pending extraction."""
        with self._transaction():
            self.runtime.store.put(
                "memory_source_tombstones", key, {"id": key, "deleted_at": _stamp(_now())}
            )
            for row in self.runtime.store.list(self.namespace):
                if key in row.get("source_keys", []):
                    self.delete(row["id"])
            for job in self.runtime.store.list("memory_jobs"):
                if key in job.get("source_keys", []):
                    self.runtime.store.delete("memory_jobs", job["id"])
            for archived in self.runtime.store.list("memory_legacy"):
                if archived.get("key") == key:
                    self.runtime.store.delete("memory_legacy", archived["id"])

    def _decay_enabled(self):
        return self.runtime.enabled("memory") and self.config["forgetting_enabled"]

    def rebase_clock(self):
        """Call after settings changes or suspension to discard unobserved intervals."""
        self._clock = time.monotonic()
        self._clock_enabled = self._decay_enabled()

    def maintain(self, now=None):
        """Count this process's enabled online time, never wall-clock absence."""
        current = float(now) if isinstance(now, (int, float)) else time.monotonic()
        elapsed = max(0, current - self._clock)
        enabled = self._decay_enabled()
        eligible = enabled and self._clock_enabled
        self._clock, self._clock_enabled = current, enabled
        result = {"decayed": 0, "forgotten": 0, "retained": 0}
        if not eligible:
            return result
        config = self.config
        with self._transaction():
            for row in self.runtime.store.list(self.namespace):
                if row.get("schema_version") != 2 or not row.get("active", True):
                    continue
                if (
                    row.get("important")
                    or row.get("protected")
                    or row.get("useful_score", 0) >= config["medium_threshold"]
                ):
                    result["retained"] += 1
                    continue
                row["decay_seconds"] = float(row.get("decay_seconds", 0)) + elapsed
                cycles = int((row["decay_seconds"] + 1e-6) // config["low_decay_seconds"])
                if cycles:
                    row["strength"] -= cycles * config["low_decay_amount"]
                    row["decay_seconds"] = max(
                        0, row["decay_seconds"] - cycles * config["low_decay_seconds"]
                    )
                    result["decayed"] += 1
                if row["strength"] <= 0:
                    self.delete(row["id"])
                    result["forgotten"] += 1
                else:
                    self.runtime.store.put(self.namespace, row["id"], row)
        return result

    def migration_status(self):
        saved = self.runtime.store.get("memory_migration", "v2", {})
        all_jobs = self.runtime.store.list("memory_jobs")
        jobs = [row for row in all_jobs if row.get("migration")]
        done = [row for row in self.runtime.store.list("memory_materials") if row.get("migration")]
        return {
            "version": 1,
            "paused": bool(saved.get("paused", False)),
            "persona_name": saved.get("persona_name", ""),
            "total": len(jobs) + len(done),
            "pending": len(jobs),
            "failed": sum(row.get("status") == "failed" for row in jobs),
            "completed": len(done),
            "waiting_persona": bool(jobs and not saved.get("persona_name")),
            "queue_pending": len(all_jobs),
            "queue_failed": sum(row.get("status") == "failed" for row in all_jobs),
            "chat_pending": sum(row.get("kind") == "chat" for row in all_jobs),
            "feedback_pending": len(self.runtime.store.list("memory_feedback")),
            "processing": self._processing,
        }

    def pause_migration(self):
        state = self.runtime.store.get("memory_migration", "v2", {})
        state["paused"] = True
        self.runtime.store.put("memory_migration", "v2", state)
        return self.migration_status()

    def resume_migration(self):
        state = self.runtime.store.get("memory_migration", "v2", {})
        state["paused"] = False
        self.runtime.store.put("memory_migration", "v2", state)
        for job in self.runtime.store.list("memory_jobs"):
            if job.get("migration"):
                job.update(retry_at=0, status="pending")
                self.runtime.store.put("memory_jobs", job["id"], job)
        return self.migration_status()

    def migrate(self, saved_settings=None, *, importing=False):
        """Archive once, then enqueue old source records without changing their dates."""
        store = self.runtime.store
        state = store.get("memory_migration", "v2", {})
        initial = not state
        with self._transaction():
            if not state:
                state = {
                    "version": 2,
                    "created_at": _stamp(_now()),
                    "persona_name": self._persona(),
                    "paused": False,
                }
                store.put("memory_migration", "v2", state)
                store.put(
                    "memory_config_history",
                    "v2",
                    {
                        "id": "v2",
                        "settings": copy.deepcopy(saved_settings or self.runtime.settings),
                    },
                )
                for task in (
                    "memory.reflect",
                    "journal.write",
                    "notes.write",
                    "journal.brief",
                    "notes.brief",
                ):
                    template = store.get("prompt_templates", task)
                    if template:
                        store.put(
                            "prompt_template_history",
                            "memory-v2:" + task,
                            {
                                "id": "memory-v2:" + task,
                                "task": task,
                                "template": template,
                                "created_at": _stamp(_now()),
                            },
                        )
                        store.delete("prompt_templates", task)
            elif not state.get("persona_name") and self._persona():
                state["persona_name"] = self._persona()
                store.put("memory_migration", "v2", state)
            if not initial and not importing:
                return self.migration_status()
            events = {str(row.get("id", "")): row for row in store.list("events")}
            observations = {str(row.get("id", "")): row for row in store.list("observations")}
            journals = {str(row.get("id", "")): row for row in store.list("journals")}
            actions = {str(row.get("id", "")): row for row in store.list("actions")}
            sources = []
            for row in store.list(self.namespace):
                if row.get("schema_version") == 2 or not row.get("id"):
                    continue
                key = "legacy-memory:" + str(row.get("id", ""))
                store.put(
                    "memory_legacy",
                    key,
                    {"id": key, "key": key, "namespace": self.namespace, "record": row},
                )
                # The source document will replace its old automatic copy.
                duplicate = (
                    str(row.get("source_event_id", "")) in events
                    or str(row.get("id", "")).removeprefix("journal:") in journals
                    or str(row.get("journal_id", "")) in journals
                    or any(
                        str(item.get("id", "")) in observations
                        or str(item.get("id", "")) in journals
                        for item in row.get("sources", [])
                        if isinstance(item, dict)
                    )
                )
                if not duplicate and row.get("active", True):
                    sources.append((key, row, row.get("source", "memory"), row.get("text", "")))
                store.delete(self.namespace, row["id"])
            for namespace, records, prefix in (
                ("events", events, "event"),
                ("observations", observations, "observation"),
                ("journals", journals, "journal"),
            ):
                for identifier, row in records.items():
                    if not identifier:
                        continue
                    key = prefix + ":" + identifier
                    archive_key = namespace + ":" + identifier
                    if store.get("memory_legacy", archive_key):
                        continue
                    store.put(
                        "memory_legacy",
                        archive_key,
                        {"id": archive_key, "key": key, "namespace": namespace, "record": row},
                    )
                    if (
                        namespace == "events"
                        and str(row.get("source_record_id", "")) in observations
                    ):
                        continue
                    if namespace == "events" and identifier.startswith("action:"):
                        action = actions.get(identifier.removeprefix("action:"), {})
                        if str(action.get("observation_id", "")) in observations:
                            continue
                    if namespace == "observations":
                        if (
                            row.get("module") == "weather"
                            or not row.get("factual_summary")
                            or row.get("reflection_status") == "failed"
                        ):
                            continue
                        source = row.get("module", "")
                        body = (
                            "事实摘要："
                            + row["factual_summary"]
                            + "\n角色感想："
                            + row.get("impression", "")
                        )
                    else:
                        source = row.get("source", row.get("kind", ""))
                        body = row.get("text", "")
                    sources.append((key, row, source, body))
            for key, row, source, body in sources:
                self.enqueue_material(
                    body,
                    scope=row.get("scope", "global"),
                    source=source,
                    key=key,
                    occurred_at=row.get("occurred_at") or row.get("created_at") or "",
                    person_id=row.get("person_id", ""),
                    sources=row.get("sources", []),
                    persona_name=state.get("persona_name", ""),
                    migration=True,
                    source_keys=row.get("source_keys", []),
                    important=row.get("important", False),
                    reading_basis=row.get("reading_basis", ""),
                    journal_day=row.get("day", ""),
                )
        return self.migration_status()
