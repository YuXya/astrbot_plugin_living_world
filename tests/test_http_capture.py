"""HTTP evidence from real AstrBot providers and SDKs against a local server."""

import ast
import asyncio
import copy
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from aiohttp import web
from astrbot.api.provider import ProviderRequest
from astrbot.core.agent.message import dump_messages_with_checkpoints
from astrbot.core.agent.tool import FunctionTool, ToolSet
from astrbot.core.provider.sources.openai_responses_source import ProviderOpenAIResponses
from astrbot.core.provider.sources.openai_source import ProviderOpenAIOfficial
from mcp.types import CallToolResult, TextContent
from openai import APIStatusError
from test_chat import GROUP, PRIVATE, Event, consume, runner_for
from test_layout import content_text, moved

from living_world.chat import GROUP_REPLY_HEADING
from living_world.config import DEFAULT_GROUP_REPLY_PROMPT
from living_world.context_catalog import BLOCK_NAMES
from living_world.layout import DEFAULT_LAYOUT

pytest_plugins = ("test_chat",)

TARGET = "default:FriendMessage:773896729"
SECRET = "sk-local-wire-test-only-not-a-real-key"


def completion(text="数学课还没结束", *, tool=False):
    message = {"role": "assistant", "content": text, "reasoning_content": "接口返回的思考"}
    if tool:
        message["content"] = None
        message["tool_calls"] = [
            {
                "id": "call_math",
                "type": "function",
                "function": {"name": "lookup", "arguments": '{"query":"数学"}'},
            }
        ]
    return {
        "id": "chatcmpl-local",
        "object": "chat.completion",
        "created": 1788678000,
        "model": "local-wire-model",
        "choices": [
            {"index": 0, "message": message, "finish_reason": "tool_calls" if tool else "stop"}
        ],
        "usage": {"prompt_tokens": 19, "completion_tokens": 8, "total_tokens": 27},
        "future_provider_field": {"keep": [1, "原始未知字段", None]},
    }


def response_api(text="Responses 的原始正文"):
    return {
        "id": "resp_local",
        "object": "response",
        "created_at": 1788678000,
        "model": "local-wire-model",
        "status": "completed",
        "output": [
            {
                "id": "msg_local",
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }
        ],
        "usage": {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14},
        "future_provider_field": {"keep": "Responses 未知字段"},
    }


def sse_chunks():
    parts = []
    for delta, finish in [
        ({"role": "assistant", "reasoning_content": "先想"}, None),
        ({"content": "你好"}, None),
        ({"content": "，世界"}, None),
        ({}, "stop"),
    ]:
        parts.append(
            {
                "id": "chatcmpl-stream-local",
                "object": "chat.completion.chunk",
                "created": 1788678000,
                "model": "local-wire-model",
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                "stream_extra": "保留事件未知字段",
            }
        )
    parts.append(
        {
            "id": "chatcmpl-stream-local",
            "object": "chat.completion.chunk",
            "created": 1788678000,
            "model": "local-wire-model",
            "choices": [],
            "usage": {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14},
        }
    )
    return ["data: " + json.dumps(part, ensure_ascii=False) + "\n\n" for part in parts] + [
        "data: [DONE]\n\n"
    ]


class WireServer:
    def __init__(self):
        self.records = []
        self.handler = self.default_handler
        self.arrived = asyncio.Event()

    async def dispatch(self, request):
        row = {"path": request.path, "request_body": await request.text()}
        self.records.append(row)
        self.arrived.set()
        return await self.handler(request, row)

    def reply(self, row, value, *, status=200):
        row["response_body"] = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        return web.Response(
            text=row["response_body"], content_type="application/json", status=status
        )

    async def default_handler(self, request, row):
        value = response_api() if request.path.endswith("/responses") else completion()
        return self.reply(row, value)


@pytest.fixture
async def wire_server():
    server = WireServer()
    app = web.Application()
    app.router.add_post("/v1/{tail:.*}", server.dispatch)
    runner = web.AppRunner(app, shutdown_timeout=1)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    server.url = f"http://127.0.0.1:{port}/v1"
    try:
        yield server
    finally:
        await runner.cleanup()


@pytest.fixture
async def real_providers(world, wire_server):
    runtime, manager, _ = world
    providers = []
    context = runtime.host.context
    context.get_all_providers = lambda: providers
    context.get_provider_by_id = lambda provider_id: next(
        (p for p in providers if p.meta().id == provider_id), None
    )

    def make(*, responses=False, extra=None, provider_name="openai", provider_cls=None):
        cls = provider_cls or (ProviderOpenAIResponses if responses else ProviderOpenAIOfficial)
        provider = cls(
            {
                "id": "model" if not providers else f"model-{len(providers)}",
                "type": "openai_responses" if responses else "openai_chat_completion",
                "provider": provider_name,
                "key": [SECRET],
                "api_base": wire_server.url,
                "model": "local-wire-model",
                "timeout": 5,
                "max_context_tokens": 0,
                "custom_extra_body": extra or {},
            },
            {},
        )
        provider.client.max_retries = 0
        providers.append(provider)
        return runtime, manager, provider

    yield make
    runtime.chat.close()
    for provider in providers:
        await provider.client.close()


