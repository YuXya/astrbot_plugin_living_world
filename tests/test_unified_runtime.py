"""End-to-end memory routing, producer, preview and final-turn contracts."""

import copy
import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from living_world.runtime import Runtime


PRIVATE = "qq:FriendMessage:42"


class Host:
    def __init__(self):
        self.calls = []

    async def persona(self, name):
        return "角色：" + name

    async def session_persona(self, scope):
        return "可可"

    async def history(self, scope):
        return ""

    async def generate_request(self, request):
        self.calls.append(copy.deepcopy(request))
        task = request["task"]
        if task == "memory.query":
            return '{"keywords":["宠物","猫","名字"]}', {}
        if task == "memory.reflect":
            data = request["dynamic_context"]
            evidence = data["materials"][0]["text"]
            return json.dumps(
                {
                    "memories": [
                        {
                            "judgment": "可可记下了：" + evidence[-100:],
                            "evidence": evidence,
                            "attribute": "事实属性",
                            "owner": "self",
                            "stable": False,
                            "tags": ["经历"],
                        }
                    ]
                },
                ensure_ascii=False,
            ), {}
        return "今天回顾了已经记住的生活经历。", {}


@pytest.fixture
async def world(tmp_path):
    runtime = Runtime(tmp_path / "world.sqlite", Host())
    runtime.kick_memory = lambda: None
    await runtime.update_settings(
        {
            "persona_id": "可可",
            "sessions": [{"umo": PRIVATE}],
            "modules": {"search": True},
        }
    )
    yield runtime
    await runtime.stop()


async def test_events_are_queued_then_only_extracted_memories_are_injected(world):
    world.record_event("今天在走廊喝柠檬茶", key="tea", source="fiction")
    before = json.loads(await world.context_text(selection=["memory.recent"]))
    assert before["recent_memories"] == []
    assert "experiences" not in before
    await world.memory.process_pending()
    after = await world.context_bundle(selection=["memory.recent"])
    assert "柠檬茶" in after["text"]
    assert {row["block_id"] for row in after["sources"]} == {"memory.recent"}
    world.record_event("今天在走廊喝柠檬茶", key="tea", source="fiction")
    assert world.store.list("memory_jobs") == []


async def test_semantic_search_and_recent_share_one_bank_without_duplicate(world):
    world.memory.remember("可可养的猫叫团子", tags=["猫", "宠物"], source="admin")
    bundle = await world.context_bundle(PRIVATE, query="家里的毛孩子叫什么", selection=["memory"])
    assert "团子" in bundle["text"]
    assert [r["task"] for r in world.host.calls] == ["memory.query"]
    world.host.calls.clear()
    bundle = await world.context_bundle(
        PRIVATE, query="毛孩子", selection=["memory", "memory.recent"]
    )
    assert bundle["text"].count("可可养的猫叫团子") == 1
    assert all(row.get("access_count", 0) == 0 for row in world.store.list("memories"))


async def test_recent_only_never_calls_query_model(world):
    world.memory.remember("昨天学了水彩", source="admin")
    await world.context_bundle(PRIVATE, query="水彩", selection=["memory.recent"])
    assert world.host.calls == []


async def test_final_chat_queues_once_and_uses_frozen_ids(world):
    memory = world.memory.remember("喜欢水彩", source="admin")
    trace = {
        "managed": True,
        "id": "turn-one",
        "bound_persona": "可可",
        "received_at": 1789000000,
        "memory_sources": [{"memory_ids": [memory["id"]]}],
    }
    event = SimpleNamespace(
        unified_msg_origin=PRIVATE,
        message_str="我今天练了画画",
        get_sender_id=lambda: "42",
        get_extra=lambda key: trace if key == "living_world_trace" else None,
    )
    await world.chat.finalize_memory(
        event, SimpleNamespace(role="assistant", completion_text="", tools_call_name=["search"])
    )
    assert not world.store.list("memory_jobs")
    response = SimpleNamespace(
        role="assistant", completion_text="今天的水彩练习很开心。", tools_call_name=[]
    )
    await world.chat.finalize_memory(event, response)
    await world.chat.finalize_memory(event, response)
    jobs = world.store.list("memory_jobs")
    assert len(jobs) == 1
    assert "今天的水彩练习很开心" in jobs[0]["text"]


async def test_journal_reads_memories_not_raw_events_and_enqueues_original(world):
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    world.store.put(
        "events",
        "unprocessed",
        {"id": "unprocessed", "text": "未经提炼的原文不能旁路进入日记", "scope": "global"},
    )
    world.memory.remember("可可在走廊喝了柠檬茶", occurred_at=now.isoformat(), source="admin")
    world.memory.remember(
        "昨天见到旧朋友", occurred_at=(now - timedelta(days=1)).isoformat(), source="admin"
    )
    entry = await world.journal.generate(str(now.date()))
    writes = [r for r in world.host.calls if r["task"] == "journal.write"]
    assert len(writes) == 1
    assert "柠檬茶" in writes[0]["prompt"]
    assert "未经提炼" not in writes[0]["prompt"] and "昨天见到旧朋友" not in writes[0]["prompt"]
    assert not any(r["task"].endswith(".brief") for r in world.host.calls)
    assert any(job["key"] == "journal:" + entry["id"] for job in world.store.list("memory_jobs"))


