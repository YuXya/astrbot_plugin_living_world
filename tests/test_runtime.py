import asyncio
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from living_world.config import MODULES, settings_from
from living_world.runtime import Runtime
from living_world.store import Store

PRIVATE = "qq:FriendMessage:42"
GROUP = "qq:GroupMessage:100"


class FakeHost:
    def __init__(self):
        self.calls = []
        self.sent = []
        self.answers = []
        self.block = None
        self.entered = asyncio.Event()

    async def persona(self, persona_id):
        if persona_id != "student":
            raise ValueError("Unknown persona")
        return "你是一位学生。"

    async def session_persona(self, scope):
        return "student" if scope in {PRIVATE, GROUP} else "other"

    async def generate(self, provider, prompt, system, scope):
        self.calls.append((provider, prompt, system, scope))
        self.entered.set()
        if self.block:
            await self.block.wait()
        value = self.answers.pop(0) if self.answers else "你好，今天过得怎么样？"
        if isinstance(value, Exception):
            raise value
        return value, {"provider": provider or "default", "input_tokens": 10}

    async def generate_request(self, request):
        task = request["task"]
        if task == "memory.query":
            return '{"keywords":["明天","约定"]}', {}
        if task == "memory.feedback":
            return '{"feedback":[]}', {}
        return await self.generate(
            request["provider_id"], request["prompt"], request["system_prompt"], request["scope"]
        )

    async def history(self, scope):
        return "私聊秘密：准备惊喜礼物" if scope == PRIVATE else "小明：数学课有点难"

    async def send(self, scope, text):
        self.sent.append((scope, text))
        return True

    def group_history_enabled(self, scope):
        return True

    def host_interjection_enabled(self, scope):
        return False

    async def catalogs(self, sessions):
        return {
            "personas": [{"id": "student", "name": "student"}],
            "providers": [],
            "sessions": [
                {"umo": s["umo"], "persona_id": await self.session_persona(s["umo"])}
                for s in sessions
            ],
        }

    async def call_tool(self, name, arguments, scope, plugin_name=None):
        return "测试搜索结果 https://example.test/article"


@pytest.fixture
async def runtime(tmp_path):
    host = FakeHost()
    runtime = Runtime(tmp_path / "world.sqlite", host)
    runtime.kick_memory = lambda: None
    await runtime.update_settings(
        {
            "persona_id": "student",
            "sessions": [{"umo": PRIVATE}, {"umo": GROUP}],
            "life": {"schedule_start": "00:00", "schedule_end": "24:00"},
        }
    )
    yield runtime
    await runtime.stop()


def test_store_claim_persistence_and_atomic_restore(tmp_path):
    path = tmp_path / "world.sqlite"
    store = Store(path)
    assert store.claim("actions", "once", {"id": "once", "status": "running"})
    store.close()
    store = Store(path)
    assert not store.claim("actions", "once", {"id": "once"})
    with pytest.raises((KeyError, ValueError)):
        store.restore([{"namespace": "events", "key": "a", "value": {"id": "a"}}, {"bad": 1}])
    assert store.get("events", "a") is None
    store.close()


async def test_scope_and_updated_memories_reach_plan(runtime):
    record = runtime.memory.remember("明天八点见", scope=PRIVATE)
    runtime.memory.update(record["id"], {"text": "明天九点见"})
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    runtime.store.put(
        "activities",
        "future",
        {
            "id": "future",
            "date": now.date().isoformat(),
            "start": (now + timedelta(minutes=5)).isoformat(),
            "end": (now + timedelta(minutes=30)).isoformat(),
            "title": "自由安排",
            "scope": "global",
            "kind": "fiction",
            "status": "planned",
            "payload": {},
        },
    )
    runtime.host.answers = ['{"updates":[{"id":"future","changes":{"title":"明天九点的约定"}}]}']
    assert await runtime.life.plan_day(scope=PRIVATE) == []
    await runtime.life.revise(scope=PRIVATE, reason="有效约定")
    prompt = runtime.host.calls[-1][1]
    assert "九点见" in prompt and "八点见" not in prompt
    activity = runtime.store.get("activities", "future")
    assert activity["title"] == "自由安排"
    assert activity["scope_overrides"][PRIVATE]["title"] == "明天九点的约定"
    assert "九点见" not in await runtime.context_text(GROUP)
    assert "九点见" not in await runtime.context_text("global")


