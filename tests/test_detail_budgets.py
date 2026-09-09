"""Detail adoption, budget accounting and races against persistent SQLite state."""

import asyncio
import copy
import json
import sqlite3
from datetime import datetime, timedelta

import pytest

from living_world.store import Store
from test_life import (
    KINDS,
    NOW,
    Runtime,
    activity,
    detail_result,
    fixed_service,
    plan_rows,
    prepared_activity,
    publish,
)


@pytest.fixture
def world(tmp_path):
    runtime = Runtime()
    runtime.store.close()
    runtime.store = Store(tmp_path / "details.sqlite")
    life = fixed_service(runtime)
    clock = [NOW]
    convert = life._now
    life._now = lambda value=None: convert(value or clock[0])
    yield runtime, life, clock
    runtime.store.close()


async def outline(world):
    runtime, life, clock = world
    _, rows = await publish(runtime, life, plan_rows(start=clock[0] + timedelta(minutes=10)))
    return rows


async def adopt(world, row, kinds=KINDS, **kwargs):
    runtime, life, _ = world
    runtime.responses = [json.dumps(detail_result(row, kinds), ensure_ascii=False)]
    return await life.detail(row["id"], **kwargs)


@pytest.mark.parametrize("kinds", [(), ("search",), KINDS])
async def test_detail_naturally_selects_none_one_or_several_actions(world, kinds):
    runtime, life, _ = world
    rows = await outline(world)
    before = life.budget()
    result = await adopt(
        world, rows[0], kinds, instruction="主动聊天本次不安排。" if not kinds else ""
    )
    assert result["detailed"] and result["detail_version"]
    assert {kind for kind, value in result["actions"].items() if value["enabled"]} == set(kinds)
    for field in ("id", "title", "content", "location", "sleep_state", "start", "end", "scope"):
        assert result[field] == rows[0][field]
    for kind, budget in life.budget().items():
        assert budget["used"] == 0
        assert budget["reserved"] == int(kind in kinds)
        assert budget["available"] == before[kind]["available"] - int(kind in kinds)
    assert not runtime.actions and not runtime.events
    assert result["detail_raw"]
    if not kinds:
        assert result["detail_request"]["context"]["管理员本次要求"] == "主动聊天本次不安排。"


def test_detail_request_is_read_only_and_filters_private_material(world):
    runtime, life, _ = world
    row = activity(runtime, start=(NOW + timedelta(minutes=10)).isoformat(), detailed=False)
    runtime.memory.entries = [
        {"scope": "global", "text": "公共兴趣是天文"},
        {"scope": "private-a", "text": "私人礼物约定"},
    ]
    runtime.store.put(
        "events", "public", {"scope": "global", "source": "news", "text": "公开新闻实际读过"}
    )
    runtime.store.put(
        "events", "secret", {"scope": "private-a", "source": "action", "text": "私聊消息正文秘密"}
    )
    activity(runtime, "later", start=(NOW + timedelta(hours=2)).isoformat(), title="今天还要去上课")
    before = runtime.store.export()
    request = life.detail_request(row, instruction="只考虑自然的行动，不要凑数。")
    text = json.dumps(request["context"], ensure_ascii=False)
    assert request["scope"] == "global" and request["template"]
    assert "公共兴趣是天文" in text and "公开新闻实际读过" in text
    assert "今天还要去上课" in text and "只考虑自然的行动" in text
    assert "私人礼物约定" not in text and "私聊消息正文秘密" not in text
    assert "上限 2" in text and "已使用 0" in text and "已预留 0" in text
    assert runtime.store.export() == before
    assert runtime.calls == runtime.events == runtime.actions == []


@pytest.mark.parametrize(
    "problem",
    [
        "json",
        "shape",
        "missing_kind",
        "enabled_type",
        "intent",
        "outside",
        "order",
        "energy",
        "outline",
    ],
)
async def test_invalid_detail_preserves_old_detail_and_reservations(world, problem):
    runtime, life, _ = world
    rows = await outline(world)
    old = await adopt(world, rows[0], ("search",))
    value = detail_result(rows[0])
    if problem == "shape":
        value = []
    elif problem == "missing_kind":
        del value["actions"]["search"]
    elif problem == "enabled_type":
        value["actions"]["news"]["enabled"] = "true"
    elif problem == "intent":
        value["actions"]["news"]["intent"] = ""
    elif problem == "outside":
        value["actions"]["social"]["at"] = rows[0]["end"]
    elif problem == "order":
        value["actions"]["news"]["at"] = (
            datetime.fromisoformat(rows[0]["start"]) + timedelta(minutes=5)
        ).isoformat()
    elif problem == "energy":
        value["energy_delta"] = 11
    elif problem == "outline":
        value["start"] = rows[1]["start"]
    runtime.responses = ["not json" if problem == "json" else json.dumps(value)]
    budget = life.budget()
    with pytest.raises((ValueError, TypeError)):
        await life.detail(rows[0]["id"], regenerate=True)
    assert runtime.store.get("activities", rows[0]["id"]) == old
    assert life.budget() == budget
    assert not runtime.actions and not runtime.events