async def call_background(real_world, *, prompt="查看数学课日程", **kwargs):
    runtime, _, provider = real_world
    runtime.chat.install()
    request = {"task": "wire.test", "scope": TARGET, "module": "reply"}
    parent = runtime.debug.begin("wire.test", {"prompt": prompt}, module="reply", scope=TARGET)
    result = await runtime.chat.audit.background(
        request,
        parent,
        provider.text_chat(prompt=prompt, request_max_retries=1, **kwargs),
    )
    runtime.debug.finish(parent, {"completion_text": result.completion_text})
    return result


def model_records(runtime):
    return [
        row
        for row in runtime.store.list("debug_records")
        if row["task"] in {"reply.model", "provider.wire.test"}
    ]


def http_calls(runtime):
    return [call for row in model_records(runtime) for call in row.get("http_calls", [])]


@pytest.mark.parametrize("provider_name", ["openai", "deepseek"])
async def test_wire_body_equals_server_body_and_preserves_unknown_fields(
    real_providers, wire_server, provider_name
):
    real_world = real_providers(
        extra={"vendor_option": {"preserve": "未知参数", "count": 7}, "temperature": 0.35},
        provider_name=provider_name,
    )
    tool = FunctionTool(
        name="lookup",
        description="Search math",
        parameters={"type": "object", "properties": {"query": {"type": "string"}}},
    )
    result = await call_background(
        real_world,
        system_prompt="稳定角色资料",
        contexts=[{"role": "user", "content": "当前对话历史"}],
        func_tool=ToolSet([tool]),
    )
    runtime = real_world[0]
    calls = http_calls(runtime)
    assert len(calls) == len(wire_server.records) == 1
    call, actual = calls[0], wire_server.records[0]
    assert call["request_body"] == actual["request_body"]
    assert call["response_body"] == actual["response_body"]
    assert call["http_status"] == 200 and call["status"] == "success"
    body = json.loads(call["request_body"])
    assert body["temperature"] == 0.35
    assert body["tools"][0]["function"]["name"] == "lookup"
    assert body["vendor_option"] == {"preserve": "未知参数", "count": 7}
    assert result.completion_text == "数学课还没结束"
    assert json.loads(call["response_body"])["future_provider_field"]["keep"][1] == "原始未知字段"
    assert "接口返回的思考" in json.dumps(call["reading"], ensure_ascii=False)
    assert SECRET not in json.dumps(runtime.store.list("debug_records"))
    assert model_records(runtime)[0]["http_capture"] == "captured"


async def test_wire_authentication_fields_are_redacted_without_changing_message_content(
    real_providers, wire_server
):
    real_world = real_providers(extra={"api_key": SECRET, "vendor_option": "keep me"})
    ordinary_text = "这里讨论 api_key 字段；普通文字不能被清空"
    await call_background(real_world, prompt=ordinary_text)
    call = http_calls(real_world[0])[0]
    original = json.loads(wire_server.records[0]["request_body"])
    captured = json.loads(call["request_body"])
    assert original.pop("api_key") == SECRET
    assert captured.pop("api_key", "redacted") != SECRET
    assert captured == original
    assert ordinary_text in call["request_body"]
    assert SECRET not in json.dumps(real_world[0].store.list("debug_records"))


async def test_sdk_http_retry_has_separate_matching_request_and_error_response(
    real_providers, wire_server
):
    async def handler(request, row):
        if len(wire_server.records) == 1:
            return wire_server.reply(
                row, {"error": {"message": "temporary backend failure"}}, status=500
            )
        return wire_server.reply(row, completion("重试后成功"))

    wire_server.handler = handler
    real_world = real_providers()
    real_world[2].client.max_retries = 1
    await call_background(real_world)
    calls = http_calls(real_world[0])
    assert len(calls) == len(wire_server.records) == 2
    assert len({call["id"] for call in calls}) == 2
    assert [call["http_status"] for call in calls] == [500, 200]
    assert [call["status"] for call in calls] == ["failed", "success"]
    for call, actual in zip(calls, wire_server.records, strict=True):
        assert call["request_body"] == actual["request_body"]
        assert call["response_body"] == actual["response_body"]


async def test_non_json_api_failure_is_kept_as_body_not_synthesized_json(
    real_providers, wire_server
):
    async def handler(request, row):
        row["response_body"] = "<html>代理明确返回的失败正文</html>"
        return web.Response(text=row["response_body"], status=418, content_type="text/html")

    wire_server.handler = handler
    real_world = real_providers()
    with pytest.raises(APIStatusError):
        await call_background(real_world)
    calls = http_calls(real_world[0])
    assert len(calls) == 1 and calls[0]["status"] == "failed"
    assert calls[0]["response_body"] == wire_server.records[0]["response_body"]
    assert calls[0]["http_status"] == 418


async def test_streaming_preserves_sse_and_provider_still_gets_complete_answer(
    real_providers, wire_server
):
    events = sse_chunks()

    async def handler(request, row):
        row["response_body"] = "".join(events)
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        for event in events:
            await response.write(event.encode("utf-8"))
        await response.write_eof()
        return response

    wire_server.handler = handler
    real_world = real_providers()
    runner = await runner_for(real_world, Event(), streaming=True)
    await consume(runner)
    calls = http_calls(real_world[0])
    assert len(calls) == 1
    assert calls[0]["response_body"] == "".join(events)
    assert calls[0]["response_type"] == "sse"
    assert calls[0]["status"] == "success"
    assert runner.get_final_llm_resp().completion_text == "你好，世界"
    reading = json.dumps(calls[0]["reading"], ensure_ascii=False)
    assert "你好，世界" in reading and "先想" in reading
    assert "保留事件未知字段" in calls[0]["response_body"]


