"""Atomically import legacy outlines, execution evidence and static templates."""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import UTC, datetime

from .prompts import PROMPTS

KINDS = ("news", "search", "social")
TASKS = ("life.plan", "life.detail", "life.revise")
MIGRATION_KEY = "activity_detail_v3"
FINAL = {"completed", "failed", "skipped", "cancelled"}
PRE_EXECUTION_REASONS = {
    "module_disabled",
    "no_eligible_targets",
    "no_delivery",
    "source_scope_mismatch",
    "target_unavailable",
    "not_whitelisted",
    "quiet_hours",
    "cooldown",
    "daily_limit",
    "disabled",
    "overdue_at_generation",
    "overdue_at_regeneration",
    "parent_plan_changed",
    "overdue_on_restart",
    "already_attempted",
    "execution_deadline_exceeded",
    "能力停用或场合不符合接入配置",
    "该行动已受理，不重复执行",
    "插件已停止",
}


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _time(value, now):
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return datetime.fromtimestamp(value, UTC).astimezone(now.tzinfo)
        if isinstance(value, str) and value:
            parsed = datetime.fromisoformat(value)
            return (
                parsed.replace(tzinfo=now.tzinfo) if parsed.tzinfo is None else parsed
            ).astimezone(now.tzinfo)
    except (ValueError, TypeError, OverflowError, OSError):
        pass
    return None


def _empty_actions():
    return {
        kind: {
            "enabled": False,
            "intent": "",
            "reason": "",
            "at": None,
            "execution": {"status": "disabled"},
        }
        for kind in KINDS
    }


def _version(row):
    value = row.get("schema_version", 0)
    return value if isinstance(value, int) else 0


def _superseded(row, marker):
    """Compare known plan versions, falling back to the adopted identity set."""
    current = marker.get("version_id")
    if not current:
        return False
    version = row.get("plan_version") or row.get("version_id")
    if version:
        return version != current
    adopted = {
        activity.get("id")
        for activity in marker.get("adopted_activities", [])
        if isinstance(activity, dict) and activity.get("id")
    }
    return bool(adopted and row.get("id") not in adopted)


def _execution_started(row):
    """A terminal skip alone cannot prove a model or source was called."""
    status = row.get("status", "")
    reason = row.get("reason") or row.get("text", "")
    if status in {"pending", "planned", "disabled", "cancelled"}:
        return False
    if status == "skipped" and reason in PRE_EXECUTION_REASONS:
        return False
    return status in {
        "running",
        "success",
        "sent",
        "completed",
        "failed",
        "unknown",
        "interrupted",
    } or bool(
        row.get("started_at")
        or reason
        in {
            "previous_execution_may_have_started",
            "execution_interrupted_outcome_unknown",
            "transport_outcome_unknown",
            "cancelled_during_send",
        }
    )


def _convert_activity(row, now):
    converted = copy.deepcopy(row)
    converted.update(schema_version=3, kind="fiction")
    start, end = _time(row.get("start"), now), _time(row.get("end"), now)
    future = bool(start and start > now and row.get("status", "planned") == "planned")
    if future:
        for field in (
            "description",
            "incident",
            "mood",
            "energy_delta",
            "detail_error",
            "detail_version",
            "detail_attempts",
            "detail_retry_at",
            "detailed_at",
        ):
            converted.pop(field, None)
        # Existing private agreements remain in their original scoped projection.
        converted.update(detailed=False, actions=_empty_actions())
    else:
        actions = _empty_actions()
        for kind in KINDS:
            old = row.get("actions", {}).get(kind)
            if not isinstance(old, dict):
                continue
            action = copy.deepcopy(old)
            action.setdefault("reason", action.get("intent", ""))
            execution = action.setdefault(
                "execution", {"status": "pending" if action.get("enabled") else "disabled"}
            )
            if execution.get("status") == "running":
                execution.update(
                    status="skipped",
                    reason="execution_interrupted_outcome_unknown",
                    finished_at=now.isoformat(),
                )
            elif (end and end <= now or row.get("status") in FINAL) and execution.get(
                "status", "pending"
            ) == "pending":
                execution.update(
                    status="skipped", reason="migration_activity_ended", finished_at=now.isoformat()
                )
            actions[kind] = action
        converted.update(actions=actions, detailed=True)
        if end and end <= now and converted.get("status") not in FINAL:
            converted.update(status="completed", finished_at=now.isoformat())
    return converted


