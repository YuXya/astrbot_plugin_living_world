import asyncio
import copy
import json
from datetime import datetime, timedelta, timezone

import pytest

from living_world.life import LifeService

NOW = datetime(2026, 9, 5, 10, 0, tzinfo=timezone(timedelta(hours=8)))


class Store:
    def __init__(self):
        self.data = {}

    def get(self, namespace, key, default=None):
        return copy.deepcopy(self.data.get(namespace, {}).get(key, default))

    def put(self, namespace, key, value):
        self.data.setdefault(namespace, {})[key] = copy.deepcopy(value)

    def list(self, namespace):
        return list(copy.deepcopy(self.data.get(namespace, {})).values())

    def delete(self, namespace, key):
        self.data.get(namespace, {}).pop(key, None)

    def claim(self, namespace, key, value):
        if key in self.data.get(namespace, {}):
            return False
        self.put(namespace, key, value)
        return True


class Memory:
    def __init__(self):
        self.entries = []

    def recall(self, **kwargs):
        # Return unfiltered entries to verify defense at the service boundary.
        return copy.deepcopy(self.entries)


class Runtime:
    def __init__(self):
        self.store = Store()
        self.memory = Memory()
        self.settings = {
            "character": {"timezone": "Asia/Shanghai"},
            "life": {"spontaneous_minutes": 0},
            "sessions": [],
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
        if not self.responses:
            return '{"activities":[]}'
        return self.responses.pop(0)

    async def execute_action(self, kind, payload, scope, action_id):
        self.actions.append((kind, payload, scope, action_id))
        return self.action_result

    def record_event(self, text, **kwargs):
        self.events.append({"text": text, **kwargs})


def activity(runtime, key="a", kind="social", scope="global", **changes):
    row = {
        "id": key,
        "date": NOW.date().isoformat(),
        "title": "数学课无聊，找群聊天",
        "kind": kind,
        "scope": scope,
        "start": NOW.isoformat(),
        "end": (NOW + timedelta(minutes=60)).isoformat(),
        "status": "planned",
        "detailed": True,
        "payload": {"reason": "数学课无聊"},
    }
    row.update(changes)
    runtime.store.put("activities", key, row)
    return row


def fixed_service(runtime, now=NOW):
    service = LifeService(runtime)
    convert = service._now
    service._now = lambda value=None: convert(value or now)
    return service


def plan_rows(count=10, start=NOW):
    rows = []
    for index in range(count):
        at = start + timedelta(hours=index)
        rows.append(
            {
                "start": at.isoformat(),
                "end": (at + timedelta(hours=1)).isoformat(),
                "title": f"生活活动 {index}",
                "content": "上数学课",
                "location": "教室",
                "sleep_state": "清醒",
                "actions": {
                    kind: {
                        "enabled": index < quota,
                        "intent": f"{kind} 的活动意图" if index < quota else "",
                        "at": at.isoformat() if index < quota else None,
                    }
                    for kind, quota in (("news", 2), ("search", 2), ("social", 3))
                },
            }
        )
    return rows


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
    assert await LifeService(runtime).plan_day(NOW, "private-a") == []
    assert runtime.calls == runtime.actions == runtime.events == []


@pytest.mark.asyncio
async def test_scoped_plan_without_memory_avoids_another_daily_outline():
    runtime = Runtime()
    assert await LifeService(runtime).plan_day(NOW, "private-a") == []
    assert runtime.calls == []


@pytest.mark.asyncio
async def test_real_action_is_executed_once_across_restart():
    runtime = Runtime()
    activity(runtime)
    service = LifeService(runtime)
    await service.tick(NOW)
    await LifeService(runtime).tick(NOW + timedelta(minutes=1))
    assert len(runtime.actions) == 1
    assert runtime.actions[0][:3] == ("social", {"reason": "数学课无聊"}, "global")
    assert runtime.store.get("activities", "a")["status"] == "completed"
    assert runtime.events == []  # Runtime owns successful external-action evidence.


@pytest.mark.asyncio
async def test_expired_or_interrupted_actions_do_not_catch_up():
    runtime = Runtime()
    activity(runtime, "late", start=(NOW - timedelta(minutes=20)).isoformat())
    activity(runtime, "interrupted", status="running")
    activity(
        runtime,
        "finished",
        end=NOW.isoformat(),
        start=(NOW - timedelta(hours=1)).isoformat(),
    )
    await LifeService(runtime).tick(NOW)
    assert runtime.actions == []
    assert {row["status"] for row in runtime.store.list("activities")} == {"skipped"}


@pytest.mark.asyncio
async def test_existing_claim_suppresses_uncertain_action():
    runtime = Runtime()
    activity(runtime)
    runtime.store.claim("life_action_claims", "a", {"started_at": NOW.isoformat()})
    await LifeService(runtime).tick(NOW)
    assert runtime.actions == []
    assert runtime.store.get("activities", "a")["status"] == "skipped"


@pytest.mark.asyncio
async def test_failure_is_not_a_successful_memory_or_retry():
    runtime = Runtime()
    runtime.action_result = {
        "status": "failed",
        "text": "",
        "reason": "provider unavailable",
    }
    activity(runtime)
    service = LifeService(runtime)
    await service.tick(NOW)
    await service.tick(NOW + timedelta(minutes=1))
    assert len(runtime.actions) == 1
    assert runtime.events == []
    assert runtime.store.get("activities", "a")["status"] == "failed"


@pytest.mark.asyncio
async def test_fiction_records_incident_at_start_then_finishes():
    runtime = Runtime()
    activity(
        runtime,
        kind="fiction",
        title="数学课",
        incident="忘带笔了",
        energy_delta=-5,
        mood="有点无聊",
    )
    service = LifeService(runtime)
    await service.tick(NOW)
    assert runtime.events[0]["source"] == "fiction"
    assert "忘带笔" in runtime.events[0]["text"]
    assert runtime.store.get("activities", "a")["status"] == "running"
    assert service.state()["energy"] == 75
    await service.tick(NOW + timedelta(hours=1))
    assert runtime.store.get("activities", "a")["status"] == "completed"
    assert len(runtime.events) == 1


@pytest.mark.asyncio
async def test_private_fiction_does_not_change_public_life_state():
    runtime = Runtime()
    activity(
        runtime,
        kind="fiction",
        scope="private-a",
        mood="私人聊天让我想哭",
        energy_delta=-5,
    )
    service = LifeService(runtime)
    await service.tick(NOW)
    assert service.state()["mood"] == "平静"
    assert service.state()["energy"] == 80
    assert runtime.events[0]["scope"] == "private-a"


@pytest.mark.asyncio
async def test_detail_cannot_add_unmarked_social_action():
    runtime = Runtime()
    activity(runtime, kind="fiction", scope="private-a", detailed=False)
    runtime.store.put("life_days", f"{NOW.date()}:global", {"status": "completed"})
    runtime.responses = [
        json.dumps(
            {
                "incident": "觉得无聊",
                "action": {"kind": "social", "payload": {"reason": "数学课无聊"}},
            }
        )
    ]
    await LifeService(runtime).tick(NOW)
    assert runtime.actions == []
    assert len(runtime.store.list("activities")) == 1
    assert "觉得无聊" in runtime.events[0]["text"]


@pytest.mark.asyncio
async def test_legacy_spontaneous_setting_no_longer_creates_actions():
    runtime = Runtime()
    runtime.settings["life"]["spontaneous_minutes"] = 30
    runtime.store.put("life_days", f"{NOW.date()}:global", {"status": "completed"})
    activity(
        runtime, kind="fiction", start=(NOW - timedelta(minutes=35)).isoformat(), status="running"
    )
    service = LifeService(runtime)
    await service.tick(NOW)
    await service.tick(NOW + timedelta(minutes=1))
    assert runtime.calls == runtime.actions == []


@pytest.mark.asyncio
async def test_disable_during_model_call_prevents_activity_writes():
    runtime = Runtime()

    async def disable(*args, **kwargs):
        runtime.disabled.add("life")
        return '{"activities":[{"start":"10:00","end":"11:00","title":"上课"}]}'

    runtime.generate = disable
    assert await LifeService(runtime).plan_day(NOW) == []
    assert runtime.store.list("activities") == []


@pytest.mark.asyncio
async def test_cancellation_marks_action_uncertain_and_never_retries():
    runtime = Runtime()
    activity(runtime)

    async def cancel(*args):
        raise asyncio.CancelledError

    runtime.execute_action = cancel
    with pytest.raises(asyncio.CancelledError):
        await LifeService(runtime).tick(NOW)
    assert runtime.store.get("activities", "a")["status"] == "skipped"
    await LifeService(runtime).tick(NOW + timedelta(minutes=1))


def test_manual_edits_cannot_relabel_scope_or_replay_completed_actions():
    runtime = Runtime()
    service = fixed_service(runtime)
    activity(runtime, start=(NOW + timedelta(minutes=10)).isoformat())
    with pytest.raises(ValueError):
        service.update_activity("a", {"scope": "global"})
    service.update_activity("a", {"title": "新安排"})
    assert runtime.store.get("activities", "a")["title"] == "新安排"
    activity(runtime, status="completed")
    with pytest.raises(ValueError):
        service.update_activity("a", {"title": "再来一次"})
    with pytest.raises(ValueError):
        service.update_state({"profile": "自动重写人设"})


@pytest.mark.asyncio
async def test_private_revision_uses_fresh_memory_in_scoped_overlay_only():
    runtime = Runtime()
    service = fixed_service(runtime)
    activity(runtime, "future", kind="fiction", start=(NOW + timedelta(minutes=20)).isoformat())
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
    result = await service.revise("private-a", "约定更新")
    assert result == {"updated": ["future"], "added": [], "cancelled": []}
    stored = runtime.store.get("activities", "future")
    assert stored["title"] == "数学课无聊，找群聊天"
    assert stored["scope_overrides"]["private-a"]["title"] == "和朋友复习数学"
    assert "新的约定是复习数学" in runtime.calls[0][1]
    assert "其他人的私人约定" not in runtime.calls[0][1]
    assert "和朋友复习数学" not in json.dumps(service._view(stored, "global"), ensure_ascii=False)
    assert "和朋友复习数学" not in json.dumps(
        service._view(stored, "private-b"), ensure_ascii=False
    )
    assert runtime.actions == runtime.events == []


def test_changing_parent_plan_cancels_unexecuted_child_action():
    runtime = Runtime()
    service = fixed_service(runtime)
    activity(
        runtime,
        "parent",
        kind="fiction",
        incident="旧细化",
        start=(NOW + timedelta(minutes=10)).isoformat(),
    )
    activity(runtime, "child", parent_id="parent")
    service.update_activity("parent", {"title": "改为认真上课"})
    assert runtime.store.get("activities", "child")["status"] == "skipped"
    assert not runtime.store.get("activities", "parent")["detailed"]
    assert "incident" not in runtime.store.get("activities", "parent")


@pytest.mark.asyncio
async def test_removed_scope_stops_existing_fiction_and_does_not_create_memories():
    runtime = Runtime()

    async def allowed(scope):
        return scope == "global"

    runtime.scope_allowed = allowed
    runtime.settings["sessions"] = [{"umo": "private-a", "enabled": True}]
    activity(runtime, kind="fiction", scope="private-a", detailed=False)
    await LifeService(runtime).tick(NOW)
    assert runtime.events == runtime.actions == []
    assert runtime.store.get("activities", "a")["status"] == "planned"
    assert all(call[2] == "global" for call in runtime.calls)


@pytest.mark.asyncio
async def test_binding_change_during_detail_stops_fiction_evidence():
    runtime = Runtime()
    permitted = True

    async def allowed(scope):
        return permitted

    async def generate(*args, **kwargs):
        nonlocal permitted
        permitted = False
        return '{"incident":"忘带笔","action":{"kind":"social","payload":{}}}'

    runtime.scope_allowed = allowed
    runtime.generate = generate
    runtime.store.put("life_days", f"{NOW.date()}:global", {"status": "completed"})
    activity(runtime, kind="fiction", detailed=False)
    await LifeService(runtime).tick(NOW)
    assert runtime.events == runtime.actions == []
    assert runtime.store.get("activities", "a")["status"] == "planned"
    assert len(runtime.store.list("activities")) == 1


@pytest.mark.asyncio
async def test_default_plan_exact_counts_overlap_once_and_original_json_survives():
    runtime = Runtime()
    runtime.memory.entries = [
        {"scope": "global", "text": "昨天公开约好复习"},
        {"scope": "private-a", "text": "私聊的秘密"},
    ]
    service, rows = await publish(runtime)
    assert len(rows) == 10
    summary = service.day_summary(NOW.date())
    assert {kind: data["planned"] for kind, data in summary["counts"].items()} == {
        "news": 2,
        "search": 2,
        "social": 3,
    }
    assert all(rows[0]["actions"][kind]["enabled"] for kind in ("news", "search", "social"))
    assert "昨天公开约好复习" in runtime.calls[0][1] and "私聊的秘密" not in runtime.calls[0][1]
    assert runtime.events == runtime.actions == []
    assert await service.plan_day(NOW) == rows
    assert await LifeService(runtime).plan_day(NOW) == rows
    assert len(runtime.calls) == 1
    generation = runtime.store.get("life_days", f"{NOW.date()}:global")
    assert json.loads(generation["raw_json"])["activities"][0]["title"] == "生活活动 0"
    assert len(generation["adopted_activities"]) == 10
    assert generation["parameters"]["daily_plan_time"] == "06:00"
    assert generation["full_request"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "problem",
    [
        "count",
        "quota",
        "missing_actions",
        "string_enabled",
        "outside_time",
        "action_order",
        "overlapping_activities",
    ],
)
async def test_invalid_model_plan_never_becomes_formal_and_retains_raw(problem):
    runtime = Runtime()
    rows = plan_rows()
    if problem == "count":
        rows.pop()
    elif problem == "quota":
        rows[2]["actions"]["social"]["enabled"] = False
    elif problem == "missing_actions":
        del rows[0]["actions"]["news"]
    elif problem == "string_enabled":
        rows[0]["actions"]["news"]["enabled"] = "true"
    elif problem == "outside_time":
        rows[0]["actions"]["social"]["at"] = "23:00"
    elif problem == "action_order":
        rows[0]["actions"]["news"]["at"] = "10:30"
    else:
        rows[1]["start"] = "10:30"
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
async def test_late_generation_skips_overdue_actions_without_catch_up():
    runtime = Runtime()
    late = NOW + timedelta(hours=2, minutes=20)
    service, rows = await publish(runtime, rows=plan_rows(), now=late)
    await service.tick(late)
    assert runtime.actions == []
    assert all(
        action["execution"]["status"] == "skipped"
        for row in rows
        for action in row["actions"].values()
        if action["enabled"]
    )
    assert service.day_summary(late.date())["counts"]["social"]["skipped"] == 3


@pytest.mark.asyncio
async def test_marked_actions_execute_news_search_social_and_never_repeat():
    runtime = Runtime()
    service, rows = await publish(runtime)
    runtime.responses = ['{"description":"数学课","incident":"忘带笔"}']
    await service.tick(NOW)
    assert [call[0] for call in runtime.actions] == ["news", "search", "social"]
    assert all(
        call[1]["planned"] and call[1]["activity_id"] == rows[0]["id"] for call in runtime.actions
    )
    await service.tick(NOW + timedelta(minutes=1))
    await fixed_service(runtime).tick(NOW + timedelta(minutes=2))
    assert len(runtime.actions) == 3
    summary = service.day_summary(NOW.date())
    assert all(value["success"] == 1 for value in summary["counts"].values())
    assert "忘带笔" in runtime.events[0]["text"]
    assert len(runtime.store.list("activities")) == 10


@pytest.mark.asyncio
async def test_unmarked_activity_never_invents_actions():
    runtime = Runtime()
    service, rows = await publish(runtime)
    runtime.responses = [
        '{"action":{"kind":"social","payload":{}},"actions":{"search":{"enabled":true}}}'
    ]
    unmarked = rows[4]
    at = NOW + timedelta(hours=4)
    await service._advance(unmarked, at)
    assert runtime.actions == []
    assert len(runtime.store.list("activities")) == 10
    assert all(
        not a["enabled"]
        for a in runtime.store.get("activities", unmarked["id"])["actions"].values()
    )


@pytest.mark.asyncio
async def test_disabled_news_and_failed_search_do_not_block_social_or_replay():
    runtime = Runtime()
    service, rows = await publish(runtime)
    runtime.disabled.add("news")

    async def execute(kind, payload, scope, action_id):
        runtime.actions.append((kind, payload, scope, action_id))
        if kind == "search":
            raise ValueError("Search unavailable")
        return {"status": "skipped", "reason": "quiet_hours"}

    runtime.execute_action = execute
    await service.tick(NOW)
    states = runtime.store.get("activities", rows[0]["id"])["actions"]
    assert states["news"]["execution"]["reason"] == "module_disabled"
    assert states["search"]["execution"]["status"] == "failed"
    assert states["social"]["execution"]["reason"] == "quiet_hours"
    runtime.disabled.clear()
    await service.tick(NOW + timedelta(minutes=1))
    assert [call[0] for call in runtime.actions] == ["search", "social"]


@pytest.mark.asyncio
async def test_restart_does_not_send_recently_overdue_modern_actions():
    runtime = Runtime()
    _service, rows = await publish(runtime)
    await fixed_service(runtime).tick(NOW + timedelta(seconds=5))
    assert runtime.actions == []
    assert (
        runtime.store.get("activities", rows[0]["id"])["actions"]["social"]["execution"]["reason"]
        == "overdue_after_restart"
    )


@pytest.mark.asyncio
async def test_modern_running_claim_becomes_uncertain_and_does_not_retry():
    runtime = Runtime()
    service, rows = await publish(runtime)
    row = rows[0]
    row["actions"]["social"]["execution"] = {"status": "running"}
    runtime.store.put("activities", row["id"], row)
    runtime.store.claim("life_action_claims", f"{row['id']}:news", {"started_at": NOW.isoformat()})
    await service.tick(NOW)
    assert [call[0] for call in runtime.actions] == ["search"]
    assert service.day_summary(NOW.date())["counts"]["social"]["success"] == 0


@pytest.mark.asyncio
async def test_cancellation_preserves_modern_execution_record():
    runtime = Runtime()
    service, rows = await publish(runtime)

    async def cancel(*args):
        raise asyncio.CancelledError

    runtime.execute_action = cancel
    with pytest.raises(asyncio.CancelledError):
        await service.tick(NOW)
    assert (
        runtime.store.get("activities", rows[0]["id"])["actions"]["news"]["execution"]["reason"]
        == "execution_interrupted_outcome_unknown"
    )
    assert runtime.store.get("life_action_claims", f"{rows[0]['id']}:news")


@pytest.mark.asyncio
async def test_future_batch_moves_quota_without_changing_original_or_executed_records():
    runtime = Runtime()
    service, rows = await publish(runtime)
    runtime.settings["life"].update(activity_count=5, news_count=1, search_count=1, social_count=1)
    old = copy.deepcopy(rows[1]["actions"])
    new = copy.deepcopy(rows[5]["actions"])
    old["social"].update(enabled=False, intent="", at=None)
    new["social"].update(enabled=True, intent="午后和群友聊天", at=rows[5]["start"])
    with pytest.raises(ValueError):
        service.update_activity(rows[5]["id"], {"actions": new})
    assert runtime.store.get("activities", rows[5]["id"]) == rows[5]
    service.update_activities(
        [
            {"id": rows[1]["id"], "changes": {"actions": old}},
            {"id": rows[5]["id"], "changes": {"actions": new}},
        ]
    )
    assert service.day_summary(NOW.date())["counts"]["social"]["planned"] == 3
    marker = runtime.store.get("life_days", f"{NOW.date()}:global")
    assert marker["parameters"]["activity_count"] == 10
    assert not marker["adopted_activities"][5]["actions"]["social"]["enabled"]
    with pytest.raises(ValueError):
        service.update_activity(rows[0]["id"], {"title": "已经开始不能改"})
    await service.tick(NOW)
    executed = runtime.store.get("activities", rows[0]["id"])
    service.update_activity(rows[6]["id"], {"title": "未来新安排"})
    assert runtime.store.get("activities", rows[0]["id"]) == executed


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
async def test_legacy_day_without_marker_is_adopted_unchanged_on_upgrade():
    runtime = Runtime()
    old = activity(runtime, kind="fiction")
    service = fixed_service(runtime)
    assert await service.plan_day(NOW) == [old]
    assert runtime.calls == []
    assert "actions" not in runtime.store.get("activities", old["id"])
    assert runtime.store.get("life_days", f"{NOW.date()}:global")["legacy"]


@pytest.mark.asyncio
async def test_prepared_formal_plan_recovers_missing_rows_without_model():
    runtime = Runtime()
    _service, rows = await publish(runtime)
    marker = runtime.store.get("life_days", f"{NOW.date()}:global")
    marker["status"] = "prepared"
    runtime.store.put("life_days", f"{NOW.date()}:global", marker)
    runtime.store.delete("activities", rows[3]["id"])
    assert len(await LifeService(runtime).plan_day(NOW)) == 10
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
    assert service.state()["location"] == "教室"
    assert service.state()["sleep_state"] == "清醒"
    assert service.current("private-a")["location"] == "私人约定地点"
    assert service.current("private-b")["location"] == "教室"
    assert "scope_overrides" not in service.current("private-b")


@pytest.mark.asyncio
async def test_failure_backoff_preserves_raw_invalid_json_and_isolated_legacy_life():
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
    assert service.list_activities() == rows
    assert len(runtime.calls) == 1


@pytest.mark.asyncio
async def test_before_generation_settings_change_is_frozen_until_next_day():
    runtime = Runtime()
    before = NOW.replace(hour=5, minute=59)
    service = fixed_service(runtime, before)
    await service.tick(before)
    runtime.settings["life"].update(
        daily_plan_time="23:00", activity_count=5, news_count=1, search_count=1, social_count=1
    )
    assert service.plan_request()["context"]["parameters"]["activity_count"] == 5
    assert service.plan_request(formal=True)["context"]["parameters"]["activity_count"] == 10
    runtime.responses = [json.dumps({"activities": plan_rows()})]
    assert len(await service.plan_day(before.replace(hour=6, minute=0))) == 10
    marker = runtime.store.get("life_days", f"{before.date()}:global")
    assert marker["parameters"]["daily_plan_time"] == "06:00"


@pytest.mark.asyncio
async def test_previous_private_commitment_refines_existing_slots_once_per_day():
    runtime = Runtime()
    service, rows = await publish(runtime)
    for row in rows:
        row["detailed"] = True
        runtime.store.put("activities", row["id"], row)
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
    assert "昨天约好今天午后一起复习数学" in runtime.calls[-1][1]
    assert runtime.calls[-1][2] == "private-a"
    assert len(service.list_activities()) == 10
    stored = runtime.store.get("activities", rows[1]["id"])
    assert stored["title"] == "生活活动 1"
    assert stored["scope_overrides"]["private-a"]["title"] == "和朋友复习数学"
    assert (
        runtime.store.get("life_scoped_revisions", f"{NOW.date()}:private-a")["status"]
        == "completed"
    )


@pytest.mark.asyncio
async def test_scoped_daily_revision_failure_has_persistent_retry_cooldown():
    runtime = Runtime()
    service, rows = await publish(runtime)
    for row in rows:
        row["detailed"] = True
        runtime.store.put("activities", row["id"], row)
    memory = {"id": "promise", "scope": "private-a", "text": "今天约好复习"}
    runtime.memory.entries = [memory]
    runtime.store.put("memories", memory["id"], memory)
    runtime.responses = ["not json"]
    await service.tick(NOW)
    marker = runtime.store.get("life_scoped_revisions", f"{NOW.date()}:private-a")
    assert marker["status"] == "failed"
    assert marker["retry_after"] == (NOW + timedelta(minutes=15)).timestamp()
    await fixed_service(runtime).tick(NOW + timedelta(minutes=1))
    assert len(runtime.calls) == 2
    assert len(service.list_activities()) == 10


@pytest.mark.asyncio
async def test_preview_does_not_reinforce_or_persist_formal_data():
    runtime = Runtime()
    memory_calls = []

    def recall(**kwargs):
        memory_calls.append(kwargs)
        return []

    runtime.memory.recall = recall
    service = fixed_service(runtime)
    request = service.plan_request()
    assert request["context"]["parameters"]["activity_count"] == 10
    assert memory_calls[0]["reinforce"] is False
    assert runtime.store.data == {}
    assert runtime.calls == runtime.events == runtime.actions == []


@pytest.mark.asyncio
@pytest.mark.parametrize("expired_by", ["activity_end", "stale_window"])
async def test_slow_news_skips_later_search_and_social_after_window(monkeypatch, expired_by):
    elapsed = 0.0
    monkeypatch.setattr("living_world.life.monotonic", lambda: elapsed)
    runtime = Runtime()
    raw = plan_rows()
    if expired_by == "activity_end":
        raw[0]["end"] = (NOW + timedelta(minutes=5)).isoformat()
    service, rows = await publish(runtime, rows=raw)
    for row in rows:
        row["detailed"] = True
        runtime.store.put("activities", row["id"], row)

    async def slow_news(kind, payload, scope, action_id):
        nonlocal elapsed
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
        assert actions[kind]["execution"]["reason"] == "expired_after_previous_action"
        assert runtime.store.get("life_action_claims", f"{rows[0]['id']}:{kind}") is None
    await service.tick(NOW + timedelta(seconds=elapsed + 1))
    assert [call[0] for call in runtime.actions] == ["news"]


@pytest.mark.asyncio
async def test_slow_detail_cannot_start_expired_activity_or_send_actions(monkeypatch):
    elapsed = 0.0
    monkeypatch.setattr("living_world.life.monotonic", lambda: elapsed)
    runtime = Runtime()
    service, rows = await publish(runtime)

    async def slow_detail(*args, **kwargs):
        nonlocal elapsed
        elapsed += 65 * 60
        return '{"description":"Late model detail","incident":"Forgot a pen"}'

    runtime.generate = slow_detail
    await service._advance(rows[0], NOW)
    stored = runtime.store.get("activities", rows[0]["id"])
    assert stored["status"] == "skipped"
    assert stored["reason"] == "expired_after_detail"
    assert all(
        action["execution"]["reason"] == "expired_after_detail"
        for action in stored["actions"].values()
    )
    assert runtime.actions == runtime.events == []


@pytest.mark.asyncio
async def test_slow_generation_does_not_execute_actions_using_tick_start_time(monkeypatch):
    elapsed = 0.0
    monkeypatch.setattr("living_world.life.monotonic", lambda: elapsed)
    runtime = Runtime()
    service = fixed_service(runtime)
    raw = plan_rows()

    async def slow_generation(*args, **kwargs):
        nonlocal elapsed
        elapsed += 11 * 60
        return json.dumps({"activities": raw})

    runtime.generate = slow_generation
    await service.tick(NOW)
    actions = service.list_activities()[0]["actions"]
    assert runtime.actions == []
    assert all(action["execution"]["status"] == "skipped" for action in actions.values())