async def test_cancelled_stream_keeps_received_fragments_and_restores_client(
    real_providers, wire_server
):
    first = sse_chunks()[1]
    release = asyncio.Event()
    delivered = asyncio.Event()

    async def handler(request, row):
        row["response_body"] = first
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        await response.write(first.encode("utf-8"))
        delivered.set()
        await release.wait()
        return response

    wire_server.handler = handler
    real_world = real_providers()
    runtime, _, provider = real_world
    original = provider.client._client._send_single_request
    runner = await runner_for(real_world, Event(), streaming=True)
    task = asyncio.create_task(consume(runner))
    try:
        await asyncio.wait_for(delivered.wait(), 5)
        # Give the SDK a chance to consume the first event before cancellation.
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    calls = http_calls(runtime)
    assert len(calls) == 1 and calls[0]["response_body"] == first
    assert calls[0]["status"] in {"cancelled", "partial"}
    runtime.chat.close()
    assert provider.client._client._send_single_request == original


async def test_responses_api_records_actual_input_and_unparsed_response(
    real_providers, wire_server
):
    real_world = real_providers(responses=True, extra={"vendor_option": {"keep": True}})
    answer = await call_background(
        real_world, prompt="Responses 请求检查", system_prompt="稳定人格"
    )
    call = http_calls(real_world[0])[0]
    assert wire_server.records[0]["path"] == "/v1/responses"
    assert call["request_body"] == wire_server.records[0]["request_body"]
    assert call["response_body"] == wire_server.records[0]["response_body"]
    body = json.loads(call["request_body"])
    assert "input" in body and "messages" not in body and body["store"] is False
    assert body["vendor_option"] == {"keep": True}
    assert answer.completion_text == "Responses 的原始正文"
    assert "Responses 的原始正文" in json.dumps(call["reading"], ensure_ascii=False)


async def test_concurrent_managed_calls_do_not_capture_unmanaged_same_sdk_client(
    real_providers, wire_server
):
    real_world = real_providers()
    runtime, _, provider = real_world
    await runtime.update_settings({"sessions": [{"umo": TARGET}, {"umo": "qq:GroupMessage:100"}]})
    first = await runner_for(real_world, Event(TARGET, "轮次甲的独立内容"))
    second = await runner_for(real_world, Event("qq:GroupMessage:42_100", "轮次乙的独立内容"))
    await asyncio.gather(
        consume(first),
        consume(second),
        provider.text_chat(prompt="不接入插件的另一场合秘密", request_max_retries=1),
    )
    rows = model_records(runtime)
    assert len(rows) == 2 and len(wire_server.records) == 3
    assert len({row["turn_id"] for row in rows}) == 2
    assert all(len(row["http_calls"]) == 1 for row in rows)
    for row in rows:
        body = row["http_calls"][0]["request_body"]
        assert ("轮次甲的独立内容" in body) != ("轮次乙的独立内容" in body)
    assert "不接入插件的另一场合秘密" not in json.dumps(rows, ensure_ascii=False)


@pytest.mark.parametrize("scope", [PRIVATE, GROUP])
@pytest.mark.parametrize("custom_layout", [False, True])
async def test_tool_followup_captures_wire_calls_but_not_third_party_inner_call(
    real_providers, wire_server, scope, custom_layout
):
    async def handler(request, row):
        data = json.loads(row["request_body"])
        if any(message["role"] == "tool" for message in data["messages"]):
            answer = completion("工具结果已收到")
        elif "第三方工具内部秘密" in row["request_body"]:
            answer = completion("内部结果")
        else:
            answer = completion(tool=True)
        return wire_server.reply(row, answer)

    wire_server.handler = handler
    real_world = real_providers()
    runtime, _, provider = real_world
    if custom_layout:
        layout = moved(DEFAULT_LAYOUT, "group_reply", "system", "anchor.system")
        layout = moved(layout, "profile", "user", "anchor.user")
        await runtime.update_settings(
            {
                "context_layout": {"order": layout},
                "character": {"profile": "FROZEN_PROFILE"},
                "context_usage": {"limits": {"memory.related": 0, "memory.recent": 0}},
            }
        )
        runtime.memory.remember("数学 FROZEN_MEMORY", scope=scope, stable=True)

    class Executor:
        async def execute(self, **kwargs):
            if custom_layout:
                await runtime.update_settings(
                    {
                        "context_layout": {"order": DEFAULT_LAYOUT},
                        "context_usage": {"limits": {"memory.self": 0}},
                    }
                )
            await provider.text_chat(prompt="第三方工具内部秘密", request_max_retries=1)
            yield CallToolResult(content=[TextContent(type="text", text="数学工具的真实结果")])

    tool = FunctionTool(
        name="lookup",
        description="Search math",
        parameters={"type": "object", "properties": {"query": {"type": "string"}}},
    )
    runner = await runner_for(
        real_world,
        Event(scope, "查数学"),
        ProviderRequest(prompt="查数学", system_prompt="Student", func_tool=ToolSet([tool])),
        Executor(),
    )
    await consume(runner)
    rows, calls = model_records(runtime), http_calls(runtime)
    assert len(rows) == len(calls) == 2 and len(wire_server.records) == 3
    assert len({row["turn_id"] for row in rows}) == 1
    assert "第三方工具内部秘密" not in json.dumps(calls, ensure_ascii=False)
    assert any("数学工具的真实结果" in call["request_body"] for call in calls)
    assert runner.get_final_llm_resp().completion_text == "工具结果已收到"
    if custom_layout:
        for call in calls:
            messages = json.loads(call["request_body"])["messages"]
            user = content_text(
                next(
                    message["content"]
                    for message in reversed(messages)
                    if message["role"] == "user"
                )
            )
            system = content_text(
                next(message["content"] for message in messages if message["role"] == "system")
            )
            assert user.index("FROZEN_PROFILE") < user.index("查数学")
            assert call["request_body"].count("FROZEN_MEMORY") == 1
            assert user.count("【" + BLOCK_NAMES["memory"] + "】") == 1
            assert "FROZEN_PROFILE" not in system
            assert GROUP_REPLY_HEADING not in user
            assert (GROUP_REPLY_HEADING in system) == (scope == GROUP)
            if scope == GROUP:
                assert system.index(GROUP_REPLY_HEADING) < system.index("Student")
                assert call["request_body"].count(GROUP_REPLY_HEADING) == 1
    elif scope == GROUP:
        for call in calls:
            messages = json.loads(call["request_body"])["messages"]
            user = next(message for message in reversed(messages) if message["role"] == "user")
            assert user["content"][-1]["text"].endswith(DEFAULT_GROUP_REPLY_PROMPT)
            assert sum(GROUP_REPLY_HEADING in part.get("text", "") for part in user["content"]) == 1
            assert GROUP_REPLY_HEADING not in messages[0]["content"]


