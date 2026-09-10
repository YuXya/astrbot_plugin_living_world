"""Journal archives and bounded, scoped model material have separate lifecycles."""

import asyncio
import copy
import json
from datetime import timedelta

import pytest

from living_world.config import settings_from
from living_world.context import is_journal_memory, record_text
from living_world.runtime import Runtime
from test_runtime import FakeHost, GROUP, PRIVATE


@pytest.fixture
async def world(tmp_path):
    runtime = Runtime(tmp_path / "brief.sqlite", FakeHost())
    await runtime.update_settings(
        {"persona_id": "student", "sessions": [{"umo": PRIVATE}, {"umo": GROUP}]}
    )
    yield runtime
    await runtime.stop()


def archive(world, key="old", scope="global", **changes):
    entry = {
        "id": key,
        "kind": "journal",
        "day": str(world.life._now().date()),
        "scope": scope,
        "text": "# 日记全文\n" + "今天发生的事情。" * 800,
        "sources": [],
        "created_at": 1,
        **changes,
    }
    world.store.put("journals", key, entry)
    return entry


def brief(world, key, text="简报关键词", **changes):
    return world.memory.remember(
        text,
        key="journal:" + key,
        source="journal:brief",
        journal_day=str(world.life._now().date()),
        **changes,
    )


async def test_generation_keeps_long_archive_and_only_brief_in_context(world):
    world.record_event("确实读到了天文学新闻", source="action")
    original = "# 日记\n" + "原文独有段落。" * 1200 + "https://example.test/original"
    world.host.answers = [
        original,
        "读到了天文学新闻，想了解恒星。\n来源：https://example.test/original",
    ]
    entry = await world.journal.generate()
    assert entry["text"] == original and entry["summary"] == "读到了天文学新闻，想了解恒星。"
    memory = world.store.get("memories", "journal:" + entry["id"])
    assert memory["text"] == entry["summary"] and memory["source"] == "journal:brief"
    bundle = await world.context_bundle(query="恒星")
    assert "日记简报" in bundle["text"] and entry["summary"] in bundle["text"]
    assert "原文独有" not in bundle["text"] and "https://" not in bundle["text"]
    assert len(world.host.calls) == 2
    assert (
        original in world.last_requests[("journal.brief", "global")]["dynamic_context"]["document"]
    )
    assert world.store.get("journals", entry["id"])["text"] == original
    assert (await world.journal.generate())["id"] == entry["id"] and len(world.host.calls) == 2
    assert world.host.sent == []


async def test_legacy_full_memories_excluded_before_limit_without_mutation(world):
    entry = archive(world)
    old = world.memory.remember(
        "角色日记（含明确标记的虚构角色经历）：" + entry["text"][:7000],
        source="journal",
        key="journal:old",
        important=True,
    )
    kept = world.memory.remember("保留的知识关键词", kind="knowledge")
    await world.update_settings({"memory": {"context_limit": 1}})
    selected = world.memory.recall()
    assert [r["id"] for r in selected] == [kept["id"]]
    assert world.store.get("memories", old["id"]) == old
    bundle = await world.context_bundle()
    assert "日记全文" not in bundle["text"]
    world.host.answers = ['{"memories":[]}']
    await world.memory.reflect("今天好", scope="global")
    assert "日记全文" not in world.host.calls[-1][1]
    world.host.answers = ["今天的一份简报"]
    result = await world.action({"action": "summarize_journal", "id": entry["id"]})
    assert result["summary"] == "今天的一份简报"
    assert world.store.get("journals", entry["id"])["text"] == entry["text"]
    assert world.memory.recall("简报")[0]["text"] == result["summary"]


async def test_total_and_journal_limits_apply_before_reinforcing_and_after_restart(world, tmp_path):
    await world.update_settings(
        {"context_usage": {"limits": {"memory.knowledge": 2, "memory.journal": 1}}}
    )
    for i in range(4):
        world.memory.remember(f"普通知识{i}", kind="knowledge")
        brief(world, str(i), f"日记概要{i}")
    rows = world.memory.recall()
    assert len(rows) == 3 and sum(is_journal_memory(r) for r in rows) == 1
    assert sum(r["access_count"] for r in world.store.list("memories")) == 3
    assert len(world.life._memories("global")) == 3
    other = Runtime(tmp_path / "brief.sqlite", FakeHost())
    try:
        assert other.settings["context_usage"]["limits"]["memory.knowledge"] == 2
        assert len(other.memory.recall()) == 3
        await other.update_settings({"context_usage": {"limits": {"memory.journal": 0}}})
        assert not any(is_journal_memory(r) for r in other.memory.recall())
        await other.update_settings({"context_usage": {"limits": {"memory.knowledge": 0}}})
        assert other.memory.recall() == other.life._memories("global") == []
    finally:
        await other.stop()


async def test_brief_length_changes_existing_projection_but_not_archive(world):
    row = brief(world, "long", "精炼事实。" * 100)
    await world.update_settings({"context_usage": {"brief_max_chars": {"memory.journal": 50}}})
    projected = world.memory.recall(reinforce=False)[0]
    assert len(projected["text"]) <= 50 and projected["text"].endswith("…")
    assert world.store.get("memories", row["id"])["text"] == row["text"]
    assert len(world.life._memories("global")[0]["text"]) <= 50
    raw = json.loads(await world.context_text())
    assert len(raw["memories"][0]["text"]) <= 50


