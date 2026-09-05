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


@pytest.mark.asyncio
async def test_plan_uses_only_matching_memory_and_never_records_execution():
    runtime = Runtime()
    runtime.memory.entries = [
        {"scope": "private-a", "kind": "event", "text": "今天十点一起复习数学"},
        {"scope": "private-b", "kind": "event", "text": "另一个人的秘密"},
    ]
    runtime.responses = [
        json.dumps(
            {
                "activities": [
                    {
                        "start": "10:00",
                        "end": "10:30",
                        "title": "按约定复习",
                        "kind": "social",
                        "payload": {"topic": "数学"},
                        "scope": "global",
                    }
                ]
            }
        )
    ]
    service = LifeService(runtime)
    result = await service.plan_day(NOW, "private-a")
    assert result[0]["scope"] == "private-a"
    assert result[0]["status"] == "planned"
    assert "今天十点一起复习数学" in runtime.calls[0][1]
    assert "另一个人的秘密" not in runtime.calls[0][1]
    assert runtime.events == runtime.actions == []
    assert await service.plan_day(NOW, "private-a") == result
    assert len(runtime.calls) == 1


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
async def test_detail_can_add_scoped_social_action_without_fabricating_response():
    runtime = Runtime()
    activity(runtime, kind="fiction", scope="private-a", detailed=False)
    runtime.store.put("life_days", f"{NOW.date()}:global", {"status": "completed"})
    runtime.responses = [
        json.dumps(
            {
                "description": "在上数学课",
                "incident": "觉得无聊",
                "action": {
                    "kind": "social",
                    "title": "找群聊天",
                    "payload": {"reason": "数学课无聊"},
                },
            }
        )
    ]
    await LifeService(runtime).tick(NOW)
    assert len(runtime.actions) == 1
    assert runtime.actions[0][2] == "private-a"
    assert runtime.store.get("activities", "a:detail")["status"] == "completed"
    assert all("回复" not in event["text"] for event in runtime.events)


@pytest.mark.asyncio
async def test_temporary_action_is_considered_once_during_long_activity():
    runtime = Runtime()
    runtime.settings["life"]["spontaneous_minutes"] = 30
    runtime.store.put("life_days", f"{NOW.date()}:global", {"status": "completed"})
    activity(
        runtime,
        kind="fiction",
        start=(NOW - timedelta(minutes=35)).isoformat(),
        status="running",
    )
    runtime.responses = [
        json.dumps(
            {
                "action": {
                    "kind": "search",
                    "title": "查一道数学题",
                    "payload": {"query": "三角函数"},
                }
            }
        )
    ]
    service = LifeService(runtime)
    await service.tick(NOW)
    await service.tick(NOW + timedelta(minutes=1))
    assert len(runtime.calls) == len(runtime.actions) == 1
    assert runtime.actions[0][0] == "search"


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
    service = LifeService(runtime)
    activity(runtime)
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
async def test_revision_uses_fresh_memories_and_only_edits_future_scoped_plans():
    runtime = Runtime()
    service = LifeService(runtime)
    convert_now = service._now
    service._now = lambda now=None: convert_now(now or NOW)
    activity(
        runtime,
        "future",
        scope="private-a",
        start=(NOW + timedelta(minutes=20)).isoformat(),
    )
    activity(runtime, "done", scope="private-a", status="completed")
    activity(
        runtime,
        "other",
        scope="private-b",
        start=(NOW + timedelta(minutes=20)).isoformat(),
    )
    runtime.memory.entries = [
        {
            "scope": "private-a",
            "kind": "event",
            "text": "新的约定是十一点复习，旧约定已更新",
        },
        {"scope": "private-b", "text": "其他人的私人约定"},
    ]
    revision = {
        "updates": [
            {
                "id": "future",
                "changes": {
                    "start": "11:00",
                    "end": "11:30",
                    "title": "新的数学复习约定",
                },
            },
            {"id": "done", "changes": {"title": "不许改已完成"}},
            {"id": "other", "changes": {"title": "不许改其他场合"}},
        ],
        "additions": [
            {
                "start": "12:00",
                "end": "12:15",
                "title": "查询复习资料",
                "kind": "search",
                "payload": {"query": "数学"},
                "scope": "global",
            }
        ],
        "cancel": ["done", "other"],
    }
    runtime.responses = [json.dumps(revision), json.dumps(revision)]
    result = await service.revise("private-a", "约定更新")
    assert result["updated"] == ["future"]
    assert len(result["added"]) == 1
    assert result["cancelled"] == []
    assert runtime.store.get("activities", result["added"][0])["scope"] == "private-a"
    assert runtime.store.get("activities", "done")["status"] == "completed"
    assert runtime.store.get("activities", "other")["title"] == "数学课无聊，找群聊天"
    assert "新的约定是十一点复习" in runtime.calls[0][1]
    assert "其他人的私人约定" not in runtime.calls[0][1]
    assert (await service.revise("private-a", "重复通知"))["added"] == []
    assert runtime.actions == runtime.events == []


def test_changing_parent_plan_cancels_unexecuted_child_action():
    runtime = Runtime()
    service = LifeService(runtime)
    activity(runtime, "parent", kind="fiction", incident="旧细化")
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
