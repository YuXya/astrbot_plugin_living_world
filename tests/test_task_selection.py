"""Global layout, per-task material selection and frozen version-four requests."""

import copy
import json
from unittest.mock import AsyncMock

import pytest
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.core.agent.message import dump_messages_with_checkpoints
from astrbot.core.agent.tool import FunctionTool, ToolSet
from mcp.types import CallToolResult, TextContent
from test_chat import GROUP, PRIVATE, Event, consume, runner_for
from test_layout import moved

from living_world.config import DEFAULT_GROUP_REPLY_PROMPT, settings_from
from living_world.layout import (
    BLOCK_NAMES,
    DEFAULT_LAYOUT,
    DEFAULT_SELECTIONS,
    TASK_NAMES,
    V2_LAYOUT,
    assemble,
    block,
    catalog,
    resolve_layout,
    resolve_selection,
    validate_selection,
)
from living_world.runtime import Runtime
from living_world.store import Store

pytest_plugins = ("test_chat",)


def business_state(runtime):
    return [row for row in runtime.store.export() if row["namespace"] != "debug_records"]


def source_ids(request):
    return {
        row["block_id"] for row in request["sources"] if not row["block_id"].startswith("anchor.")
    }


def request_text(request):
    return request["system_prompt"] + "\n" + request["prompt"]


def all_choices():
    return [
        key for key in BLOCK_NAMES if not key.startswith("anchor.") and key != "schedule.recent"
    ]


@pytest.mark.parametrize("task", list(TASK_NAMES))
def test_every_task_accepts_the_entire_catalog_with_one_shared_order(task):
    settings = settings_from()
    choices = all_choices()
    settings["context_layout"]["tasks"][task] = validate_selection(choices)
    settings["context_layout"]["order"] = moved(
        DEFAULT_LAYOUT, "task.thoughts", "system", "anchor.system"
    )
    assert set(resolve_selection(settings, task)) == set(choices)
    order = resolve_layout(settings, task)
    assert all(resolve_layout(settings, other) == order for other in TASK_NAMES)
    entry = next(row for row in catalog()["tasks"] if row["id"] == task)
    assert entry["blocks"] == list(BLOCK_NAMES)
    blocks = [block(key, key, f"SELECTED:{key}") for key in choices]
    request = assemble(order, blocks, "SYSTEM", "USER", selection=choices)
    assert source_ids(request) == set(choices)
    assert request["system_prompt"].index("SELECTED:task.thoughts") < request[
        "system_prompt"
    ].index("SYSTEM")


@pytest.mark.parametrize("selection", [[], ["schedule"], ["schedule.recent"]])
def test_schedule_variants_can_be_selected_individually_or_both_omitted(selection):
    rows = [
        block("schedule", "完整", "FULL_SCHEDULE"),
        block("schedule.recent", "简版", "RECENT_SCHEDULE"),
    ]
    result = assemble(DEFAULT_LAYOUT, rows, selection=selection)
    assert source_ids(result) == set(selection)
    assert ("FULL_SCHEDULE" in result["prompt"]) == ("schedule" in selection)
    assert ("RECENT_SCHEDULE" in result["prompt"]) == ("schedule.recent" in selection)


@pytest.mark.parametrize(
    "selection", [["schedule", "schedule.recent"], ["anchor.system"], ["time", "time"], ["unknown"]]
)
def test_invalid_task_choices_are_rejected(selection):
    with pytest.raises(ValueError):
        validate_selection(selection)


@pytest.mark.parametrize("task", ["memory.reflect", "life.plan", "social.interject"])
async def test_arbitrary_task_material_stays_absent_without_explicit_input(world, task):
    runtime, manager, provider = world
    selected = [
        "task.material",
        "task.evidence",
        "task.candidates",
        "task.activity",
        "task.instruction",
    ]
    await runtime.update_settings({"context_layout": {"tasks": {task: selected}}})
    before = business_state(runtime)
    request = await runtime.prepare_request(task, "life", "ORIGINAL_TASK", {}, PRIVATE)
    assert source_ids(request) == set()
    assert request["prompt"] == "ORIGINAL_TASK"
    assert business_state(runtime) == before
    assert not provider.calls and not manager.rows
    runtime.host.context.send_message.assert_not_called()


