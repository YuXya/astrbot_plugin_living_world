"""Role placement, snapshot isolation and preservation of host history."""

import asyncio
import copy
import json

import pytest
from astrbot.api.provider import ProviderRequest
from astrbot.core.agent.message import TextPart, dump_messages_with_checkpoints
from test_chat import GROUP, PRIVATE, Event, consume, runner_for

from living_world.config import settings_from
from living_world.layout import (
    BLOCK_NAMES,
    DEFAULT_LAYOUT,
    DEFAULT_SETTINGS,
    TASK_NAMES,
    assemble,
    block,
    catalog,
    collect_task_blocks,
    resolve_layout,
    validate_layout,
)
from living_world.prompts import PROMPTS
from living_world.runtime import Runtime

pytest_plugins = ("test_chat",)


def moved(layout, identifier, role, before=None):
    result = copy.deepcopy(layout)
    for lane in result.values():
        lane.remove(identifier) if identifier in lane else None
    at = result[role].index(before) if before else len(result[role])
    result[role].insert(at, identifier)
    return result


def content_text(content):
    if isinstance(content, str):
        return content
    return "\n".join(part.get("text", "") for part in content or [])


def test_catalog_covers_templates_and_stable_blocks():
    assert set(PROMPTS) <= set(TASK_NAMES)
    assert set(DEFAULT_LAYOUT["system"] + DEFAULT_LAYOUT["user"]) == set(BLOCK_NAMES)
    assert all("（" in item["label"] for item in catalog()["tasks"])
    assert settings_from()["context_layout"] == DEFAULT_SETTINGS


@pytest.mark.parametrize("invalid", ["duplicate", "missing", "unknown", "role", "anchor", "array"])
def test_invalid_layout_rejected(invalid):
    value = copy.deepcopy(DEFAULT_LAYOUT)
    if invalid == "duplicate":
        value["user"].append("news")
    elif invalid == "missing":
        value["user"].remove("news")
    elif invalid == "unknown":
        value["user"].append("unknown")
    elif invalid == "role":
        value["assistant"] = []
    elif invalid == "anchor":
        value = moved(value, "anchor.user", "system")
    else:
        value["user"] = "news"
    with pytest.raises(ValueError):
        validate_layout(value)


async def test_layout_inheritance_persistence_restore_and_failed_save(world, tmp_path):
    runtime, _, _ = world
    general = moved(DEFAULT_LAYOUT, "news", "system", "anchor.system")
    special = moved(general, "memories", "user", "anchor.user")
    version = runtime.config_version
    await runtime.update_settings(
        {"context_layout": {"default": general, "tasks": {"social.message": special}}}
    )
    assert runtime.config_version == version
    assert resolve_layout(runtime.settings, "social.message") == special
    assert resolve_layout(runtime.settings, "chat.group") == general
    changed = moved(general, "news", "user")
    await runtime.update_settings({"context_layout": {"default": changed}})
    assert resolve_layout(runtime.settings, "social.message") == special
    previous = copy.deepcopy(runtime.settings)
    with pytest.raises(ValueError):
        await runtime.update_settings({"context_layout": {"tasks": {"unknown": special}}})
    assert runtime.settings == previous
    backup = runtime.export()
    await runtime.stop()
    reopened = Runtime(tmp_path / "world.sqlite", runtime.host)
    try:
        assert resolve_layout(reopened.settings, "social.message") == special
        await reopened.update_settings({"context_layout": {"tasks": {"social.message": None}}})
        assert resolve_layout(reopened.settings, "social.message") == changed
        await reopened.restore(backup)
        assert resolve_layout(reopened.settings, "social.message") == special
        assert not reopened.enabled("debug")
    finally:
        await reopened.stop()


@pytest.mark.parametrize("role", ["system", "user"])
def test_anchor_order_and_source_order_match(role):
    layout = moved(DEFAULT_LAYOUT, "news", role, "anchor." + role)
    layout = moved(layout, "search", role)
    result = assemble(
        layout, [block("news", "新闻", "FIRST"), block("search", "搜索", "LAST")], "SYSTEM", "USER"
    )
    text = result["system_prompt" if role == "system" else "prompt"]
    assert text.index("FIRST") < text.index(role.upper()) < text.index("LAST")
    sources = [item["block_id"] for item in result["sources"] if item["role"] == role]
    assert sources == ["news", "anchor." + role, "search"]


