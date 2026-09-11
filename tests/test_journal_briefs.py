"""Full archives feed unified extraction; historical briefs are never reactivated."""

import asyncio
import json

import pytest

from test_journal import memory, world as world
from living_world.prompts import PROMPTS


def archive(world, *, key="old", text="已经存档的日记全文", scope="global"):
    entry = {
        "id": key,
        "kind": "journal",
        "day": str(world.life._now().date()),
        "scope": scope,
        "text": text,
        "sources": [],
        "created_at": 1700000000,
        "persona_name": "可可",
    }
    world.store.put("journals", key, entry)
    return entry


async def test_long_archive_is_preserved_and_never_injected_as_memory(world):
    memory(world, "实际读到了天文学知识")
    original = "完整原件。" * 4000
    world.host.responses["journal.write"] = original
    entry = await world.journal.generate()
    assert entry["text"] == original
    assert "summary" not in entry
    bundle = await world.context_bundle(selection=["memory", "memory.recent"])
    assert "完整原件" not in bundle["text"]
    assert not any(row["task"].endswith(".brief") for row in world.host.calls)
    assert world.store.get("journals", entry["id"])["text"] == original


async def test_legacy_summarize_action_only_enqueues_once_and_preserves_archive(world):
    entry = archive(world)
    first, second = await asyncio.gather(
        world.journal.summarize(entry["id"]), world.journal.summarize(entry["id"], regenerate=True)
    )
    assert first["id"] == second["id"]
    assert world.host.calls == []
    assert len(world.store.list("memory_jobs")) == 1
    await world.memory.process_pending()
    assert world.store.get("journals", entry["id"]) == entry
    assert len(world.store.list("memories")) == 1
    assert [row["task"] for row in world.host.calls] == ["memory.reflect"]


async def test_extraction_failure_preserves_archive_previous_memory_and_pending_material(world):
    entry = archive(world)
    previous = memory(world, "以前已经提炼的结论")
    await world.journal.summarize(entry["id"])
    world.host.responses["memory.reflect"] = RuntimeError("offline")
    result = await world.memory.process_pending()
    assert result["failed"] == 1
    assert world.store.get("journals", entry["id"]) == entry
    assert world.store.get("memories", previous["id"]) == previous
    assert world.store.list("memory_jobs")[0]["text"] == entry["text"]


@pytest.mark.parametrize("change", ["delete", "disable", "cancel"])
async def test_inflight_extraction_cannot_resurrect_deleted_or_disabled_archive(world, change):
    entry = archive(world)
    await world.journal.summarize(entry["id"])
    world.host.block_task = "memory.reflect"
    world.host.block = asyncio.Event()
    task = asyncio.create_task(world.memory.process_pending())
    await world.host.entered.wait()
    if change == "delete":
        world.journal.delete(entry["id"])
    elif change == "disable":
        await world.update_settings({"modules": {"journal": False}})
    else:
        task.cancel()
    world.host.block.set()
    if change == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        await task
    assert world.store.list("memories") == []
    if change == "delete":
        assert world.store.get("journals", entry["id"]) is None
        assert world.store.list("memory_jobs") == []
    else:
        assert world.store.get("journals", entry["id"]) == entry
        assert len(world.store.list("memory_jobs")) == 1




async def test_extraction_transaction_rolls_back_memory_if_completion_marker_fails(
    world, monkeypatch
):
    entry = archive(world)
    await world.journal.summarize(entry["id"])
    original = world.store.put

    def fail(namespace, key, value):
        if namespace == "memory_materials":
            raise RuntimeError("disk full")
        return original(namespace, key, value)

    monkeypatch.setattr(world.store, "put", fail)
    result = await world.memory.process_pending()
    assert result["failed"] == 1
    assert world.store.get("journals", entry["id"]) == entry
    assert world.store.list("memories") == []
    assert len(world.store.list("memory_jobs")) == 1


async def test_trial_memory_extraction_does_not_modify_archives_or_memories(world):
    entry = archive(world)
    before = world.store.export()
    request = await world.prepare_request(
        "memory.reflect",
        "memory",
        PROMPTS["memory.reflect"],
        {"material": entry["text"], "materials": [{"text": entry["text"]}]},
    )
    world.host.responses["memory.reflect"] = json.dumps(
        {"memories": [{"judgment": "试跑结果", "evidence": entry["text"]}]}, ensure_ascii=False
    )
    await world.test_request(request)
    ignored = {"llm_calls", "test_drafts", "test_requests", "debug_records", "model_test_drafts"}
    assert [row for row in world.store.export() if row["namespace"] not in ignored] == [
        row for row in before if row["namespace"] not in ignored
    ]
