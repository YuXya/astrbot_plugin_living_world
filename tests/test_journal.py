import copy
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta, timezone

import pytest

from living_world.journal import JournalService

NOW = datetime(2026, 9, 5, 23, tzinfo=timezone(timedelta(hours=8)))


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

    @contextmanager
    def transaction(self):
        before = copy.deepcopy(self.data)
        try:
            yield
        except BaseException:
            self.data = before
            raise


class Memory:
    def __init__(self):
        self.entries = []
        self.writes = []
        self.deletes = []

    def recall(self, **kwargs):
        return copy.deepcopy(self.entries)

    def remember(self, text, **kwargs):
        result = {"text": text, **kwargs}
        self.writes.append(result)
        return result

    def delete(self, key):
        self.deletes.append(key)


class Runtime:
    def __init__(self):
        self.store = Store()
        self.memory = Memory()
        self.settings = {"character": {"timezone": "Asia/Shanghai"}}
        self.disabled = set()
        self.calls = []

    def enabled(self, name):
        return name not in self.disabled

    async def generate(self, module, prompt, scope="global"):
        self.calls.append((module, prompt, scope))
        return "今天在角色生活里忘了带笔；实际发出了一条问候，还没有回复。"


def event(runtime, key, text, scope="global", source="action", **changes):
    row = {
        "id": key,
        "text": text,
        "scope": scope,
        "source": source,
        "kind": "event",
        "created_at": NOW.timestamp(),
    }
    row.update(changes)
    runtime.store.put("events", key, row)


@pytest.mark.asyncio
async def test_public_journal_cannot_use_private_evidence_or_future_plans():
    runtime = Runtime()
    event(runtime, "fiction", "忘带笔", source="fiction")
    event(runtime, "sent", "消息已发出，未收到回复")
    event(runtime, "private", "私人谈话秘密", scope="private-a")
    event(runtime, "tomorrow", "明天计划看视频", kind="plan")
    event(runtime, "failed", "搜索失败不能说已看过", status="failed")
    event(
        runtime,
        "yesterday",
        "昨天的事情",
        created_at=(NOW - timedelta(days=1)).timestamp(),
    )
    runtime.memory.entries = [
        {"scope": "private-a", "kind": "knowledge", "text": "私人的知识"},
        {"scope": "global", "kind": "event", "text": "明天计划登月"},
    ]
    service = JournalService(runtime)
    entry = await service.generate("2026-09-05")
    prompt = runtime.calls[0][1]
    assert "忘带笔" in prompt and "消息已发出，未收到回复" in prompt
    for excluded in (
        "私人谈话秘密",
        "明天计划看视频",
        "搜索失败不能说已看过",
        "昨天的事情",
        "私人的知识",
        "明天计划登月",
    ):
        assert excluded not in prompt
    assert {source["id"] for source in entry["sources"]} == {"fiction", "sent"}
    assert runtime.memory.writes[0]["scope"] == "global"
    assert runtime.memory.writes[0]["source"] == "journal:brief"
    assert any(s["fiction"] for s in runtime.memory.writes[0]["sources"])


@pytest.mark.asyncio
async def test_private_journal_keeps_scope_and_is_not_publicly_listed():
    runtime = Runtime()
    event(runtime, "private", "私人约定今天已兑现", scope="private-a")
    event(runtime, "another", "别处内容", scope="private-b")
    service = JournalService(runtime)
    entry = await service.generate("2026-09-05", scope="private-a")
    assert entry["scope"] == runtime.memory.writes[0]["scope"] == "private-a"
    assert "私人约定今天已兑现" in runtime.calls[0][1]
    assert "别处内容" not in runtime.calls[0][1]
    assert service.list_entries("global") == []
    assert service.list_entries("private-b") == []
    assert service.list_entries("private-a") == [entry]


@pytest.mark.asyncio
async def test_generation_is_deduplicated_by_kind_day_scope_across_restart():
    runtime = Runtime()
    event(runtime, "real", "读到了一篇新闻", source="https://example.test/news")
    first = await JournalService(runtime).generate("2026-09-05")
    second = await JournalService(runtime).generate("2026-09-05")
    assert first == second
    assert len(runtime.calls) == 2 and len(runtime.memory.writes) == 1
    note = await JournalService(runtime).generate("2026-09-05", kind="notes")
    assert note["id"] != first["id"]
    assert note["sources"][0]["source"] == "https://example.test/news"


@pytest.mark.asyncio
async def test_notes_exclude_fiction_and_no_evidence_does_not_generate():
    runtime = Runtime()
    event(runtime, "fiction", "在梦里看到新闻", source="fiction")
    result = await JournalService(runtime).generate("2026-09-05", kind="notes")
    assert result == {"status": "skipped", "reason": "no_events"}
    assert runtime.calls == []


@pytest.mark.asyncio
async def test_disable_during_generation_prevents_journal_and_memory_writes():
    runtime = Runtime()
    event(runtime, "real", "已发送问候")

    async def disable(*args, **kwargs):
        runtime.disabled.add("journal")
        return "今天的日记"

    runtime.generate = disable
    assert (await JournalService(runtime).generate("2026-09-05"))["reason"] == "module_disabled"
    assert runtime.store.list("journals") == runtime.memory.writes == []


@pytest.mark.asyncio
async def test_delete_removes_derived_memory_through_public_service():
    runtime = Runtime()
    event(runtime, "real", "已发送问候")
    service = JournalService(runtime)
    entry = await service.generate("2026-09-05")
    service.delete(entry["id"])
    assert service.list_entries() == []
    assert runtime.memory.deletes == [f"journal:{entry['id']}"]


@pytest.mark.asyncio
async def test_civil_day_uses_configured_timezone_at_midnight():
    runtime = Runtime()
    event(
        runtime,
        "utc",
        "本地凌晨发生",
        created_at=datetime(2026, 9, 4, 16, 5, tzinfo=UTC).timestamp(),
    )
    entry = await JournalService(runtime).generate("2026-09-05")
    assert entry["sources"][0]["id"] == "utc"
