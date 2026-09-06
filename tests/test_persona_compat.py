"""Routing contracts using host persona resolvers instead of always-successful stubs."""

import ast
import copy
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from astrbot.api.provider import ProviderRequest
from astrbot.core import persona_mgr
from test_chat import Event, consume, runner_for

pytest_plugins = ("test_chat",)

TARGET = "default:FriendMessage:773896729"


@pytest.fixture(params=["4.27.5", "4.28"])
def host_resolver(request, world, monkeypatch):
    runtime, _, _ = world
    context = runtime.host.context
    config = context.get_config(TARGET)
    config["agent_runner"] = {
        "runner_type": "local",
        "config": {"persona": {"persona_id": "student"}},
    }
    manager = context.persona_manager
    manager.acm = SimpleNamespace(get_conf=context.get_config)

    async def get_rules(*, scope, scope_id, **kwargs):
        return {"persona_id": context.test_rules.get(scope_id)}

    if request.param == "4.27.5":
        source_path = (
            Path(__file__).resolve().parents[1] / "dist/compat/astrbot-4.27.5-persona_mgr.py"
        )
        if not source_path.exists():
            pytest.skip("Official 4.27.5 source not cached; see validation instructions")
        source = source_path.read_bytes()
        assert (
            hashlib.sha256(source).hexdigest()
            == "14bfc9413f37ce3ab09e1a11233525874929b6707dcd41ee4c554f3b83633606"
        )
        tree = ast.parse(source.decode("utf-8"))
        method = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "resolve_selected_persona"
        )
        module = ast.Module(
            body=[
                ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
                method,
            ],
            type_ignores=[],
        )
        ast.fix_missing_locations(module)
        namespace = {"sp": SimpleNamespace(get_async=get_rules)}
        exec(compile(module, str(source_path), "exec"), namespace)  # noqa: S102 - One hash-verified official method, isolated test namespace.
        resolver = namespace["resolve_selected_persona"]
    else:
        monkeypatch.setattr(persona_mgr, "sp", SimpleNamespace(get_async=get_rules))
        resolver = persona_mgr.PersonaManager.resolve_selected_persona
    manager.resolve_selected_persona = MethodType(resolver, manager)
    return request.param


@pytest.mark.parametrize(
    "conversation,forced,default,code,source",
    [
        (None, None, "student", "allowed", "host_default"),
        ("student", None, "other", "allowed", "conversation"),
        ("other", "student", "other", "allowed", "session_rule"),
        ("[%None]", None, "student", "persona_disabled", "conversation"),
        (None, None, None, "persona_unconfigured", "host_default"),
        ("deleted", None, "student", "persona_missing", "conversation"),
        ("other", None, "student", "persona_mismatch", "conversation"),
    ],
)
async def test_real_resolver_precedence(
    world, host_resolver, conversation, forced, default, code, source
):
    runtime, manager, _ = world
    await runtime.update_settings({"sessions": [{"umo": TARGET}]})
    cid = await manager.new_conversation(TARGET, persona_id=conversation)
    await manager.update_conversation(TARGET, cid, [{"role": "user", "content": "旧消息"}] * 24)
    context = runtime.host.context
    context.test_rules[TARGET] = forced
    settings = context.get_config(TARGET)
    settings["provider_settings"]["default_personality"] = default
    settings["agent_runner"]["config"]["persona"]["persona_id"] = default
    if (
        host_resolver == "4.27.5"
        and conversation is None
        and forced is None
        and default == "student"
    ):
        legacy = await context.persona_manager.resolve_selected_persona(
            umo=TARGET, conversation_persona_id=None, platform_name="aiocqhttp"
        )
        assert legacy[0] is None, "Omitting provider_settings must reproduce the reported failure"
    result = await runtime.chat.inspect(TARGET)
    assert result["history_count"] == 24 and result["history_status"] == "found"
    assert result["reason_code"] == code and result["persona_source"] == source
    assert result["allowed"] == (code == "allowed") == await runtime.scope_allowed(TARGET)
    assert manager.created == 1