@pytest.mark.parametrize("scope", [GROUP, PRIVATE])
async def test_chat_moves_blocks_across_anchors_without_saving_dynamic_system(world, scope):
    runtime, _, provider = world
    layout = moved(DEFAULT_LAYOUT, "memories", "user", "anchor.user")
    layout = moved(layout, "news", "system", "anchor.system")
    layout = moved(layout, "group_reply", "system")
    await runtime.update_settings(
        {
            "context_layout": {"default": layout},
            "modules": {"news": True},
            "reply": {"group_prompt": "SYSTEM_GROUP_RULE"},
        }
    )
    runtime.memory.remember("MEMORY_SENTINEL mathematics", scope=scope)
    runtime.store.put(
        "observations",
        "visible",
        {"id": "visible", "scope": scope, "module": "news", "text": "NEWS_SENTINEL"},
    )
    runtime.store.put(
        "observations",
        "private",
        {
            "id": "private",
            "scope": "qq:FriendMessage:999",
            "module": "news",
            "text": "PRIVATE_SECRET",
        },
    )
    event = Event(scope, "ORIGINAL_USER mathematics")
    req = ProviderRequest(prompt=event.message_str, system_prompt="ORIGINAL_SYSTEM")
    runner = await runner_for(world, event, req)
    late = TextPart(text="LATE_PLUGIN_DATA")
    runner.run_context.messages[-1].content.append(late)
    await consume(runner)
    messages = provider.calls[0]["contexts"]
    system = content_text(next(row["content"] for row in messages if row["role"] == "system"))
    user = content_text(next(row["content"] for row in reversed(messages) if row["role"] == "user"))
    assert system.index("NEWS_SENTINEL") < system.index("ORIGINAL_SYSTEM")
    assert (
        user.index("MEMORY_SENTINEL") < user.index("ORIGINAL_USER") < user.index("LATE_PLUGIN_DATA")
    )
    assert "NEWS_SENTINEL" not in user and "PRIVATE_SECRET" not in system + user
    assert ("SYSTEM_GROUP_RULE" in system) == (scope == GROUP)
    assert "SYSTEM_GROUP_RULE" not in user
    saved = json.dumps(
        dump_messages_with_checkpoints(runner.run_context.messages), ensure_ascii=False
    )
    assert all(
        text not in saved for text in ("NEWS_SENTINEL", "MEMORY_SENTINEL", "SYSTEM_GROUP_RULE")
    )
    assert "ORIGINAL_SYSTEM" in saved and "ORIGINAL_USER" in saved and "LATE_PLUGIN_DATA" in saved
    assert req.system_prompt == "ORIGINAL_SYSTEM"
    view = runtime.debug.views()[0]
    assert next(row for row in view["sources"] if row.get("block_id") == "news")["role"] == "system"


async def test_concurrent_turns_keep_layout_and_material_snapshot(world):
    runtime, _, provider = world
    await runtime.update_settings({"character": {"profile": "FROZEN_PROFILE"}})
    first = await runner_for(world, Event(PRIVATE, "first"))
    layout = moved(DEFAULT_LAYOUT, "profile", "user", "anchor.user")
    await runtime.update_settings({"context_layout": {"default": layout}})
    second = await runner_for(world, Event(PRIVATE, "second"))
    await asyncio.gather(consume(first), consume(second))
    for call in provider.calls:
        user = content_text(
            next(row["content"] for row in reversed(call["contexts"]) if row["role"] == "user")
        )
        system = content_text(
            next(row["content"] for row in call["contexts"] if row["role"] == "system")
        )
        if "first" in user:
            assert "FROZEN_PROFILE" in system and "FROZEN_PROFILE" not in user
        else:
            assert user.index("FROZEN_PROFILE") < user.index("second")
            assert "FROZEN_PROFILE" not in system


