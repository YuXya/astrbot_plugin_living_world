"""Regression coverage for diagnostic retention and isolated model trials."""

import asyncio
import json

import pytest

from living_world.config import settings_from
from living_world.debug import json_value
from living_world.runtime import Runtime


class Host:
    def __init__(self):
        self.requests = []
        self.tools = []
        self.sent = []
        self.release = None
        self.entered = asyncio.Event()

    async def persona(self, key):
        return "A fixed persona."

    async def session_persona(self, scope):
        return "student"

    async def generate_request(self, request):
        self.requests.append(request)
        self.entered.set()
        if self.release:
            await self.release.wait()
        return (
            "model text",
            {"tokens": 3},
            {
                "completion_text": "model text",
                "raw_completion": {"tool_calls": [{"name": "send_message_to_user"}]},
            },
        )

    async def call_tool(self, name, arguments, scope, plugin_name=None):
        self.tools.append((name, arguments))
        return "tool result" * 3000

    async def send(self, scope, text):
        self.sent.append((scope, text))
        return True


@pytest.fixture
async def runtime(tmp_path):
    instance = Runtime(tmp_path / "world.db", Host())
    await instance.update_settings(
        {"persona_id": "student", "sessions": [{"umo": "qq:GroupMessage:100"}]}
    )
    yield instance
    await instance.stop()


async def test_complete_records_full_request_response_and_separate_template(runtime):
    private_material = "private dynamic material " * 2000
    await runtime.complete(
        "memory.reflect", "memory", "Extract grounded facts.", {"material": private_material}
    )
    row = runtime.store.list("debug_records")[0]
    assert row["request"]["dynamic_context"]["material"] == private_material
    assert private_material in row["request"]["prompt"]
    assert row["request"]["persona"] == "A fixed persona."
    assert row["request"]["system_prompt"] and row["request"]["parameters"] == {}
    assert row["response"]["raw_completion"]["tool_calls"]
    assert runtime.debug.get_default("memory.reflect") == "Extract grounded facts."
    await runtime.action(
        {"action": "save_template", "task": "memory.reflect", "template": "New instruction."}
    )
    await runtime.complete(
        "memory.reflect", "memory", "Extract grounded facts.", {"material": "new"}
    )
    assert runtime.host.requests[-1]["template"] == "New instruction."
    assert private_material not in json.dumps(runtime.debug.snapshot())


async def test_retention_is_per_task_and_never_deletes_business_records(runtime, monkeypatch):
    # Equal clock ticks still retain the most recently inserted records.
    monkeypatch.setattr("living_world.store.time.time", lambda: 1800000000.0)
    runtime.store.put("life_days", "today", {"raw_json": "formal", "id": "today"})
    runtime.store.put("actions", "sent", {"id": "sent", "status": "success"})
    for i in range(15):
        runtime.audit_external("news.read", {"i": i}, {"status": "success"})
        runtime.audit_external("search.tool", {"i": i}, {"status": "success"})
    rows = runtime.store.list("debug_records")
    assert len(rows) == 20
    assert all(r["request"]["i"] >= 5 for r in rows)
    await runtime.update_settings({"debug": {"retain_per_category": 3}})
    assert len(runtime.store.list("debug_records")) == 6
    await runtime.action({"action": "debug_clear", "category": "news.read"})
    assert len(runtime.store.list("debug_records")) == 3
    assert runtime.store.get("life_days", "today")["raw_json"] == "formal"
    assert runtime.store.get("actions", "sent")["status"] == "success"


async def test_trial_has_no_business_writes_and_ignores_returned_tool_calls(runtime):
    runtime.memory.remember("A durable fact.")
    before = runtime.store.export()
    request = {
        "task": "memory.reflect",
        "module": "memory",
        "scope": "global",
        "prompt": "test",
        "system_prompt": "system",
        "contexts": [{"role": "user", "content": "context"}],
        "parameters": {"temperature": 0.4},
        "tools": [{"name": "send_message_to_user"}],
    }
    result = await runtime.action({"action": "debug_test", "request": request})
    assert result["test_only"]
    assert runtime.host.requests[-1]["tools"] == []
    assert not runtime.host.tools and not runtime.host.sent
    after = [r for r in runtime.store.export() if r["namespace"] != "debug_records"]
    assert before == after
    assert runtime.store.list("debug_records")[0]["response"]["raw_completion"]["tool_calls"]


