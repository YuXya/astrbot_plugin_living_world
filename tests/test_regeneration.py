"""Manual plan replacement against real SQLite, with model and action substitutes."""

import asyncio
import copy
import json
import sqlite3
from datetime import timedelta
from types import SimpleNamespace

import pytest

from living_world.store import Store
from test_life import NOW, Runtime, fixed_service, plan_rows, prepared_activity


@pytest.fixture
def world(tmp_path):
    runtime = Runtime()
    runtime.store = Store(tmp_path / "world.sqlite")
    life = fixed_service(runtime)
    clock = [NOW]
    convert = life._now
    life._now = lambda value=None: convert(value or clock[0])
    yield runtime, life, clock
    runtime.store.close()


def model_result(rows=None):
    return json.dumps({"activities": rows if rows is not None else plan_rows()}, ensure_ascii=False)


async def seed_day(runtime, life):
    runtime.responses = [model_result()]
    rows = await life.plan_day()
    for row in rows:
        row["detailed"] = True
        runtime.store.put("activities", row["id"], row)
    return rows


async def test_repeated_regeneration_archives_execution_and_uses_latest_parameters(world):
    runtime, life, _ = world
    old = await seed_day(runtime, life)
    old[0] = prepared_activity(runtime, old[0], ("news",))
    assert life.consume_action(old[0]["id"], "news", now=NOW)
    old[0]["actions"]["news"]["execution"] = {"status": "success", "result": {"text": "read"}}
    runtime.store.put("activities", old[0]["id"], old[0])
    runtime.store.put("activities", "child", {**old[1], "id": "child", "parent_id": old[0]["id"]})
    yesterday = {**old[0], "id": "yesterday", "date": str(NOW.date() - timedelta(days=1))}
    runtime.store.put("activities", "yesterday", yesterday)
    protected = (
        "life_action_claims",
        "life_fiction_claims",
        "deliveries",
        "actions",
        "events",
        "memories",
    )
    for namespace in protected:
        runtime.store.put(namespace, "keep", {"id": "keep", "status": "success", "count": 4})
    previous_marker = runtime.store.get("life_days", f"{NOW.date()}:global")
    runtime.settings["life"].update(activity_count=5, news_count=1, search_count=2, social_count=3)
    proposal = plan_rows(count=5)
    proposal[0]["title"] = "新计划中的数学课"
    runtime.responses = [model_result(proposal), model_result(proposal)]
    first = await life.regenerate_day()
    second = await life.regenerate_day()
    assert len(second) == 5
    ids = [{row["id"] for row in group} for group in (old, first, second)]
    assert not (ids[0] & ids[1] or ids[1] & ids[2] or ids[0] & ids[2])
    marker = runtime.store.get("life_days", f"{NOW.date()}:global")
    assert marker["parameters"]["activity_count"] == 5
    assert marker["request"]["context"]["parameters"] == {
        "daily_plan_time": "06:00",
        "activity_count": 5,
    }
    assert life.limits() == {"news": 1, "search": 2, "social": 3}
    assert all(not a["enabled"] for row in second for a in row["actions"].values())
    assert marker["adopted_activities"] == second
    history = runtime.store.list("life_day_history")
    assert len(history) == 2
    archived = next(item for item in history if len(item["activities"]) == 11)
    assert archived["raw_json"] == previous_marker["raw_json"]
    assert archived["parameters"] == previous_marker["parameters"]
    assert next(row for row in archived["activities"] if row["id"] == old[0]["id"]) == old[0]
    assert runtime.store.get("activities", "child") is None
    assert runtime.store.get("activities", "yesterday") == yesterday
    assert [row["title"] for row in life.schedule_context()["activities"]] == [
        row["title"] for row in second
    ]
    assert life.day_summary()["activity_count"] == 5
    assert life.budget()["news"]["used"] == 1
    assert len(runtime.store.list("life_action_usage")) == 1
    for namespace in protected:
        assert runtime.store.get(namespace, "keep")["count"] == 4
    calls = len(runtime.calls)
    assert await fixed_service(runtime).plan_day() == second
    assert len(runtime.calls) == calls