async def test_model_cannot_forge_execution_records_in_a_detail(world):
    runtime, life, _ = world
    rows = await outline(world)
    result = detail_result(rows[0], ("search",))
    result["actions"]["search"]["execution"] = {"status": "success", "text": "模型编造的搜索结果"}
    runtime.responses = [json.dumps(result)]
    adopted = await life.detail(rows[0]["id"])
    assert adopted["actions"]["search"]["execution"] == {"status": "pending"}
    assert life.budget()["search"]["used"] == 0
    assert not runtime.actions and not runtime.events


async def test_redetail_replaces_reservation_and_archives_previous_version(world):
    runtime, life, _ = world
    rows = await outline(world)
    old = await adopt(world, rows[0], ("search",))
    unchanged_calls = len(runtime.calls)
    assert await life.detail(rows[0]["id"]) == old
    assert len(runtime.calls) == unchanged_calls
    new = await adopt(
        world, rows[0], ("social",), regenerate=True, instruction="这次改成和朋友聊天"
    )
    assert new["detail_version"] != old["detail_version"]
    assert life.budget()["search"]["reserved"] == 0
    assert life.budget()["social"]["reserved"] == 1
    history = runtime.store.list("life_detail_history")
    assert len(history) == 1 and history[0]["activity"] == old
    assert new["detail_request"]["context"]["管理员本次要求"] == "这次改成和朋友聊天"


