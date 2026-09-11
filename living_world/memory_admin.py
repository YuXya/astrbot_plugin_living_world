"""Read-only, paginated memory administration without invoking retrieval or maintenance."""

import copy
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from .memory import ATTRIBUTES
from .profile_names import profile_labels, named_profiles

READ_ACTIONS = frozenset(
    {"memory.profiles", "memory.records", "memory.detail", "memory.sources", "memory.names"}
)
META_FIELDS = (
    "id",
    "schema_version",
    "identity",
    "owner",
    "person_id",
    "persona_name",
    "owner_name",
    "attribute",
    "active",
    "stable",
    "inferred",
    "important",
    "protected",
    "scope",
    "source",
    "occurred_at",
    "updated_at",
    "version",
)
CARD_FIELDS = (
    *META_FIELDS,
    "text",
    "judgment",
    "reasoning",
    "tags",
    "strength",
    "useful_score",
    "created_at",
    "replaced_by",
)
SOURCE_KINDS = {"event": "events", "observation": "observations", "journal": "journals"}


class MemoryAdmin:
    def __init__(self, runtime):
        self.runtime = runtime
        self.store = runtime.store

    def metadata(self):
        rows = [
            row
            for row in self.store.project("memories", META_FIELDS)
            if row.get("schema_version") == 2
        ]
        # SQLite JSON scalar projection represents booleans as integer 0/1.
        for row in rows:
            for key in ("active", "stable", "inferred", "important", "protected"):
                if key in row:
                    row[key] = bool(row[key])
        return rows

    def count(self):
        with self.store.lock:
            return self.store.db.execute(
                "SELECT count(*) FROM objects WHERE namespace=?", ("memories",)
            ).fetchone()[0]

    def _time(self, value):
        if value is None or value == "":
            return float("-inf")
        try:
            if isinstance(value, (int, float)):
                return float(value) / (1000 if abs(value) >= 1e12 else 1)
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(
                    tzinfo=ZoneInfo(self.runtime.settings["character"]["timezone"])
                )
            return parsed.timestamp()
        except (ValueError, TypeError, OverflowError):
            return float("-inf")

    def _display_time(self, value):
        try:
            return datetime.fromtimestamp(
                self._time(value), ZoneInfo(self.runtime.settings["character"]["timezone"])
            ).isoformat()
        except (ValueError, OverflowError, OSError):
            return ""

    def _profiles(self, metadata=None):
        rows = self.metadata() if metadata is None else metadata
        profiles = {row["id"]: copy.deepcopy(row) for row in self.store.list("memory_profiles")}
        name = self.runtime.memory._persona()
        if name:
            identity = self.runtime.memory._identity("", name)
            profiles.setdefault(
                identity,
                {
                    "id": identity,
                    "owner": "self",
                    "persona_name": name,
                    "name": name,
                    "person_id": "",
                },
            )
        for row in rows:
            identity = row.get("identity")
            if identity:
                profiles.setdefault(
                    identity,
                    {
                        "id": identity,
                        "owner": row.get("owner"),
                        "person_id": row.get("person_id", ""),
                        "persona_name": row.get("persona_name", ""),
                        "name": row.get("owner_name", ""),
                    },
                )
        for profile in profiles.values():
            profile.update(
                current=profile.get("owner") == "self" and profile.get("persona_name") == name,
                count=0,
                stable_count=0,
                protected_count=0,
                inferred_count=0,
                attributes=dict.fromkeys(ATTRIBUTES, 0),
                latest_at="",
                updated_at="",
            )
        for row in rows:
            profile = profiles.get(row.get("identity"))
            if profile is None or row.get("active", True) is False:
                continue
            profile["count"] += 1
            profile["stable_count"] += bool(row.get("stable"))
            profile["protected_count"] += bool(row.get("important") or row.get("protected"))
            profile["inferred_count"] += bool(row.get("inferred"))
            if row.get("attribute") in ATTRIBUTES:
                profile["attributes"][row["attribute"]] += 1
            for source, dest in (("occurred_at", "latest_at"), ("updated_at", "updated_at")):
                if self._time(row.get(source)) > self._time(profile[dest]):
                    profile[dest] = row[source]
        labeled, _ = profile_labels(self.runtime, list(profiles.values()), rows)
        for profile in labeled:
            for key in ("latest_at", "updated_at"):
                profile[key] = self._display_time(profile[key])
        return sorted(
            labeled,
            key=lambda row: (
                0 if row["current"] else 1 if row.get("owner") == "person" else 2,
                -self._time(row["updated_at"]),
                row["id"],
            ),
        )

    def profile(self, identity):
        profile = next((row for row in self._profiles() if row["id"] == identity), None)
        if not profile:
            raise ValueError("画像档案不存在，请返回画像列表刷新")
        return profile

    @staticmethod
    def _offset(value):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("分页位置必须为非负整数")
        return value

    def profiles(self, data):
        profiles = self._profiles()
        if data.get("profile_id"):
            profiles = [row for row in profiles if row["id"] == data["profile_id"]]
        kind = data.get("kind", "")
        if kind not in {"", "person", "current", "historical"}:
            raise ValueError("未知档案类型")
        query = str(data.get("query", "")).strip().casefold()[:200]
        profiles = [
            row
            for row in profiles
            if (
                not kind
                or (kind == "person" and row["owner"] == "person")
                or (kind == "current" and row["current"])
                or (kind == "historical" and row["owner"] == "self" and not row["current"])
            )
            and (
                not query
                or query
                in " ".join(
                    str(row.get(k, "")) for k in ("name", "person_id", "persona_name")
                ).casefold()
            )
        ]
        offset = self._offset(data.get("offset", 0))
        return {
            "items": profiles[offset : offset + 12],
            "total": len(profiles),
            "offset": offset,
            "limit": 12,
            "memory_count": self.count(),
        }

    def _matching_ids(self, query):
        # Only administrator-entered literal text is searched; no semantic model is used.
        with self.store.lock:
            rows = self.store.db.execute(
                "SELECT key FROM objects WHERE namespace=? AND "
                "instr(lower(coalesce(json_extract(value,'$.judgment'),json_extract(value,'$.text'),'')"
                " || ' ' || coalesce(json_extract(value,'$.reasoning'),'')"
                " || ' ' || coalesce(json_extract(value,'$.tags'),'')), ?) > 0",
                ("memories", query.lower()),
            ).fetchall()
        return {row[0] for row in rows}

    def _filter(self, rows, data):
        state = data.get("state", "active")
        if state not in {"active", "all", "replaced", "stable", "inferred", "protected"}:
            raise ValueError("未知保留状态")
        query = str(data.get("query", "")).strip()[:200]
        matched = self._matching_ids(query) if query else None
        return [
            row
            for row in rows
            if (not data.get("profile_id") or row.get("identity") == data["profile_id"])
            and (not data.get("scope") or row.get("scope") == data["scope"])
            and (not data.get("source") or row.get("source") == data["source"])
            and (not data.get("attribute") or row.get("attribute") == data["attribute"])
            and (matched is None or row["id"] in matched)
            and (
                state == "all"
                or (
                    row.get("active", True) is False
                    if state == "replaced"
                    else row.get("active", True) is not False
                    and (
                        state == "active"
                        or state in {"stable", "inferred"}
                        and row.get(state)
                        or state == "protected"
                        and (row.get("important") or row.get("protected"))
                    )
                )
            )
        ]

    def _record(self, row):
        result = {key: copy.deepcopy(row[key]) for key in CARD_FIELDS if key in row}
        for key in ("occurred_at", "created_at", "updated_at"):
            result[key] = self._display_time(row.get(key))
        return result

    def _page(self, rows, offset, limit):
        items = []
        for row in rows[offset : offset + limit]:
            full = self.store.get("memories", row["id"])
            if full and full.get("schema_version") == 2:
                items.append(self._record(full))
        return {
            "items": items,
            "total": len(rows),
            "offset": offset,
            "limit": limit,
            "has_more": offset + limit < len(rows),
        }

    def records(self, data):
        rows = self.metadata()
        profile = self.profile(data["profile_id"]) if data.get("profile_id") else None
        available = [row for row in rows if not profile or row.get("identity") == profile["id"]]
        filters = {
            key: sorted({str(row.get(key)) for row in available if row.get(key)})
            for key in ("scope", "source")
        }
        filtered = sorted(
            self._filter(available, data),
            key=lambda row: (-self._time(row.get("occurred_at")), row["id"]),
        )
        mode = data.get("mode", "timeline")
        if mode not in {"grouped", "timeline"}:
            raise ValueError("未知画像浏览方式")
        if data.get("attribute") and data["attribute"] not in ATTRIBUTES:
            raise ValueError("未知画像属性")
        result = {"profile": profile, "filters": filters, "total": len(filtered)}
        if mode == "grouped":
            offsets = data.get("offsets", {})
            if not isinstance(offsets, dict):
                raise ValueError("分组分页格式无效")
            result["groups"] = {
                attr: self._page(
                    [row for row in filtered if row.get("attribute") == attr],
                    self._offset(offsets.get(attr, 0)),
                    10,
                )
                for attr in ATTRIBUTES
                if not data.get("attribute") or attr == data["attribute"]
            }
        else:
            result.update(self._page(filtered, self._offset(data.get("offset", 0)), 20))
        # Names here only use saved/manual/cache values; network lookup is a separate operation.
        visible = (
            result.get("items", [])
            if mode == "timeline"
            else [row for group in result["groups"].values() for row in group["items"]]
        )
        ids = {row.get("identity") for row in visible}
        if profile:
            ids.add(profile["id"])
        result["owners"] = [p for p in self._profiles(rows) if p["id"] in ids]
        return result

    def history(self, data):
        memory_id = str(data["id"])
        current = self.detail(memory_id)
        offset = self._offset(data.get("offset", 0))
        with self.store.lock:
            rows = self.store.db.execute(
                "SELECT value FROM objects WHERE namespace=? AND key>=? AND key<? "
                "AND json_extract(value,'$.memory_id')=? "
                "ORDER BY CAST(json_extract(value,'$.version') AS INTEGER) DESC LIMIT 21 OFFSET ?",
                ("memory_versions", memory_id + ":", memory_id + ";", memory_id, offset),
            ).fetchall()
        records = [json.loads(row[0]) for row in rows[:20]]
        for row in records:
            row["record"] = self._record(row["record"])
        return {
            "records": records,
            "current": self._record(current),
            "has_more": len(rows) > 20,
            "offset": offset,
            "limit": 20,
        }

    def detail(self, memory_id):
        row = self.store.get("memories", memory_id)
        if not row or row.get("schema_version") != 2:
            raise ValueError("这条记忆已不存在，请刷新列表")
        return row

    def sources(self, data):
        memory = self.detail(str(data.get("id", "")))
        keys = list(memory.get("source_keys", []))
        if memory.get("source_event_id"):
            keys.append("event:" + str(memory["source_event_id"]))
        items = []
        for key in dict.fromkeys(str(value) for value in keys):
            prefix, _, source_id = key.partition(":")
            namespace = SOURCE_KINDS.get(prefix)
            available = False
            if namespace and source_id:
                with self.store.lock:
                    available = (
                        self.store.db.execute(
                            "SELECT 1 FROM objects WHERE namespace=? AND key=?",
                            (namespace, source_id),
                        ).fetchone()
                        is not None
                    )
            items.append(
                {
                    "key": key,
                    "kind": prefix,
                    "available": available,
                    "notice": ""
                    if available
                    else "聊天原文未在插件中保留"
                    if prefix == "chat"
                    else "来源原记录已不可用",
                }
            )
        result = {
            "items": items,
            "evidence": memory.get("reasoning", ""),
            "notice": "" if items else "未保存可定位的原记录；可查看已有事实依据",
        }
        if data.get("source_key"):
            key = str(data["source_key"])
            item = next((item for item in items if item["key"] == key), None)
            if not item or not item["available"]:
                raise ValueError("来源原记录已不可用")
            prefix, _, source_id = key.partition(":")
            original = self.store.get(SOURCE_KINDS[prefix], source_id)
            if original is None:
                raise ValueError("来源原记录已不可用")
            result["record"] = {
                field: copy.deepcopy(original[field])
                for field in (
                    "id",
                    "title",
                    "text",
                    "raw_text",
                    "factual_summary",
                    "impression",
                    "scope",
                    "kind",
                    "module",
                    "source",
                    "created_at",
                    "occurred_at",
                    "day",
                    "status",
                    "reading_basis",
                    "sources",
                )
                if field in original
            }
            for field in ("created_at", "occurred_at"):
                result["record"][field] = self._display_time(original.get(field))
        return result

    def receipt(self, identity):
        return {
            "profile": next((row for row in self._profiles() if row["id"] == identity), None),
            "memory_count": self.count(),
        }

    async def handle(self, action, data):
        if action == "memory.names":
            ids = data.get("ids", [])
            if (
                not isinstance(ids, list)
                or len(ids) > 50
                or any(not isinstance(i, str) for i in ids)
            ):
                raise ValueError("一次最多补全 50 个画像名字")
            metadata = self.metadata()
            profiles = [row for row in self._profiles(metadata) if row["id"] in ids]
            named = await named_profiles(self.runtime, profiles, metadata)
            return {
                "items": [
                    {key: row.get(key) for key in ("id", "name", "name_status")} for row in named
                ]
            }
        if action == "memory.profiles":
            return self.profiles(data)
        if action == "memory.records":
            return self.records(data)
        if action == "memory.detail":
            row = self.detail(str(data.get("id", "")))
            keys = list(row.get("source_keys", []))
            if row.get("source_event_id"):
                keys.append("event:" + str(row["source_event_id"]))
            return {
                "record": self._record(row),
                "source_keys": list(dict.fromkeys(keys)),
                "replaced_by": row.get("replaced_by", ""),
            }
        if action == "memory.sources":
            return self.sources(data)
        raise ValueError("未知记忆查询")
