"""Legacy outline upgrades against real SQLite, without model or transport calls."""

import copy
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from living_world.life_migration import MIGRATION_KEY, migrate_life
from living_world.prompts import PROMPTS
from living_world.store import Store

NOW = datetime(2026, 9, 9, 12, tzinfo=timezone(timedelta(hours=8)))


@pytest.fixture
def world(tmp_path):
    store = Store(tmp_path / "world.sqlite")
    service = SimpleNamespace(
        runtime=SimpleNamespace(store=store),
        _now=lambda: NOW,
        parameters=lambda: {"daily_plan_time": "06:00", "activity_count": 10},
    )
    yield store, service
    store.close()


def activity(key="future", *, start="15:00", end="16:00", kind="fiction", **changes):
    row = {
        "id": key,
        "date": "2026-09-09",
        "scope": "global",
        "kind": kind,
        "start": f"2026-09-09T{start}:00+08:00",
        "end": f"2026-09-09T{end}:00+08:00",
        "title": "散步",
        "content": "沿河走走",
        "location": "河边",
        "sleep_state": "清醒",
        "schema_version": 2,
        "status": "planned",
        "detailed": True,
        "description": "旧细节",
        "incident": "旧小插曲",
        "energy_delta": -2,
        "mood": "放松",
        "detail_error": "旧错误",
        "created_at": "2026-09-09T06:00:00+08:00",
        "actions": {
            name: {
                "enabled": True,
                "intent": "看看相关内容",
                "at": f"2026-09-09T{start}:00+08:00",
                "execution": {"status": "pending"},
            }
            for name in ("news", "search", "social")
        },
    }
    row.update(changes)
    return row


def seed(store, rows, **changes):
    marker = {
        "date": "2026-09-09",
        "scope": "global",
        "schema_version": 2,
        "status": "completed",
        "parameters": {
            "daily_plan_time": "07:00",
            "activity_count": len(rows),
            "news_count": 2,
            "search_count": 2,
            "social_count": 3,
        },
        "raw_output": "旧完整生成内容",
        "adopted_activities": copy.deepcopy(rows),
    }
    marker.update(changes)
    store.put("life_days", "2026-09-09:global", marker)
    for row in rows:
        store.put("activities", row["id"], row)
    return marker


def test_future_outline_keeps_scope_and_archives_exact_old_data(world):
    store, service = world
    old = activity(
        scope_overrides={
            "qq:FriendMessage:42": {
                "title": "私下约定",
                "content": "私密计划",
                "description": "约定详情",
            }
        }
    )
    marker = seed(store, [old])
    result = migrate_life(service)
    assert result["converted"] == 1
    converted = store.get("activities", old["id"])
    assert converted["schema_version"] == 3 and not converted["detailed"]
    assert converted["scope_overrides"] == old["scope_overrides"]
    for name in ("title", "content", "location", "sleep_state", "start", "end", "id"):
        assert converted[name] == old[name]
    assert (
        not set(("description", "incident", "mood", "energy_delta", "detail_error"))
        & converted.keys()
    )
    assert all(
        not value["enabled"] and value["execution"]["status"] == "disabled"
        for value in converted["actions"].values()
    )
    history = store.list("life_day_history")[0]
    assert history["activities"] == [old]
    assert history["parameters"] == marker["parameters"]
    assert history["raw_output"] == "旧完整生成内容"
    current = store.get("life_days", "2026-09-09:global")
    assert current["parameters"] == {"daily_plan_time": "07:00", "activity_count": 1}
    assert current["adopted_activities"] == [converted]


