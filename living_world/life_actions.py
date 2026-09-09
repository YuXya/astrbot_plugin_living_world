"""Persistent action starts, deadline checks and historical outcome accounting."""

from __future__ import annotations

import copy
import uuid
from datetime import date, timedelta


KINDS = ("news", "search", "social")


class ActionLedger:
    @staticmethod
    def _empty_actions():
        return {
            kind: {
                "enabled": False,
                "intent": "",
                "reason": "尚未安排",
                "at": None,
                "execution": {"status": "disabled"},
            }
            for kind in KINDS
        }

    def _action_key(self, activity, kind):
        return activity.get("actions", {}).get(kind, {}).get("id") or f"{activity['id']}:{kind}"

    def _pending_actions(self):
        rows = {row["id"]: row for row in self.list_activities()}
        result = []
        used = {row["id"] for row in self.runtime.store.list("life_action_usage")}
        for row in rows.values():
            if row.get("status") not in {"planned", "running"}:
                continue
            for kind in KINDS:
                action = row.get("actions", {}).get(kind, {})
                if (
                    action.get("enabled")
                    and action.get("at")
                    and action.get("execution", {}).get("status") in {"pending", "running"}
                    and self._action_key(row, kind) not in used
                ):
                    at = self._parse_time(action["at"], date.fromisoformat(row["date"]))
                    result.append((at, row, kind))
        return sorted(result, key=lambda item: (item[0], item[1]["id"], item[2]))

    def reconcile_actions(self):
        """Expire pending actions and stop disabled capabilities without replaying them."""
        store, now = self.runtime.store, self._now()
        with store.transaction():
            changed = {}
            for at, original, kind in self._pending_actions():
                row = changed.get(original["id"], original)
                end = self._parse_time(row["end"], date.fromisoformat(row["date"]))
                expired = now >= end or now > at + timedelta(
                    minutes=max(0, self._setting("stale_action_minutes", 10))
                )
                module = "proactive" if kind == "social" else kind
                reason = (
                    "expired_action_window"
                    if expired
                    else "module_disabled"
                    if not self.runtime.enabled(module)
                    else ""
                )
                if reason:
                    row = copy.deepcopy(row)
                    row["actions"][kind]["execution"] = {
                        "status": "skipped",
                        "reason": reason,
                        "finished_at": now.isoformat(),
                    }
                    changed[row["id"]] = row
            if changed:
                store.apply_batch([("activities", key, row) for key, row in changed.items()])
        return list(changed.values())

    def consume_action(self, activity_id, kind, now=None):
        """Claim one attempt immediately before the first irreversible external/model call."""
        store, now = self.runtime.store, self._now(now)
        with store.transaction():
            row = store.get("activities", activity_id)
            if (
                not row
                or row.get("schema_version") != 3
                or store.get("life_retired_activities", activity_id)
                or not self.runtime.enabled("life")
                or not self.runtime.enabled("proactive" if kind == "social" else kind)
            ):
                return False
            action = row.get("actions", {}).get(kind, {})
            if not action.get("enabled") or action.get("execution", {}).get("status") not in {
                "pending",
                "running",
            }:
                return False
            at = self._parse_time(action["at"], date.fromisoformat(row["date"]))
            end = self._parse_time(row["end"], date.fromisoformat(row["date"]))
            if (
                now < at
                or now >= end
                or now - at > timedelta(minutes=max(0, self._setting("stale_action_minutes", 10)))
            ):
                return False
            key = self._action_key(row, kind)
            if store.get("life_action_usage", key) or store.get("drive_debits", key):
                return False
            store.put(
                "life_action_usage",
                key,
                {
                    "id": key,
                    "activity_id": activity_id,
                    "kind": kind,
                    "date": str(now.date()),
                    "scope": row["scope"],
                    "started_at": now.isoformat(),
                },
            )
            self.runtime.drives.debit(key, kind)
            return True

    def _detail_archive(self, activity, reason):
        key = uuid.uuid4().hex
        return (
            "life_detail_history",
            key,
            {
                "id": key,
                "activity_id": activity["id"],
                "archived_at": self._now().isoformat(),
                "reason": reason,
                "activity": copy.deepcopy(activity),
            },
        )

    def day_results(self, day):
        """Count actual outcomes across retired plans, using the attempt's civil date."""
        store, target = self.runtime.store, str(day)
        rows = []
        for history in reversed(store.list("life_day_history")):
            rows.extend(history.get("activities", []))
        for history in reversed(store.list("life_detail_history")):
            if history.get("activity"):
                rows.append(history["activity"])
        rows.extend(self.list_activities())
        decisions = {}
        for row in rows:
            for kind, action in row.get("actions", {}).items():
                if kind in KINDS:
                    decisions[self._action_key(row, kind)] = (kind, row, action)
        usage = {r["id"]: r for r in store.list("life_action_usage")}
        executed = {r["id"]: r for r in store.list("actions")}
        result = {k: {"success": 0, "failed": 0, "skipped": 0, "reasons": []} for k in KINDS}
        for key in decisions.keys() | usage.keys():
            consumed = usage.get(key, {})
            kind, row, action = decisions.get(key, (consumed.get("kind"), {}, {}))
            if kind not in KINDS:
                continue
            execution = action.get("execution", {})
            actual = executed.get(key, {})
            status = actual.get("status") if consumed and actual else execution.get("status")
            status = {"interrupted": "skipped", "cancelled": "skipped", "unknown": "skipped"}.get(
                status, status
            )
            if status not in {"success", "failed", "skipped"}:
                continue
            when = consumed.get("date")
            if when is None:
                timestamp = execution.get("finished_at") or action.get("at")
                try:
                    when = str(self._parse_time(timestamp, date.fromisoformat(row["date"])).date())
                except (TypeError, ValueError, KeyError):
                    when = row.get("date")
            if when == target:
                result[kind][status] += 1
                if status != "success":
                    result[kind]["reasons"].append(
                        execution.get("reason") or actual.get("reason", "")
                    )
        return result