@pytest.mark.parametrize("scope", ["global", GROUP, PRIVATE])
async def test_private_briefs_and_raw_tasks_stay_in_scope(world, scope):
    await world.update_settings({"context_usage": {"limits": {"memory.journal": 2}}})
    public = archive(world, "public", text="公开日记")
    private = archive(world, "private", PRIVATE, text="私人秘密正文")
    world.host.answers = ["私人秘密简报"]
    await world.journal.summarize(private["id"])
    world.host.answers = ["公开简报"]
    await world.journal.summarize(public["id"])
    bundle = await world.context_bundle(scope)
    assert ("私人秘密简报" in bundle["text"]) == (scope == PRIVATE)
    assert "私人秘密正文" not in bundle["text"]
    request = await world.build_test_request("journal.brief", scope)
    assert ("私人秘密正文" in request["prompt"]) == (scope == PRIVATE)
    before = copy.deepcopy(world.store.list("journals"))
    world.host.answers = ["这是试跑简报"]
    await world.test_request(request)
    assert world.store.list("journals") == before and not world.host.sent


async def test_fiction_diary_only_today_knowledge_note_can_recall_later(world):
    now = world.life._now()
    fiction = [{"source": "fiction", "fiction": True}]
    today = brief(world, "today", "今日日常简报", sources=fiction)
    row = brief(world, "old", "昨日日常简报", sources=fiction)
    row["journal_day"] = str(now.date() - timedelta(days=1))
    world.store.put("memories", row["id"], row)
    world.memory.remember("笔记知识", source="notes:brief", journal_day=row["journal_day"])
    selected = world.memory.recall(context_now=now, reinforce=False)
    assert {r["text"] for r in selected} == {today["text"], "笔记知识"}
    tomorrow = world.memory.recall(context_now=now + timedelta(days=1), reinforce=False)
    assert [r["text"] for r in tomorrow] == ["笔记知识"]
    assert "角色虚构日常" in (await world.context_bundle())["text"]
    assert "日记简报" in record_text(today, now, memory=True)


async def test_brief_failure_keeps_full_entry_and_previous_brief(world):
    world.record_event("实际事件", source="action")
    world.host.answers = ["完整日记", RuntimeError("offline")]
    failed = await world.journal.generate()
    assert failed["status"] == "partial" and failed["reason"] == "brief_failed"
    entry = world.store.get("journals", failed["id"])
    assert entry["text"] == "完整日记" and not world.store.get("memories", "journal:" + entry["id"])
    world.host.answers = ["简报成功"]
    saved = await world.journal.summarize(entry["id"])
    world.host.answers = [RuntimeError("offline again")]
    await world.journal.summarize(entry["id"], regenerate=True)
    assert world.store.get("journals", entry["id"]) == saved
    assert world.memory.recall("简报")[0]["text"] == "简报成功"


@pytest.mark.parametrize("change", ["delete", "disable", "cancel"])
async def test_changed_or_cancelled_brief_never_resurrects_archive(world, change):
    entry = archive(world)
    world.host.block = asyncio.Event()
    task = asyncio.create_task(world.journal.summarize(entry["id"]))
    await world.host.entered.wait()
    if change == "delete":
        world.journal.delete(entry["id"])
    elif change == "disable":
        await world.update_settings({"modules": {"journal": False}})
    else:
        task.cancel()
    world.host.block.set()
    try:
        await task
    except asyncio.CancelledError:
        assert change in {"cancel", "disable"}
    assert not world.store.get("memories", "journal:" + entry["id"])
    assert not (world.store.get("journals", entry["id"]) or {}).get("summary")


async def test_sqlite_brief_and_derived_memory_commit_atomically(world, monkeypatch):
    entry = archive(world)
    world.host.answers = ["简报"]
    original = world.store.put

    def fail(namespace, key, value):
        if namespace == "memories":
            raise RuntimeError("disk full")
        return original(namespace, key, value)

    monkeypatch.setattr(world.store, "put", fail)
    with pytest.raises(RuntimeError, match="disk full"):
        await world.journal.summarize(entry["id"])
    assert world.store.get("journals", entry["id"]) == entry
    assert not world.store.get("memories", "journal:" + entry["id"])


async def test_concurrent_brief_requests_only_call_once(world):
    entry = archive(world)
    world.host.answers = ["同一简报"]
    results = await asyncio.gather(
        world.journal.summarize(entry["id"]), world.journal.summarize(entry["id"])
    )
    assert results[0] == results[1] and len(world.host.calls) == 1


@pytest.mark.parametrize(
    "key,value",
    [
        ("context_limit", -1),
        ("journal_limit", 51),
        ("context_limit", 1.5),
        ("brief_max_chars", 49),
        ("brief_max_chars", 1001),
        ("journal_limit", True),
    ],
)
def test_invalid_limits_rejected(key, value):
    with pytest.raises((TypeError, ValueError)):
        settings_from({"memory": {key: value}})
