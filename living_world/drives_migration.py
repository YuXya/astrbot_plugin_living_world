"""Import program-owned drives without replaying legacy activity decisions."""

from __future__ import annotations

import copy
import math

from .life_migration import TASKS, _digest, _empty_actions, _superseded, _time, _version
from .prompts import PROMPTS

MIGRATION_KEY = "inner_drives_v1"
DRIVE_SCHEMA_VERSION = 1
TEMPLATE_SCHEMA_VERSION = 4
DETAIL_FIELDS = (
    "description",
    "incident",
    "energy_delta",
    "mood",
    "detail_error",
    "detail_version",
    "detail_retry_at",
    "detail_retry_after",
    "detailed_at",
    "detail_request",
    "detail_raw",
    "detail_requirements",
)


def _initial_energy(store, legacy_settings):
    candidates = (
        store.get("life_state", "current", {}).get("energy"),
        legacy_settings.get("character", {}).get("energy"),
        70,
    )
    for value in candidates:
        if isinstance(value, bool):
            continue
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            continue
        if math.isfinite(number):
            return max(0, min(100, number))
    return 70


def _clear_detail(row, *, override=False):
    for field in DETAIL_FIELDS:
        row.pop(field, None)
    if override:
        for field in ("actions", "detailed", "detail_attempts"):
            row.pop(field, None)
    else:
        row.update(detailed=False, detail_attempts=0, actions=_empty_actions())


def migrate_drives(runtime, legacy_settings=None):
    """Convert unmarked rows on every import; the global marker is not an early exit."""
    store, now = runtime.store, runtime.life._now()
    if legacy_settings is None:
        legacy_settings = store.get("settings", "current", {})
    with store.transaction():
        writes, deletes, changed = [], [], {}
        converted_count = retired_count = template_backups = 0
        current_state = store.get("drive_state", "current")
        if current_state is None:
            writes.append(
                (
                    "drive_state",
                    "current",
                    {"values": {"loneliness": 0, "energy": _initial_energy(store, legacy_settings)}},
                )
            )

        active = {row["id"]: row for row in store.list("activities")}
        markers = store.list("life_days")
        for marker in markers:
            if marker.get("status") == "prepared":
                for row in marker.get("adopted_activities", []):
                    if isinstance(row, dict) and row.get("id"):
                        active.setdefault(row["id"], row)
        retired_ids = {row["id"] for row in store.list("life_retired_activities")}
        for original in active.values():
            row = copy.deepcopy(original)
            activity_id = row["id"]
            day = str(row.get("date") or row.get("start", "")[:10])
            scope = row.get("scope", "global")
            marker_key = f"{day}:{scope}"
            marker = store.get("life_days", marker_key, {})
            superseded = _superseded(row, marker)
            retired = activity_id in retired_ids
            if (
                original.get("drive_schema_version", 0) >= DRIVE_SCHEMA_VERSION
                and not superseded
                and not retired
            ):
                continue
            start = _time(row.get("start"), now)
            future = bool(start and start > now and row.get("status", "planned") == "planned")
            if future or superseded or retired:
                history_id = "migration-drives-v1:" + _digest(original)
                if not store.get("life_detail_history", history_id):
                    writes.append(
                        (
                            "life_detail_history",
                            history_id,
                            {
                                "id": history_id,
                                "activity_id": activity_id,
                                "archived_at": now.isoformat(),
                                "reason": "inner_drives_upgrade",
                                "migration": MIGRATION_KEY,
                                "activity": original,
                            },
                        )
                    )
            if superseded or retired:
                if not retired:
                    writes.append(
                        (
                            "life_retired_activities",
                            activity_id,
                            {
                                "id": activity_id,
                                "archived_at": now.isoformat(),
                                "reason": "superseded_backup_activity",
                                "migration": MIGRATION_KEY,
                            },
                        )
                    )
                deletes.append(("activities", activity_id))
                retired_ids.add(activity_id)
                retired_count += 1
                continue
            if future:
                _clear_detail(row)
                for override in row.get("scope_overrides", {}).values():
                    if isinstance(override, dict):
                        _clear_detail(override, override=True)
            row["drive_schema_version"] = DRIVE_SCHEMA_VERSION
            writes.append(("activities", activity_id, row))
            changed[activity_id] = row
            converted_count += 1

        # Keep the current outline snapshot consistent with its active activity rows.
        for marker in markers:
            row = copy.deepcopy(marker)
            if "adopted_activities" in row:
                row["adopted_activities"] = [
                    copy.deepcopy(changed.get(activity.get("id"), activity))
                    for activity in row["adopted_activities"]
                    if isinstance(activity, dict) and activity.get("id") not in retired_ids
                ]
            row["drive_schema_version"] = DRIVE_SCHEMA_VERSION
            if row != marker:
                writes.append(("life_days", f"{row['date']}:{row.get('scope', 'global')}", row))

        for task in TASKS:
            for namespace in ("prompt_defaults", "prompt_templates"):
                original = store.get(namespace, task)
                if original and _version(original) >= TEMPLATE_SCHEMA_VERSION:
                    continue
                if original:
                    history_id = "migration-drives-v1:" + _digest(
                        {"namespace": namespace, "task": task, "row": original}
                    )
                    if not store.get("prompt_template_history", history_id):
                        writes.append(
                            (
                                "prompt_template_history",
                                history_id,
                                {
                                    **copy.deepcopy(original),
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
                            "schema_version": TEMPLATE_SCHEMA_VERSION,
                        },
                    )
                )
        if not store.get("life_migrations", MIGRATION_KEY):
            writes.append(
                (
                    "life_migrations",
                    MIGRATION_KEY,
                    {"id": MIGRATION_KEY, "created_at": now.isoformat(), "schema_version": 1},
                )
            )
        if writes or deletes:
            store.apply_batch(writes, deletes)
        return {
            "converted": converted_count,
            "retired": retired_count,
            "template_backups": template_backups,
        }