async def test_detail_replacement_sqlite_failure_rolls_back_history_and_reservations(world):
    runtime, life, _ = world
    rows = await outline(world)
    old = await adopt(world, rows[0], ("search",))
    before = runtime.store.export()
    runtime.store.db.execute(
        "CREATE TEMP TRIGGER reject_detail_history BEFORE INSERT ON objects "
        "WHEN NEW.namespace='life_detail_history' "
        "BEGIN SELECT RAISE(ABORT, 'simulated detail disk failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="detail disk failure"):
        await adopt(world, rows[0], ("social",), regenerate=True)
    assert runtime.store.export() == before
    assert runtime.store.get("activities", rows[0]["id"]) == old
    assert life.budget()["search"]["reserved"] == 1
    assert life.budget()["social"]["reserved"] == 0


async def test_parallel_details_cannot_reserve_the_last_slot_twice(world):
    runtime, life, _ = world
    rows = await outline(world)
    runtime.settings["life"]["search_count"] = 1
    runtime.responses = [json.dumps(detail_result(row, ("search",))) for row in rows[:2]]
    results = await asyncio.gather(
        *(life.detail(row["id"]) for row in rows[:2]), return_exceptions=True
    )
    assert sum(isinstance(result, dict) for result in results) == 1
    assert sum(isinstance(result, ValueError) for result in results) == 1
    assert life.budget()["search"] == {"limit": 1, "used": 0, "reserved": 1, "available": 0}
    assert not runtime.actions


@pytest.mark.parametrize("change", ["started", "edited", "disabled", "scope"])
async def test_inflight_detail_cannot_publish_after_environment_changes(world, change):
    runtime, life, clock = world
    rows = await outline(world)
    row = rows[0]
    allowed = True

    async def scope_allowed(scope):
        return allowed

    async def generate(*args, **kwargs):
        nonlocal allowed
        if change == "started":
            clock[0] = datetime.fromisoformat(row["start"])
        elif change == "edited":
            updated = copy.deepcopy(row)
            updated["title"] = "另一次已保存的大纲修改"
            runtime.store.put("activities", row["id"], updated)
        elif change == "disabled":
            runtime.disabled.add("life")
        else:
            allowed = False
        return json.dumps(detail_result(row))

    runtime.scope_allowed = scope_allowed
    runtime.generate = generate
    with pytest.raises(ValueError):
        await life.detail(row["id"])
    stored = runtime.store.get("activities", row["id"])
    assert not stored["detailed"] and all(not a["enabled"] for a in stored["actions"].values())
    assert all(b["reserved"] == 0 for b in life.budget().values())


async def test_duplicate_detail_and_edit_rejected_during_model_call(world):
    runtime, life, _ = world
    rows = await outline(world)
    entered, release = asyncio.Event(), asyncio.Event()

    async def generate(*args, **kwargs):
        entered.set()
        await release.wait()
        return json.dumps(detail_result(rows[0]))

    runtime.generate = generate
    task = asyncio.create_task(life.detail(rows[0]["id"]))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        with pytest.raises(ValueError):
            await life.detail(rows[0]["id"], regenerate=True)
        with pytest.raises(ValueError):
            life.update_activity(rows[0]["id"], {"title": "并发编辑"})
    finally:
        release.set()
    result = await task
    assert result["detailed"]
    assert all(b["reserved"] == 1 for b in life.budget().values())


async def test_cancellation_during_redetail_preserves_old_version(world):
    runtime, life, _ = world
    rows = await outline(world)
    old = await adopt(world, rows[0], ("search",))
    entered = asyncio.Event()

    async def generate(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    runtime.generate = generate
    task = asyncio.create_task(life.detail(rows[0]["id"], regenerate=True))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert runtime.store.get("activities", rows[0]["id"]) == old
    assert life.budget()["search"]["reserved"] == 1
    assert not life._detail_lock.locked()


async def test_auto_detail_retries_once_after_sixty_seconds_and_persists_restart(
    world, monkeypatch
):
    runtime, life, clock = world
    monkeypatch.setattr("living_world.life.monotonic", lambda: 0.0)
    rows = await outline(world)
    runtime.responses = ["invalid", "invalid"]
    await life.tick()
    first = runtime.store.get("activities", rows[0]["id"])
    assert first["detail_attempts"] == 1 and first["detail_error"]
    assert not first["detailed"]
    clock[0] += timedelta(seconds=59)
    await life.tick()
    assert len(runtime.calls) == 2
    clock[0] += timedelta(seconds=1)
    restarted = fixed_service(runtime, clock[0])
    await restarted.tick()
    second = runtime.store.get("activities", rows[0]["id"])
    assert second["detail_attempts"] == 2
    await restarted.tick(clock[0] + timedelta(minutes=2))
    assert len(runtime.calls) == 3
    await restarted.tick(datetime.fromisoformat(rows[0]["start"]))
    assert len(runtime.calls) == 3 and not runtime.actions
    assert not runtime.store.get("activities", rows[0]["id"])["detailed"]


async def test_successful_auto_retry_reserves_actions_and_manual_retry_still_available(
    world, monkeypatch
):
    runtime, life, clock = world
    monkeypatch.setattr("living_world.life.monotonic", lambda: 0.0)
    rows = await outline(world)
    runtime.responses = ["invalid", json.dumps(detail_result(rows[0], ("search",)))]
    await life.tick()
    clock[0] += timedelta(seconds=60)
    await life.tick()
    row = runtime.store.get("activities", rows[0]["id"])
    assert row["detailed"] and not row.get("detail_error")
    assert life.budget()["search"]["reserved"] == 1
    await adopt(world, rows[0], (), regenerate=True)
    assert life.budget()["search"]["reserved"] == 0


async def test_lower_limit_keeps_earliest_reservations_and_raising_does_not_restore(world):
    runtime, life, _ = world
    rows = await outline(world)
    runtime.settings["life"]["search_count"] = 3
    for row in reversed(rows[:3]):
        await adopt(world, row, ("search",))
    runtime.settings["life"]["search_count"] = 1
    life.reconcile_reservations()
    assert (
        runtime.store.get("activities", rows[0]["id"])["actions"]["search"]["execution"]["status"]
        == "pending"
    )
    for row in rows[1:3]:
        execution = runtime.store.get("activities", row["id"])["actions"]["search"]["execution"]
        assert execution["status"] == "skipped" and execution["reason"] == "daily_limit_reduced"
    runtime.settings["life"]["search_count"] = 3
    life.reconcile_reservations()
    assert life.budget()["search"]["reserved"] == 1
    await adopt(world, rows[1], ("search",), regenerate=True)
    assert life.budget()["search"]["reserved"] == 2


async def test_used_counts_survive_day_regeneration_and_sqlite_reopen(world):
    runtime, life, clock = world
    rows = await outline(world)
    runtime.settings["life"]["search_count"] = 1
    first = await adopt(world, rows[0], ("search",))
    clock[0] = datetime.fromisoformat(first["start"])
    assert life.consume_action(first["id"], "search")
    assert not life.consume_action(first["id"], "search")
    for _ in range(2):
        runtime.responses = [json.dumps({"activities": plan_rows(start=clock[0])})]
        current = await life.regenerate_day()
        assert life.budget()["search"]["used"] == 1
        with pytest.raises(ValueError):
            await adopt(world, current[1], ("search",))
    path = runtime.store.db.execute("PRAGMA database_list").fetchone()[2]
    runtime.store.close()
    runtime.store = Store(path)
    restarted = fixed_service(runtime, clock[0])
    assert restarted.budget()["search"] == {"limit": 1, "used": 1, "reserved": 0, "available": 0}
    assert len(runtime.store.list("life_action_usage")) == 1


async def test_concurrent_execution_cannot_consume_one_remaining_slot_twice(world):
    runtime, life, clock = world
    runtime.settings["life"]["search_count"] = 1
    first = activity(runtime, "first", kind="search")
    second = activity(runtime, "second", kind="search")
    results = await asyncio.gather(
        *[
            asyncio.to_thread(life.consume_action, row["id"], "search", clock[0])
            for row in (first, second)
        ]
    )
    assert sum(results) == 1 and life.budget()["search"]["used"] == 1
    assert len(runtime.store.list("life_action_usage")) == 1


def test_cross_midnight_execution_uses_actual_date_and_expired_actions_do_not_consume(world):
    runtime, life, clock = world
    start = NOW.replace(hour=23, minute=55)
    row = activity(
        runtime, start=start.isoformat(), end=(start + timedelta(minutes=20)).isoformat()
    )
    row = prepared_activity(runtime, row, ("search",))
    at = start.replace(minute=59, second=58)
    row["actions"]["search"]["at"] = at.isoformat()
    runtime.store.put("activities", row["id"], row)
    assert life.budget(start.date())["search"]["reserved"] == 1
    assert not life.consume_action(row["id"], "search", now=at - timedelta(seconds=1))
    clock[0] = at + timedelta(seconds=3)
    assert life.consume_action(row["id"], "search")
    assert life.budget(start.date())["search"]["used"] == 0
    assert life.budget(clock[0].date())["search"]["used"] == 1
    other = activity(
        runtime,
        "expired",
        kind="search",
        start=start.isoformat(),
        end=(start + timedelta(minutes=20)).isoformat(),
    )
    assert not life.consume_action(other["id"], "search", now=start + timedelta(minutes=21))
    assert len(runtime.store.list("life_action_usage")) == 1


async def test_lower_limit_during_running_attempt_does_not_reset_or_cancel_usage(world):
    runtime, life, clock = world
    runtime.settings["life"]["search_count"] = 2
    running = activity(runtime, "running", kind="search")
    future = activity(
        runtime, "future", kind="search", start=(NOW + timedelta(hours=1)).isoformat()
    )
    assert life.consume_action(running["id"], "search", now=clock[0])
    runtime.settings["life"]["search_count"] = 0
    life.reconcile_reservations()
    assert life.budget()["search"] == {"limit": 0, "used": 1, "reserved": 0, "available": 0}
    assert (
        runtime.store.get("activities", running["id"])["actions"]["search"]["execution"]["status"]
        == "pending"
    )
    assert (
        runtime.store.get("activities", future["id"])["actions"]["search"]["execution"]["reason"]
        == "daily_limit_reduced"
    )


async def test_lower_limit_during_detail_call_is_checked_again_before_adoption(world):
    runtime, life, _ = world
    rows = await outline(world)

    async def generate(*args, **kwargs):
        runtime.settings["life"]["search_count"] = 0
        life.reconcile_reservations()
        return json.dumps(detail_result(rows[0], ("search",)))

    runtime.generate = generate
    with pytest.raises(ValueError):
        await life.detail(rows[0]["id"])
    assert not runtime.store.get("activities", rows[0]["id"])["detailed"]
    assert life.budget()["search"]["reserved"] == 0


async def test_slow_auto_detail_cannot_create_fiction_after_activity_has_expired(
    world, monkeypatch
):
    runtime, life, _ = world
    elapsed = 0.0
    monkeypatch.setattr("living_world.life.monotonic", lambda: elapsed)
    rows = await outline(world)

    async def slow_detail(*args, **kwargs):
        nonlocal elapsed
        elapsed += 75 * 60
        return json.dumps(detail_result(rows[0]))

    runtime.generate = slow_detail
    await life._advance(rows[0], NOW)
    stored = runtime.store.get("activities", rows[0]["id"])
    assert not stored["detailed"] and stored["status"] == "skipped"
    assert runtime.events == runtime.actions == []
    assert all(value["reserved"] == 0 for value in life.budget().values())


async def test_action_completion_does_not_restore_another_action_cancelled_during_await(world):
    runtime, life, clock = world
    rows = await outline(world)
    row = await adopt(world, rows[0], ("news", "search"))
    clock[0] = datetime.fromisoformat(row["start"])

    async def execute(kind, payload, scope, action_id):
        assert runtime.consume(kind, payload)
        runtime.actions.append((kind, payload, scope, action_id))
        if kind == "news":
            runtime.settings["life"]["search_count"] = 0
            life.reconcile_reservations()
            runtime.settings["life"]["search_count"] = 2
            life.reconcile_reservations()
        return {"status": "success"}

    runtime.execute_action = execute
    await life.tick()
    stored = runtime.store.get("activities", row["id"])
    assert [call[0] for call in runtime.actions] == ["news"]
    assert stored["actions"]["search"]["execution"]["reason"] == "daily_limit_reduced"
    assert life.budget()["search"]["used"] == 0


@pytest.mark.parametrize("tick_seconds", [60, 120])
async def test_zero_detail_lead_uses_last_scheduling_tick_before_start(
    world, monkeypatch, tick_seconds
):
    runtime, life, clock = world
    monkeypatch.setattr("living_world.life.monotonic", lambda: 0.0)
    runtime.settings["life"].update(detail_minutes=0, tick_seconds=tick_seconds)
    rows = await outline(world)
    start = datetime.fromisoformat(rows[0]["start"])
    runtime.responses = [json.dumps(detail_result(rows[0], ("search",)))]
    clock[0] = start - timedelta(seconds=tick_seconds + 1)
    await life.tick()
    assert (
        len(runtime.calls) == 1 and not runtime.store.get("activities", rows[0]["id"])["detailed"]
    )
    clock[0] = start - timedelta(seconds=tick_seconds)
    await life.tick()
    assert runtime.store.get("activities", rows[0]["id"])["detailed"]
    assert life.budget()["search"]["reserved"] == 1
    assert len(runtime.calls) == 2 and not runtime.actions
    clock[0] = start
    await life.tick()
    assert [call[0] for call in runtime.actions] == ["search"]
    assert len(runtime.calls) == 2 and life.budget()["search"]["used"] == 1


@pytest.mark.parametrize("start_after_midnight", [False, True])
async def test_result_statistics_use_actual_start_date_even_when_action_crosses_midnight(
    world, monkeypatch, start_after_midnight
):
    runtime, life, clock = world
    elapsed = 0.0
    monkeypatch.setattr("living_world.life.monotonic", lambda: elapsed)
    start = NOW.replace(hour=23, minute=55)
    at = start.replace(minute=59, second=58)
    actual = at + timedelta(seconds=3 if start_after_midnight else 1)
    row = activity(
        runtime, start=start.isoformat(), end=(start + timedelta(minutes=20)).isoformat()
    )
    row = prepared_activity(runtime, row, ("search",))
    row["actions"]["search"]["at"] = at.isoformat()
    runtime.store.put("activities", row["id"], row)
    clock[0] = actual

    async def execute(kind, payload, scope, action_id):
        nonlocal elapsed
        assert runtime.consume(kind, payload)
        if not start_after_midnight:
            elapsed += 3
        return {"status": "success"}

    runtime.execute_action = execute
    await life._advance(row, actual)
    clock[0] = actual + timedelta(seconds=elapsed)
    stored = runtime.store.get("activities", row["id"])
    assert datetime.fromisoformat(
        stored["actions"]["search"]["execution"]["finished_at"]
    ).date() == start.date() + timedelta(days=1)
    for day in (start.date(), start.date() + timedelta(days=1)):
        values = life.day_summary(day)["counts"]["search"]
        assert values["used"] == values["success"] == int(day == actual.date())
