"""Daily outlines and once-only action execution with external I/O substituted."""

import asyncio
import copy
import json
from datetime import datetime, timedelta, timezone

import pytest

from living_world.drives import DRIVE_DEFAULTS, DriveService
from living_world.life import LifeService
from living_world.store import Store

NOW = datetime(2026, 9, 5, 10, 0, tzinfo=timezone(timedelta(hours=8)))
KINDS = ("news", "search", "social")


class Memory:
    def __init__(self):
        self.entries = []

    def recall(self, **kwargs):
        # Return unfiltered entries to verify defense at the service boundary.
        return copy.deepcopy(self.entries)


class Runtime:
    def __init__(self):
        self.store = Store(":memory:")
        self.memory = Memory()
        self.settings = {
            "character": {"timezone": "Asia/Shanghai"},
            "life": {"spontaneous_minutes": 0},
            "sessions": [],
            "drives": copy.deepcopy(DRIVE_DEFAULTS),
        }
        self.disabled = set()
        self.responses = []
        self.calls = []
        self.actions = []
        self.events = []
        self.action_result = {"status": "success", "text": "消息已发出，尚未收到回复。"}

    def enabled(self, name):
        return name not in self.disabled

    async def generate(self, module, prompt, scope="global"):
        self.calls.append((module, prompt, scope))
        return self.responses.pop(0) if self.responses else '{"activities":[]}'

    def consume(self, kind, payload):
        row = self.store.get("activities", payload["activity_id"])
        started = row["actions"][kind]["execution"].get("started_at")
        now = datetime.fromisoformat(started) if started else self.life._now()
        return self.life.consume_action(row["id"], kind, now=now)

    async def execute_action(self, kind, payload, scope, action_id):
        if not self.consume(kind, payload):
            return {"status": "skipped", "reason": "start_rejected"}
        self.actions.append((kind, payload, scope, action_id))
        return self.action_result

    def record_event(self, text, **kwargs):
        self.events.append({"text": text, **kwargs})


def fixed_service(runtime, now=NOW):
    service = LifeService(runtime)
    convert = service._now
    service._now = lambda value=None: convert(value or now)
    runtime.life = service
    runtime.drives = DriveService(runtime, clock=lambda: 0.0)
    return service


def action_counts(service, day=None):
    """Read public started statistics and independently inspect pending stored decisions."""
    current_day = str(day or service._now().date())
    counts = service.day_summary(day)["counts"]
    result = {kind: {"started": counts[kind]["started"], "pending": 0} for kind in KINDS}
    for row in service.runtime.store.list("activities"):
        for kind in KINDS:
            action = row.get("actions", {}).get(kind, {})
            at = str(action.get("at", ""))[:10]
            if (
                action.get("enabled")
                and at == current_day
                and action.get("execution", {}).get("status") == "pending"
            ):
                result[kind]["pending"] += 1
    return result


def plan_rows(count=10, start=NOW):
    return [
        {
            "start": (start + timedelta(hours=index)).isoformat(),
            "end": (start + timedelta(hours=index + 1)).isoformat(),
            "title": f"生活活动 {index}",
            "content": "上数学课",
            "location": "教室",
            "sleep_state": "清醒",
        }
        for index in range(count)
    ]


def detail_result(row, kinds=KINDS, **changes):
    result = {
        "description": "数学课间看看窗外，继续当天生活。",
        "incident": "忘带笔，向同桌借了一支。",
        "mood": "好奇",
        "actions": {
            kind: {
                "enabled": kind in kinds,
                "intent": f"{kind} 的具体活动意图" if kind in kinds else "",
                "reason": "当前活动有具体需要" if kind in kinds else "当前活动不需要",
                "at": row["start"] if kind in kinds else None,
            }
            for kind in KINDS
        },
    }
    result.update(changes)
    return result


