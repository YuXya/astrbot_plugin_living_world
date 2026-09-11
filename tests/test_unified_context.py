"""Unified context selection and historical snapshot compatibility."""

import copy

import pytest

from living_world.context import context_from_data, memory_blocks
from living_world.context_catalog import BLOCK_NAMES, MEMORY_DEFAULTS, V3_BLOCK_NAMES
from living_world.context_usage import (
    DEFAULT_USAGE,
    LEGACY_DEFAULT_USAGE,
    usage_for,
    validate_usage,
)
from living_world.layout import (
    DEFAULT_LAYOUT,
    DEFAULT_SELECTIONS,
    DEFAULT_SETTINGS,
    V2_LAYOUT,
    V3_DEFAULT_SELECTIONS,
    V3_LAYOUT,
    assemble,
    catalog,
    collect_task_blocks,
    validate_settings,
)


def row(key, text, **extra):
    return {
        "id": key,
        "schema_version": 2,
        "version": 1,
        "judgment": text,
        "text": text,
        "attribute": "事实属性",
        "owner": "self",
        "owner_name": "可可",
        "persona_name": "可可",
        "scope": "global",
        "occurred_at": "2026-09-10T18:00:00+08:00",
        **extra,
    }


def snapshot(**extra):
    return {
        "current_time": "2026-09-11T08:00:00+08:00",
        "timezone": "Asia/Shanghai",
        "schedule": {"status": "missing", "activities": []},
        "memories": [],
        "recent_memories": [],
        "observations": [],
        "context_usage": copy.deepcopy(DEFAULT_USAGE),
        **extra,
    }


def test_complete_catalog_uses_two_unified_entries_for_every_task():
    directory = catalog()
    assert directory["version"] == 4
    assert {"memory", "memory.recent"} <= BLOCK_NAMES.keys()
    assert (
        not {
            *MEMORY_DEFAULTS,
            "experiences",
            "observations",
            "task.events",
            "task.document",
            "task.brief_limit",
        }
        & BLOCK_NAMES.keys()
    )
    assert set(DEFAULT_LAYOUT["system"] + DEFAULT_LAYOUT["user"]) == set(BLOCK_NAMES)
    assert all(set(task["blocks"]) == set(BLOCK_NAMES) for task in directory["tasks"])
    for task in ("chat.group", "chat.private", "social.message"):
        assert {"memory", "memory.recent", "schedule.recent"} <= set(DEFAULT_SELECTIONS[task])
    assert "memory.query" in DEFAULT_SELECTIONS
    assert not {"journal.brief", "notes.brief"} & DEFAULT_SELECTIONS.keys()


def test_v3_conversion_preserves_first_memory_role_and_separate_default_baseline():
    order = copy.deepcopy(V3_LAYOUT)
    order["user"].remove("memory.profile")
    order["system"].insert(0, "memory.profile")
    order["user"].remove("experiences")
    order["system"].append("experiences")
    tasks = copy.deepcopy(V3_DEFAULT_SELECTIONS)
    tasks["chat.private"] = []
    tasks["news.select"] = ["observations", "task.document"]
    original = {"version": 3, "order": order, "baseline_order": V3_LAYOUT, "tasks": tasks}
    before = copy.deepcopy(original)
    result = validate_settings(original)
    assert result["version"] == 4
    assert result["order"]["system"][0] == "memory"
    assert result["order"]["system"][-1] == "memory.recent"
    assert "memory" in result["baseline_order"]["user"]
    assert result["tasks"]["chat.private"] == []
    assert set(result["tasks"]["news.select"]) == {"memory", "task.material"}
    assert original == before
    assert validate_settings(result) == result


def test_v2_migration_archived_task_order_does_not_override_global_order():
    global_order = copy.deepcopy(V2_LAYOUT)
    independent = copy.deepcopy(V2_LAYOUT)
    independent["user"].remove("memory.knowledge")
    independent["system"].insert(0, "memory.knowledge")
    result = validate_settings(
        {"version": 2, "default": global_order, "tasks": {"chat.private": independent}}
    )
    assert "memory" in result["order"]["user"]
    assert result["baseline_order"] == result["order"]


def test_v2_usage_has_independent_limits_and_selection_gates_whole_memory_block():
    assert usage_for({}) == DEFAULT_USAGE
    recent_only = usage_for({}, ["memory.recent"])
    assert recent_only["limits"] == {
        "memory.self": 0,
        "memory.people": 0,
        "memory.related": 0,
        "memory.recent": 5,
        "weather": 0,
    }
    assert recent_only["people_limit"] == 0
    profile_only = usage_for({}, ["memory"])
    assert profile_only["limits"]["memory.recent"] == 0
    assert profile_only["people_limit"] == 3
    assert profile_only["limits"]["memory.related"] == 10
    old = copy.deepcopy(LEGACY_DEFAULT_USAGE)
    old["limits"]["weather"] = 0
    converted = validate_usage(old)
    assert converted["version"] == 2 and converted["limits"]["weather"] == 0
    assert "brief_max_chars" not in converted
    assert validate_usage(converted) == converted