@pytest.mark.parametrize("responses", [False, True])
@pytest.mark.parametrize("scope", [PRIVATE, GROUP])
async def test_ordered_layout_matches_actual_http_and_excludes_saved_history(
    real_providers, wire_server, responses, scope
):
    real_world = real_providers(responses=responses)
    runtime, _, _ = real_world
    task = "chat.group" if scope == GROUP else "chat.private"
    guidance = "group_reply" if scope == GROUP else "private_reply"
    layout = moved(DEFAULT_LAYOUT, "memory.recent", "user", "memory")
    layout = moved(layout, "weather", "system", "anchor.system")
    layout = moved(layout, guidance, "system")
    selection = [
        key for key in runtime.settings["context_layout"]["tasks"][task] if key != "task.material"
    ]
    await runtime.update_settings(
        {
            "context_layout": {"order": layout, "tasks": {task: selection}},
            "modules": {"news": True, "weather": True},
            "context_usage": {"limits": {"memory.related": 0, "memory.recent": 1}},
            "reply": {
                "group_prompt": "GROUP_HTTP_RULE",
                "private_prompt": "PRIVATE_HTTP_RULE",
                "proactive_prompt": "PROACTIVE_HTTP_RULE",
            },
        }
    )
    runtime.memory.remember("MEMORY_SENTINEL mathematics", scope=scope, stable=True)
    hidden = runtime.memory.remember("UNCHECKED_SENTINEL mathematics", scope=scope, stable=False)
    runtime.memory.remember(
        "EXPERIENCE_SENTINEL",
        scope=scope,
        source="fiction",
        occurred_at=runtime.life._now().isoformat(),
    )
    runtime.store.put(
        "observations",
        "news",
        {"id": "news", "module": "weather", "scope": scope, "text": "WEATHER_SENTINEL"},
    )
    runtime.store.put(
        "observations",
        "secret",
        {
            "id": "secret",
            "module": "news",
            "scope": "qq:FriendMessage:999",
            "text": "PRIVATE_SECRET",
        },
    )
    req = ProviderRequest(prompt="mathematics", system_prompt="SYSTEM_ANCHOR")
    runner = await runner_for(real_world, Event(scope, "mathematics"), req)
    await consume(runner)
    raw = wire_server.records[0]["request_body"]
    assert http_calls(runtime)[0]["request_body"] == raw
    payload = json.loads(raw)
    messages = payload.get("messages", payload.get("input"))
    system = payload.get("instructions") or content_text(
        next(message["content"] for message in messages if message.get("role") == "system")
    )
    user = content_text(
        next(message["content"] for message in reversed(messages) if message.get("role") == "user")
    )
    assert system.index("WEATHER_SENTINEL") < system.index("SYSTEM_ANCHOR")
    assert user.index("EXPERIENCE_SENTINEL") < user.index("MEMORY_SENTINEL")
    assert "WEATHER_SENTINEL" not in user and "PRIVATE_SECRET" not in raw
    assert (GROUP_REPLY_HEADING in system) == (scope == GROUP)
    assert GROUP_REPLY_HEADING not in user
    assert "【" + BLOCK_NAMES[guidance] + "】" in system
    expected_rule = "GROUP_HTTP_RULE" if scope == GROUP else "PRIVATE_HTTP_RULE"
    assert expected_rule in system and expected_rule not in user
    assert "PROACTIVE_HTTP_RULE" not in raw and "UNCHECKED_SENTINEL" not in raw
    assert runtime.store.get("memories", hidden["id"])["access_count"] == 0
    assert all(
        raw.count(text) == 1
        for text in ("WEATHER_SENTINEL", "MEMORY_SENTINEL", "EXPERIENCE_SENTINEL")
    )
    saved = json.dumps(
        dump_messages_with_checkpoints(runner.run_context.messages), ensure_ascii=False
    )
    assert all(
        text not in saved
        for text in (
            "WEATHER_SENTINEL",
            "MEMORY_SENTINEL",
            "EXPERIENCE_SENTINEL",
            GROUP_REPLY_HEADING,
            expected_rule,
        )
    )
    assert req.system_prompt == "SYSTEM_ANCHOR"