@pytest.mark.parametrize("task", list(PROMPTS))
async def test_all_background_tasks_share_layout_and_frozen_trial_composition(world, task):
    runtime, _, _ = world
    layout = moved(DEFAULT_LAYOUT, "memories", "system", "anchor.system")
    layout = moved(layout, "task.document", "system")
    layout = moved(layout, "task.material", "user", "anchor.user")
    await runtime.update_settings({"context_layout": {"default": layout}})
    data = {"memories": ["MEMORY_INPUT"], "material": "TASK_INPUT", "document": "FULL_DOCUMENT"}
    request = await runtime.prepare_request(task, task.split(".")[0], "TASK_TEMPLATE", data)
    assert request["system_prompt"].index("MEMORY_INPUT") < request["system_prompt"].index(
        request["base_system_prompt"]
    )
    assert "FULL_DOCUMENT" in request["system_prompt"] and "FULL_DOCUMENT" not in request["prompt"]
    assert request["prompt"].index("TASK_INPUT") < request["prompt"].index("TASK_TEMPLATE")
    await runtime.update_settings({"context_layout": {"default": DEFAULT_LAYOUT}})
    before = runtime.store.export()
    trial = await runtime.prepare_trial_request(request)
    assert (
        trial["prompt"] == request["prompt"] and trial["system_prompt"] == request["system_prompt"]
    )
    assert runtime.store.export() == before
    raw = await runtime.prepare_trial_request(
        {**request, "prompt_mode": "raw", "prompt": "RAW_USER", "system_prompt": "RAW_SYSTEM"}
    )
    assert raw["prompt"] == "RAW_USER" and raw["system_prompt"] == "RAW_SYSTEM"


def test_source_modules_group_separately_without_splitting_evidence():
    snapshot = {
        "current_time": "2026-09-10T12:00:00+08:00",
        "memories": [],
        "schedule": {"status": "missing"},
        "observations": [
            {"module": module, "text": module.upper()}
            for module in ("news", "search", "bilibili", "daily_digest", "weather")
        ],
    }
    blocks = collect_task_blocks(
        {"context": json.dumps(snapshot), "external_data": {"raw": ["EVIDENCE"]}}
    )
    for identifier in ("news", "search", "bilibili", "daily_digest", "weather"):
        assert len([row for row in blocks if row["block_id"] == identifier]) == 1
    evidence = next(row for row in blocks if row["block_id"] == "task.evidence")
    assert json.loads(evidence["content"]) == {"raw": ["EVIDENCE"]}
    rendered = assemble(DEFAULT_LAYOUT, blocks)
    assert rendered["prompt"].count("EVIDENCE") == 1
    for field in ("document", "external_data", "material"):
        original = json.dumps(snapshot)
        items = collect_task_blocks({field: original})
        assert len(items) == 1 and items[0]["content"] == original


@pytest.mark.parametrize("change", ["parts", "altered", "history"])
async def test_late_host_changes_do_not_duplicate_or_archive_temporary_system(world, change):
    runtime, _, provider = world
    layout = moved(DEFAULT_LAYOUT, "group_reply", "system", "anchor.system")
    await runtime.update_settings(
        {
            "context_layout": {"default": layout},
            "character": {"profile": "PRIVATE_PROFILE"},
            "reply": {"group_prompt": "PRIVATE_GUIDANCE"},
        }
    )
    event = Event(GROUP)
    req = ProviderRequest(
        prompt="new",
        system_prompt="HOST_SYSTEM",
        contexts=[{"role": "user", "content": "host history"}],
    )
    runner = await runner_for(world, event, req)
    system = runner.run_context.messages[0]
    if change == "parts":
        system.content = [TextPart(text=system.content), TextPart(text="LATE_PLUGIN_SYSTEM")]
    elif change == "altered":
        system.content = (
            event.get_extra("living_world_trace")["layout_system_initial"].replace(
                "PRIVATE_GUIDANCE", "CHANGED_GUIDANCE"
            )
            + " LATE_PLUGIN_SYSTEM"
        )
        req.system_prompt = system.content
    else:
        runner.run_context.messages[1].content = "LATE_PLUGIN_HISTORY"
    await consume(runner)
    actual = json.dumps(provider.calls, ensure_ascii=False)
    saved = json.dumps(
        dump_messages_with_checkpoints(runner.run_context.messages), ensure_ascii=False
    )
    assert all(
        secret not in saved
        for secret in ("PRIVATE_PROFILE", "PRIVATE_GUIDANCE", "CHANGED_GUIDANCE")
    )
    assert req.system_prompt == "HOST_SYSTEM"
    if change == "history":
        assert "LATE_PLUGIN_HISTORY" in actual and "PRIVATE_PROFILE" not in actual
    else:
        assert "LATE_PLUGIN_SYSTEM" in actual
    if change == "parts":
        assert actual.count("PRIVATE_PROFILE") == actual.count("PRIVATE_GUIDANCE") == 1
        assert "HOST_SYSTEM" in saved and "LATE_PLUGIN_SYSTEM" in saved