@pytest.mark.parametrize(
    "field,value",
    [
        ("memory.self", 51),
        ("memory.people", True),
        ("memory.related", -1),
        ("memory.recent", 0.5),
        ("weather", 2),
        ("people_limit", 21),
    ],
)
def test_invalid_unified_limits_rejected(field, value):
    usage = copy.deepcopy(DEFAULT_USAGE)
    if field == "people_limit":
        usage[field] = value
    else:
        usage["limits"][field] = value
    with pytest.raises(ValueError):
        validate_usage(usage)


def test_recent_first_dedup_preserves_different_conclusions_from_same_source():
    recent = row("recent", "昨天学会调水彩", source_keys=["document:one"])
    other = row("related", "画纸先打湿可让颜色晕开", source_keys=["document:one"])
    data = snapshot(recent_memories=[recent], memories=[recent, other])
    before = copy.deepcopy(data)
    rendered = context_from_data(data)
    assert data == before
    assert rendered["text"].count("昨天学会调水彩") == 1
    assert "画纸先打湿可让颜色晕开" in rendered["text"]
    sources = {item["block_id"]: item for item in rendered["sources"]}
    assert sources["memory.recent"]["memory_ids"] == ["recent"]
    assert sources["memory"]["memory_ids"] == ["related"]
    assert sources["memory.recent"]["memory_versions"] == {"recent": 1}
    assert "2026-09-10 18：00" in rendered["text"]


def test_unselected_recent_does_not_consume_related_memory_or_render_old_unconverted_data():
    selected = row("one", "昨天的生活")
    data = snapshot(
        memories=[selected, {"id": "old", "text": "待迁移旧记忆", "kind": "event"}],
        recent_memories=[selected],
        context_selection=["memory"],
        experiences=[{"text": "旧经历直接注入不允许"}],
        observations=[{"module": "news", "text": "旧综合见闻"}],
    )
    result = context_from_data(data)
    assert [source["block_id"] for source in result["sources"]] == ["memory"]
    assert result["sources"][0]["memory_ids"] == ["one"]
    assert all(text not in result["text"] for text in ("待迁移", "旧经历", "旧综合见闻"))


def test_assembly_deduplicates_independently_supplied_sources_using_frozen_rows():
    shared, extra = row("one", "重复记录"), row("two", "另一条记录")
    blocks = memory_blocks([shared, extra]) + memory_blocks([shared], identifier="memory.recent")
    original = copy.deepcopy(blocks)
    result = assemble(DEFAULT_LAYOUT, blocks, selection=["memory", "memory.recent"])
    assert result["prompt"].count("重复记录") == 1
    source = next(item for item in result["sources"] if item["block_id"] == "memory")
    assert source["memory_ids"] == ["two"] and source["memory_versions"] == {"two": 1}
    assert source["memory_snapshots"] == [extra]
    assert blocks == original
    without_recent = assemble(DEFAULT_LAYOUT, blocks, selection=["memory"])
    source = next(item for item in without_recent["sources"] if item["block_id"] == "memory")
    assert source["memory_ids"] == ["one", "two"]


def test_projection_keeps_evidence_inference_and_actual_time_without_mechanics():
    memory = row(
        "internal-id",
        "可能偏好蓝色",
        reasoning="谈话中多次挑选蓝色画笔。",
        inferred=True,
        stable=True,
        occurred_at="",
        created_at="2026-09-11T08:00:00+08:00",
        strength=10,
        usefulness=2.5,
    )
    text = memory_blocks([memory])[0]["content"]
    assert "有依据的推断" in text and "事实依据：谈话中多次挑选蓝色画笔" in text
    assert "稳定画像" in text
    assert all(
        value not in text
        for value in ("internal-id", "2026-09-11", "strength", "usefulness", "global")
    )
    known = memory_blocks([memory], include_identifiers=True)[0]
    assert "internal-id" in known["content"]


def test_task_originals_only_become_explicit_material_and_round_snapshot_is_immutable():
    source = {
        "document": "原始日记正文",
        "events": ["待提炼旧经历"],
        "max_chars": 100,
        "known": [row("known-id", "已保存结论")],
    }
    before = copy.deepcopy(source)
    blocks = collect_task_blocks(source)
    assert {item["block_id"] for item in blocks} == {"task.material", "memory"}
    result = assemble(
        DEFAULT_LAYOUT, blocks, "SYSTEM", "USER", selection=["task.material", "memory"]
    )
    assert "原始日记正文" in result["prompt"] and "待提炼旧经历" in result["prompt"]
    assert "max_chars" not in result["prompt"]
    assert source == before
    assert validate_settings(DEFAULT_SETTINGS) == DEFAULT_SETTINGS


@pytest.mark.parametrize("version,layout", [(2, V2_LAYOUT), (3, V3_LAYOUT)])
def test_old_snapshot_keeps_old_taxonomy_names_and_source_rows(version, layout):
    data = snapshot(
        memories=[{"id": "old", "text": "旧知识仍照原版解释", "kind": "knowledge"}],
        observations=[{"module": "news", "text": "旧新闻资料"}],
    )
    data["context_usage"] = copy.deepcopy(LEGACY_DEFAULT_USAGE)
    result = assemble(layout, context_from_data(data, version=version)["sources"], version=version)
    assert f"【{V3_BLOCK_NAMES['memory.knowledge']}】" in result["prompt"]
    assert "旧知识仍照原版解释" in result["prompt"] and "旧新闻资料" in result["prompt"]
    assert "近期记忆" not in result["prompt"]