async def test_background_task_can_read_selected_shared_context_without_running_sources(world):
    runtime, manager, provider = world
    selected = ["time", "memory", "memory.recent", "task.thoughts", "private_reply"]
    await runtime.update_settings(
        {
            "context_layout": {"tasks": {"memory.reflect": selected}},
            "reply": {"private_prompt": "PRIVATE_REQUIREMENT"},
            "modules": {"news": True},
        }
    )
    runtime.memory.remember("PUBLIC_KNOWLEDGE", stable=True, occurred_at="")
    runtime.memory.remember("PRIVATE_KNOWLEDGE", stable=True, scope=PRIVATE, occurred_at="")
    runtime.memory.remember("OTHER_SCOPE_KNOWLEDGE", kind="knowledge", scope="qq:FriendMessage:99")
    runtime.memory.remember(
        "ALREADY_READ_NEWS", source="news", occurred_at=runtime.life._now().isoformat()
    )
    runtime.host.call_tool = AsyncMock(side_effect=AssertionError("Unexpected source execution"))
    before = business_state(runtime)
    request = await runtime.prepare_request("memory.reflect", "journal", "BRIEF", {}, PRIVATE)
    assert source_ids(request) == set(selected)
    text = request_text(request)
    for expected in (
        "PUBLIC_KNOWLEDGE",
        "PRIVATE_KNOWLEDGE",
        "ALREADY_READ_NEWS",
        "PRIVATE_REQUIREMENT",
    ):
        assert expected in text
    assert "OTHER_SCOPE_KNOWLEDGE" not in text
    assert business_state(runtime) == before
    assert not provider.calls and not manager.rows
    runtime.host.call_tool.assert_not_called()
    await runtime.update_settings({"modules": {"news": False, "memory": False, "drives": False}})
    disabled = await runtime.prepare_request("memory.reflect", "journal", "BRIEF", {}, PRIVATE)
    assert source_ids(disabled) == {"time", "private_reply"}


@pytest.mark.parametrize("old", ["", "  \n", "用户原有文案"])
def test_reply_migration_copies_group_text_once_including_blank(old):
    migrated = settings_from({"reply": {"group_prompt": old}})
    assert migrated["reply"] == dict.fromkeys(
        ("group_prompt", "private_prompt", "proactive_prompt"), old
    )
    migrated["reply"]["group_prompt"] = "群聊已修改"
    migrated["reply"]["private_prompt"] = "私聊已修改"
    repeated = settings_from(migrated)
    assert repeated["reply"] == {
        "group_prompt": "群聊已修改",
        "private_prompt": "私聊已修改",
        "proactive_prompt": old,
    }
    assert settings_from()["reply"]["private_prompt"] == DEFAULT_GROUP_REPLY_PROMPT


@pytest.mark.parametrize("field", ["group_prompt", "private_prompt", "proactive_prompt"])
@pytest.mark.parametrize("invalid", [None, 1, {}, "x" * 8001])
def test_all_reply_fields_enforce_text_and_eight_thousand_character_limit(field, invalid):
    with pytest.raises((TypeError, ValueError)):
        settings_from({"reply": {field: invalid}})
    assert settings_from({"reply": {field: "x" * 8000}})["reply"][field] == "x" * 8000


@pytest.mark.parametrize(
    "task,expected",
    [
        ("chat.group", "group_reply"),
        ("chat.private", "private_reply"),
        ("social.message", "proactive_reply"),
        ("social.interject", None),
    ],
)
def test_reply_defaults_are_distinct_and_interjection_decision_has_none(task, expected):
    selected = set(DEFAULT_SELECTIONS[task]) & {"group_reply", "private_reply", "proactive_reply"}
    assert selected == ({expected} if expected else set())


