"""Lifecycle and request-hook checks against installed AstrBot classes."""

import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def plugin_module():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    return importlib.import_module("astrbot_plugin_living_world.main")


async def test_real_plugin_load_page_and_unload_preserve_data(plugin_module, tmp_path, monkeypatch):
    from astrbot.api.star import Context
    from astrbot.api.web import PluginRequest, bind_request_context
    from starlette.requests import Request

    ctx = Context.__new__(Context)
    ctx.registered_web_apis = [("/another/api", None, ["GET"], "Other plugin")]
    ctx.persona_manager = SimpleNamespace(personas_v3=[])
    ctx.get_all_providers = list
    monkeypatch.setattr(plugin_module.StarTools, "get_data_dir", lambda _: tmp_path)
    plugin = plugin_module.Main(ctx)
    await plugin.initialize()
    assert len(ctx.registered_web_apis) == 6
    settings_route = next(row[1] for row in ctx.registered_web_apis if row[0].endswith("/settings"))
    body = json.dumps({"character": {"profile": "测试角色资料"}}).encode()

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request(
        {"type": "http", "method": "POST", "path": "/settings", "headers": [], "query_string": b""},
        receive,
    )
    with bind_request_context(PluginRequest(request)):
        response = await settings_route()
    assert response.status_code == 200
    assert json.loads(response.body)["settings"]["character"]["profile"] == "测试角色资料"
    state = next(row[1] for row in ctx.registered_web_apis if row[0].endswith("/state"))
    assert json.loads((await state()).body)["version"] == plugin_module.__version__
    await plugin.terminate()
    assert len(ctx.registered_web_apis) == 1
    assert not plugin.runtime.tasks
    assert not plugin.runtime.background
    assert plugin.runtime.scheduler.done()
    assert (await settings_route()).status_code == 503
    plugin2 = plugin_module.Main(ctx)
    await plugin2.initialize()
    try:
        assert plugin2.runtime.settings["character"]["profile"] == "测试角色资料"
        assert len(ctx.registered_web_apis) == 6
    finally:
        await plugin2.terminate()


async def test_unmanaged_reply_keeps_other_tools_and_does_not_inject(plugin_module):
    from astrbot.api.provider import ProviderRequest
    from astrbot.core.agent.tool import FunctionTool, ToolSet

    plugin = plugin_module.Main(SimpleNamespace())
    plugin.runtime = SimpleNamespace(scope_status=AsyncMock(return_value={"allowed": False}))
    schema = {"type": "object", "properties": {}}
    owned = FunctionTool(name="living_world_social", description="social", parameters=schema)
    unrelated = FunctionTool(name="unrelated", description="other", parameters=schema)
    original = ToolSet([owned, unrelated])
    req = ProviderRequest(system_prompt="Keep original persona", func_tool=original)
    await plugin.augment(SimpleNamespace(unified_msg_origin="qq:GroupMessage:999"), req)
    assert req.system_prompt == "Keep original persona"
    assert [tool.name for tool in req.func_tool.tools] == ["unrelated"]
    assert len(original.tools) == 2


async def test_observe_tracks_real_group_scope_with_interjection_off(plugin_module, tmp_path):
    from test_chat import Event
    from test_runtime import FakeHost

    from living_world.runtime import Runtime

    host = FakeHost()
    host.session_persona = AsyncMock(return_value="student")
    runtime = Runtime(tmp_path / "chat.sqlite", host)
    await runtime.update_settings(
        {"persona_id": "student", "sessions": [{"umo": "qq:GroupMessage:100"}]}
    )
    plugin = plugin_module.Main(SimpleNamespace())
    plugin.runtime = runtime
    event = Event("qq:GroupMessage:42_100", "群里讨论数学")
    try:
        await plugin.observe(event)
        assert runtime.chat.resolve("qq:GroupMessage:100") == event.unified_msg_origin
        assert "群里讨论数学" in (await runtime.chat.history(event.unified_msg_origin))["text"]
        assert not runtime.enabled("interjection") and not host.sent
    finally:
        await runtime.stop()


async def test_late_host_response_does_not_access_closed_store(plugin_module):
    from unittest.mock import Mock

    plugin = plugin_module.Main(SimpleNamespace())
    plugin.runtime = SimpleNamespace(
        stopped=True, enabled=lambda _: False, debug=SimpleNamespace(finish=Mock())
    )
    event = SimpleNamespace(get_extra=lambda _: {"id": "running-request"})
    await plugin.remember_reply(event, SimpleNamespace(completion_text="late"))
    plugin.runtime.debug.finish.assert_not_called()
