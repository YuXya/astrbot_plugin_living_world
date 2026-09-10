"""Independent allowances, canonical names, migration and frozen request material."""

import copy
import json
from datetime import timedelta

import pytest

from living_world.config import settings_from
from living_world.context_catalog import (
    BLOCK_NAMES,
    MEMORY_DEFAULTS,
    OWNERS,
    PAGES,
    memory_category,
)
from living_world.context_usage import DEFAULT_USAGE, legacy_usage
from living_world.layout import LEGACY_LAYOUT, assemble, collect_task_blocks, validate_settings
from living_world.runtime import Runtime
from test_runtime import FakeHost, GROUP, PRIVATE

pytest_plugins = ("test_runtime",)


def test_names_are_real_menu_names_and_categories_are_disjoint():
    for key, (page, tab, _) in OWNERS.items():
        assert BLOCK_NAMES[key].split("：")[0] == PAGES[page][1][tab]
    assert memory_category({"kind": "knowledge", "profile": True}) == "memory.profile"
    assert memory_category({"kind": "knowledge", "source": "notes:brief"}) == "memory.notes"
    assert memory_category({"kind": "emotional", "source": "journal:brief"}) == "memory.journal"
    assert memory_category({"kind": "event", "text": "知识记忆"}) == "memory.event"
    assert memory_category({"id": "journal:legacy", "kind": "knowledge"}) is None
    assert memory_category({"source": "journal", "kind": "emotional"}) is None
    assert memory_category({"source": "notes", "kind": "knowledge"}) is None


@pytest.mark.parametrize("total", [0, 1, 3, 10, 17, 50])
def test_legacy_allowances_are_apportioned_once(total):
    old = {"memory": {"context_limit": total, "journal_limit": 7, "brief_max_chars": 180}}
    migrated = settings_from(old)
    limits = migrated["context_usage"]["limits"]
    assert sum(limits[key] for key in list(MEMORY_DEFAULTS)[:5]) == total
    assert limits["memory.journal"] + limits["memory.notes"] == min(total, 7)
    assert limits["memory.journal"] >= limits["memory.notes"]
    assert set(migrated["context_usage"]["brief_max_chars"].values()) == {180}
    assert not {"context_limit", "journal_limit", "brief_max_chars"} & migrated["memory"].keys()
    assert settings_from(migrated) == migrated
    assert legacy_usage({}) == DEFAULT_USAGE
    if total == 3:
        assert [limits[key] for key in list(MEMORY_DEFAULTS)[:5]] == [1, 1, 0, 0, 1]
    if total == 17:
        assert [limits[key] for key in list(MEMORY_DEFAULTS)[:5]] == [7, 3, 2, 2, 3]


def test_old_roles_and_positions_expand_without_resetting_other_blocks():
    old = copy.deepcopy(LEGACY_LAYOUT)
    old["user"].remove("memories")
    old["system"].insert(0, "memories")
    old["user"].remove("news")
    old["system"].append("news")
    value = validate_settings({"version": 1, "default": old, "tasks": {"social.message": old}})
    assert value["version"] == 2
    assert value["default"]["system"][:7] == list(MEMORY_DEFAULTS)
    assert value["default"]["system"][-1] == "observations"
    assert value["tasks"]["social.message"] == value["default"]
    assert not {"news", "search", "bilibili", "daily_digest", "memories"} & set(
        value["default"]["user"]
    )


@pytest.mark.parametrize("value", [-1, 51, 1.2, True, float("nan"), "4"])
def test_invalid_independent_allowance_is_rejected(value):
    with pytest.raises(ValueError):
        settings_from({"context_usage": {"limits": {"memory.knowledge": value}}})


def test_invalid_catalog_identifiers_and_weather_allowance_are_rejected():
    for patch in (
        {"limits": {"unknown": 2}},
        {"limits": {"weather": 2}},
        {"version": True},
        {"brief_max_chars": {"memory.journal": 49}},
    ):
        with pytest.raises(ValueError):
            settings_from({"context_usage": patch})