async def test_custom_reply_requirements_remain_independent_instruction_blocks(world):
    runtime, _, _ = world
    order = moved(DEFAULT_LAYOUT, "private_reply", "system", "anchor.system")
    await runtime.update_settings(
        {
            "reply": {
                "group_prompt": "GROUP_RULE",
                "private_prompt": "PRIVATE_RULE",
                "proactive_prompt": "PROACTIVE_RULE",
            },
            "context_layout": {
                "order": order,
                "tasks": {"life.plan": ["group_reply", "private_reply", "proactive_reply"]},
            },
        }
    )
    result = await runtime.prepare_request("life.plan", "life", "TASK", {}, PRIVATE)
    assert source_ids(result) == {"group_reply", "private_reply", "proactive_reply"}
    assert "PRIVATE_RULE" in result["system_prompt"] and "PRIVATE_RULE" not in result["prompt"]
    assert "GROUP_RULE" in result["prompt"] and "PROACTIVE_RULE" in result["prompt"]
    assert "<living_world_context>" not in request_text(result)
    await runtime.update_settings(
        {"reply": {"group_prompt": "", "private_prompt": "CHANGED_PRIVATE"}}
    )
    result = await runtime.prepare_request("life.plan", "life", "TASK", {}, PRIVATE)
    assert source_ids(result) == {"private_reply", "proactive_reply"}
    assert "CHANGED_PRIVATE" in result["system_prompt"] and "PROACTIVE_RULE" in result["prompt"]


async def test_v2_migration_keeps_global_baseline_and_archives_task_orders_idempotently(
    world, tmp_path
):
    runtime, _, _ = world
    old_settings = copy.deepcopy(runtime.settings)
    old_order = moved(V2_LAYOUT, "observations", "system", "anchor.system")
    independent = moved(V2_LAYOUT, "memory.event", "system")
    old_settings["context_layout"] = {
        "version": 2,
        "default": old_order,
        "tasks": {"life.plan": independent, "chat.private": None},
    }
    old_settings["reply"] = {"group_prompt": ""}
    path = tmp_path / "migration.sqlite"
    store = Store(path)
    store.put("settings", "current", old_settings)
    store.close()
    migrated = Runtime(path, runtime.host)
    try:
        converted = copy.deepcopy(migrated.settings["context_layout"])
        assert converted["version"] == 4 and converted["order"] == converted["baseline_order"]
        assert converted["order"]["system"] == [
            key for key in old_order["system"] if key != "observations"
        ]
        assert (
            converted["order"]["user"].index("schedule.recent")
            == converted["order"]["user"].index("schedule") + 1
        )
        assert (
            converted["order"]["user"].index("private_reply")
            == converted["order"]["user"].index("group_reply") + 1
        )
        assert converted["tasks"] == DEFAULT_SELECTIONS
        history = migrated.store.list("context_settings_history")
        assert len(history) == 1 and history[0]["settings"] == old_settings
        backup = migrated.export()
        await migrated.stop()
        migrated = Runtime(path, runtime.host)
        assert migrated.settings["context_layout"] == converted
        assert len(migrated.store.list("context_settings_history")) == 1
        changed_order = moved(converted["order"], "weather", "system")
        await migrated.update_settings({"context_layout": {"order": changed_order}})
        assert migrated.settings["context_layout"]["baseline_order"] == converted["baseline_order"]
        await migrated.restore(backup)
        assert migrated.settings["context_layout"] == converted
        legacy_backup = {**backup, "settings": old_settings}
        for _ in range(2):
            await migrated.restore(legacy_backup)
            assert migrated.settings["context_layout"] == converted
            assert len(migrated.store.list("context_settings_history")) == 1
            assert migrated.settings["reply"] == dict.fromkeys(
                ("group_prompt", "private_prompt", "proactive_prompt"), ""
            )
    finally:
        await migrated.stop()


async def test_preparing_and_calling_do_not_strengthen_without_usefulness_feedback(
    world,
):
    runtime, manager, provider = world
    await runtime.update_settings(
        {
            "context_layout": {"tasks": {"life.plan": ["memory"]}},
            "context_usage": {"limits": {"memory.related": 0, "memory.self": 1}},
        }
    )
    selected = runtime.memory.remember(
        "CHOSEN_KNOWLEDGE", stable=True, scope=PRIVATE, source_event_id="same"
    )
    excluded = runtime.memory.remember(
        "UNSELECTED_EMOTION", kind="emotional", scope=PRIVATE, source_event_id="same"
    )
    before = business_state(runtime)
    request = await runtime.prepare_request("life.plan", "life", "TASK", {}, PRIVATE)
    assert source_ids(request) == {"memory"}
    assert "CHOSEN_KNOWLEDGE" in request_text(request) and "UNSELECTED_EMOTION" not in request_text(
        request
    )
    assert business_state(runtime) == before

    async def generate(chat_provider_id, **kwargs):
        return await provider.text_chat(**kwargs)

    runtime.host.context.llm_generate = generate
    assert not manager.rows
    runtime.host.context.send_message.assert_not_called()
    await runtime._model_call(request)
    assert runtime.store.get("memories", selected["id"])["access_count"] == 0
    assert runtime.store.get("memories", excluded["id"])["access_count"] == 0