def _usage_records(objects, activity_evidence, now, cutoff):
    """Join evidence by the real action id; one social draw may have many deliveries."""
    candidates, identities, confidence = {}, {}, {}

    def identity(action_id, kind, scope, fallback=None):
        if action_id and kind in KINDS:
            identities.setdefault(action_id, (kind, scope or "global", fallback))

    def evidence(action_id, row, *, forced=False, fallback=None):
        if action_id not in identities or (not forced and not _execution_started(row)):
            return
        kind, scope, activity_time = identities[action_id]
        dated = next(
            (
                (index, parsed)
                for index, field in enumerate(
                    ("started_at", "created_at", "finished_at", "updated_at")
                )
                if (parsed := _time(row.get(field), now))
            ),
            (4, None),
        )
        rank, started = dated
        started = started or _time(fallback or activity_time, now) or now
        candidate = {
            "id": action_id,
            "kind": kind,
            "date": str(started.date()),
            "scope": scope,
            "started_at": started.isoformat(),
            "imported": True,
        }
        previous = candidates.get(action_id)
        if (
            not previous
            or rank < confidence[action_id]
            or (rank == confidence[action_id] and candidate["started_at"] < previous["started_at"])
        ):
            candidates[action_id] = candidate
            confidence[action_id] = rank

    for activity in activity_evidence:
        activity_id = activity.get("id")
        if not activity_id or _version(activity) >= 3:
            continue
        scope = activity.get("scope", "global")
        if activity.get("kind", "fiction") in KINDS:
            identity(activity_id, activity["kind"], scope, activity.get("start"))
            # The activity's created_at is plan creation, not execution start.
            execution = {key: value for key, value in activity.items() if key != "created_at"}
            evidence(activity_id, execution)
        for kind, action in activity.get("actions", {}).items():
            if kind not in KINDS or not isinstance(action, dict):
                continue
            action_id = action.get("id") or action.get("action_id") or f"{activity_id}:{kind}"
            identity(action_id, kind, scope, action.get("at") or activity.get("start"))
            evidence(action_id, action.get("execution", {}))

    for action_id, row in objects.get("actions", {}).items():
        created = _time(row.get("created_at"), now)
        # Later calls must not reinterpret current-generation preflight records as usage.
        if action_id not in identities and (_version(row) >= 3 or (created and created > cutoff)):
            continue
        identity(action_id, row.get("kind"), row.get("scope"), row.get("created_at"))
        evidence(action_id, row)

    delivery_groups = {}
    for row in objects.get("deliveries", {}).values():
        if row.get("interjection") or not row.get("action_id"):
            continue
        delivery_groups.setdefault(row["action_id"], []).append(row)
    for row in objects.get("social_actions", {}).values():
        action_id = row.get("action_id")
        started = _time(row.get("created_at"), now)
        if (
            action_id
            and action_id not in identities
            and not action_id.startswith("interject:")
            and _version(row) < 3
            and (not started or started <= cutoff)
        ):
            identity(action_id, "social", row.get("scope"), row.get("created_at"))
        if action_id not in identities:
            continue
        deliveries = delivery_groups.get(action_id, [])
        explicit_no_work = deliveries and all(
            not delivery.get("attempted")
            and not delivery.get("text")
            and delivery.get("status") == "skipped"
            and delivery.get("reason") in PRE_EXECUTION_REASONS
            for delivery in deliveries
        )
        if not explicit_no_work:
            evidence(action_id, row, forced=bool(row.get("targets")))
    for action_id, deliveries in delivery_groups.items():
        for row in deliveries:
            started = _time(row.get("created_at"), now)
            if (
                action_id not in identities
                and not action_id.startswith("interject:")
                and _version(row) < 3
                and (not started or started <= cutoff)
            ):
                identity(action_id, "social", row.get("scope"), row.get("created_at"))
            evidence(action_id, row, forced=bool(row.get("attempted") or row.get("text")))
    for action_id, row in objects.get("life_action_claims", {}).items():
        actual = objects.get("actions", {}).get(action_id, {})
        if action_id not in identities:
            if _version(row) >= 3 or _version(actual) >= 3:
                continue
            suffix = action_id.rsplit(":", 1)[-1]
            started = _time(row.get("started_at"), now)
            if not started or started <= cutoff:
                identity(action_id, suffix, row.get("scope", "global"), row.get("started_at"))
        # Claims precede the old preflight. A recorded no-op proves no budget was used.
        if (
            actual.get("status") == "skipped"
            and (actual.get("reason") or actual.get("text", "")) in PRE_EXECUTION_REASONS
            and action_id not in candidates
        ):
            continue
        evidence(action_id, row, forced=True)
    return candidates