def test_started_and_ended_outlines_keep_results_without_replaying(world):
    store, service = world
    started = activity("started", start="11:00", end="13:00")
    started["actions"]["news"]["execution"] = {
        "status": "success",
        "result": {"text": "真实新闻"},
        "finished_at": "2026-09-09T11:05:00+08:00",
    }
    started["actions"]["search"]["execution"] = {
        "status": "running",
        "started_at": "2026-09-09T11:59:00+08:00",
    }
    ended = activity("ended", start="09:00", end="10:00")
    seed(store, [started, ended])
    migrate_life(service)
    current = store.get("activities", "started")
    assert current["detailed"] and current["description"] == "旧细节"
    assert current["actions"]["news"] == {**started["actions"]["news"], "reason": "看看相关内容"}
    assert current["actions"]["search"]["execution"]["status"] == "skipped"
    assert current["actions"]["social"]["execution"]["status"] == "pending"
    completed = store.get("activities", "ended")
    assert completed["status"] == "completed"
    assert all(item["execution"]["status"] == "skipped" for item in completed["actions"].values())
    assert {item["id"] for item in store.list("life_action_usage")} == {
        "started:news",
        "started:search",
    }


def test_standalone_actions_and_children_are_retired_with_uncertain_usage(world):
    store, service = world
    standalone = activity(
        "old-search", kind="search", schema_version=1, actions={}, status="running"
    )
    child = activity("child", parent_id="future", schema_version=1)
    seed(store, [activity(), standalone, child])
    store.put("life_action_claims", "old-search", {"started_at": NOW.isoformat()})
    migrate_life(service)
    assert {row["id"] for row in store.list("activities")} == {"future"}
    assert {row["id"] for row in store.list("life_retired_activities")} == {"child", "old-search"}
    assert store.get("life_action_usage", "old-search")["kind"] == "search"
    assert store.get("life_action_claims", "old-search")
    assert len(store.list("life_day_history")[0]["activities"]) == 3


def test_usage_joins_history_claims_actions_and_multiple_deliveries(world):
    store, service = world
    old = activity("retired", start="08:00", end="09:00")
    old["actions"]["social"]["execution"] = {
        "status": "success",
        "finished_at": "2026-09-09T08:50:00+08:00",
    }
    store.put("life_day_history", "archived", {"id": "archived", "activities": [old]})
    store.put("life_action_claims", "retired:social", {"started_at": "2026-09-08T16:01:00+00:00"})
    store.put(
        "actions",
        "retired:social",
        {
            "id": "retired:social",
            "kind": "social",
            "scope": "global",
            "status": "success",
            "created_at": datetime.fromisoformat("2026-09-09T08:00:00+08:00").timestamp(),
        },
    )
    store.put(
        "social_actions",
        "draw",
        {
            "id": "draw",
            "action_id": "retired:social",
            "targets": ["one", "two"],
            "status": "success",
            "created_at": "2026-09-09T08:00:00+08:00",
        },
    )
    for key in ("one", "two"):
        store.put(
            "deliveries",
            key,
            {
                "id": key,
                "action_id": "retired:social",
                "scope": key,
                "status": "sent",
                "attempted": True,
                "created_at": NOW.isoformat(),
            },
        )
    migrate_life(service)
    assert len(store.list("life_action_usage")) == 1
    record = store.get("life_action_usage", "retired:social")
    assert record["date"] == "2026-09-09"
    assert record["started_at"] == "2026-09-09T00:01:00+08:00"
    assert record["scope"] == "global"


@pytest.mark.parametrize(
    "reason", ["no_eligible_targets", "quiet_hours", "cooldown", "module_disabled"]
)
def test_explicit_preflight_skip_does_not_import_usage(world, reason):
    store, service = world
    old = activity("skipped", start="11:00", end="13:00")
    old["actions"]["social"]["execution"] = {"status": "skipped", "reason": reason}
    seed(store, [old])
    store.put("life_action_claims", "skipped:social", {"started_at": NOW.isoformat()})
    store.put(
        "actions",
        "skipped:social",
        {
            "id": "skipped:social",
            "kind": "social",
            "status": "skipped",
            "reason": reason,
            "created_at": NOW.timestamp(),
        },
    )
    migrate_life(service)
    assert store.list("life_action_usage") == []