@pytest.mark.parametrize(
    "scope,task,reply_field,reply_id",
    [
        (GROUP, "chat.group", "group_prompt", "group_reply"),
        (PRIVATE, "chat.private", "private_prompt", "private_reply"),
    ],
)
async def test_tool_followup_freezes_selection_layout_memory_and_reply_without_archiving_them(
    world, scope, task, reply_field, reply_id
):
    runtime, _, provider = world
    await runtime.update_settings(
        {
            "reply": {reply_field: "FROZEN_REQUIREMENT"},
            "context_layout": {"tasks": {task: ["memory", reply_id]}},
        }
    )
    memory = runtime.memory.remember("数学 FROZEN_KNOWLEDGE", kind="knowledge", scope=scope)
    provider.answers = [
        LLMResponse(
            role="assistant",
            tools_call_name=["lookup"],
            tools_call_args=[{}],
            tools_call_ids=["lookup-1"],
        ),
        LLMResponse(role="assistant", completion_text="查到了"),
    ]

    class Executor:
        async def execute(self, **kwargs):
            await runtime.update_settings(
                {
                    "reply": {reply_field: "UPDATED_REQUIREMENT"},
                    "context_layout": {
                        "order": moved(DEFAULT_LAYOUT, reply_id, "system"),
                        "tasks": {task: [reply_id]},
                    },
                }
            )
            runtime.memory.update(memory["id"], {"text": "数学 UPDATED_KNOWLEDGE"})
            yield CallToolResult(content=[TextContent(type="text", text="TOOL_RESULT")])

    tool = FunctionTool(
        name="lookup", description="Read fixture", parameters={"type": "object", "properties": {}}
    )
    event = Event(scope, "查数学")
    runner = await runner_for(
        world,
        event,
        ProviderRequest(prompt=event.message_str, func_tool=ToolSet([tool])),
        Executor(),
    )
    await consume(runner)
    assert len(provider.calls) == 2
    for call in provider.calls:
        actual = json.dumps(call, ensure_ascii=False)
        assert "FROZEN_REQUIREMENT" in actual and "FROZEN_KNOWLEDGE" in actual
        assert "UPDATED_REQUIREMENT" not in actual and "UPDATED_KNOWLEDGE" not in actual
        system = json.dumps(
            [row for row in call["contexts"] if row["role"] == "system"], ensure_ascii=False
        )
        assert "FROZEN_REQUIREMENT" not in system
    saved = json.dumps(
        dump_messages_with_checkpoints(runner.run_context.messages), ensure_ascii=False
    )
    assert "FROZEN_REQUIREMENT" not in saved and "FROZEN_KNOWLEDGE" not in saved
    assert "TOOL_RESULT" in saved
    assert runtime.store.get("memories", memory["id"])["access_count"] == 0
    await consume(await runner_for(world, Event(scope, "再查数学")))
    fresh = json.dumps(provider.calls[-1], ensure_ascii=False)
    assert "UPDATED_REQUIREMENT" in fresh
    assert "FROZEN_KNOWLEDGE" not in fresh and "UPDATED_KNOWLEDGE" not in fresh