def prepared_activity(runtime, row, kinds=KINDS):
    """Seed an already adopted detail to isolate execution behavior from model parsing."""
    row = copy.deepcopy(row)
    row.update(detail_result(row, kinds))
    row.update(schema_version=3, detailed=True, detail_version=f"fixture-{row['id']}")
    for action in row["actions"].values():
        action["execution"] = {"status": "pending" if action["enabled"] else "disabled"}
    runtime.store.put("activities", row["id"], row)
    return row


def activity(runtime, key="a", kind="fiction", scope="global", **changes):
    start = changes.get("start", NOW.isoformat())
    row = {
        **plan_rows(1, datetime.fromisoformat(start))[0],
        "id": key,
        "date": datetime.fromisoformat(start).date().isoformat(),
        "title": "数学课无聊，找群聊天",
        "kind": "fiction",
        "scope": scope,
        "status": "planned",
        "detailed": True,
        "schema_version": 3,
        "actions": {
            name: {**value, "execution": {"status": "disabled"}}
            for name, value in detail_result({"start": start}, ())["actions"].items()
        },
    }
    row.update(changes)
    runtime.store.put("activities", key, row)
    return prepared_activity(runtime, row, (kind,)) if kind in KINDS else row


async def publish(runtime, service=None, rows=None, now=NOW):
    service = service or fixed_service(runtime, now)
    runtime.responses = [
        json.dumps(
            {"activities": rows if rows is not None else plan_rows(start=now)}, ensure_ascii=False
        )
    ]
    return service, await service.plan_day(now)


@pytest.mark.asyncio
async def test_private_memories_do_not_create_additional_daily_outlines():
    runtime = Runtime()
    runtime.memory.entries = [{"scope": "private-a", "text": "今天十点复习"}]
    assert await fixed_service(runtime).plan_day(NOW, "private-a") == []
    assert runtime.calls == runtime.actions == runtime.events == []


@pytest.mark.asyncio
async def test_default_plan_is_outline_only_and_original_json_survives():
    runtime = Runtime()
    runtime.memory.entries = [
        {"scope": "global", "text": "昨天公开约好复习"},
        {"scope": "private-a", "text": "私聊的秘密"},
    ]
    service, rows = await publish(runtime)
    assert len(rows) == 10
    assert all(row["schema_version"] == 3 and not row["detailed"] for row in rows)
    assert all(not action["enabled"] for row in rows for action in row["actions"].values())
    assert service.parameters() == {"daily_plan_time": "06:00", "activity_count": 10}
    assert "昨天公开约好复习" not in runtime.calls[0][1] and "私聊的秘密" not in runtime.calls[0][1]
    assert "memories" not in service.plan_request(NOW)["context"]
    assert runtime.events == runtime.actions == []
    assert await service.plan_day(NOW) == rows
    assert await fixed_service(runtime).plan_day(NOW) == rows
    assert len(runtime.calls) == 1
    generation = runtime.store.get("life_days", f"{NOW.date()}:global")
    assert json.loads(generation["raw_json"])["activities"][0]["title"] == "生活活动 0"
    assert len(generation["adopted_activities"]) == 10
    assert generation["parameters"] == service.parameters()
    assert generation["full_request"]