async def test_daily_schedule_reaches_actual_provider_through_message_hook(
    world, host_resolver, monkeypatch
):
    import importlib
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    plugin_module = importlib.import_module("astrbot_plugin_living_world.main")
    runtime, manager, provider = world
    await runtime.update_settings({"sessions": [{"umo": TARGET}]})
    cid = await manager.new_conversation(TARGET)
    await manager.update_conversation(TARGET, cid, [{"role": "user", "content": "之前的私聊"}] * 24)
    now = datetime.fromisoformat("2026-09-06T12:00:00+08:00")
    monkeypatch.setattr(runtime.life, "_now", lambda value=None: value or now)
    activity = {
        "id": "math",
        "date": str(now.date()),
        "start": (now - timedelta(hours=1)).isoformat(),
        "end": (now + timedelta(hours=1)).isoformat(),
        "title": "莉莉在上数学课",
        "status": "running",
        "scope": "global",
        "scope_overrides": {
            TARGET: {"description": "下课后和优夏聊数学"},
            "default:FriendMessage:999": {"description": "不能外泄的私人约定"},
        },
    }
    runtime.store.put("activities", activity["id"], activity)
    runtime.store.put(
        "activities",
        "private",
        {
            **activity,
            "id": "private",
            "scope": "default:FriendMessage:999",
            "title": "不能泄露的私人日程",
        },
    )
    before = copy.deepcopy(manager.rows[cid].history)
    assert (await runtime.chat.inspect(TARGET))["context_status"] == {}
    event = Event(TARGET, "莉莉现在日程是什么？")
    plugin = plugin_module.Main(runtime.host.context)
    plugin.runtime = runtime
    await plugin.observe(event)
    req = ProviderRequest(
        prompt=event.message_str,
        system_prompt="Student",
        contexts=json.loads(before),
        conversation=manager.rows[cid],
    )
    await plugin.augment(event, req)
    check = await runtime.chat.inspect(TARGET)
    assert check["allowed"] and check["persona_source"] == "host_default"
    assert check["context_status"]["last_attempt"]["status"] == "prepared"
    assert "last_injected" not in check["context_status"]
    runner = await runner_for(world, event, req)
    await consume(runner)
    actual = json.dumps(provider.calls[0], ensure_ascii=False)
    assert all(
        text in actual
        for text in (
            "莉莉在上数学课",
            "下课后和优夏聊数学",
            "2026-09-06T12:00:00+08:00",
            "今日日程",
            "当前活动",
        )
    )
    assert "不能外泄" not in actual and "不能泄露" not in actual
    assert manager.rows[cid].history == before
    status = (await runtime.chat.inspect(TARGET))["context_status"]
    assert status["last_injected"]["status"] == "injected"
    assert status["last_injected"]["turn_id"] == event.get_extra("living_world_trace")["id"]
    assert any(r["task"] == "reply.model" for r in runtime.store.list("debug_records"))
    runtime.debug.clear()
    assert (await runtime.chat.inspect(TARGET))["context_status"] == status


@pytest.mark.parametrize(
    "problem,code",
    [
        ("connection", "connection_missing"),
        ("platform", "platform_unsupported"),
        ("read", "resolution_error"),
        ("module", "module_disabled"),
    ],
)
async def test_inspection_separates_route_failure_from_history(world, problem, code):
    runtime, manager, _ = world
    await runtime.update_settings({"sessions": [{"umo": TARGET}]})
    cid = await manager.new_conversation(TARGET)
    await manager.update_conversation(TARGET, cid, [{"role": "user", "content": "已有历史"}])
    context = runtime.host.context
    if problem == "connection":
        context.test_platforms.pop("default")
    elif problem == "platform":
        context.test_platforms["default"].meta = lambda: SimpleNamespace(
            id="default", name="webchat"
        )
    elif problem == "read":
        context.persona_manager.resolve_selected_persona = AsyncMock(side_effect=OSError("offline"))
    else:
        await runtime.update_settings({"modules": {"reply": False}})
    result = await runtime.chat.inspect(TARGET)
    assert not result["allowed"] and result["reason_code"] == code
    assert result["history_status"] == "found"


async def test_missing_and_disabled_schedule_are_explicit(world):
    runtime, _, _ = world
    await runtime.update_settings({"sessions": [{"umo": TARGET}]})
    data = json.loads(await runtime.context_text(TARGET))
    assert data["schedule"]["status"] == "missing" and data["current_time"]
    await runtime.update_settings({"modules": {"life": False}})
    data = json.loads(await runtime.context_text(TARGET))
    assert data["schedule"]["status"] == "disabled" and not data["schedule"]["activities"]
