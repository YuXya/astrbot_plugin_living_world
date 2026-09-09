"""Runtime regressions for concurrent outline edits and atomic administration."""

import asyncio
import copy
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from living_world.runtime import Runtime

NOW = datetime(2030, 9, 5, 10, tzinfo=timezone(timedelta(hours=8)))


class NoExternalCalls:
    """Resolve the bound persona while rejecting any unexpected model or transport work."""

    async def persona(self, persona_id):
        assert persona_id == "student"
        return "你是一位学生。"

    async def generate(self, *args, **kwargs):
        raise AssertionError("This regression must not call a model")

    async def send(self, *args, **kwargs):
        raise AssertionError("This regression must not send a message")


@pytest.fixture
async def world(tmp_path):
    runtime = Runtime(tmp_path / "world.sqlite", NoExternalCalls())
    clock = [NOW]
    convert = runtime.life._now
    runtime.life._now = lambda value=None: convert(value or clock[0])
    await runtime.update_settings({"persona_id": "student", "modules": {"news": True}})
    try:
        yield runtime, clock
    finally:
        await runtime.stop()


def outline(runtime, *, start=None):
    start = start or NOW + timedelta(minutes=5)
    row = runtime.life._make_activity(
        {
            "start": start.isoformat(),
            "end": (NOW + timedelta(hours=2)).isoformat(),
            "title": "散步",
            "content": "沿河走走",
            "location": "河边",
            "sleep_state": "清醒",
        },
        NOW.date(),
        "global",
        "activity",
    )
    row["detailed"] = True
    runtime.store.put("activities", row["id"], row)
    return row


async def test_scope_check_cannot_overwrite_a_future_outline_edit(world, monkeypatch):
    runtime, clock = world
    row = outline(runtime, start=NOW + timedelta(seconds=1))
    entered, release = asyncio.Event(), asyncio.Event()
    original = runtime.scope_allowed
    checks = 0

    async def paused_scope_check(scope):
        nonlocal checks
        allowed = await original(scope)
        checks += 1
        # The second check occurs after fetching the row and before starting its activity.
        if checks == 2:
            entered.set()
            await release.wait()
        return allowed

    monkeypatch.setattr(runtime, "scope_allowed", paused_scope_check)
    monkeypatch.setattr("living_world.life.elapsed_clock", lambda now: lambda: clock[0])
    progressing = asyncio.create_task(runtime.run("life", runtime.life._advance(row, NOW)))
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        new_start = NOW + timedelta(hours=1)
        await runtime.action(
            {
                "action": "update_activity",
                "id": row["id"],
                "patch": {"start": new_start.isoformat(), "title": "改到十一点散步"},
            }
        )
        clock[0] = NOW + timedelta(seconds=2)
        release.set()
        await asyncio.wait_for(progressing, timeout=2)
        saved = runtime.store.get("activities", row["id"])
        assert saved["start"] == new_start.isoformat()
        assert saved["title"] == "改到十一点散步"
        assert saved["status"] == "planned" and not saved["detailed"]
        assert runtime.store.list("life_fiction_claims") == []
        assert runtime.store.list("events") == []
        assert runtime.store.list("life_action_usage") == []
    finally:
        release.set()
        if not progressing.done():
            progressing.cancel()
        await asyncio.gather(progressing, return_exceptions=True)


async def test_limit_update_and_reservation_cancellation_roll_back_together(world):
    runtime, _ = world
    row = outline(runtime)
    row["actions"]["news"] = {
        "enabled": True,
        "intent": "看看相关新闻",
        "reason": "有具体兴趣",
        "at": row["start"],
        "execution": {"status": "pending"},
    }
    runtime.store.put("activities", row["id"], row)
    before_settings = copy.deepcopy(runtime.settings)
    before_version = runtime.config_version
    before_records = runtime.store.export()
    runtime.store.db.execute(
        "CREATE TRIGGER reject_reservation_cancel BEFORE UPDATE ON objects "
        "WHEN NEW.namespace = 'activities' BEGIN "
        "SELECT RAISE(ABORT, 'reservation write failed'); END"
    )
    runtime.store.db.commit()
    try:
        with pytest.raises(sqlite3.IntegrityError, match="reservation write failed"):
            await runtime.update_settings({"life": {"news_count": 0}})
        assert runtime.settings == before_settings
        assert runtime.config_version == before_version
        assert runtime.store.get("settings", "current") == before_settings
        assert runtime.store.export() == before_records
        assert runtime.life.budget()["news"]["reserved"] == 1
    finally:
        runtime.store.db.execute("DROP TRIGGER reject_reservation_cancel")
        runtime.store.db.commit()
    await runtime.update_settings({"life": {"news_count": 0}})
    saved = runtime.store.get("activities", row["id"])
    assert runtime.settings["life"]["news_count"] == 0
    assert saved["actions"]["news"]["execution"]["reason"] == "daily_limit_reduced"
    assert runtime.life.budget()["news"]["reserved"] == 0


async def test_restore_archives_original_running_evidence_before_disabling_reservations(world):
    runtime, _ = world
    started_at = (NOW - timedelta(minutes=5)).isoformat()
    legacy = {
        "id": "legacy-active",
        "date": str(NOW.date()),
        "scope": "global",
        "kind": "fiction",
        "start": (NOW - timedelta(minutes=10)).isoformat(),
        "end": (NOW + timedelta(minutes=10)).isoformat(),
        "title": "正在进行的旧活动",
        "content": "旧大纲",
        "location": "家",
        "sleep_state": "清醒",
        "schema_version": 2,
        "status": "running",
        "detailed": True,
        "description": "旧细节",
        "actions": runtime.life._empty_actions(),
    }
    legacy["actions"]["news"] = {
        "enabled": True,
        "intent": "旧阅读意图",
        "at": started_at,
        "execution": {"status": "running", "started_at": started_at},
    }
    legacy["actions"]["search"] = {
        "enabled": True,
        "intent": "稍后搜索",
        "at": (NOW + timedelta(minutes=1)).isoformat(),
        "execution": {"status": "pending"},
    }
    marker = {
        "schema_version": 2,
        "date": str(NOW.date()),
        "scope": "global",
        "status": "completed",
        "parameters": {
            "daily_plan_time": "06:00",
            "activity_count": 1,
            "news_count": 2,
            "search_count": 2,
            "social_count": 3,
        },
        "raw_json": "旧模型生成正文原样保留",
    }
    backup = {
        "format": "living-world",
        "version": 1,
        "settings": copy.deepcopy(runtime.settings),
        "records": [
            {"namespace": "activities", "key": legacy["id"], "value": copy.deepcopy(legacy)},
            {
                "namespace": "life_days",
                "key": f"{NOW.date()}:global",
                "value": copy.deepcopy(marker),
            },
        ],
    }
    await runtime.restore(backup)
    history = runtime.store.list("life_day_history")
    assert len(history) == 1
    assert history[0]["activities"] == [legacy]
    assert history[0]["parameters"] == marker["parameters"]
    assert history[0]["raw_json"] == marker["raw_json"]
    usage = runtime.store.get("life_action_usage", "legacy-active:news")
    assert usage and usage["started_at"] == started_at and usage["imported"]
    assert usage["date"] == str(NOW.date())
    assert runtime.store.get("life_action_usage", "legacy-active:search") is None
    assert runtime.store.get("activities", legacy["id"])["schema_version"] == 3
    assert not any(runtime.settings["modules"].values())
    assert runtime.store.list("actions") == []
    assert runtime.store.list("deliveries") == []
