"""Drive upgrades preserve evidence and atomically retire obsolete future decisions."""

import copy
import sqlite3
from types import SimpleNamespace

import pytest

from living_world.drives_migration import MIGRATION_KEY, migrate_drives
from living_world.prompts import PROMPTS
from living_world.store import Store
from test_life_migration import NOW, activity, seed


@pytest.fixture
def world(tmp_path):
    store = Store(tmp_path / "upgrade.sqlite")
    runtime = SimpleNamespace(store=store, life=SimpleNamespace(_now=lambda: NOW))
    yield runtime
    store.close()


def test_future_detail_and_private_overlay_are_archived_before_clearing(world):
    old = activity(
        schema_version=3,
        detail_attempts=2,
        detail_raw="original JSON",
        scope_overrides={
            "qq:FriendMessage:42": {
                "title": "私人散步约定",
                "content": "留在私人场合",
                "location": "小桥",
                "description": "旧私人细节",
                "incident": "私人小插曲",
                "energy_delta": 5,
                "actions": {"social": {"enabled": True}},
                "detailed": True,
                "detail_attempts": 2,
            }
        },
    )
    marker = seed(world.store, [old], schema_version=3)
    result = migrate_drives(world)
    assert result["converted"] == 1
    current = world.store.get("activities", old["id"])
    assert current["schema_version"] == 3 and current["drive_schema_version"] == 1
    assert not current["detailed"] and current["detail_attempts"] == 0
    for field in ("description", "incident", "energy_delta", "mood", "detail_error", "detail_raw"):
        assert field not in current
    assert all(not a["enabled"] for a in current["actions"].values())
    assert current["scope_overrides"] == {
        "qq:FriendMessage:42": {
            "title": "私人散步约定",
            "content": "留在私人场合",
            "location": "小桥",
        }
    }
    for key in ("id", "start", "end", "title", "content", "scope", "location", "sleep_state"):
        assert current[key] == old[key]
    history = world.store.list("life_detail_history")
    assert len(history) == 1 and history[0]["activity"] == old
    assert history[0]["reason"] == "inner_drives_upgrade"
    saved = world.store.get("life_days", f"{NOW.date()}:global")
    assert saved["raw_output"] == marker["raw_output"]
    assert saved["adopted_activities"] == [current]


@pytest.mark.parametrize("status", ["running", "completed"])
def test_started_activity_keeps_exact_decisions_and_execution_evidence(world, status):
    old = activity("started", start="11:00", end="13:00", schema_version=3, status=status)
    old["actions"]["news"]["execution"] = {"status": "success", "result": {"text": "实际阅读"}}
    old["actions"]["search"]["execution"] = {"status": "running", "started_at": NOW.isoformat()}
    seed(world.store, [old], schema_version=3)
    world.store.put("life_action_usage", "started:news", {"id": "started:news", "kind": "news"})
    migrate_drives(world)
    current = world.store.get("activities", "started")
    assert current == {**old, "drive_schema_version": 1}
    assert len(world.store.list("life_action_usage")) == 1
    assert world.store.get("drive_state", "current")["values"] == {"loneliness": 0, "energy": 70}
    assert not world.store.list("drive_debits")


@pytest.mark.parametrize(
    "state_energy,config_energy,expected",
    [(32.5, 88, 32.5), (None, 88, 88), (None, None, 70), (-5, 88, 0), (120, 88, 100)],
)
def test_initial_energy_uses_saved_state_then_legacy_configuration(
    world, state_energy, config_energy, expected
):
    if state_energy is not None:
        world.store.put("life_state", "current", {"energy": state_energy, "mood": "平静"})
    migrate_drives(world, {"character": {"energy": config_energy}})
    assert world.store.get("drive_state", "current")["values"] == {
        "loneliness": 0,
        "energy": expected,
    }


def test_repeat_upgrade_keeps_new_values_details_and_templates(world):
    seed(world.store, [activity(schema_version=3)], schema_version=3)
    for task in ("life.plan", "life.detail", "life.revise"):
        world.store.put(
            "prompt_templates", task, {"template": "旧模板 " + task, "schema_version": 3}
        )
    migrate_drives(world)
    archives = copy.deepcopy(world.store.list("prompt_template_history"))
    assert len(archives) == 3
    for task in ("life.plan", "life.detail", "life.revise"):
        assert world.store.get("prompt_templates", task) == {
            "id": task,
            "template": PROMPTS[task],
            "schema_version": 4,
        }
    world.store.put("drive_state", "current", {"values": {"loneliness": 83, "energy": 14}})
    current = world.store.get("activities", "future")
    current.update(detailed=True, description="升级后新细化")
    world.store.put("activities", "future", current)
    world.store.put(
        "prompt_templates", "life.detail", {"template": "升级后新指令", "schema_version": 4}
    )
    before = world.store.export()
    assert migrate_drives(world)["converted"] == 0
    assert world.store.export() == before
    assert world.store.list("prompt_template_history") == archives