async def test_manual_generation_before_due_without_existing_plan(world):
    runtime, life, clock = world
    clock[0] = NOW.replace(hour=5)
    assert await life.plan_day() == []
    assert runtime.calls == []
    runtime.responses = [model_result()]
    result = await life.regenerate_day(str(clock[0].date()))
    assert len(result) == 10
    assert runtime.store.list("life_day_history") == []
    assert await life.plan_day() == result
    assert len(runtime.calls) == 1


@pytest.mark.parametrize("bad", ["not json", "[]", '{"activities":[]}', model_result(plan_rows(9))])
async def test_invalid_generation_keeps_all_previous_records(world, bad):
    runtime, life, _ = world
    await seed_day(runtime, life)
    before = runtime.store.export()
    runtime.responses = [bad]
    with pytest.raises((ValueError, TypeError)):
        await life.regenerate_day()
    assert runtime.store.export() == before
    assert not life.regenerating


@pytest.mark.parametrize("change", ["midnight", "disabled", "binding", "timezone", "error"])
async def test_generation_failure_or_environment_change_never_publishes(world, change):
    runtime, life, clock = world
    await seed_day(runtime, life)
    before = runtime.store.export()

    async def generate(*args, **kwargs):
        if change == "midnight":
            clock[0] += timedelta(days=1)
        elif change == "disabled":
            runtime.disabled.add("life")
        elif change == "binding":
            runtime.settings["persona_id"] = "another-persona"
        elif change == "timezone":
            runtime.settings["character"]["timezone"] = "UTC"
        else:
            raise OSError("Model unavailable")
        return model_result()

    runtime.generate = generate
    with pytest.raises((ValueError, OSError)):
        await life.regenerate_day()
    assert runtime.store.export() == before
    assert not life.regenerating


async def test_late_publication_does_not_invent_actions_for_started_activities(world):
    runtime, life, clock = world

    async def generate(*args, **kwargs):
        clock[0] += timedelta(minutes=65)
        return model_result()

    runtime.generate = generate
    result = await life.regenerate_day()
    assert all(not a["enabled"] for row in result for a in row["actions"].values())
    for row in result[:2]:
        with pytest.raises(ValueError):
            await life.detail(row["id"])
    assert not runtime.actions


async def test_regeneration_rejects_wrong_date_scope_and_unavailable_module(world):
    runtime, life, _ = world
    for args in ({"day": "2000-01-01"}, {"scope": "private-a"}):
        with pytest.raises(ValueError):
            await life.regenerate_day(**args)
    runtime.disabled.add("life")
    with pytest.raises(ValueError, match="已关闭"):
        await life.regenerate_day()
    runtime.disabled.clear()
    runtime.scope_allowed = lambda scope: asyncio.sleep(0, result=False)
    with pytest.raises(ValueError, match="未接入"):
        await life.regenerate_day()
    assert not life.regenerating
    assert runtime.calls == []
    assert runtime.store.export() == []


async def test_waiting_regeneration_freezes_parameters_template_and_rejects_edits(world):
    runtime, life, _ = world
    rows = await seed_day(runtime, life)
    runtime.settings["template"] = "First saved template"
    runtime.debug = SimpleNamespace(template=lambda task, fallback: runtime.settings["template"])
    received = []

    async def complete(task, module, template, context, scope="global", **kwargs):
        received.append((template, copy.deepcopy(context), kwargs))
        return model_result()

    runtime.complete = complete
    await life._tick_lock.acquire()
    task = asyncio.create_task(life.regenerate_day())
    try:
        await asyncio.sleep(0)
        assert life.regenerating
        runtime.settings["life"]["activity_count"] = 5
        runtime.settings["template"] = "Later saved template"
        with pytest.raises(ValueError, match="等待"):
            await life.regenerate_day()
        with pytest.raises(ValueError, match="编辑"):
            life.update_activity(rows[1]["id"], {"title": "Unwanted edit"})
        with pytest.raises(ValueError, match="细化"):
            await life.detail(rows[1]["id"])
        await life.tick()
        assert received == []
    finally:
        life._tick_lock.release()
    result = await task
    assert len(result) == 10
    assert received[0][0] == "First saved template"
    assert received[0][1]["parameters"]["activity_count"] == 10
    assert received[0][2]["frozen_template"] is True
    assert not life.regenerating