async def test_every_category_has_its_own_budget_without_hidden_total(runtime):
    await runtime.update_settings(
        {
            "modules": {"journal": True, "notes": True},
            "context_usage": {"limits": dict.fromkeys(MEMORY_DEFAULTS, 50)},
        }
    )
    now = runtime.life._now().isoformat()
    for category in MEMORY_DEFAULTS:
        for i in range(52):
            key = f"{category}-{i}"
            subtype = category.split(".")[1]
            row = {
                "id": key,
                "text": key,
                "kind": subtype
                if subtype in {"knowledge", "event", "skill", "emotional"}
                else "knowledge",
                "scope": PRIVATE,
                "active": True,
                "source": subtype + ":brief" if subtype in {"journal", "notes"} else "chat",
                "updated_at": now,
                "created_at": now,
                "profile": subtype == "profile",
                "person_id": "qq:42" if subtype == "profile" else "",
                "access_count": 0,
            }
            runtime.store.put("memories", key, row)
    selected = runtime.memory.recall(scope=PRIVATE, person_id="qq:42")
    assert len(selected) == 350
    assert all(
        sum(memory_category(row) == category for row in selected) == 50
        for category in MEMORY_DEFAULTS
    )
    assert sum(row["access_count"] for row in runtime.store.list("memories")) == 350
    await runtime.update_settings(
        {"context_usage": {"limits": {"memory.skill": 0, "memory.notes": 0, "memory.event": 1}}}
    )
    selected = runtime.memory.recall(scope=PRIVATE, person_id="qq:42", reinforce=False)
    assert len(selected) == 201
    assert not any(memory_category(row) in {"memory.skill", "memory.notes"} for row in selected)
    await runtime.update_settings({"context_usage": {"limits": dict.fromkeys(MEMORY_DEFAULTS, 0)}})
    assert runtime.memory.recall() == []
    assert len(runtime.store.list("memories")) == 364


async def test_combined_observations_weather_experiences_and_reinforcement(runtime):
    await runtime.update_settings(
        {
            "modules": {
                "news": True,
                "search": True,
                "bilibili": True,
                "daily_digest": True,
                "weather": True,
            },
            "context_usage": {"limits": {"observations": 3, "weather": 1, "experiences": 1}},
        }
    )
    for i, module in enumerate(
        ("news", "search", "bilibili", "daily_digest", "weather", "weather")
    ):
        runtime.store.put(
            "observations",
            str(i),
            {
                "id": str(i),
                "module": module,
                "text": f"SOURCE_{i}",
                "scope": "global",
                "created_at": i,
            },
        )
    runtime.store.put(
        "observations",
        "secret",
        {
            "id": "secret",
            "module": "news",
            "text": "PRIVATE_SECRET",
            "scope": PRIVATE,
            "created_at": 100,
        },
    )
    runtime.record_event("唯一生活经历", source="fiction", key="one")
    request = await runtime.prepare_request(
        "social.message", "social", "写消息", {"context": await runtime.context_text()}
    )
    sources = {row["block_id"]: row for row in request["sources"]}
    assert sources["observations"]["count"] == sources["observations"]["limit"] == 3
    assert sources["weather"]["count"] == 1
    assert (
        request["prompt"].index("SOURCE_3")
        < request["prompt"].index("SOURCE_2")
        < request["prompt"].index("SOURCE_1")
    )
    assert all(text not in request["prompt"] for text in ("SOURCE_0", "SOURCE_4", "PRIVATE_SECRET"))
    assert request["prompt"].count("唯一生活经历") == 1
    assert runtime.store.get("memories", "event:one")["access_count"] == 0
    assert sources["experiences"]["limit"] == 1
    await runtime.update_settings(
        {"context_usage": {"limits": {"observations": 0, "experiences": 0, "weather": 0}}}
    )
    trial = await runtime.prepare_trial_request(request)
    assert trial["prompt"] == request["prompt"]
    raw = json.loads(await runtime.context_text(reinforce=False))
    assert not raw["observations"] and not raw["experiences"]
    assert len(runtime.store.list("observations")) == 7