async def test_disabled_modules_and_empty_blocks_remain_absent_after_role_moves(world):
    runtime, _, provider = world
    layout = moved(DEFAULT_LAYOUT, "news", "system")
    layout = moved(layout, "memories", "system")
    await runtime.update_settings(
        {
            "context_layout": {"default": layout},
            "modules": {"news": False, "memory": False, "life": False, "state": False},
            "character": {"profile": "", "world": ""},
        }
    )
    runtime.memory.remember("HIDDEN_MEMORY mathematics", scope=PRIVATE)
    runtime.store.put(
        "observations", "news", {"module": "news", "scope": PRIVATE, "text": "HIDDEN_NEWS"}
    )
    runner = await runner_for(world, Event(PRIVATE, "mathematics"))
    await consume(runner)
    actual = json.dumps(provider.calls, ensure_ascii=False)
    assert "HIDDEN_MEMORY" not in actual and "HIDDEN_NEWS" not in actual
    assert "【角色补充资料】" not in actual and "【世界设定】" not in actual


async def test_composed_trial_calls_only_model_with_frozen_layout(world):
    runtime, manager, provider = world
    layout = moved(DEFAULT_LAYOUT, "task.document", "system", "anchor.system")
    await runtime.update_settings({"context_layout": {"default": layout}})
    draft = await runtime.prepare_request(
        "journal.brief", "journal", "MAKE_BRIEF", {"document": "DOCUMENT_SENTINEL"}, PRIVATE
    )
    await runtime.update_settings({"context_layout": {"default": DEFAULT_LAYOUT}})

    async def generate(chat_provider_id, **kwargs):
        return await provider.text_chat(**kwargs)

    runtime.host.context.llm_generate = generate
    before = runtime.store.export()
    result = await runtime.test_request(draft)
    assert result["test_only"] and len(provider.calls) == 1
    assert "DOCUMENT_SENTINEL" in provider.calls[0]["system_prompt"]
    assert "DOCUMENT_SENTINEL" not in provider.calls[0]["prompt"]
    after = runtime.store.export()
    before = [row for row in before if row["namespace"] != "debug_records"]
    after = [row for row in after if row["namespace"] != "debug_records"]
    assert after == before and not manager.rows
    runtime.host.context.send_message.assert_not_called()


@pytest.mark.parametrize("begin", [False, True])
@pytest.mark.parametrize("scope", [GROUP, PRIVATE])
async def test_unload_never_leaves_temporary_system_in_history(world, begin, scope):
    runtime, _, _ = world
    layout = moved(DEFAULT_LAYOUT, "speaker", "system")
    await runtime.update_settings(
        {"context_layout": {"default": layout}, "character": {"profile": "PRIVATE_PROFILE"}}
    )
    event = Event(scope)
    req = ProviderRequest(
        prompt="HOST_USER",
        system_prompt="HOST_SYSTEM",
        contexts=[{"role": "user", "content": "HOST_HISTORY"}],
    )
    runner = await runner_for(world, event, req)
    assert "PRIVATE_PROFILE" not in req.system_prompt
    if begin:
        runtime.chat.agent_begin(event, runner.run_context)
    runtime.chat.close()
    saved = json.dumps(
        dump_messages_with_checkpoints(runner.run_context.messages), ensure_ascii=False
    )
    assert "PRIVATE_PROFILE" not in saved
    assert all(text in saved for text in ("HOST_SYSTEM", "HOST_HISTORY", "HOST_USER"))
    assert req.system_prompt == "HOST_SYSTEM"