@pytest.mark.parametrize("responses", [False, True])
@pytest.mark.parametrize("scope", [PRIVATE, GROUP])
async def test_independent_usage_and_selected_target_match_actual_http(
    real_providers, wire_server, responses, scope
):
    real_world = real_providers(responses=responses)
    runtime, _, _ = real_world
    layout = moved(DEFAULT_LAYOUT, "speaker", "system", "anchor.system")
    await runtime.update_settings(
        {
            "sessions": [{"umo": scope, "display_name": "本次目标称呼"}],
            "modules": {"news": True, "search": True, "bilibili": True, "daily_digest": True},
            "context_layout": {"order": layout},
            "context_usage": {
                "limits": {"memory.self": 2, "memory.related": 0, "memory.recent": 3}
            },
        }
    )
    for i in range(4):
        runtime.memory.remember(f"INDEPENDENT_PROFILE_{i}", stable=True, scope=scope)
    for i, module in enumerate(("news", "search", "bilibili", "daily_digest")):
        runtime.memory.remember(
            f"LATEST_SOURCE_{i}",
            scope=scope,
            source=module,
            occurred_at=1800000000 + i,
        )
    before = runtime.store.export()
    request = await runtime.prepare_request("social.message", "social", "写消息", {}, scope)
    assert runtime.store.export() == before
    assert runtime.host.context.send_message.await_count == 0
    sources = {row["block_id"]: row for row in request["sources"]}
    assert sources["memory"]["count"] == 2
    assert sources["memory.recent"]["count"] == sources["memory.recent"]["limit"] == 3
    await runtime.update_settings(
        {"context_usage": {"limits": {"memory.self": 0, "memory.recent": 0}}}
    )
    await call_background(
        real_world, prompt=request["prompt"], system_prompt=request["system_prompt"]
    )
    raw = wire_server.records[0]["request_body"]
    assert raw.count("INDEPENDENT_PROFILE_") == 2
    assert raw.count("LATEST_SOURCE_") == 3
    assert "LATEST_SOURCE_0" not in raw
    assert (
        raw.index("LATEST_SOURCE_3") < raw.index("LATEST_SOURCE_2") < raw.index("LATEST_SOURCE_1")
    )
    assert "本次目标称呼" in raw
    assert ("群聊名字：" if scope == GROUP else "目标名字：") in raw
    assert "目标群号：" not in raw and "目标QQ号：" not in raw
    assert ("整个群" if scope == GROUP else "一对一私聊") in raw
    assert scope not in raw
    for identifier in ("speaker", "memory", "memory.recent"):
        assert raw.count("【" + BLOCK_NAMES[identifier] + "】") == 1
    assert BLOCK_NAMES["speaker"] in request["system_prompt"]
    assert BLOCK_NAMES["speaker"] not in request["prompt"]
    assert "→" not in raw
    assert runtime.host.context.send_message.await_count == 0


@pytest.mark.parametrize("responses", [False, True])
async def test_memory_query_and_extraction_use_captured_real_http(
    real_providers, wire_server, responses
):
    runtime, _, provider = real_providers(responses=responses)

    async def generate(*, chat_provider_id, **arguments):
        assert chat_provider_id == provider.meta().id
        return await provider.text_chat(**arguments)

    runtime.host.context.llm_generate = generate
    runtime.chat.install()
    await runtime.update_settings(
        {
            "context_usage": {
                "limits": {
                    "memory.self": 0,
                    "memory.people": 0,
                    "memory.recent": 0,
                    "memory.related": 1,
                }
            }
        }
    )
    pet = runtime.memory.remember(
        "养了一只猫，名字叫团子。", tags=["猫", "宠物", "名字"], scope=PRIVATE
    )
    runtime.memory.remember("PRIVATE_OTHER_PERSON", scope="qq:FriendMessage:999")
    material = "今天查到猫咪需要保持饮水充足。"

    async def handler(request, row):
        value = (
            {"keywords": ["宠物", "猫", "名字"]}
            if len(wire_server.records) == 1
            else {
                "memories": [
                    {
                        "judgment": "知道猫咪需要保持饮水充足。",
                        "evidence": material,
                        "attribute": "事实属性",
                        "tags": ["猫", "饮水"],
                        "owner": "self",
                        "stable": False,
                    }
                ],
                "feedback": [],
            }
        )
        text = json.dumps(value, ensure_ascii=False)
        return wire_server.reply(row, response_api(text) if responses else completion(text))

    wire_server.handler = handler
    before = runtime.store.get("memories", pet["id"])
    selected = await runtime.memory.select_context(
        PRIVATE, query="家里的毛孩子叫什么？", task="chat.private", selection=["memory"]
    )
    assert [row["id"] for row in selected["memories"]] == [pet["id"]]
    assert len(wire_server.records) == 1
    assert "毛孩子" in wire_server.records[0]["request_body"]
    assert "PRIVATE_OTHER_PERSON" not in wire_server.records[0]["request_body"]
    assert runtime.store.get("memories", pet["id"]) == before

    runtime.memory.enqueue_material(
        material,
        scope=PRIVATE,
        source="chat",
        key="http-extraction-source",
        occurred_at="2026-09-10T17:00:00+08:00",
    )
    result = await runtime.memory.process_pending()
    assert result["processed"] == 1 and result["failed"] == 0
    assert len(wire_server.records) == 2, "Extraction does not recursively request query expansion"
    extracted = next(
        row for row in runtime.memory.recall("猫 饮水", scope=PRIVATE) if "饮水" in row["text"]
    )
    assert extracted["reasoning"] == material
    assert extracted["occurred_at"].startswith("2026-09-10")
    assert extracted["scope"] == PRIVATE and extracted["owner"] == "self"
    assert not runtime.store.list("memory_jobs")
    captured = [
        call
        for record in runtime.store.list("debug_records")
        for call in record.get("http_calls", [])
    ]
    assert len(captured) == 2
    assert {call["request_body"] for call in captured} == {
        row["request_body"] for row in wire_server.records
    }
    assert {call["response_body"] for call in captured} == {
        row["response_body"] for row in wire_server.records
    }
    assert "PRIVATE_OTHER_PERSON" not in json.dumps(captured, ensure_ascii=False)
    assert runtime.host.context.send_message.await_count == 0


