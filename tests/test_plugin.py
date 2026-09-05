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
    assert json.loads((await state()).body)["version"] == "0.1.0"
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
    plugin.runtime = SimpleNamespace(scope_allowed=AsyncMock(return_value=False))
    schema = {"type": "object", "properties": {}}
    owned = FunctionTool(name="living_world_social", description="social", parameters=schema)
    unrelated = FunctionTool(name="unrelated", description="other", parameters=schema)
    original = ToolSet([owned, unrelated])
    req = ProviderRequest(system_prompt="Keep original persona", func_tool=original)
    await plugin.augment(SimpleNamespace(unified_msg_origin="qq:GroupMessage:999"), req)
    assert req.system_prompt == "Keep original persona"
    assert [tool.name for tool in req.func_tool.tools] == ["unrelated"]
    assert len(original.tools) == 2
