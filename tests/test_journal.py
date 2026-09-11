"""Journal writing consumes scoped unified memories and archives full output."""

import asyncio
import copy
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from living_world.runtime import Runtime

PRIVATE = "qq:FriendMessage:42"
OTHER = "qq:FriendMessage:43"


class Host:
    def __init__(self):
        self.calls = []
        self.entered = asyncio.Event()
        self.block = None
        self.block_task = ""
        self.responses = {}

    async def persona(self, name):
        return "角色：" + name

    async def session_persona(self, scope):
        return "可可"

    async def history(self, scope):
        return ""

    async def generate_request(self, request):
        self.calls.append(copy.deepcopy(request))
        task = request["task"]
        if self.block and task == self.block_task:
            self.entered.set()
            await self.block.wait()
        response = self.responses.get(task)
        if isinstance(response, Exception):
            raise response
        if response is not None:
            return response, {}
        if task == "memory.query":
            return '{"keywords":["知识","新闻","天文","学习"]}', {}
        if task == "memory.reflect":
            evidence = request["dynamic_context"]["materials"][0]["text"]
            return json.dumps(
                {
                    "memories": [
                        {
                            "judgment": "整理得到了新的学习体会",
                            "evidence": evidence,
                            "attribute": "事实属性",
                            "owner": "self",
                            "stable": False,
                        }
                    ]
                },
                ensure_ascii=False,
            ), {}
        if task == "memory.feedback":
            return '{"feedback":[]}', {}
        return "今天在角色生活里认真练琴；实际发出了一条问候，还没有收到回复。", {}


@pytest.fixture
async def world(tmp_path):
    world = Runtime(tmp_path / "journal.sqlite", Host())
    world.kick_memory = lambda: None
    await world.update_settings(
        {"persona_id": "可可", "sessions": [{"umo": PRIVATE}, {"umo": OTHER}]}
    )
    yield world
    await world.stop()


def memory(world, text, *, scope="global", occurred_at=None, **changes):
    return world.memory.remember(
        text,
        scope=scope,
        source="fiction",
        occurred_at=occurred_at or world.life._now().isoformat(),
        **changes,
    )


async def test_public_journal_uses_only_current_day_self_memories(world):
    own = memory(world, "练琴时忘带谱子")
    sent = memory(world, "已发出消息，还未收到回复")
    memory(world, "私人的约定", scope=PRIVATE)
    memory(world, "别人的画作", person_id="qq:42")
    memory(world, "昨日事情", occurred_at=(world.life._now() - timedelta(days=1)).isoformat())
    world.store.put("events", "raw", {"id": "raw", "text": "未提炼的聊天原文", "scope": "global"})
    entry = await world.journal.generate()
    request = next(row for row in world.host.calls if row["task"] == "journal.write")
    for wanted in ("练琴时忘带谱子", "已发出消息，还未收到回复"):
        assert wanted in request["prompt"]
    for excluded in ("私人的约定", "别人的画作", "昨日事情", "未提炼的聊天原文"):
        assert excluded not in request["prompt"]
    assert {source["id"] for source in entry["sources"]} == {own["id"], sent["id"]}
    assert [row["task"] for row in world.host.calls] == ["journal.write"]
    assert world.store.list("memory_jobs")[0]["text"] == entry["text"]


async def test_private_journal_stays_in_scope(world):
    memory(world, "私人的约定", scope=PRIVATE)
    memory(world, "别处的约定", scope=OTHER)
    entry = await world.journal.generate(scope=PRIVATE)
    request = next(row for row in world.host.calls if row["task"] == "journal.write")
    assert "私人的约定" in request["prompt"] and "别处的约定" not in request["prompt"]
    assert entry["scope"] == PRIVATE
    assert world.journal.list_entries("global") == []
    assert world.journal.list_entries(OTHER) == []
    assert world.journal.list_entries(PRIVATE) == [entry]
    assert world.store.list("memory_jobs")[0]["scope"] == PRIVATE


async def test_generation_deduplicates_day_scope_persona_across_restart(world):
    memory(world, "学习天文学")
    first = await world.journal.generate()
    second = await world.journal.generate()
    assert first == second
    assert len([row for row in world.host.calls if row["task"] == "journal.write"]) == 1
    assert len(world.store.list("memory_jobs")) == 1
    await world.update_settings({"persona_id": "另一个名字"})
    assert (await world.journal.generate())["reason"] == "no_memories"
    await world.update_settings({"persona_id": "可可"})
    assert await world.journal.generate() == first


async def test_no_memory_skips_without_model_even_when_raw_events_exist(world):
    world.store.put("events", "raw", {"id": "raw", "text": "未提炼事件"})
    result = await world.journal.generate()
    assert result == {"status": "skipped", "reason": "no_memories"}
    assert world.host.calls == []


async def test_notes_use_topic_recall_not_day_filter_or_people(world):
    own = memory(world, "三个月前学到了天文学知识", occurred_at="2026-06-01T09:00:00+08:00")
    memory(world, "其他人的天文学偏好", person_id="qq:42")
    entry = await world.journal.generate(kind="notes", topic="天文学")
    request = next(row for row in world.host.calls if row["task"] == "notes.write")
    assert "三个月前学到了天文学知识" in request["prompt"]
    assert "其他人的天文学偏好" not in request["prompt"]
    assert entry["sources"][0]["id"] == own["id"]
    assert not any(row["task"].endswith(".brief") for row in world.host.calls)


@pytest.mark.parametrize("change", ["disable", "rename", "cancel"])
async def test_configuration_change_or_cancellation_never_saves_generated_archive(world, change):
    memory(world, "练琴")
    world.host.block_task = "journal.write"
    world.host.block = asyncio.Event()
    task = asyncio.create_task(world.journal.generate())
    await world.host.entered.wait()
    if change == "disable":
        await world.update_settings({"modules": {"journal": False}})
    elif change == "rename":
        await world.update_settings({"persona_id": "新名字"})
    else:
        task.cancel()
    world.host.block.set()
    try:
        result = await task
        assert result["reason"] == "configuration_changed"
    except asyncio.CancelledError:
        pass
    assert world.store.list("journals") == []
    assert world.store.list("memory_jobs") == []


async def test_journal_timezone_boundaries(world):
    now = world.life._now()
    day = str(now.date())
    local_midnight = datetime.combine(now.date(), datetime.min.time(), ZoneInfo("Asia/Shanghai"))
    kept = memory(
        world, "本地凌晨发生", occurred_at=(local_midnight + timedelta(minutes=5)).isoformat()
    )
    memory(world, "上个本地日期", occurred_at=(local_midnight - timedelta(minutes=5)).isoformat())
    entry = await world.journal.generate(day)
    assert {row["id"] for row in entry["sources"]} == {kept["id"]}


async def test_concurrent_journal_generation_uses_one_model_call(world):
    memory(world, "练琴")
    first, second = await asyncio.gather(world.journal.generate(), world.journal.generate())
    assert first == second
    assert len([row for row in world.host.calls if row["task"] == "journal.write"]) == 1


async def test_archive_and_extraction_job_are_committed_atomically(world, monkeypatch):
    memory(world, "练琴")
    original = world.store.put

    def fail(namespace, key, value):
        if namespace == "memory_jobs":
            raise RuntimeError("disk full")
        return original(namespace, key, value)

    monkeypatch.setattr(world.store, "put", fail)
    with pytest.raises(RuntimeError, match="disk full"):
        await world.journal.generate()
    assert world.store.list("journals") == []
    assert world.store.list("memory_jobs") == []