@pytest.mark.parametrize("responses", [False, True])
async def test_editable_group_prompt_reaches_actual_http_user_tail(
    real_providers, wire_server, responses
):
    real_world = real_providers(responses=responses)
    runtime, _, _ = real_world
    custom = "只回答本轮一个重点。\n不要展开背景。"
    await runtime.update_settings({"reply": {"group_prompt": custom}})
    runner = await runner_for(real_world, Event(GROUP, "今天怎么样？"))
    await consume(runner)
    raw = wire_server.records[0]["request_body"]
    assert http_calls(runtime)[0]["request_body"] == raw
    payload = json.loads(raw)
    messages = payload.get("messages", payload.get("input"))
    user = next(message for message in reversed(messages) if message.get("role") == "user")
    assert user["content"][-1]["text"].endswith(GROUP_REPLY_HEADING + "\n" + custom)
    assert raw.count(GROUP_REPLY_HEADING) == 1
    assert GROUP_REPLY_HEADING not in payload.get("instructions", "")


async def test_daily_schedule_reaches_http_body_with_no_diagnostic_ids_or_private_leak(
    real_providers, wire_server, monkeypatch
):
    real_world = real_providers()
    runtime, manager, _ = real_world
    await runtime.update_settings({"sessions": [{"umo": TARGET}]})
    cid = await manager.new_conversation(TARGET)
    await manager.update_conversation(TARGET, cid, [{"role": "user", "content": "当前会话历史"}])
    history = copy.deepcopy(manager.rows[cid].history)
    now = datetime.fromisoformat("2026-09-06T12:00:00+08:00")
    monkeypatch.setattr(runtime.life, "_now", lambda value=None: value or now)
    runtime.store.put(
        "activities",
        "internal-activity-uuid",
        {
            "id": "internal-activity-uuid",
            "date": str(now.date()),
            "start": (now - timedelta(hours=1)).isoformat(),
            "end": (now + timedelta(hours=1)).isoformat(),
            "title": "莉莉在上数学课",
            "location": "教室",
            "status": "running",
            "scope": "global",
            "scope_overrides": {
                TARGET: {"description": "下课后和优夏聊数学"},
                "default:FriendMessage:999": {"description": "不可外泄的私人约定"},
            },
        },
    )
    runner = await runner_for(
        real_world,
        Event(TARGET, "莉莉现在日程是什么？"),
        ProviderRequest(
            prompt="莉莉现在日程是什么？",
            system_prompt="Student",
            contexts=json.loads(history),
            conversation=manager.rows[cid],
        ),
    )
    await consume(runner)
    body = wire_server.records[0]["request_body"]
    assert http_calls(runtime)[0]["request_body"] == body
    assert all(text in body for text in ["莉莉在上数学课", "下课后和优夏聊数学", "当前会话历史"])
    assert all(
        text not in body
        for text in [
            cid,
            "internal-activity-uuid",
            "conversation_id",
            "history_count",
            "history_status",
            "不可外泄的私人约定",
        ]
    )
    assert manager.rows[cid].history == history


async def test_debug_disabled_and_unload_restore_http_client_without_losing_reply(
    real_providers, wire_server
):
    real_world = real_providers()
    runtime, _, provider = real_world
    original = provider.client._client._send_single_request
    await call_background(real_world)
    assert len(http_calls(runtime)) == 1
    assert provider.client._client._send_single_request != original
    await runtime.update_settings({"modules": {"debug": False}})
    assert provider.client._client._send_single_request == original
    await call_background(real_world, prompt="调试关闭后的正常回复")
    assert len(http_calls(runtime)) == 1
    runtime.chat.close()
    assert provider.client._client._send_single_request == original
    assert len(wire_server.records) == 2