def test_sqlite_failure_rolls_back_values_templates_and_detail_archives(world):
    seed(world.store, [activity(schema_version=3)], schema_version=3)
    before = world.store.export()
    world.store.db.execute(
        "CREATE TEMP TRIGGER reject_drive_archive BEFORE INSERT ON objects WHEN NEW.namespace='life_detail_history' BEGIN SELECT RAISE(ABORT, 'archive disk failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="archive disk failure"):
        migrate_drives(world)
    assert world.store.export() == before
    assert world.store.get("life_migrations", MIGRATION_KEY) is None
    world.store.db.execute("DROP TRIGGER reject_drive_archive")
    assert migrate_drives(world)["converted"] == 1


def test_import_after_migration_converts_new_legacy_rows_without_resetting_values(world):
    migrate_drives(world)
    world.store.put("drive_state", "current", {"values": {"loneliness": 61, "energy": 27}})
    world.store.put("drive_debits", "keep", {"id": "keep", "kind": "search"})
    old = activity("restored", schema_version=3)
    world.store.restore([{"namespace": "activities", "key": old["id"], "value": old}])
    migrate_drives(world, {"character": {"energy": 99}})
    assert not world.store.get("activities", "restored")["detailed"]
    assert world.store.get("drive_state", "current")["values"] == {"loneliness": 61, "energy": 27}
    assert world.store.get("drive_debits", "keep")["kind"] == "search"
    assert world.store.list("life_detail_history")[0]["activity"] == old


def test_imported_obsolete_version_cannot_join_current_execution_queue(world):
    current = activity("current", schema_version=3, drive_schema_version=1, version_id="new")
    seed(world.store, [current], schema_version=3, drive_schema_version=1, version_id="new")
    old = activity("restored", schema_version=3, version_id="old")
    world.store.put("activities", old["id"], old)
    result = migrate_drives(world)
    assert result["retired"] == 1
    assert world.store.list("activities") == [current]
    assert world.store.get("life_retired_activities", "restored")
    assert world.store.list("life_detail_history")[0]["activity"] == old


def test_upgraded_backup_with_obsolete_plan_version_is_still_retired(world):
    current = activity("current", schema_version=3, drive_schema_version=1, plan_version="new")
    seed(world.store, [current], schema_version=3, drive_schema_version=1, version_id="new")
    old = activity("restored", schema_version=3, drive_schema_version=1, plan_version="old")
    world.store.restore([{"namespace": "activities", "key": old["id"], "value": old}])
    result = migrate_drives(world)
    assert result["retired"] == 1 and result["converted"] == 0
    assert world.store.list("activities") == [current]
    assert world.store.get("life_retired_activities", "restored")
    assert world.store.list("life_detail_history")[0]["activity"] == old
    assert world.store.get("life_days", f"{NOW.date()}:global")["adopted_activities"] == [current]


def test_prepared_marker_discards_upgraded_obsolete_or_retired_rows_without_reviving_them(world):
    current = activity("current", schema_version=3, drive_schema_version=1, plan_version="new")
    obsolete = activity("obsolete", schema_version=3, drive_schema_version=1, plan_version="old")
    retired = activity("retired", schema_version=3, drive_schema_version=1, plan_version="new")
    seed(
        world.store,
        [current],
        status="prepared",
        schema_version=3,
        drive_schema_version=1,
        version_id="new",
        adopted_activities=[current, obsolete, retired],
    )
    world.store.put(
        "life_retired_activities", "retired", {"id": "retired", "reason": "previously_replaced"}
    )
    result = migrate_drives(world)
    assert result["retired"] == 2
    assert world.store.list("activities") == [current]
    assert world.store.get("life_days", f"{NOW.date()}:global")["adopted_activities"] == [current]
    archived = {
        entry["activity_id"]: entry["activity"] for entry in world.store.list("life_detail_history")
    }
    assert archived == {"obsolete": obsolete, "retired": retired}
    before = world.store.export()
    assert migrate_drives(world)["retired"] == 0
    assert world.store.export() == before


@pytest.mark.parametrize("with_version", [False, True])
def test_current_marker_member_survives_regenerated_plan_upgrade(world, with_version):
    current = activity("current", schema_version=3)
    if with_version:
        current["plan_version"] = "new"
    seed(world.store, [current], schema_version=3, version_id="new")
    old = activity("obsolete", schema_version=3, plan_version="old")
    world.store.put("activities", old["id"], old)
    result = migrate_drives(world)
    assert result["converted"] == 1 and result["retired"] == 1
    saved = world.store.get("activities", "current")
    assert not saved["detailed"] and saved["drive_schema_version"] == 1
    assert world.store.get("activities", "obsolete") is None
    assert world.store.get("life_retired_activities", "obsolete")


def test_prepared_old_marker_publishes_only_converted_outline(world):
    old = activity("prepared", schema_version=3, plan_version="prepared-version")
    seed(
        world.store,
        [],
        status="prepared",
        schema_version=3,
        version_id="prepared-version",
        adopted_activities=[old],
    )
    migrate_drives(world)
    saved = world.store.get("activities", "prepared")
    assert saved and saved["drive_schema_version"] == 1 and not saved["detailed"]
    assert all(not a["enabled"] for a in saved["actions"].values())
    assert world.store.get("life_days", f"{NOW.date()}:global")["adopted_activities"] == [saved]
    assert world.store.list("life_detail_history")[0]["activity"] == old


def test_startup_rollback_keeps_legacy_schedule_when_drive_upgrade_fails(tmp_path, monkeypatch):
    from living_world.runtime import Runtime

    path = tmp_path / "startup.sqlite"
    store = Store(path)
    seed(store, [activity()])
    before = store.export()
    store.db.execute(
        "CREATE TRIGGER reject_drive_state BEFORE INSERT ON objects WHEN NEW.namespace='drive_state' BEGIN SELECT RAISE(ABORT, 'drive initialization failure'); END"
    )
    store.db.commit()
    store.close()
    opened = []

    def tracked_store(path):
        connection = Store(path)
        opened.append(connection)
        return connection

    monkeypatch.setattr("living_world.runtime.Store", tracked_store)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="drive initialization failure"):
            Runtime(path, SimpleNamespace())
        assert opened[0].export() == before
        assert opened[0].get("life_migrations", MIGRATION_KEY) is None
        assert not opened[0].list("life_day_history")
    finally:
        for connection in opened:
            connection.close()