@pytest.mark.asyncio
async def test_outline_ignores_model_action_fields_instead_of_enforcing_old_quotas():
    runtime = Runtime()
    rows = plan_rows()
    rows[0]["actions"] = {"search": {"enabled": "true", "at": "23:59"}}
    service, adopted = await publish(runtime, rows=rows)
    assert all(not action["enabled"] for row in adopted for action in row["actions"].values())
    assert action_counts(service)["search"]["pending"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("problem", ["count", "overlap", "reversed", "title", "date"])
async def test_invalid_outline_never_becomes_formal_and_retains_raw(problem):
    runtime = Runtime()
    rows = plan_rows()
    if problem == "count":
        rows.pop()
    elif problem == "overlap":
        rows[1]["start"] = "10:30"
    elif problem == "reversed":
        rows[0]["end"] = (NOW - timedelta(minutes=1)).isoformat()
    elif problem == "title":
        rows[0]["title"] = ""
    else:
        rows[0]["start"] = (NOW - timedelta(days=1)).isoformat()
    with pytest.raises((ValueError, TypeError)):
        await publish(runtime, rows=rows)
    assert runtime.store.list("activities") == []
    generation = runtime.store.get("life_days", f"{NOW.date()}:global")
    assert generation["status"] == "failed" and generation["raw_json"]
    assert runtime.actions == runtime.events == []


@pytest.mark.asyncio
async def test_generation_waits_until_six_and_only_missing_day_is_generated():
    runtime = Runtime()
    service = fixed_service(runtime)
    before = NOW.replace(hour=5, minute=59)
    runtime.responses = [json.dumps({"activities": plan_rows()})]
    assert await service.plan_day(before) == []
    assert runtime.calls == []
    assert len(await service.plan_day(before.replace(hour=6, minute=0))) == 10
    assert len(await service.plan_day(NOW)) == 10
    assert len(runtime.calls) == 1


@pytest.mark.asyncio
async def test_late_generation_does_not_detail_or_catch_up_started_activities():
    runtime = Runtime()
    late = NOW + timedelta(hours=2, minutes=20)
    service, rows = await publish(runtime, rows=plan_rows(), now=late)
    await service.tick(late)
    assert runtime.actions == []
    assert len(runtime.calls) == 1
    assert all(not a["enabled"] for row in rows for a in row["actions"].values())


@pytest.mark.asyncio
async def test_actions_execute_news_search_social_once_across_restart():
    runtime = Runtime()
    service, rows = await publish(runtime)
    prepared_activity(runtime, rows[0])
    await service.tick(NOW)
    assert [call[0] for call in runtime.actions] == list(KINDS)
    assert all(
        call[1]["planned"] and call[1]["activity_id"] == rows[0]["id"] for call in runtime.actions
    )
    await service.tick(NOW + timedelta(minutes=1))
    await fixed_service(runtime).tick(NOW + timedelta(minutes=2))
    assert len(runtime.actions) == 3
    assert all(
        value["started"] == 1 and value["pending"] == 0 for value in action_counts(service).values()
    )
    assert "忘带笔" in runtime.events[0]["text"]
    assert len(runtime.store.list("activities")) == 10


@pytest.mark.asyncio
async def test_fiction_records_incident_at_start_then_finishes():
    runtime = Runtime()
    row = activity(runtime, incident="忘带笔了", energy_delta=-5, mood="有点无聊")
    service = fixed_service(runtime)
    await service.tick(NOW)
    assert runtime.events[0]["source"] == "fiction"
    assert "忘带笔" in runtime.events[0]["text"]
    assert runtime.store.get("activities", row["id"])["status"] == "running"
    assert "energy" not in service.state()
    assert runtime.drives.snapshot()["meters"]["energy"]["value"] == 70
    await service.tick(NOW + timedelta(hours=1))
    assert runtime.store.get("activities", row["id"])["status"] == "completed"
    assert len(runtime.events) == 1 and not runtime.actions


@pytest.mark.asyncio
async def test_private_fiction_does_not_change_public_life_state():
    runtime = Runtime()
    activity(runtime, scope="private-a", mood="私人聊天让我想哭", energy_delta=-5)
    service = fixed_service(runtime)
    await service.tick(NOW)
    assert service.state()["mood"] == "平静" and "energy" not in service.state()
    assert runtime.drives.snapshot()["meters"]["energy"]["value"] == 70
    assert runtime.events[0]["scope"] == "private-a"


@pytest.mark.asyncio
async def test_legacy_spontaneous_setting_no_longer_creates_actions():
    runtime = Runtime()
    runtime.settings["life"]["spontaneous_minutes"] = 30
    runtime.store.put("life_days", f"{NOW.date()}:global", {"status": "completed"})
    activity(runtime, start=(NOW - timedelta(minutes=35)).isoformat(), status="running")
    service = fixed_service(runtime)
    await service.tick(NOW)
    await service.tick(NOW + timedelta(minutes=1))
    assert runtime.calls == runtime.actions == []


@pytest.mark.asyncio
async def test_disable_during_plan_call_prevents_activity_writes():
    runtime = Runtime()

    async def disable(*args, **kwargs):
        runtime.disabled.add("life")
        return json.dumps({"activities": plan_rows()})

    runtime.generate = disable
    assert await fixed_service(runtime).plan_day(NOW) == []
    assert runtime.store.list("activities") == []


@pytest.mark.asyncio
async def test_disabled_and_failed_actions_release_only_unstarted_reservations():
    runtime = Runtime()
    service, rows = await publish(runtime)
    prepared_activity(runtime, rows[0])
    runtime.disabled.add("news")

    async def execute(kind, payload, scope, action_id):
        runtime.actions.append((kind, payload, scope, action_id))
        if kind == "search":
            assert runtime.consume(kind, payload)
            raise ValueError("Search unavailable")
        return {"status": "skipped", "reason": "cooldown"}

    runtime.execute_action = execute
    await service.tick(NOW)
    states = runtime.store.get("activities", rows[0]["id"])["actions"]
    assert states["news"]["execution"]["reason"] == "module_disabled"
    assert states["search"]["execution"]["status"] == "failed"
    assert states["social"]["execution"]["reason"] == "cooldown"
    assert {k: v["started"] for k, v in action_counts(service).items()} == {
        "news": 0,
        "search": 1,
        "social": 0,
    }
    assert all(value["pending"] == 0 for value in action_counts(service).values())
    runtime.disabled.clear()
    await service.tick(NOW + timedelta(minutes=1))
    assert [call[0] for call in runtime.actions] == ["search", "social"]


@pytest.mark.asyncio
async def test_restart_skips_recently_overdue_prepared_actions():
    runtime = Runtime()
    _service, rows = await publish(runtime)
    prepared_activity(runtime, rows[0])
    await fixed_service(runtime).tick(NOW + timedelta(seconds=5))
    assert runtime.actions == []
    actions = runtime.store.get("activities", rows[0]["id"])["actions"]
    assert all(a["execution"]["reason"] == "overdue_after_restart" for a in actions.values())


@pytest.mark.asyncio
async def test_running_or_claimed_action_is_uncertain_and_never_retried():
    runtime = Runtime()
    service, rows = await publish(runtime)
    row = prepared_activity(runtime, rows[0])
    row["actions"]["social"]["execution"] = {"status": "running"}
    runtime.store.put("activities", row["id"], row)
    runtime.store.claim("life_action_claims", f"{row['id']}:news", {"started_at": NOW.isoformat()})
    await service.tick(NOW)
    await fixed_service(runtime).tick(NOW + timedelta(minutes=1))
    assert [call[0] for call in runtime.actions] == ["search"]


@pytest.mark.asyncio
async def test_cancellation_after_start_keeps_usage_and_execution_claim():
    runtime = Runtime()
    service, rows = await publish(runtime)
    prepared_activity(runtime, rows[0], ("news",))

    async def cancel(kind, payload, scope, action_id):
        assert runtime.consume(kind, payload)
        raise asyncio.CancelledError

    runtime.execute_action = cancel
    with pytest.raises(asyncio.CancelledError):
        await service.tick(NOW)
    execution = runtime.store.get("activities", rows[0]["id"])["actions"]["news"]["execution"]
    assert execution["reason"] == "execution_interrupted_outcome_unknown"
    assert runtime.store.get("life_action_claims", f"{rows[0]['id']}:news")
    assert action_counts(service)["news"]["started"] == 1
    await fixed_service(runtime).tick(NOW + timedelta(minutes=1))
    assert len(runtime.store.list("life_action_usage")) == 1


def test_manual_edits_cannot_relabel_scope_or_replay_completed_actions():
    runtime = Runtime()
    service = fixed_service(runtime)
    activity(runtime, start=(NOW + timedelta(minutes=10)).isoformat())
    with pytest.raises(ValueError):
        service.update_activity("a", {"scope": "private-a"})
    service.update_activity("a", {"title": "新安排"})
    assert runtime.store.get("activities", "a")["title"] == "新安排"
    activity(runtime, status="completed")
    with pytest.raises(ValueError):
        service.update_activity("a", {"title": "再来一次"})
    with pytest.raises(ValueError):
        service.update_state({"profile": "自动重写人设"})


@pytest.mark.asyncio
async def test_outline_edit_invalidates_detail_and_releases_reservation():
    runtime = Runtime()
    service, rows = await publish(runtime)
    row = prepared_activity(runtime, rows[1])
    assert action_counts(service)["search"]["pending"] == 1
    service.update_activity(row["id"], {"title": "改为认真上课"})
    changed = runtime.store.get("activities", row["id"])
    assert not changed["detailed"]
    assert not changed.get("incident")
    assert all(not action["enabled"] for action in changed["actions"].values())
    assert all(value["pending"] == 0 for value in action_counts(service).values())
    marker = runtime.store.get("life_days", f"{NOW.date()}:global")
    assert marker["adopted_activities"][1]["title"] == rows[1]["title"]


@pytest.mark.asyncio
async def test_private_revision_uses_fresh_memory_in_scoped_overlay_only():
    runtime = Runtime()
    service = fixed_service(runtime)
    activity(runtime, "future", start=(NOW + timedelta(minutes=20)).isoformat())
    activity(runtime, "done", scope="private-a", status="completed")
    activity(runtime, "other", scope="private-b", start=(NOW + timedelta(minutes=20)).isoformat())
    runtime.memory.entries = [
        {"scope": "private-a", "text": "新的约定是复习数学，旧约定已更新"},
        {"scope": "private-b", "text": "其他人的私人约定"},
    ]
    runtime.responses = [
        json.dumps(
            {
                "updates": [
                    {"id": "future", "changes": {"title": "和朋友复习数学"}},
                    {"id": "done", "changes": {"title": "不许改"}},
                    {"id": "other", "changes": {"title": "不许改"}},
                ]
            }
        )
    ]
    assert await service.revise("private-a", "约定更新") == {
        "updated": ["future"],
        "added": [],
        "cancelled": [],
    }
    stored = runtime.store.get("activities", "future")
    assert stored["title"] == "数学课无聊，找群聊天"
    assert stored["scope_overrides"]["private-a"]["title"] == "和朋友复习数学"
    assert "新的约定是复习数学" not in runtime.calls[0][1]
    assert "其他人的私人约定" not in runtime.calls[0][1]
    for scope in ("global", "private-b"):
        assert "和朋友复习数学" not in json.dumps(service._view(stored, scope), ensure_ascii=False)
    assert runtime.actions == runtime.events == []


@pytest.mark.asyncio
async def test_removed_scope_stops_existing_fiction_and_does_not_create_memories():
    runtime = Runtime()

    async def allowed(scope):
        return scope == "global"

    runtime.scope_allowed = allowed
    runtime.settings["sessions"] = [{"umo": "private-a", "enabled": True}]
    activity(runtime, scope="private-a", detailed=False)
    await fixed_service(runtime).tick(NOW)
    assert runtime.events == runtime.actions == []
    assert runtime.store.get("activities", "a")["status"] == "planned"
    assert all(call[2] == "global" for call in runtime.calls)


@pytest.mark.asyncio
async def test_model_revision_cannot_expand_or_cancel_day():
    runtime = Runtime()
    service, rows = await publish(runtime)
    for invalid in (
        {"updates": [], "additions": [plan_rows()[0]]},
        {"updates": [], "cancel": [rows[1]["id"]]},
    ):
        runtime.responses = [json.dumps(invalid)]
        with pytest.raises(ValueError):
            await service.revise()
    assert service.list_activities() == rows


@pytest.mark.asyncio
async def test_prepared_formal_plan_recovers_missing_rows_without_model():
    runtime = Runtime()
    _service, rows = await publish(runtime)
    marker = runtime.store.get("life_days", f"{NOW.date()}:global")
    marker["status"] = "prepared"
    runtime.store.put("life_days", f"{NOW.date()}:global", marker)
    runtime.store.delete("activities", rows[3]["id"])
    assert len(await fixed_service(runtime).plan_day(NOW)) == 10
    assert len(runtime.calls) == 1
    assert runtime.store.get("life_days", f"{NOW.date()}:global")["status"] == "completed"


@pytest.mark.asyncio
async def test_current_state_uses_global_activity_and_private_view_is_filtered():
    runtime = Runtime()
    service = fixed_service(runtime)
    assert service.state()["location"] == service.state()["sleep_state"] == "未知"
    await publish(runtime, service=service)
    row = service.list_activities()[0]
    row["scope_overrides"] = {"private-a": {"location": "私人约定地点", "title": "私人复习"}}
    runtime.store.put("activities", row["id"], row)
    assert service.state()["location"] == "教室" and service.state()["sleep_state"] == "清醒"
    assert service.current("private-a")["location"] == "私人约定地点"
    assert service.current("private-b")["location"] == "教室"
    assert "scope_overrides" not in service.current("private-b")


@pytest.mark.asyncio
async def test_failed_outline_backoff_preserves_raw_invalid_json():
    runtime = Runtime()
    service = fixed_service(runtime)
    runtime.responses = ['{"activities": []}']
    await service.tick(NOW)
    marker = runtime.store.get("life_days", f"{NOW.date()}:global")
    assert marker["raw_json"] == '{"activities": []}'
    await service.tick(NOW + timedelta(minutes=1))
    assert len(runtime.calls) == 1
    assert not runtime.store.list("activities")


@pytest.mark.asyncio
async def test_life_switch_off_stops_generation_and_actions_without_deleting_data():
    runtime = Runtime()
    service, rows = await publish(runtime)
    runtime.disabled.add("life")
    await service.tick(NOW)
    assert runtime.actions == runtime.events == []
    assert service.list_activities() == rows and len(runtime.calls) == 1


@pytest.mark.asyncio
async def test_outline_parameters_freeze_without_former_action_limits():
    runtime = Runtime()
    before = NOW.replace(hour=5, minute=59)
    service = fixed_service(runtime, before)
    await service.tick(before)
    runtime.settings["life"].update(daily_plan_time="23:00", activity_count=5)
    assert service.plan_request()["context"]["parameters"]["activity_count"] == 5
    assert service.plan_request(formal=True)["context"]["parameters"]["activity_count"] == 10
    runtime.responses = [json.dumps({"activities": plan_rows()})]
    assert len(await service.plan_day(before.replace(hour=6, minute=0))) == 10
    marker = runtime.store.get("life_days", f"{before.date()}:global")
    assert marker["parameters"] == {"daily_plan_time": "06:00", "activity_count": 10}


@pytest.mark.asyncio
async def test_previous_private_commitment_refines_existing_slots_once_per_day():
    runtime = Runtime()
    service, rows = await publish(runtime)
    memory = {"id": "promise", "scope": "private-a", "text": "昨天约好今天午后一起复习数学"}
    runtime.memory.entries = [memory]
    runtime.store.put("memories", memory["id"], memory)
    runtime.responses = [
        json.dumps(
            {"updates": [{"id": rows[1]["id"], "changes": {"title": "和朋友复习数学"}}]},
            ensure_ascii=False,
        )
    ]
    await service.tick(NOW)
    await service.tick(NOW + timedelta(minutes=1))
    await fixed_service(runtime).tick(NOW + timedelta(minutes=2))
    assert len(runtime.calls) == 2
    assert "昨天约好今天午后一起复习数学" not in runtime.calls[-1][1]
    assert runtime.calls[-1][2] == "private-a"
    stored = runtime.store.get("activities", rows[1]["id"])
    assert stored["title"] == "生活活动 1"
    assert stored["scope_overrides"]["private-a"]["title"] == "和朋友复习数学"


@pytest.mark.asyncio
async def test_scoped_daily_revision_failure_has_persistent_retry_cooldown(monkeypatch):
    monkeypatch.setattr("living_world.life.monotonic", lambda: 0.0)
    runtime = Runtime()
    service, _rows = await publish(runtime)
    memory = {"id": "promise", "scope": "private-a", "text": "今天约好复习"}
    runtime.memory.entries = [memory]
    runtime.store.put("memories", memory["id"], memory)
    runtime.responses = ["not json"]
    await service.tick(NOW)
    marker = runtime.store.get("life_scoped_revisions", f"{NOW.date()}:private-a")
    assert marker["status"] == "failed"
    assert marker["retry_after"] == (NOW + timedelta(minutes=15)).timestamp()
    await fixed_service(runtime).tick(NOW + timedelta(minutes=1))
    assert len(runtime.calls) == 2 and len(service.list_activities()) == 10


def test_plan_preview_does_not_reinforce_memories_or_write_data():
    runtime = Runtime()
    memory_calls = []

    def recall(**kwargs):
        memory_calls.append(kwargs)
        return []

    runtime.memory.recall = recall
    service = fixed_service(runtime)
    before = runtime.store.export()
    request = service.plan_request()
    assert request["context"]["parameters"]["activity_count"] == 10
    assert memory_calls == []
    assert runtime.store.export() == before
    assert runtime.calls == runtime.events == runtime.actions == []


@pytest.mark.asyncio
@pytest.mark.parametrize("expired_by", ["activity_end", "stale_window"])
async def test_slow_news_skips_later_actions_after_window(monkeypatch, expired_by):
    elapsed = 0.0
    monkeypatch.setattr("living_world.life.monotonic", lambda: elapsed)
    runtime = Runtime()
    raw = plan_rows()
    if expired_by == "activity_end":
        raw[0]["end"] = (NOW + timedelta(minutes=5)).isoformat()
    service, rows = await publish(runtime, rows=raw)
    prepared_activity(runtime, rows[0])

    async def slow_news(kind, payload, scope, action_id):
        nonlocal elapsed
        assert runtime.consume(kind, payload)
        runtime.actions.append((kind, payload, scope, action_id))
        if kind == "news":
            elapsed += 6 * 60 if expired_by == "activity_end" else 11 * 60
        return {"status": "success", "text": "Actual news result"}

    runtime.execute_action = slow_news
    await service.tick(NOW)
    actions = runtime.store.get("activities", rows[0]["id"])["actions"]
    assert [call[0] for call in runtime.actions] == ["news"]
    assert actions["news"]["execution"]["status"] == "success"
    assert (
        actions["news"]["execution"]["finished_at"]
        == (NOW + timedelta(seconds=elapsed)).isoformat()
    )
    for kind in ("search", "social"):
        assert actions[kind]["execution"]["status"] == "skipped"
        assert actions[kind]["execution"]["reason"] == "expired_before_action"
    assert action_counts(service)["news"]["started"] == 1
    assert action_counts(service)["social"]["started"] == 0


@pytest.mark.asyncio
async def test_slow_outline_never_introduces_actions_using_tick_start_time(monkeypatch):
    elapsed = 0.0
    monkeypatch.setattr("living_world.life.monotonic", lambda: elapsed)
    runtime = Runtime()
    service = fixed_service(runtime)

    async def slow_generation(*args, **kwargs):
        nonlocal elapsed
        elapsed += 11 * 60
        return json.dumps({"activities": plan_rows()})

    runtime.generate = slow_generation
    await service.tick(NOW)
    actions = service.list_activities()[0]["actions"]
    assert runtime.actions == []
    assert all(not action["enabled"] for action in actions.values())