async def test_current_plan_preview_is_read_only_and_uses_new_config(runtime):
    runtime.memory.remember("Remember a quiet library.")
    await runtime.update_settings({"life": {"news_count": 1}})
    before = runtime.store.export()
    result = await runtime.action({"action": "debug_build", "task": "life.plan"})
    request = result["request"]
    assert request["dynamic_context"]["parameters"] == runtime.life.parameters()
    assert "news_count" not in request["dynamic_context"]["parameters"]
    assert request["prompt_mode"] == "structured"
    assert before == runtime.store.export()


async def test_structured_trial_compiles_edited_template_and_context(runtime):
    templates = runtime.store.list("prompt_templates")
    request = await runtime.build_test_request("life.plan")
    request.update(
        template="Local test instruction",
        dynamic_context={"new": "edited context"},
        model="edited-model",
    )
    await runtime.test_request(request)
    sent = runtime.host.requests[-1]
    assert "edited context" in sent["prompt"] and sent["prompt"].startswith(
        "Local test instruction"
    )
    assert sent["model"] == "edited-model"
    assert runtime.store.list("prompt_templates") == templates
    assert not runtime.store.list("life_days")
    request.update(prompt_mode="raw", prompt="Raw edited prompt")
    await runtime.test_request(request)
    assert runtime.host.requests[-1]["prompt"] == "Raw edited prompt"


async def test_detail_trial_uses_current_budget_without_reserving_or_widening_scope(runtime):
    await runtime.update_settings({"life": {"search_count": 7}})
    runtime.memory.remember("A private commitment must stay private.", scope="qq:FriendMessage:42")
    before = runtime.store.export()
    request = await runtime.build_test_request("life.detail")
    assert "上限 7" in request["dynamic_context"]["行动额度"]
    assert "A private commitment" not in request["prompt"]
    assert "scope_overrides" not in request["prompt"]
    assert runtime.store.export() == before
    await runtime.test_request(request)
    after = [row for row in runtime.store.export() if row["namespace"] != "debug_records"]
    assert after == [row for row in before if row["namespace"] != "debug_records"]
    assert not runtime.host.tools and not runtime.host.sent


async def test_trial_media_is_preserved_and_unsupported_fields_fail_explicitly(runtime):
    await runtime.test_request(
        {
            "prompt": "Inspect",
            "image_urls": ["https://example.test/a.png"],
            "audio_urls": ["https://example.test/a.wav"],
        }
    )
    assert runtime.host.requests[-1]["image_urls"] == ["https://example.test/a.png"]
    assert runtime.host.requests[-1]["audio_urls"] == ["https://example.test/a.wav"]
    with pytest.raises(ValueError, match="contexts"):
        await runtime.test_request({"prompt": "test", "tool_calls_result": [{"legacy": "result"}]})


async def test_preview_all_available_tasks_never_updates_business_data(runtime):
    runtime.memory.remember("Scoped factual background.")
    before = runtime.store.export()
    for template in runtime.debug.snapshot()["templates"]:
        request = await runtime.build_test_request(template["task"])
        assert request["template"] == template["default_template"]
        assert request["dynamic_context"] is not None
    assert runtime.store.export() == before


async def test_plan_button_passes_actual_today_time(runtime):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    captured = []

    async def plan_day(now, scope):
        captured.append(now)
        return []

    runtime.life.plan_day = plan_day
    now = datetime.now(ZoneInfo(runtime.settings["character"]["timezone"]))
    await runtime.action({"action": "plan_day", "date": now.date().isoformat()})
    assert abs((captured[0] - now).total_seconds()) < 10
    with pytest.raises(ValueError):
        await runtime.action({"action": "plan_day", "date": "2000-01-01"})