async def test_legacy_trial_preserves_four_sources_and_old_names(runtime):
    data = {
        "context": {
            "current_time": "2026-09-10T12:00:00+08:00",
            "memories": [],
            "schedule": {"status": "missing"},
            "observations": [
                {"module": module, "text": module.upper()}
                for module in ("news", "search", "bilibili", "daily_digest")
            ],
        }
    }
    expected = assemble(
        LEGACY_LAYOUT, collect_task_blocks(data, legacy=True), "SYSTEM", "TEMPLATE", legacy=True
    )
    request = {
        "task": "social.message",
        "module": "social",
        "scope": "global",
        "provider_id": "",
        "prompt_mode": "structured",
        "context_layout": LEGACY_LAYOUT,
        "context_blocks": [],
        "base_system_prompt": "SYSTEM",
        "template": "TEMPLATE",
        "dynamic_context": data,
    }
    trial = await runtime.prepare_trial_request(request)
    assert trial["prompt"] == expected["prompt"]
    assert "【近期见闻】" in trial["prompt"]
    assert "近期见闻：综合见闻" not in trial["prompt"]


@pytest.mark.parametrize(
    "scope, expected", [(GROUP, "会话类型：QQ群聊"), (PRIVATE, "会话类型：一对一私聊")]
)
async def test_selected_recipient_is_explicit_and_trial_has_no_send(runtime, scope, expected):
    await runtime.update_settings(
        {
            "modules": {"proactive": True},
            "sessions": [{"umo": scope, "display_name": "测试对话称呼"}],
            "social": {"target_count": 20},
        }
    )
    assert runtime.settings["social"]["target_count"] == 1
    result = await runtime.social.send(action_id="recipient")
    assert result["status"] == "success"
    request = runtime.last_requests[("social.message", scope)]
    assert expected in request["prompt"] and "测试对话称呼" in request["prompt"]
    assert "【聊天白名单：交谈对象与场合】" in request["prompt"]
    assert scope not in request["prompt"]
    assert len(runtime.host.sent) == 1
    trial = await runtime.build_test_request("social.message", scope)
    assert expected in trial["prompt"] and len(runtime.host.sent) == 1
    before = runtime.store.export()
    await runtime.prepare_trial_request(trial)
    assert runtime.store.export() == before


async def test_usage_restore_and_conversion_backup_are_persistent(runtime, tmp_path):
    await runtime.update_settings(
        {
            "context_usage": {
                "limits": {"memory.knowledge": 17},
                "brief_max_chars": {"memory.notes": 321},
            }
        }
    )
    backup = runtime.export()
    other = Runtime(tmp_path / "restore.sqlite", FakeHost())
    try:
        await other.restore(backup)
        assert other.settings["context_usage"] == runtime.settings["context_usage"]
        assert other.store.list("context_settings_history")
        old = {**backup, "settings": {**backup["settings"], "memory": {"context_limit": 0}}}
        old["settings"].pop("context_usage")
        await other.restore(old)
        assert all(other.settings["context_usage"]["limits"][key] == 0 for key in MEMORY_DEFAULTS)
    finally:
        await other.stop()


async def test_reflection_keeps_typed_briefs_and_replacement_identifiers(runtime, monkeypatch):
    await runtime.update_settings({"modules": {"journal": True}})
    remembered = runtime.memory.remember("旧的明确约定", scope=PRIVATE, source="chat")
    runtime.memory.remember(
        "独立的日记简报",
        kind="knowledge",
        scope=PRIVATE,
        source="journal:brief",
        key="journal:typed",
    )
    yesterday = runtime.life._now() - timedelta(days=1)
    runtime.record_event(
        "昨天的虚构经历不可召回",
        source="fiction",
        key="old-fiction",
        occurred_at=yesterday.isoformat(),
    )
    captured = []

    async def complete(task, module, template, context, scope):
        request = await runtime.prepare_request(task, module, template, context, scope)
        captured.append(request)
        return json.dumps(
            {
                "memories": [
                    {
                        "text": "新的明确约定",
                        "kind": "event",
                        "replace_id": remembered["id"],
                        "evidence": "新的明确约定",
                    }
                ]
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(runtime, "complete", complete)
    await runtime.memory.reflect("新的明确约定", scope=PRIVATE)
    request = captured[0]
    sources = {row["block_id"]: row for row in request["sources"]}
    assert remembered["id"] in sources["memory.event"]["content"]
    assert "独立的日记简报" in sources["memory.journal"]["content"]
    assert "memory.knowledge" not in sources
    assert "昨天的虚构经历不可召回" not in request["prompt"]
    assert runtime.store.get("memories", remembered["id"])["text"] == "新的明确约定"
    assert '"known"' in sources["memory.event"]["content"]