def migrate_life(service):
    """Upgrade old records before scheduling, including records merged from old backups."""
    store, now = service.runtime.store, service._now()
    with store.transaction():
        objects = {}
        for record in store.export():
            objects.setdefault(record["namespace"], {})[record["key"]] = record["value"]
        previous = objects.get("life_migrations", {}).get(MIGRATION_KEY, {})
        cutoff = _time(previous.get("created_at"), now) or now
        writes, deletes, archived = [], [], {}
        active = copy.deepcopy(objects.get("activities", {}))
        markers = copy.deepcopy(objects.get("life_days", {}))
        retired = objects.get("life_retired_activities", {})
        evidence_rows = list(active.values())
        for history in objects.get("life_day_history", {}).values():
            evidence_rows.extend(history.get("activities", []))
            evidence_rows.extend(history.get("adopted_activities", []))
        for marker in markers.values():
            evidence_rows.extend(marker.get("adopted_activities", []))
            if _version(marker) < 3 and marker.get("status") == "prepared":
                for row in marker.get("adopted_activities", []):
                    if isinstance(row, dict) and row.get("id"):
                        active.setdefault(row["id"], copy.deepcopy(row))

        changed_days, converted_count, retired_count = set(), 0, 0
        for key, row in list(active.items()):
            if key in retired:
                deletes.append(("activities", key))
                active.pop(key)
                continue
            if _version(row) >= 3:
                continue
            day = str(row.get("date") or row.get("start", "")[:10])
            scope = row.get("scope", "global")
            marker_key = f"{day}:{scope}"
            marker = markers.get(marker_key, {})
            archived.setdefault(marker_key, []).append(copy.deepcopy(row))
            changed_days.add(marker_key)
            # An orphan from a superseded backup cannot join an already adopted new version.
            superseded = _version(marker) >= 3 and _superseded(row, marker)
            if row.get("parent_id") or row.get("kind", "fiction") != "fiction" or superseded:
                writes.append(
                    (
                        "life_retired_activities",
                        key,
                        {
                            "id": key,
                            "archived_at": now.isoformat(),
                            "reason": "legacy_activity_retired",
                            "migration": MIGRATION_KEY,
                        },
                    )
                )
                deletes.append(("activities", key))
                active.pop(key)
                retired_count += 1
            else:
                converted = _convert_activity(row, now)
                writes.append(("activities", key, converted))
                active[key] = converted
                converted_count += 1

        changed_days.update(key for key, marker in markers.items() if _version(marker) < 3)
        for marker_key in sorted(changed_days):
            old = objects.get("life_days", {}).get(marker_key, {})
            rows = archived.get(marker_key, [])
            day, _, scope = marker_key.partition(":")
            history_id = "migration-v3:" + _digest(
                {"key": marker_key, "marker": old, "activities": rows}
            )
            if not store.get("life_day_history", history_id):
                history = {
                    **copy.deepcopy(old),
                    "id": history_id,
                    "date": day,
                    "scope": scope,
                    "archived_at": now.isoformat(),
                    "activities": rows,
                    "migration": MIGRATION_KEY,
                }
                writes.append(("life_day_history", history_id, history))
            if _version(old) >= 3:
                continue
            adopted = sorted(
                (
                    row
                    for row in active.values()
                    if row.get("date") == day and row.get("scope", "global") == scope
                ),
                key=lambda row: (row.get("start", ""), row.get("id", "")),
            )
            parameters = {**service.parameters(), **old.get("parameters", {})}
            parameters = {
                key: value
                for key, value in parameters.items()
                if key in {"daily_plan_time", "activity_count"}
            }
            # Retired standalone rows were part of some old activity counts. Freeze the
            # surviving outline count so later edits can still validate the whole day.
            if adopted:
                parameters["activity_count"] = len(adopted)
            marker = {
                **copy.deepcopy(old),
                "date": day,
                "scope": scope,
                "schema_version": 3,
                "parameters": parameters,
                "adopted_activities": adopted,
                "migrated_at": now.isoformat(),
                "migration": MIGRATION_KEY,
            }
            if old.get("status") == "prepared" or (not old and adopted):
                marker["status"] = "completed"
            writes.append(("life_days", marker_key, marker))

        usage = _usage_records(objects, evidence_rows, now, cutoff)
        imported_count = 0
        for key, value in usage.items():
            if key not in objects.get("life_action_usage", {}):
                writes.append(("life_action_usage", key, value))
                imported_count += 1
        template_backups = 0
        for task in TASKS:
            for namespace in ("prompt_defaults", "prompt_templates"):
                row = objects.get(namespace, {}).get(task)
                if row and _version(row) < 3:
                    history_id = "migration-v3:" + _digest(
                        {"namespace": namespace, "task": task, "row": row}
                    )
                    if not store.get("prompt_template_history", history_id):
                        writes.append(
                            (
                                "prompt_template_history",
                                history_id,
                                {
                                    **copy.deepcopy(row),
                                    "id": history_id,
                                    "task": task,
                                    "namespace": namespace,
                                    "archived_at": now.isoformat(),
                                    "migration": MIGRATION_KEY,
                                },
                            )
                        )
                        template_backups += 1
                    writes.append(
                        (
                            namespace,
                            task,
                            {
                                "id": task,
                                "template": PROMPTS[task],
                                "schema_version": 4,
                            },
                        )
                    )
            if not previous and task not in objects.get("prompt_defaults", {}):
                writes.append(
                    (
                        "prompt_defaults",
                        task,
                        {
                            "id": task,
                            "template": PROMPTS[task],
                            "schema_version": 4,
                        },
                    )
                )
            # A default-valued override also blocks legacy backup INSERT OR IGNORE merges.
            if not previous and task not in objects.get("prompt_templates", {}):
                writes.append(
                    (
                        "prompt_templates",
                        task,
                        {
                            "id": task,
                            "template": PROMPTS[task],
                            "schema_version": 4,
                        },
                    )
                )
        if not previous:
            writes.append(
                (
                    "life_migrations",
                    MIGRATION_KEY,
                    {
                        "id": MIGRATION_KEY,
                        "schema_version": 3,
                        "created_at": now.isoformat(),
                    },
                )
            )
        if writes or deletes:
            store.apply_batch(writes, deletes)
        return {
            "converted": converted_count,
            "retired": retired_count,
            "imported": imported_count,
            "template_backups": template_backups,
        }