async def test_regenerate_action_records_versions_and_uses_frozen_public_template(runtime):
    from test_life import NOW, plan_rows

    runtime.life._now = lambda value=None: value or NOW
    runtime.store.put(
        "prompt_templates", "life.plan", {"template": "Use this saved public template."}
    )

    async def generate(request):
        runtime.host.requests.append(request)
        text = json.dumps({"activities": plan_rows()}, ensure_ascii=False)
        return text, {}, {"completion_text": text}

    runtime.host.generate_request = generate
    date = str(NOW.date())
    first = await runtime.action({"action": "regenerate_day", "date": date})
    second = await runtime.action({"action": "regenerate_day", "date": date})
    assert len(first) == len(second) == 10
    assert runtime.host.requests[-1]["template"] == "Use this saved public template."
    assert runtime.host.tools == runtime.host.sent == []
    history = runtime.store.list("life_day_history")
    assert len(history) == 1
    assert history[0]["full_request"]["_debug_record_id"]
    adopted = [item for view in runtime.debug.views() for item in view["adopted"]]
    assert any(item["title"] == "历史日程采用结果" for item in adopted)
    assert any(item["title"] == "正式日程采用结果" for item in adopted)
    before = runtime.store.get("life_days", f"{date}:global")
    with pytest.raises(ValueError):
        await runtime.action({"action": "regenerate_day", "date": "2000-01-01"})
    runtime.debug.clear()
    assert runtime.store.list("life_day_history") == history
    assert runtime.store.get("life_days", f"{date}:global") == before


@pytest.mark.parametrize(
    "parameters", [{"tools": []}, {"api_key": "secret"}, {"base_url": "https://example.test"}]
)
async def test_trial_rejects_transport_and_execution_overrides(runtime, parameters):
    with pytest.raises(ValueError):
        await runtime.test_request({"parameters": parameters})
    assert not runtime.host.requests


async def test_clear_inflight_and_disable_debug_do_not_resurrect_records(runtime):
    runtime.host.release = asyncio.Event()
    job = asyncio.create_task(
        runtime.test_request({"prompt": "test", "task": "custom", "module": "debug"})
    )
    await runtime.host.entered.wait()
    runtime.debug.clear()
    runtime.host.release.set()
    await job
    assert not runtime.store.list("debug_records")
    await runtime.update_settings({"modules": {"debug": False}})
    with pytest.raises(ValueError):
        await runtime.test_request({"prompt": "another"})
    await runtime.complete("memory.reflect", "memory", "Extract.", {"material": "x"})
    assert not runtime.store.list("debug_records")
    assert runtime.last_requests[("memory.reflect", "global")]


async def test_tool_and_transport_records_preserve_full_results(runtime):
    result = await runtime.call_tool("tool", {"query": "q"}, "global", "external")
    assert runtime.store.list("debug_records")[0]["response"] == result
    await runtime.send_message("qq:GroupMessage:100", "A complete message.")
    row = runtime.store.list("debug_records")[0]
    assert row["status"] == "sent" and row["request"]["text"] == "A complete message."
    assert json_value({"api_key": "secret", "material": "normal"}) == {
        "api_key": "[认证信息已隐藏]",
        "material": "normal",
    }


async def test_whitelist_uses_destination_but_preserves_actual_scope(runtime):
    actual = "qq:GroupMessage:42_100"
    assert await runtime.scope_allowed(actual)
    runtime.note_scope(actual)
    assert runtime.social._sessions()[0]["scope"] == actual
    assert not await runtime.scope_allowed("qq:GroupMessage:42_200")
    runtime.memory.remember("A private appointment", scope=actual)
    assert "private appointment" not in await runtime.context_text("qq:GroupMessage:43_100")


def test_new_defaults_and_legacy_configuration_migration():
    config = settings_from(
        {
            "life": {"max_activities": 12},
            "news": {"feeds": ["https://example.test/rss"]},
            "sessions": [{"platform_id": "qq", "type": "group", "number": "123", "weight": 2}],
        }
    )
    assert [
        config["life"][k] for k in ("activity_count", "news_count", "search_count", "social_count")
    ] == [10, 2, 2, 3]
    assert config["news"]["sources"][0]["url"] == "https://example.test/rss"
    assert config["sessions"][0]["umo"] == "qq:GroupMessage:123"
    assert len(settings_from()["news"]["sources"]) == 6
    assert settings_from()["daily_digest"]["sources"][0]["time"] == "12:00"
    form = settings_from()
    form["daily_digest"]["sources"][0]["keywords"] = ["早报", "日报"]
    assert settings_from(form)["daily_digest"]["sources"][0]["keywords"] == "早报 日报"
    for patch in (
        {"life": {"news_count": 49}},
        {"life": {"social_count": 1.5}},
        {"debug": {"retain_per_category": 0}},
    ):
        with pytest.raises(ValueError):
            settings_from(patch)