async def test_persona_model_and_module_boundary(runtime):
    await runtime.update_settings({"models": {"default": "general", "memory": "small"}})
    await runtime.generate("memory", "extract", PRIVATE)
    assert runtime.host.calls[-1][0] == "small"
    assert "学生" in runtime.host.calls[-1][2]
    assert not await runtime.scope_allowed("qq:FriendMessage:other")
    await runtime.update_settings({"modules": {"memory": False}})
    with pytest.raises(ValueError):
        await runtime.generate("memory", "extract", PRIVATE)
    assert runtime.enabled("life")


async def test_close_memory_during_model_call_preserves_data(runtime):
    runtime.memory.remember("保留记录", scope=PRIVATE)
    runtime.host.block = asyncio.Event()
    task = asyncio.create_task(runtime.generate("memory", "extract", PRIVATE))
    await runtime.host.entered.wait()
    await runtime.update_settings({"modules": {"memory": False}})
    with pytest.raises(asyncio.CancelledError):
        await task
    assert runtime.store.list("memories")
    assert runtime.store.list("usage")[0]["status"] == "cancelled"


async def test_scheduled_social_sends_and_records_only_target_scope(runtime):
    await runtime.update_settings(
        {"modules": {"proactive": True}, "sessions": [{"umo": GROUP, "weight": 1}]}
    )
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    activity = {
        "id": "math-social",
        "date": now.date().isoformat(),
        "start": (now - timedelta(seconds=5)).isoformat(),
        "end": (now + timedelta(minutes=5)).isoformat(),
        "title": "数学课无聊，找群聊天",
        "kind": "fiction",
        "schema_version": 3,
        "payload": {"reason": "数学课无聊"},
        "scope": "global",
        "status": "planned",
        "detailed": True,
        "actions": runtime.life._empty_actions(),
    }
    activity["actions"]["social"] = {
        "enabled": True,
        "intent": "数学课无聊",
        "reason": "课间休息聊聊",
        "at": now.isoformat(),
        "execution": {"status": "pending"},
    }
    runtime.store.put("activities", activity["id"], activity)
    for scope in ("global", GROUP):
        runtime.store.put("life_days", f"{now.date().isoformat()}:{scope}", {"status": "completed"})
    await runtime.life.tick(now)
    assert len(runtime.host.sent) == 1
    assert (
        runtime.store.get("activities", activity["id"])["actions"]["social"]["execution"]["status"]
        == "success"
    )
    events = [event for event in runtime.store.list("events") if event["kind"] == "social"]
    assert len(events) == 1 and events[0]["scope"] == GROUP
    assert not [e for e in events if e["scope"] == "global"]
    await runtime.life.tick(now)
    assert len(runtime.host.sent) == 1


async def test_action_duplicate_persists_after_restart(runtime, tmp_path):
    await runtime.update_settings({"modules": {"proactive": True}, "sessions": [{"umo": GROUP}]})
    first = await runtime.execute_action("social", {"reason": "聊聊"}, "global", "stable-id")
    assert first["status"] == "success"
    path = tmp_path / "world.sqlite"
    await runtime.stop()
    second = Runtime(path, FakeHost())
    try:
        result = await second.execute_action("social", {"reason": "聊聊"}, "global", "stable-id")
        assert result["status"] == "skipped" and not second.host.sent
    finally:
        await second.stop()


async def test_diary_cannot_leak_private_events(runtime):
    runtime.record_event("私人约定：礼物保密", scope=PRIVATE, source="action")
    runtime.record_event("角色今天忘带笔", scope="global", source="fiction")
    runtime.memory.remember("私人约定：礼物保密", scope=PRIVATE, source="chat")
    runtime.memory.remember("角色今天忘带笔", scope="global", source="fiction")
    runtime.host.answers = ["角色今天忘带笔。"]
    await runtime.journal.generate(scope="global")
    assert "礼物保密" not in runtime.host.calls[-1][1]
    assert "忘带笔" in runtime.host.calls[-1][1]