async def test_replaced_sdk_client_is_captured_and_both_clients_are_restored(
    real_providers, wire_server
):
    real_world = real_providers()
    runtime, _, provider = real_world
    old_client = provider.client
    old_send = old_client._client._send_single_request
    await call_background(real_world, prompt="替换前")
    provider.client = type(old_client)(
        api_key=SECRET,
        base_url=wire_server.url,
        max_retries=0,
        http_client=provider._create_http_client(provider.provider_config),
    )
    new_send = provider.client._client._send_single_request
    try:
        await call_background(real_world, prompt="替换后")
        calls = http_calls(runtime)
        assert len(calls) == len(wire_server.records) == 2
        assert {call["request_body"] for call in calls} == {
            row["request_body"] for row in wire_server.records
        }
        runtime.chat.close()
        assert old_client._client._send_single_request == old_send
        assert provider.client._client._send_single_request == new_send
    finally:
        await old_client.close()


async def test_cancellation_before_response_headers_has_no_fabricated_response_body(
    real_providers, wire_server
):
    release = asyncio.Event()

    async def handler(request, row):
        await release.wait()
        return wire_server.reply(row, completion())

    wire_server.handler = handler
    real_world = real_providers()
    task = asyncio.create_task(call_background(real_world))
    try:
        await asyncio.wait_for(wire_server.arrived.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    calls = http_calls(real_world[0])
    assert len(calls) == 1 and calls[0]["status"] == "cancelled"
    assert calls[0]["request_body"] == wire_server.records[0]["request_body"]
    assert calls[0]["response_body"] is None and calls[0]["http_status"] is None


@pytest.mark.parametrize("malformed_field", ["choices_null", "reasoning_object"])
async def test_sdk_or_reading_failure_cannot_discard_valid_json_http_evidence(
    real_providers, wire_server, malformed_field
):
    payload = completion()
    if malformed_field == "choices_null":
        payload["choices"] = None
    else:
        payload["choices"][0]["message"]["reasoning_content"] = {"unexpected": "保留原文"}

    async def handler(request, row):
        return wire_server.reply(row, payload)

    wire_server.handler = handler
    real_world = real_providers()
    # The real SDK/provider may reject the payload after its HTTP body was received.
    await asyncio.gather(call_background(real_world), return_exceptions=True)
    call = http_calls(real_world[0])[0]
    assert call["response_body"] == wire_server.records[0]["response_body"]
    assert json.loads(call["response_body"]) == payload
    assert call["http_status"] == 200
    if malformed_field == "reasoning_object":
        assert "解析失败" in call["reading"]["error"]


async def test_literal_done_in_stream_text_is_not_a_terminal_event(real_providers, wire_server):
    chunk = json.loads(sse_chunks()[1][6:].strip())
    chunk["choices"][0]["delta"]["content"] = "字面量 [DONE] 还没结束"
    body = "data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n"

    async def handler(request, row):
        row["response_body"] = body
        return web.Response(text=body, content_type="text/event-stream")

    wire_server.handler = handler
    real_world = real_providers()
    runner = await runner_for(real_world, Event(), streaming=True)
    await asyncio.gather(consume(runner), return_exceptions=True)
    call = http_calls(real_world[0])[0]
    assert call["response_body"] == body and call["status"] == "partial"
    assert call["reading"]["text"] == "字面量 [DONE] 还没结束"


@pytest.mark.parametrize("terminal,status", [("failed", "failed"), ("incomplete", "partial")])
async def test_responses_stream_failed_or_incomplete_preserves_raw_and_reason(
    real_providers, wire_server, terminal, status
):
    response = response_api("部分输出")
    response["status"] = terminal
    detail = (
        {"code": "server_error", "message": "接口声明失败"}
        if terminal == "failed"
        else {"reason": "max_output_tokens"}
    )
    response["error" if terminal == "failed" else "incomplete_details"] = detail
    event = {"type": "response." + terminal, "response": response, "sequence_number": 0}
    body = (
        "event: response." + terminal + "\ndata: " + json.dumps(event, ensure_ascii=False) + "\n\n"
    )

    async def handler(request, row):
        row["response_body"] = body
        return web.Response(text=body, content_type="text/event-stream")

    wire_server.handler = handler
    real_world = real_providers(responses=True)
    runner = await runner_for(real_world, Event(), streaming=True)
    await asyncio.gather(consume(runner), return_exceptions=True)
    call = http_calls(real_world[0])[0]
    assert call["response_body"] == body and call["status"] == status
    assert call["reading"]["error"] == detail


async def test_ordinary_nested_fields_named_like_credentials_are_preserved(
    real_providers, wire_server
):
    fields = {"authorization": "剧情中的授权称呼", "api_key": "字段讲解，不是认证凭据"}
    real_world = real_providers(extra={"vendor_option": fields})

    async def handler(request, row):
        value = completion()
        value["vendor_option"] = fields
        return wire_server.reply(row, value)

    wire_server.handler = handler
    await call_background(real_world)
    call = http_calls(real_world[0])[0]
    assert call["request_body"] == wire_server.records[0]["request_body"]
    assert call["response_body"] == wire_server.records[0]["response_body"]
    assert json.loads(call["request_body"])["vendor_option"] == fields
    assert json.loads(call["response_body"])["vendor_option"] == fields


async def test_turning_debug_off_and_on_does_not_resume_an_old_stream(real_providers, wire_server):
    allow_middle, allow_last = asyncio.Event(), asyncio.Event()
    consumed_first, consumed_middle = asyncio.Event(), asyncio.Event()
    parts = []
    for text in ("关闭前", "关闭期间", "重新开启后"):
        chunk = json.loads(sse_chunks()[1][6:].strip())
        chunk["choices"][0]["delta"]["content"] = text
        parts.append("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n")

    async def handler(request, row):
        row["response_body"] = "".join(parts) + "data: [DONE]\n\n"
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        await response.write(parts[0].encode("utf-8"))
        await allow_middle.wait()
        await response.write(parts[1].encode("utf-8"))
        await allow_last.wait()
        await response.write((parts[2] + "data: [DONE]\n\n").encode("utf-8"))
        await response.write_eof()
        return response

    wire_server.handler = handler
    real_world = real_providers()
    runtime, _, provider = real_world
    runtime.chat.install()
    received = []

    async def collect():
        async for item in provider.text_chat_stream(prompt="流中切换调试", request_max_retries=1):
            received.append(item.completion_text)
            if item.completion_text == "关闭前":
                consumed_first.set()
            elif item.completion_text == "关闭期间":
                consumed_middle.set()

    request = {"task": "wire.test", "scope": TARGET, "module": "reply"}
    parent = runtime.debug.begin("wire.test", {}, module="reply", scope=TARGET)
    task = asyncio.create_task(runtime.chat.audit.background(request, parent, collect()))
    try:
        await asyncio.wait_for(consumed_first.wait(), 5)
        await runtime.update_settings({"modules": {"debug": False}})
        immediately_after_disable = copy.deepcopy(http_calls(runtime)[0])
        allow_middle.set()
        await asyncio.wait_for(consumed_middle.wait(), 5)
        await runtime.update_settings({"modules": {"debug": True}})
        allow_last.set()
        await asyncio.wait_for(task, 5)
    finally:
        allow_middle.set()
        allow_last.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    call = http_calls(runtime)[0]
    assert immediately_after_disable["status"] == "partial"
    assert immediately_after_disable["response_body"] == parts[0]
    assert call["status"] == "partial" and call["response_body"] == parts[0]
    assert "关闭期间" in "".join(received) and "重新开启后" in "".join(received)
    assert "data: [DONE]" not in call["response_body"]


@pytest.fixture(scope="module")
def official_4275_provider_classes():
    """Load hash-verified 4.27.5 classes, leaving host registration untouched."""
    classes = {}
    for filename, class_name, digest in [
        (
            "openai_source.py",
            "ProviderOpenAIOfficial",
            "cd648baf5ab92357e3cb08326dcf4313662906f1e2d04959934762a299b72d13",
        ),
        (
            "openai_responses_source.py",
            "ProviderOpenAIResponses",
            "78a0a2e6af3fb3d8c0e5c075bae9a578cc74733421da9e5886112fdf4dfc8a6d",
        ),
    ]:
        path = Path(__file__).resolve().parents[1] / "dist/compat" / ("astrbot-4.27.5-" + filename)
        if not path.exists():
            pytest.skip("Official 4.27.5 Provider source not cached; see validation instructions")
        source = path.read_bytes()
        assert hashlib.sha256(source).hexdigest() == digest
        tree = ast.parse(source.decode("utf-8"))
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == class_name:
                node.decorator_list = []
        # Responses must inherit the 4.27.5 base, rather than the installed host's base.
        tree.body = [
            node
            for node in tree.body
            if not (isinstance(node, ast.ImportFrom) and node.module == "openai_source")
        ]
        ast.fix_missing_locations(tree)
        namespace = {
            "__name__": "astrbot.core.provider.sources.living_world_compat_" + filename[:-3],
            "__package__": "astrbot.core.provider.sources",
            **classes,
        }
        exec(compile(tree, str(path), "exec"), namespace)  # noqa: S102 - Reviewed, hash-verified official classes; only registration is removed.
        classes[class_name] = namespace[class_name]
    return classes


@pytest.mark.parametrize("responses", [False, True], ids=["chat_completions", "responses"])
@pytest.mark.parametrize("streaming", [False, True], ids=["json", "sse"])
async def test_official_4275_provider_classes_use_same_real_http_capture_boundary(
    real_providers, wire_server, official_4275_provider_classes, responses, streaming
):
    if streaming:
        if responses:
            event = {"type": "response.completed", "sequence_number": 0, "response": response_api()}
            body = (
                "event: response.completed\ndata: " + json.dumps(event, ensure_ascii=False) + "\n\n"
            )
        else:
            body = "".join(sse_chunks())

        async def handler(request, row):
            row["response_body"] = body
            return web.Response(text=body, content_type="text/event-stream")

        wire_server.handler = handler
    cls = official_4275_provider_classes[
        "ProviderOpenAIResponses" if responses else "ProviderOpenAIOfficial"
    ]
    real_world = real_providers(responses=responses, provider_cls=cls)
    if streaming:
        runner = await runner_for(real_world, Event(), streaming=True)
        await consume(runner)
        assert runner.get_final_llm_resp().completion_text
    else:
        result = await call_background(real_world)
        assert result.completion_text
    calls = http_calls(real_world[0])
    assert len(calls) == len(wire_server.records) == 1
    assert calls[0]["request_body"] == wire_server.records[0]["request_body"]
    assert calls[0]["response_body"] == wire_server.records[0]["response_body"]
    assert calls[0]["status"] == "success"
    assert calls[0]["response_type"] == ("sse" if streaming else "json")