@pytest.mark.parametrize("waiting", [True, False])
async def test_cancellation_releases_locks_and_keeps_old_day(world, waiting):
    runtime, life, _ = world
    await seed_day(runtime, life)
    before = runtime.store.export()
    entered = asyncio.Event()

    async def generate(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    runtime.generate = generate
    if waiting:
        await life._tick_lock.acquire()
    task = asyncio.create_task(life.regenerate_day())
    if waiting:
        await asyncio.sleep(0)
    else:
        await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    if waiting:
        life._tick_lock.release()
    assert not life.regenerating
    assert (
        not life._tick_lock.locked()
        and not life._plan_lock.locked()
        and not life._detail_lock.locked()
    )
    assert runtime.store.export() == before


async def test_atomic_store_failure_rolls_back_archive_and_activity_replacement(world):
    runtime, life, _ = world
    await seed_day(runtime, life)
    before = runtime.store.export()
    runtime.store.db.execute(
        "CREATE TEMP TRIGGER reject_regeneration BEFORE INSERT ON objects "
        "WHEN NEW.namespace='life_days' AND NEW.value LIKE '%manual_regeneration%' "
        "BEGIN SELECT RAISE(ABORT, 'simulated disk failure'); END"
    )
    runtime.responses = [model_result()]
    with pytest.raises(sqlite3.IntegrityError, match="disk failure"):
        await life.regenerate_day()
    assert runtime.store.export() == before
    assert not life.regenerating
    runtime.store.db.execute("DROP TRIGGER reject_regeneration")
    runtime.responses = [model_result()]
    await life.regenerate_day()
    assert len(runtime.store.list("life_day_history")) == 1


async def test_running_tick_finishes_before_replacement_and_new_flags_run_once(world):
    runtime, life, clock = world
    old = await seed_day(runtime, life)
    old[0] = prepared_activity(runtime, old[0])
    life._first_tick = False
    entered, release = asyncio.Event(), asyncio.Event()

    async def execute(kind, payload, scope, action_id):
        assert runtime.consume(kind, payload)
        runtime.actions.append((kind, payload, scope, action_id))
        entered.set()
        await release.wait()
        return {"status": "success"}

    runtime.execute_action = execute
    tick = asyncio.create_task(life.tick())
    await asyncio.wait_for(entered.wait(), 2)
    runtime.responses = [model_result()]
    regenerate = asyncio.create_task(life.regenerate_day())
    await asyncio.sleep(0)
    assert len(runtime.calls) == 1
    assert not regenerate.done()
    release.set()
    await tick
    new = await regenerate
    history = runtime.store.list("life_day_history")[0]
    retired = next(row for row in history["activities"] if row["id"] == old[0]["id"])
    assert all(action["execution"]["status"] == "success" for action in retired["actions"].values())
    prepared_activity(runtime, new[1])
    clock[0] += timedelta(hours=1)
    await life.tick()
    await life.tick()
    keys = [call[3] for call in runtime.actions]
    assert len(keys) == len(set(keys)) == 6
    assert [call[0] for call in runtime.actions] == ["news", "search", "social"] * 2
    assert all(value["used"] == 2 for value in life.budget().values())
    restarted = fixed_service(runtime, clock[0])
    await restarted.tick(clock[0] + timedelta(seconds=1))
    assert len(runtime.actions) == 6


async def test_private_revisions_reapply_per_version_without_crossing_scopes(world):
    runtime, life, _ = world
    old = await seed_day(runtime, life)
    old[1]["scope_overrides"] = {"private-a": {"title": "旧版私人约定"}}
    runtime.store.put("activities", old[1]["id"], old[1])
    runtime.store.put("life_scoped_revisions", f"{NOW.date()}:private-a", {"status": "completed"})
    memory = {"id": "promise", "scope": "private-a", "text": "秘密约定"}
    runtime.memory.entries = [memory]
    runtime.store.put("memories", memory["id"], memory)
    runtime.responses = [model_result()]
    new = await life.regenerate_day()
    assert "秘密约定" not in runtime.calls[-1][1]
    runtime.responses = [
        json.dumps({"updates": [{"id": new[1]["id"], "changes": {"title": "新版私人约定"}}]})
    ]
    await life._revise_daily_scopes(NOW)
    count = len(runtime.calls)
    await life._revise_daily_scopes(NOW)
    assert len(runtime.calls) == count
    assert "新版私人约定" in json.dumps(life.schedule_context("private-a"), ensure_ascii=False)
    for scope in ("global", "private-b"):
        assert "私人约定" not in json.dumps(life.schedule_context(scope), ensure_ascii=False)
    assert len(runtime.store.list("life_scoped_revisions")) == 2
    assert (
        runtime.store.get("life_scoped_revisions", f"{NOW.date()}:private-a")["status"]
        == "completed"
    )


async def test_current_day_survives_sqlite_reopen(world):
    runtime, life, _ = world
    await seed_day(runtime, life)
    runtime.responses = [model_result()]
    new = await life.regenerate_day()
    path = runtime.store.db.execute("PRAGMA database_list").fetchone()[2]
    runtime.store.close()
    runtime.store = Store(path)
    restarted = fixed_service(runtime)
    assert await restarted.plan_day() == new
    assert len(runtime.store.list("life_day_history")) == 1
    assert len(runtime.calls) == 2


async def test_old_backup_cannot_reactivate_retired_activities(world):
    runtime, life, _ = world
    old = await seed_day(runtime, life)
    tomorrow = str(NOW.date() + timedelta(days=1))
    runtime.store.put(
        "activities",
        "child",
        {**old[1], "id": "child", "date": tomorrow, "parent_id": old[0]["id"]},
    )
    backup = runtime.store.export()
    runtime.responses = [model_result()]
    new = await life.regenerate_day()
    runtime.store.restore(backup)
    assert runtime.store.get("activities", old[0]["id"]) is not None
    assert life.list_activities() == new
    assert life.day_summary()["activity_count"] == 10
    assert await fixed_service(runtime).plan_day() == new


async def test_actual_result_totals_survive_regeneration_and_deduplicate_history(world):
    runtime, life, clock = world
    rows = await seed_day(runtime, life)
    prepared_activity(runtime, rows[0])
    life._first_tick = False

    async def execute(kind, payload, scope, action_id):
        runtime.actions.append((kind, payload, scope, action_id))
        if kind == "social":
            return {"status": "skipped", "reason": "quiet_hours"}
        assert runtime.consume(kind, payload)
        return {"status": "success" if kind == "news" else "failed", "reason": "fixture"}

    runtime.execute_action = execute
    await life.tick()
    first_result = life.day_summary()["counts"]
    assert first_result["news"]["success"] == first_result["news"]["used"] == 1
    assert first_result["search"]["failed"] == first_result["search"]["used"] == 1
    assert first_result["social"]["skipped"] == 1 and first_result["social"]["used"] == 0
    retired = runtime.store.get("activities", rows[0]["id"])
    for _ in range(2):
        runtime.responses = [model_result()]
        current = await life.regenerate_day()
    duplicate = copy.deepcopy(runtime.store.list("life_day_history")[-1])
    duplicate.update(id="duplicate-import", activities=[retired])
    runtime.store.put("life_day_history", duplicate["id"], duplicate)
    runtime.store.put(
        "life_detail_history",
        "duplicate-detail",
        {
            "id": "duplicate-detail",
            "activity_id": retired["id"],
            "activity": retired,
        },
    )
    historical = life.day_summary()["counts"]
    for kind, outcome in (("news", "success"), ("search", "failed"), ("social", "skipped")):
        assert historical[kind][outcome] == 1
        assert historical[kind]["used"] == int(kind != "social")
    prepared_activity(runtime, current[1], ("news",))
    clock[0] += timedelta(hours=1)
    await life.tick()
    final = life.day_summary()["counts"]
    assert final["news"]["used"] == final["news"]["success"] == 2
    assert final["search"]["failed"] == final["social"]["skipped"] == 1