def test_orphan_claim_and_action_failure_are_imported_but_interjection_is_not(world):
    store, service = world
    store.put("life_action_claims", "lost:search", {"started_at": NOW.isoformat()})
    store.put(
        "actions",
        "legacy-news",
        {"id": "legacy-news", "kind": "news", "status": "failed", "created_at": NOW.timestamp()},
    )
    store.put(
        "social_actions",
        "interjection",
        {
            "id": "interjection",
            "action_id": "interject:chatter",
            "targets": ["qq:GroupMessage:1"],
            "status": "success",
            "created_at": NOW.isoformat(),
        },
    )
    store.put(
        "deliveries",
        "interjection",
        {
            "id": "interjection",
            "action_id": "interject:chatter",
            "interjection": True,
            "status": "sent",
            "attempted": True,
            "created_at": NOW.isoformat(),
        },
    )
    migrate_life(service)
    assert {row["id"] for row in store.list("life_action_usage")} == {"lost:search", "legacy-news"}


def test_prepared_marker_is_converted_before_publication_and_retirement_wins(world):
    store, service = world
    rows = [activity("fresh"), activity("retired")]
    seed(store, [], status="prepared", adopted_activities=rows)
    store.put("life_retired_activities", "retired", {"id": "retired", "replaced_by": "new"})
    migrate_life(service)
    assert store.get("activities", "retired") is None
    current = store.get("activities", "fresh")
    assert current["schema_version"] == 3 and not current["detailed"]
    marker = store.get("life_days", "2026-09-09:global")
    assert marker["status"] == "completed"
    assert marker["adopted_activities"] == [current]
    assert store.list("life_day_history")[0]["adopted_activities"] == rows


def test_repeated_upgrade_preserves_new_detail_templates_and_existing_usage(world):
    store, service = world
    seed(store, [activity()])
    for task in ("life.plan", "life.detail", "life.revise"):
        store.put("prompt_templates", task, {"id": task, "template": "旧自定义模板：" + task})
        store.put("prompt_defaults", task, {"id": task, "template": "旧默认：" + task})
    first = migrate_life(service)
    assert first["template_backups"] == 6
    for task in ("life.plan", "life.detail", "life.revise"):
        assert store.get("prompt_templates", task)["template"] == PROMPTS[task]
    custom = {"id": "life.detail", "template": "新流程的用户修改", "schema_version": 4}
    store.put("prompt_templates", "life.detail", custom)
    row = store.get("activities", "future")
    row.update(detailed=True, description="新细节")
    store.put("activities", "future", row)
    ledger = {
        "id": "future:news",
        "date": "2026-09-09",
        "kind": "news",
        "started_at": NOW.isoformat(),
        "imported": False,
    }
    store.put("life_action_usage", "future:news", ledger)
    before = store.export()
    assert migrate_life(service) == {
        "converted": 0,
        "retired": 0,
        "imported": 0,
        "template_backups": 0,
    }
    assert store.export() == before
    assert store.get("prompt_templates", "life.detail") == custom
    assert store.get("life_action_usage", "future:news") == ledger


def test_restore_old_backup_cannot_reactivate_retired_rows_or_templates(world):
    store, service = world
    seed(store, [activity(), activity("child", parent_id="future")])
    store.put(
        "prompt_templates", "life.detail", {"id": "life.detail", "template": "旧禁止行动模板"}
    )
    backup = store.export()
    migrate_life(service)
    store.restore(backup)
    migrate_life(service)
    assert store.get("activities", "child") is None
    assert store.get("activities", "future")["schema_version"] == 3
    assert store.get("prompt_templates", "life.detail")["template"] == PROMPTS["life.detail"]
    assert len(store.list("life_day_history")) == 1