@pytest.mark.parametrize("task", ["news.select", "search.topic", "news.reflect"])
async def test_source_preparation_filters_recall_without_reinforcing_or_running_sources(
    world, task
):
    runtime, _, provider = world
    runtime.memory._complete = AsyncMock(return_value='{"keywords": []}')
    await runtime.update_settings({"context_layout": {"tasks": {task: ["memory"]}}})
    selected = runtime.memory.remember(
        "QUERY SELECTED", kind="knowledge", scope=PRIVATE, source_event_id="shared-source"
    )
    excluded = runtime.memory.remember(
        "QUERY_EXCLUDED",
        kind="emotional",
        scope="qq:FriendMessage:99",
        source_event_id="shared-source",
        important=True,
    )
    before = business_state(runtime)
    raw = await runtime.sources._context(PRIVATE, "QUERY", task)
    assert [row["id"] for row in json.loads(raw)["memories"]] == [selected["id"]]
    assert business_state(runtime) == before and not provider.calls
    runtime.memory._complete.assert_awaited_once()
    assert runtime.memory._complete.await_args.args[0] == "memory.query"
    assert runtime.store.get("memories", excluded["id"])["access_count"] == 0
    await runtime.update_settings({"context_layout": {"tasks": {task: []}}})
    raw = await runtime.sources._context(PRIVATE, "QUERY", task)
    assert json.loads(raw)["memories"] == []
    assert runtime.store.get("memories", selected["id"])["access_count"] == 0


async def test_explicit_plan_memories_deduplicate_selected_recent_memory(world):
    runtime, _, _ = world
    runtime.memory._complete = AsyncMock(return_value='{"keywords": []}')
    await runtime.update_settings(
        {"context_layout": {"tasks": {"life.plan": ["memory", "memory.recent"]}}}
    )
    now = runtime.life._now()
    saved_memory = runtime.memory.remember(
        "SAME_FICTION_EVENT", source="fiction", key="same-fiction", occurred_at=now.isoformat()
    )
    draft = runtime.life.plan_request(now)
    before = business_state(runtime)
    explicit = {
        "now": now.isoformat(),
        "memories": [saved_memory],
    }
    for material in (draft["context"], explicit):
        result = await runtime.prepare_request("life.plan", "life", draft["template"], material)
        assert request_text(result).count("SAME_FICTION_EVENT") == 1
    assert business_state(runtime) == before


@pytest.mark.parametrize("task", ["life.revise", "memory.reflect"])
async def test_business_request_filters_memory_before_cross_category_deduplication(world, task):
    runtime, _, provider = world
    await runtime.update_settings(
        {
            "context_layout": {"tasks": {task: ["memory"]}},
            "context_usage": {"limits": {"memory.related": 0}},
        }
    )
    runtime.memory.remember(
        "SELECTED_KNOWLEDGE", stable=True, scope=PRIVATE, source_event_id="shared-event"
    )
    runtime.memory.remember(
        "UNSELECTED_EMOTION",
        kind="emotional",
        scope="qq:FriendMessage:99",
        source_event_id="shared-event",
        important=True,
    )
    before = business_state(runtime)
    request = await runtime.prepare_request(task, task.split(".")[0], "TASK", {}, PRIVATE)
    assert "SELECTED_KNOWLEDGE" in request_text(request)
    assert "UNSELECTED_EMOTION" not in request_text(request)
    assert business_state(runtime) == before and not provider.calls


@pytest.mark.parametrize("field", ["context", "available_context"])
async def test_explicit_life_snapshot_empty_categories_are_not_reread_or_filled(world, field):
    runtime, _, _ = world
    await runtime.update_settings(
        {"context_layout": {"tasks": {"news.select": ["time", "memory"]}}}
    )
    runtime.memory.remember("UNRELATED_KNOWLEDGE", kind="knowledge", scope=PRIVATE)
    raw = await runtime.context_text(
        PRIVATE, query="MATH_QUERY", reinforce=False, task="news.select"
    )
    assert json.loads(raw)["memories"] == []
    runtime.memory.remember("MATH_QUERY_LATE_KNOWLEDGE", kind="knowledge", scope=PRIVATE)
    runtime.context_bundle = AsyncMock(side_effect=AssertionError("Snapshot must not be reread"))
    before = business_state(runtime)
    request = await runtime.prepare_request("news.select", "news", "TASK", {field: raw}, PRIVATE)
    assert "UNRELATED_KNOWLEDGE" not in request_text(request)
    assert "MATH_QUERY_LATE_KNOWLEDGE" not in request_text(request)
    assert source_ids(request) == {"time"}
    runtime.context_bundle.assert_not_called()
    assert business_state(runtime) == before