async def test_build_and_trial_do_not_enqueue_or_reinforce(world):
    world.memory.remember("可可喜欢画画", stable=True, source="admin")
    before = world.store.export()
    request = await world.build_test_request("life.plan")
    assert world.host.calls == []
    assert request["context_layout_version"] == 4
    assert world.store.export() == before
    await world.test_request(request)
    assert not world.store.list("memory_jobs")
    assert not world.store.list("memory_feedback")


async def test_recall_tool_history_excludes_content_but_keeps_pairing(world):
    from astrbot.core.agent.message import Message, dump_messages_with_checkpoints

    trace = {"managed": True}
    event = SimpleNamespace(get_extra=lambda key: trace)
    context = SimpleNamespace(
        messages=[
            Message(
                role="assistant",
                tool_calls=[
                    {
                        "id": "recall",
                        "type": "function",
                        "function": {"name": "living_world_recall", "arguments": "{}"},
                    }
                ],
            ),
            Message(role="tool", tool_call_id="recall", content="本次临时召回的完整秘密"),
        ]
    )
    world.chat.protect_memory_history(event, context)
    world.chat.protect_memory_history(event, context)
    saved = dump_messages_with_checkpoints(context.messages)
    assert "完整秘密" not in json.dumps(saved, ensure_ascii=False)
    assert saved[-1]["tool_call_id"] == "recall"
    assert len(saved[-1]["content"]) == 1


async def test_query_wait_does_not_refresh_weather_thoughts_or_memory_snapshot(world):
    await world.update_settings(
        {
            "modules": {"weather": True},
            "context_layout": {"tasks": {"life.plan": ["memory", "weather", "task.thoughts"]}},
        }
    )
    memory = world.memory.remember("可可养的猫叫团子", source="admin")
    world.store.put(
        "observations",
        "weather",
        {
            "id": "weather",
            "module": "weather",
            "scope": "global",
            "text": "晴天 BEFORE",
            "created_at": 100,
        },
    )
    world.drives.thoughts = lambda: "想画画 BEFORE"
    original = world.host.generate_request

    async def mutate(request):
        if request["task"] == "memory.query":
            world.store.put(
                "observations",
                "weather",
                {
                    "id": "weather",
                    "module": "weather",
                    "scope": "global",
                    "text": "下雨 DURING",
                    "created_at": 101,
                },
            )
            world.drives.thoughts = lambda: "想睡觉 DURING"
            world.memory.update(memory["id"], {"text": "新的猫名 DURING"})
        return await original(request)

    world.host.generate_request = mutate
    request = await world.prepare_request("life.plan", "life", "TASK", {}, PRIVATE)
    text = request["system_prompt"] + request["prompt"]
    assert "晴天 BEFORE" in text and "想画画 BEFORE" in text
    assert "团子" in text, [item["task"] for item in world.host.calls]
    assert "DURING" not in text


async def test_explicit_empty_recent_snapshot_is_not_refilled_from_other_days(world):
    world.memory.remember("别的日期不能补进来", source="admin")
    await world.update_settings(
        {"context_layout": {"tasks": {"journal.write": ["memory", "memory.recent"]}}}
    )
    request = await world.prepare_request(
        "journal.write", "journal", "TASK", {"memories": [], "recent_memories": []}
    )
    assert "别的日期" not in request["prompt"] and world.host.calls == []


async def test_backup_cannot_restore_forgotten_memory_or_versions(world):
    world.store.put(
        "events",
        "old",
        {
            "id": "old",
            "text": "来源原记录",
            "source": "fiction",
            "scope": "global",
            "created_at": 1700000000,
        },
    )
    row = world.memory.remember("提炼的旧结论", source_keys=["event:old"])
    world.memory.update(row["id"], {"text": "修正的旧结论"})
    backup = world.export()
    world.memory.delete(row["id"])
    await world.restore(backup)
    assert world.store.get("memories", row["id"]) is None
    assert world.memory.history(row["id"]) == []
    assert world.store.get("events", "old")["text"] == "来源原记录"
    assert not any(
        "event:old" in job.get("source_keys", []) for job in world.store.list("memory_jobs")
    )


async def test_binding_change_during_query_rejects_mixed_request(world):
    world.memory.remember("可可养的猫叫团子", source="admin")
    original = world.host.generate_request

    async def change(request):
        if request["task"] == "memory.query":
            world.settings["persona_id"] = "另一个人格"
        return await original(request)

    world.host.generate_request = change
    with pytest.raises(ValueError, match="配置已改变"):
        await world.prepare_request("life.plan", "life", "TASK", {}, PRIVATE)


async def test_new_memory_does_not_inherit_time_before_creation(world, monkeypatch):
    await world.update_settings({"memory": {"forgetting_enabled": True, "low_decay_seconds": 60}})
    clock = [world.memory._clock]
    monkeypatch.setattr("living_world.memory.time.monotonic", lambda: clock[0])
    old = world.memory.remember("先前的记忆")
    clock[0] += 60
    new = world.memory.remember("刚刚出现的记忆")
    world.memory.maintain()
    assert world.store.get("memories", old["id"])["strength"] == 9
    assert world.store.get("memories", new["id"])["strength"] == 10
