"""Independent regression checks for configuration changes across queued work."""

import asyncio
import copy
from types import SimpleNamespace

import pytest

from living_world.host import AstrBotHost
from living_world.runtime import Runtime


class ReviewHost:
    def __init__(self):
        self.calls = []

    async def persona(self, persona_id):
        return f"Persona: {persona_id}"

    async def session_persona(self, scope):
        return "student"

    async def generate(self, provider, prompt, system, scope):
        self.calls.append((provider, prompt, system, scope))
        return "ok", {}


@pytest.fixture
async def review_runtime(tmp_path):
    runtime = Runtime(tmp_path / "review.sqlite", ReviewHost())
    await runtime.update_settings({"persona_id": "student"})
    yield runtime
    await runtime.stop()


async def test_queued_model_request_does_not_start_after_module_disable(review_runtime):
    runtime = review_runtime
    await runtime.model_semaphore.acquire()
    await runtime.model_semaphore.acquire()
    task = asyncio.create_task(runtime.generate("memory", "Queued extraction"))
    await asyncio.sleep(0)
    await runtime.update_settings({"modules": {"memory": False}})
    runtime.model_semaphore.release()
    runtime.model_semaphore.release()
    outcome = await asyncio.gather(task, return_exceptions=True)
    assert runtime.host.calls == [], "Disabling a module must also invalidate queued model work"
    assert isinstance(outcome[0], (ValueError, asyncio.CancelledError))


async def test_invalid_backup_does_not_change_live_settings(review_runtime):
    runtime = review_runtime
    settings = copy.deepcopy(runtime.settings)
    backup = copy.deepcopy(runtime.export())
    backup["settings"]["character"]["profile"] = "Changed by invalid backup"
    backup["records"] = [{"malformed": True}]
    with pytest.raises((KeyError, TypeError, ValueError)):
        await runtime.restore(backup)
    assert runtime.settings == settings, "Reject invalid backup before changing live settings"


async def test_queued_request_does_not_use_stale_persona_after_settings_change(review_runtime):
    runtime = review_runtime
    await runtime.model_semaphore.acquire()
    await runtime.model_semaphore.acquire()
    task = asyncio.create_task(runtime.generate("memory", "Queued extraction"))
    await asyncio.sleep(0)
    await runtime.update_settings({"persona_id": "new-persona", "models": {"default": "new-model"}})
    runtime.model_semaphore.release()
    runtime.model_semaphore.release()
    await asyncio.gather(task, return_exceptions=True)
    assert not runtime.host.calls or all(
        call[0] == "new-model" and "Persona: new-persona" in call[2] for call in runtime.host.calls
    ), "Queued work must be cancelled or rebuilt for the newly bound persona"


async def test_host_background_event_works_with_real_astrbot_tool_executor():
    from astrbot.core.agent.tool import FunctionTool
    from astrbot.core.star.context import Context

    captured = []

    async def search(event, query):
        captured.append((event.unified_msg_origin, event.get_group_id(), query))
        return "A factual result https://example.test/result"

    tool = FunctionTool(
        name="review_search",
        description="Read-only test search",
        parameters={"type": "object", "properties": {"query": {"type": "string"}}},
        handler=search,
    )
    context = object.__new__(Context)
    context.provider_manager = SimpleNamespace(
        llm_tools=SimpleNamespace(get_func=lambda name: tool)
    )
    host = AstrBotHost(context)
    text = await host.call_tool("review_search", {"query": "stars"}, "qq:GroupMessage:100")
    assert "factual result" in text
    assert captured == [("qq:GroupMessage:100", "100", "stars")]
