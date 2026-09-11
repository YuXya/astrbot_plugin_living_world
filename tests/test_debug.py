"""Regression coverage for diagnostic retention and template management."""

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


async def test_drive_module_switch_freezes_growth_debits_and_injection_but_allows_admin_edits(
    runtime,
):
    import copy

    clock = [0.0]
    runtime.drives.clock = lambda: clock[0]
    runtime.drives.rebase()
    await runtime.action({"action": "set_drive_value", "id": "loneliness", "value": 20})
    runtime.drives.set_value("energy", 30)
    version = runtime.config_version
    clock[0] = 1800
    await runtime.update_settings({"modules": {"drives": False}})
    assert runtime.config_version == version
    disabled = runtime.drives.snapshot()
    assert not disabled["enabled"]
    assert disabled["meters"]["loneliness"]["value"] == 25
    assert disabled["meters"]["energy"]["value"] == 35
    clock[0] += 7200
    runtime.drives.debit("disabled-round", "social")
    runtime.drives.debit("disabled-news", "news")
    assert runtime.drives.snapshot() == disabled
    assert not runtime.drives.thoughts()
    await runtime.action({"action": "set_drive_value", "id": "loneliness", "value": 50})
    config = copy.deepcopy(disabled["meters"]["loneliness"]["config"])
    config["growth_per_hour"] = 20
    await runtime.action({"action": "save_drive_settings", "id": "loneliness", "config": config})
    clock[0] += 3600
    assert runtime.drives.snapshot()["meters"]["loneliness"]["value"] == 50
    await runtime.update_settings({"modules": {"drives": True}})
    clock[0] += 1800
    assert runtime.drives.snapshot()["meters"]["loneliness"]["value"] == 60
    assert runtime.drives.snapshot()["meters"]["energy"]["value"] == 40
    assert not runtime.host.requests and not runtime.host.sent


async def test_legacy_energy_state_edit_is_rejected_and_cannot_change_drive(runtime):
    before = runtime.drives.snapshot()["meters"]["energy"]["display_value"]
    with pytest.raises(ValueError):
        await runtime.action({"action": "update_state", "patch": {"energy": 5}})
    assert runtime.drives.snapshot()["meters"]["energy"]["display_value"] == before
    assert "energy" not in runtime.life.state()


async def test_drive_checkpoint_runs_while_life_scheduler_is_waiting(runtime, monkeypatch):
    clock = [0.0]
    runtime.drives.clock = lambda: clock[0]
    runtime.drives.rebase()
    runtime.drives.set_value("loneliness", 0)
    runtime.drives.set_value("energy", 70)
    life_waiting, checkpoint_done = asyncio.Event(), asyncio.Event()
    original_sleep = asyncio.sleep
    waits = 0

    async def waiting_schedule():
        life_waiting.set()
        await asyncio.Event().wait()

    async def checkpoint_sleep(seconds):
        nonlocal waits
        if seconds != 60:
            return await original_sleep(seconds)
        waits += 1
        if waits == 1:
            clock[0] = 60
            return await original_sleep(0)
        checkpoint_done.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(runtime, "_loop", waiting_schedule)
    monkeypatch.setattr("living_world.runtime.asyncio.sleep", checkpoint_sleep)
    await runtime.start()
    await asyncio.wait_for(life_waiting.wait(), 2)
    await asyncio.wait_for(checkpoint_done.wait(), 2)
    stored = runtime.store.get("drive_state", "current")["values"]
    assert stored["loneliness"] == pytest.approx(1 / 6)
    assert stored["energy"] == pytest.approx(70 + 1 / 6)
    assert not runtime.scheduler.done()
    assert not runtime.host.requests


async def test_restore_keeps_newer_drive_values_and_debit_receipts_with_all_modules_off(runtime):
    import copy

    clock = [0.0]
    runtime.drives.clock = lambda: clock[0]
    runtime.drives.rebase()
    runtime.drives.set_value("energy", 80)
    old_settings, old_records = copy.deepcopy(runtime.settings), runtime.store.export()
    runtime.drives.set_value("energy", 40)
    runtime.drives.debit("already-started", "search")
    receipt = runtime.store.get("drive_debits", "already-started")
    await runtime.restore(
        {"format": "living-world", "version": 1, "settings": old_settings, "records": old_records}
    )
    assert not any(runtime.settings["modules"].values())
    assert runtime.drives.snapshot()["meters"]["energy"]["value"] == 30
    assert runtime.store.get("drive_debits", "already-started") == receipt
    await runtime.update_settings({"modules": {"drives": True}})
    runtime.drives.debit("already-started", "search")
    assert runtime.drives.snapshot()["meters"]["energy"]["value"] == 30


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


async def test_clear_inflight_and_disable_debug_do_not_resurrect_records(runtime):
    runtime.host.release = asyncio.Event()
    job = asyncio.create_task(
        runtime.complete("memory.reflect", "memory", "Extract.", {"material": "x"})
    )
    await runtime.host.entered.wait()
    runtime.debug.clear()
    runtime.host.release.set()
    await job
    assert not runtime.store.list("debug_records")
    await runtime.update_settings({"modules": {"debug": False}})
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
    assert config["life"]["activity_count"] == 10
    assert not {"news_count", "search_count", "social_count"} & config["life"].keys()
    assert config["modules"]["drives"]
    assert set(config["drives"]) == {"loneliness", "energy"}
    assert config["news"]["sources"][0]["url"] == "https://example.test/rss"
    assert config["sessions"][0]["umo"] == "qq:GroupMessage:123"
    assert len(settings_from()["news"]["sources"]) == 6
    assert settings_from()["daily_digest"]["sources"][0]["time"] == "12:00"
    form = settings_from()
    form["daily_digest"]["sources"][0]["keywords"] = ["早报", "日报"]
    assert settings_from(form)["daily_digest"]["sources"][0]["keywords"] == "早报 日报"
    for patch in (
        {"life": {"activity_count": 0}},
        {"life": {"activity_count": 1.5}},
        {"debug": {"retain_per_category": 0}},
    ):
        with pytest.raises(ValueError):
            settings_from(patch)