def test_later_restore_converts_unknown_day_and_legacy_template_once(world):
    store, service = world
    migrate_life(service)
    # A reset/deletion from older software must not allow an unversioned template to stay active.
    store.delete("prompt_templates", "life.plan")
    old = activity("restored")
    store.restore(
        [
            {"namespace": "activities", "key": "restored", "value": old},
            {
                "namespace": "prompt_templates",
                "key": "life.plan",
                "value": {"id": "life.plan", "template": "旧规则"},
            },
        ]
    )
    assert migrate_life(service)["converted"] == 1
    assert store.get("activities", "restored")["schema_version"] == 3
    assert store.get("prompt_templates", "life.plan")["template"] == PROMPTS["life.plan"]
    before = store.export()
    migrate_life(service)
    assert store.export() == before


def test_superseded_legacy_orphan_never_joins_new_adopted_version(world):
    store, service = world
    seed(
        store,
        [activity("current", schema_version=3, version_id="new")],
        schema_version=3,
        version_id="new",
    )
    store.put("activities", "orphan", activity("orphan"))
    migrate_life(service)
    assert store.get("activities", "orphan") is None
    assert store.get("life_retired_activities", "orphan")
    assert store.get("activities", "current")["version_id"] == "new"


def test_migration_failure_rolls_back_every_namespace(world):
    store, service = world
    seed(store, [activity(), activity("child", parent_id="future")])
    store.put("prompt_templates", "life.detail", {"id": "life.detail", "template": "旧模板"})
    store.put("life_action_claims", "future:news", {"started_at": NOW.isoformat()})
    before = store.export()
    store.db.execute(
        "CREATE TRIGGER reject_migration BEFORE INSERT ON objects "
        "WHEN NEW.namespace = 'prompt_template_history' BEGIN "
        "SELECT RAISE(ABORT, 'migration failure'); END"
    )
    store.db.commit()
    with pytest.raises(sqlite3.IntegrityError, match="migration failure"):
        migrate_life(service)
    assert store.export() == before
    assert not store.get("life_migrations", MIGRATION_KEY)
    store.db.execute("DROP TRIGGER reject_migration")
    store.db.commit()
    assert migrate_life(service)["converted"] == 1


def test_current_generation_preflight_is_not_reinterpreted_as_legacy_usage(world):
    store, service = world
    migrate_life(service)
    row = activity("new", schema_version=3)
    row["actions"]["news"]["execution"] = {"status": "failed", "finished_at": NOW.isoformat()}
    store.put("activities", "new", row)
    store.put(
        "actions",
        "new:news",
        {
            "id": "new:news",
            "kind": "news",
            "status": "failed",
            "schema_version": 3,
            "created_at": NOW.timestamp(),
        },
    )
    store.put("life_action_claims", "new:news", {"started_at": NOW.isoformat()})
    migrate_life(service)
    assert store.list("life_action_usage") == []


def test_orphan_social_delivery_and_interrupted_action_remain_traceable(world):
    store, service = world
    store.put(
        "social_actions",
        "draw",
        {
            "id": "draw",
            "action_id": "old-round",
            "targets": ["one"],
            "status": "unknown",
            "created_at": NOW.isoformat(),
        },
    )
    store.put(
        "deliveries",
        "orphan",
        {
            "id": "orphan",
            "action_id": "separate-round",
            "interjection": False,
            "status": "sent",
            "attempted": True,
            "created_at": NOW.isoformat(),
        },
    )
    store.put(
        "actions",
        "interrupted",
        {
            "id": "interrupted",
            "kind": "search",
            "status": "interrupted",
            "created_at": NOW.timestamp(),
        },
    )
    migrate_life(service)
    assert {row["id"] for row in store.list("life_action_usage")} == {
        "old-round",
        "separate-round",
        "interrupted",
    }


def test_unknown_legacy_claim_without_timestamp_is_conservatively_counted(world):
    store, service = world
    store.put("life_action_claims", "unknown:search", {})
    migrate_life(service)
    record = store.get("life_action_usage", "unknown:search")
    assert record["date"] == str(NOW.date())
    assert record["imported"]
    assert migrate_life(service)["imported"] == 0
