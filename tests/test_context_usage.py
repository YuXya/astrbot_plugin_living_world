"""Independent allowances, canonical names, migration and frozen request material."""

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
from living_world.context_usage import DEFAULT_USAGE
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


@pytest.mark.parametrize("value", [-1, 51, 1.2, True, float("nan"), "4"])
def test_invalid_independent_allowance_is_rejected(value):
    with pytest.raises(ValueError):
        settings_from({"context_usage": {"limits": {"memory.related": value}}})


def test_invalid_catalog_identifiers_and_weather_allowance_are_rejected():
    for patch in (
        {"limits": {"unknown": 2}},
        {"limits": {"weather": 2}},
        {"version": True},
        {"brief_max_chars": {"memory.journal": 49}},
    ):
        with pytest.raises(ValueError):
            settings_from({"context_usage": patch})


async def test_every_unified_allowance_has_its_own_budget_without_hidden_total(runtime):
    limits = {
        key: 50 for key in ("memory.self", "memory.people", "memory.related", "memory.recent")
    }
    await runtime.update_settings({"context_usage": {"limits": limits, "people_limit": 3}})
    now = runtime.life._now()
    for i in range(52):
        runtime.memory.remember(f"数学自身画像 {i}", scope=PRIVATE, stable=True, occurred_at="")
        runtime.memory.remember(f"数学相关结论 {i}", scope=PRIVATE, occurred_at="")
        runtime.memory.remember(
            f"数学近期经历 {i}", scope=PRIVATE, occurred_at=(now - timedelta(minutes=i)).isoformat()
        )
        for person in ("qq:42", "qq:43", "qq:44", "qq:45"):
            runtime.memory.remember(
                f"数学人物画像 {person} {i}", scope=PRIVATE, person_id=person, stable=True
            )
    selected = await runtime.memory.select_context(
        PRIVATE, "数学", person_id="qq:42", people=["qq:43", "qq:44", "qq:45"], semantic=False
    )
    assert len(selected["recent_memories"]) == 50
    assert len(selected["memories"]) == 250
    all_rows = selected["recent_memories"] + selected["memories"]
    assert len({row["id"] for row in all_rows}) == 300
    assert not any(row.get("person_id") == "qq:45" for row in all_rows)
    assert sum(row["access_count"] for row in runtime.store.list("memories")) == 0
    await runtime.update_settings({"context_usage": {"limits": dict.fromkeys(limits, 0)}})
    assert await runtime.memory.select_context(PRIVATE, "数学", semantic=False) == {
        "memories": [],
        "recent_memories": [],
    }
    assert len(runtime.store.list("memories")) == 364


async def test_source_archives_are_not_direct_context_and_weather_is_independent(runtime):
    await runtime.update_settings(
        {
            "modules": {
                "news": True,
                "search": True,
                "bilibili": True,
                "daily_digest": True,
                "weather": True,
            },
            "context_usage": {"limits": {"memory.recent": 1, "weather": 1}},
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
    memory = runtime.memory.remember(
        "唯一生活经历", source="fiction", occurred_at=runtime.life._now().isoformat()
    )
    request = await runtime.prepare_request(
        "social.message", "social", "写消息", {"context": await runtime.context_text()}
    )
    sources = {row["block_id"]: row for row in request["sources"]}
    assert sources["memory.recent"]["count"] == sources["memory.recent"]["limit"] == 1
    assert sources["weather"]["count"] == 1
    assert "observations" not in sources and "experiences" not in sources
    assert all(
        text not in request["prompt"]
        for text in ("SOURCE_0", "SOURCE_1", "SOURCE_2", "SOURCE_3", "SOURCE_4", "PRIVATE_SECRET")
    )
    assert "SOURCE_5" in request["prompt"]
    assert request["prompt"].count("唯一生活经历") == 1
    assert runtime.store.get("memories", memory["id"])["access_count"] == 0
    await runtime.update_settings(
        {
            "context_usage": {
                "limits": {
                    "memory.recent": 0,
                    "memory.self": 0,
                    "memory.people": 0,
                    "memory.related": 0,
                    "weather": 0,
                }
            }
        }
    )
    raw = json.loads(await runtime.context_text(reinforce=False))
    assert not raw["observations"] and not raw["recent_memories"] and not raw["memories"]
    assert len(runtime.store.list("observations")) == 7


@pytest.mark.parametrize(
    "scope, expected", [(GROUP, "会话类型：QQ群聊"), (PRIVATE, "会话类型：一对一私聊")]
)
async def test_selected_recipient_is_explicit_in_sent_message(runtime, scope, expected):
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


async def test_usage_restore_and_conversion_backup_are_persistent(runtime, tmp_path):
    await runtime.update_settings(
        {
            "context_usage": {
                "limits": {"memory.related": 17},
                "people_limit": 4,
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
        assert other.settings["context_usage"] == DEFAULT_USAGE
    finally:
        await other.stop()


async def test_reflection_keeps_replacement_ids_and_version_history(runtime, monkeypatch):
    remembered = runtime.memory.remember("旧的明确约定", scope=PRIVATE, source="chat")
    captured = []

    async def complete(task, module, template, context, scope):
        request = await runtime.prepare_request(task, module, template, context, scope)
        captured.append(request)
        return json.dumps(
            {
                "memories": [
                    {
                        "judgment": "新的明确约定",
                        "attribute": "事实属性",
                        "replace_id": remembered["id"],
                        "evidence": "新的明确约定",
                        "owner": "self",
                    }
                ]
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(runtime, "complete", complete)
    await runtime.memory.reflect("新的明确约定", scope=PRIVATE)
    request = captured[0]
    sources = {row["block_id"]: row for row in request["sources"]}
    assert remembered["id"] in sources["memory"]["content"]
    assert not set(MEMORY_DEFAULTS) & sources.keys()
    assert runtime.store.get("memories", remembered["id"])["text"] == "新的明确约定"
    assert runtime.store.get("memories", remembered["id"])["version"] == remembered["version"] + 1
    assert '"known"' in sources["memory"]["content"]
    assert runtime.store.list("memory_versions")