async def test_restore_keeps_claims_and_disables_all_automatic_modules(runtime):
    runtime.store.put("actions", "once", {"id": "once", "status": "success"})
    backup = runtime.export()
    backup["settings"]["character"]["profile"] = "恢复的资料"
    backup["records"].append(
        {"namespace": "events", "key": "new", "value": {"id": "new", "text": "旧经历"}}
    )
    await runtime.restore(backup)
    assert not any(runtime.enabled(m) for m in MODULES)
    assert runtime.settings["character"]["profile"] == "恢复的资料"
    assert runtime.store.get("events", "new")
    assert runtime.store.get("actions", "once")["status"] == "success"


async def test_no_raw_chat_copy_and_memory_evidence(runtime):
    runtime.host.answers = [
        json.dumps(
            {"memories": [{"text": "明天九点碰面", "kind": "event", "evidence": "明天九点碰面"}]},
            ensure_ascii=False,
        ),
        '{"updates":[],"additions":[],"cancel":[]}',
    ]
    runtime.memory.enqueue_chat(
        "明天九点碰面", "好的，九点碰面", scope=PRIVATE, person_id="42", round_id="final-turn"
    )
    await runtime.update_settings({"memory": {"chat_batch_rounds": 1}})
    await runtime.memory.process_pending()
    assert runtime.store.list("memories")
    assert not runtime.store.list("events")


async def test_current_chat_memory_can_form_scoped_diary(runtime):
    row = runtime.memory.remember("明天早上约好聊天", scope=PRIVATE, source="chat")
    runtime.memory.update(row["id"], {"text": "明天下午约好聊天"})
    runtime.host.answers = ["今天约好明天下午聊天。"]
    entry = await runtime.journal.generate(scope=PRIVATE)
    prompt = runtime.host.calls[-1][1]
    assert "明天下午约好聊天" in prompt and "明天早上约好聊天" not in prompt
    assert entry["scope"] == PRIVATE
    assert not runtime.journal.list_entries("global")


async def test_failed_recheck_blocks_outdated_future_action(runtime):
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    runtime.store.put(
        "activities",
        "old",
        {
            "id": "old",
            "date": now.date().isoformat(),
            "start": (now + timedelta(minutes=5)).isoformat(),
            "end": (now + timedelta(minutes=10)).isoformat(),
            "title": "旧约定",
            "kind": "social",
            "scope": PRIVATE,
            "status": "planned",
            "payload": {},
            "detailed": True,
        },
    )
    runtime.host.answers = [ValueError("Model failed")]
    with pytest.raises(ValueError):
        await runtime.revise(PRIVATE, "约定已经改变", force=True)
    activity = runtime.store.get("activities", "old")
    assert activity["needs_review"]
    await runtime.life._advance(activity, now + timedelta(minutes=6))
    assert runtime.host.sent == []


async def test_bilibili_watch_memories_stop_with_module(runtime):
    runtime.memory.remember("视频中的公开知识", source="bilibili_watch")
    assert not runtime.memory.recall("视频")
    await runtime.update_settings({"modules": {"bilibili": True}})
    assert runtime.memory.recall("视频")


async def test_shutdown_cancels_inflight_actions_before_closing_store(runtime):
    await runtime.update_settings({"modules": {"proactive": True}, "sessions": [{"umo": PRIVATE}]})
    runtime.host.block = asyncio.Event()
    action = asyncio.create_task(
        runtime.execute_action("social", {"reason": "chat"}, "global", "closing")
    )
    await runtime.host.entered.wait()
    await runtime.stop()
    result = await asyncio.gather(action, return_exceptions=True)
    assert not isinstance(result[0], Exception)
    assert not runtime.host.sent


@pytest.mark.parametrize(
    "patch",
    [
        {"social": {"target_count": 0}},
        {"sessions": [{"umo": "1234"}]},
        {"sessions": [{"umo": PRIVATE, "weight": float("nan")}]},
        {"social": {"cooldown_minutes": -1}},
    ],
)
def test_invalid_config_is_rejected(patch):
    with pytest.raises((ValueError, TypeError)):
        settings_from(patch)
