"""Real AstrBot requests/runners with local providers and conversation storage."""

import asyncio
import copy
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.message_components import Plain
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.core.agent.hooks import BaseAgentRunHooks
from astrbot.core.agent.message import TextPart, dump_messages_with_checkpoints
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
from astrbot.core.agent.tool import FunctionTool, ToolSet
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_session import MessageSession
from astrbot.core.platform.platform_metadata import PlatformMetadata
from mcp.types import CallToolResult, TextContent

from living_world.chat import DYNAMIC_MARKER, _text
from living_world.debug import json_value
from living_world.host import AstrBotHost
from living_world.runtime import Runtime

PRIVATE = "qq:FriendMessage:42"
GROUP = "qq:GroupMessage:42_100"


class Event(AstrMessageEvent):
    def __init__(self, scope=PRIVATE, text="你好"):
        session = MessageSession.from_str(scope)
        message = AstrBotMessage()
        message.type = session.message_type
        message.self_id = "bot"
        message.message_id = uuid.uuid4().hex
        message.sender = MessageMember(session.session_id.split("_", 1)[0], "小明")
        message.group_id = session.session_id.split("_")[-1] if "GroupMessage" in scope else ""
        message.message = [Plain(text)]
        message.raw_message = {}
        super().__init__(
            text,
            message,
            PlatformMetadata("aiocqhttp", "QQ", session.platform_id),
            session.session_id,
        )
        self.sent = []
        self.fail_send = False

    async def send(self, message):
        if self.fail_send:
            raise OSError("test transport failure")
        self.sent.append(json_value(message))


class Conversations:
    def __init__(self):
        self.selected = {}
        self.rows = {}
        self.created = 0

    async def get_curr_conversation_id(self, scope):
        return self.selected.get(scope)

    async def get_conversation(self, scope, cid):
        return self.rows.get(cid)

    async def new_conversation(self, scope, **kwargs):
        self.created += 1
        cid = uuid.uuid4().hex
        self.selected[scope] = cid
        self.rows[cid] = SimpleNamespace(
            cid=cid, persona_id=kwargs.get("persona_id"), history="[]", token_usage=0
        )
        return cid

    async def update_conversation(self, scope, cid, history):
        self.rows[cid].history = json.dumps(history, ensure_ascii=False)


class Provider:
    def __init__(self, answers=()):
        self.answers = list(answers)
        self.calls = []
        self.provider_config = {"id": "model", "model": "local-test", "max_context_tokens": 0}
        self.block = None
        self.entered = asyncio.Event()

    def meta(self):
        return SimpleNamespace(id="model", model="local-test", type="local-test")

    def get_model(self):
        return "local-test"

    async def text_chat(self, **kwargs):
        self.calls.append(json_value(kwargs))
        self.entered.set()
        if self.block:
            await self.block.wait()
        answer = (
            self.answers.pop(0)
            if self.answers
            else LLMResponse(role="assistant", completion_text="你好呀")
        )
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def text_chat_stream(self, **kwargs):
        yield LLMResponse(role="assistant", completion_text="你好", is_chunk=True)
        yield LLMResponse(role="assistant", completion_text="你好呀", is_chunk=False)


@pytest.fixture
async def world(tmp_path):
    manager, provider = Conversations(), Provider()
    personas = {name: {"name": name, "prompt": "Student"} for name in ("student", "other")}
    defaults = {
        "provider_settings": {"default_personality": "student"},
        "provider_ltm_settings": {"group_message_history_enable": False},
    }
    rules = {}
    platforms = {
        name: SimpleNamespace(meta=lambda name=name: PlatformMetadata("aiocqhttp", "QQ", name))
        for name in ("qq", "default")
    }

    async def persona(*, umo, conversation_persona_id, platform_name, provider_settings=None):
        forced = rules.get(umo)
        selected = forced or conversation_persona_id
        if not forced and conversation_persona_id is None:
            selected = (provider_settings or {}).get("default_personality")
        return selected, personas.get(selected), forced, False

    context = SimpleNamespace(
        conversation_manager=manager,
        persona_manager=SimpleNamespace(
            resolve_selected_persona=persona,
            get_persona_v3_by_id=personas.get,
            personas_v3=list(personas.values()),
        ),
        get_platform_inst=platforms.get,
        get_config=lambda _: defaults,
        test_rules=rules,
        test_platforms=platforms,
        get_all_providers=lambda: [provider],
        get_provider_by_id=lambda _: provider,
        get_current_chat_provider_id=AsyncMock(return_value="model"),
        send_message=AsyncMock(return_value=True),
        platform_manager=SimpleNamespace(platform_insts=list(platforms.values())),
    )
    runtime = Runtime(tmp_path / "world.sqlite", AstrBotHost(context))
    await runtime.update_settings(
        {
            "persona_id": "student",
            "sessions": [{"umo": PRIVATE}, {"umo": "qq:GroupMessage:100"}],
            "social": {"quiet_start": "00:00", "quiet_end": "00:00"},
        }
    )
    yield runtime, manager, provider
    await runtime.stop()


class Hooks(BaseAgentRunHooks):
    def __init__(self, chat):
        self.chat = chat

    async def on_agent_begin(self, context):
        self.chat.agent_begin(context.context.event, context)

    async def on_agent_done(self, context, response):
        self.chat.response(context.context.event, response)
        self.chat.restore_history(context.context.event, context)

    async def on_tool_start(self, context, tool, args):
        self.chat.tool_start(context.context.event, tool, args)

    async def on_tool_end(self, context, tool, args, result):
        self.chat.tool_end(context.context.event, tool, args, result)


async def runner_for(world, event, req=None, executor=None, streaming=False):
    runtime, _, provider = world
    await runtime.chat.observe(event)
    req = req or ProviderRequest(
        prompt=event.message_str,
        system_prompt="Stable persona",
        session_id=event.unified_msg_origin,
    )
    await runtime.chat.augment(event, req)
    runtime.chat.install()
    runner = ToolLoopAgentRunner()
    await runner.reset(
        provider=provider,
        request=req,
        run_context=ContextWrapper(context=SimpleNamespace(event=event)),
        tool_executor=executor,
        agent_hooks=Hooks(runtime.chat),
        streaming=streaming,
    )
    return runner


async def consume(runner):
    async for _ in runner.step_until_done(5):
        pass


async def test_first_private_reply_and_complete_provider_trace(world):
    runtime, _, provider = world
    event = Event()
    runner = await runner_for(world, event)
    await consume(runner)
    await event.send(MessageChain([Plain(runner.get_final_llm_resp().completion_text)]))
    rows = runtime.store.list("debug_records")
    assert {row["task"] for row in rows} >= {
        "chat.turn",
        "chat.route",
        "chat.context",
        "reply.request",
        "reply.model",
        "reply.result",
        "reply.send",
    }
    assert len({row["turn_id"] for row in rows}) == 1
    assert (
        next(row for row in rows if row["task"] == "reply.model")["response"]["completion_text"]
        == "你好呀"
    )
    assert (
        next(row for row in rows if row["task"] == "reply.send")["request"]["message"]["chain"][0][
            "text"
        ]
        == "你好呀"
    )
    assert all("reply" not in row for row in rows)
    assert DYNAMIC_MARKER not in runner.req.system_prompt
    assert DYNAMIC_MARKER in json.dumps(provider.calls[0], ensure_ascii=False)
    saved = dump_messages_with_checkpoints(runner.run_context.messages)
    assert DYNAMIC_MARKER not in json.dumps(saved, ensure_ascii=False)
    assert (await runtime.chat.inspect(PRIVATE))["history_status"] == "empty"


async def test_group_observes_without_host_history_and_restores_original(world):
    runtime, manager, provider = world
    cid = await manager.new_conversation(GROUP, persona_id="student")
    history = [
        {"role": "user", "content": "旧的宿主群历史"},
        {"role": "assistant", "content": "旧回复"},
    ]
    await manager.update_conversation(GROUP, cid, history)
    event = Event(GROUP, "数学作业讨论")
    runtime.memory.remember("我的私聊密码是秘密", scope=PRIVATE, person_id="qq:42")
    req = ProviderRequest(
        prompt=event.message_str,
        system_prompt="Student",
        contexts=history,
        conversation=manager.rows[cid],
        extra_user_content_parts=[
            TextPart(
                text="<system_reminder>\nYou are in a group chat.\n--- BEGIN CONTEXT---\n宿主重复块\n--- END CONTEXT ---\n</system_reminder>"
            )
        ],
    )
    runner = await runner_for(world, event, req)
    assert req.contexts == history
    await consume(runner)
    sent = json.dumps(provider.calls, ensure_ascii=False)
    assert "数学作业讨论" in sent and "小明" in sent
    assert "旧的宿主群历史" not in sent and "宿主重复块" not in sent
    assert "私聊密码" not in sent
    persisted = json.dumps(
        dump_messages_with_checkpoints(runner.run_context.messages), ensure_ascii=False
    )
    assert "旧的宿主群历史" in persisted and DYNAMIC_MARKER not in persisted
    assert req.contexts == history and len((await runtime.chat.history(GROUP))["messages"]) == 1
    status = await runtime.chat.inspect("qq:GroupMessage:100")
    assert status["history_status"] == "found" and status["actual_scope"] == GROUP


async def test_private_current_conversation_only_and_readonly_check(world):
    runtime, manager, _ = world
    before = runtime.store.export()
    result = await runtime.chat.inspect(PRIVATE)
    assert result["allowed"] and result["history_status"] == "empty" and manager.created == 0
    assert runtime.store.export() == before
    old = await manager.new_conversation(PRIVATE)
    await manager.update_conversation(PRIVATE, old, [{"role": "user", "content": "旧对话秘密"}])
    await manager.new_conversation(PRIVATE)
    assert "旧对话秘密" not in (await runtime.chat.history(PRIVATE))["text"]
    manager.get_conversation = AsyncMock(side_effect=OSError("offline"))
    assert (await runtime.chat.inspect(PRIVATE))["history_status"] == "error"


async def test_first_proactive_private_message_saved_only_after_transport(world):
    runtime, manager, _ = world
    assert await runtime.send_message(PRIVATE, "今天上数学课")
    assert manager.created == 1
    assert "今天上数学课" in (await runtime.chat.history(PRIVATE))["text"]
    runtime.host.context.send_message.return_value = False
    assert not await runtime.send_message(PRIVATE, "不能发出的文字")
    assert "不能发出的文字" not in (await runtime.chat.history(PRIVATE))["text"]
    manager.update_conversation = AsyncMock(side_effect=OSError("full disk"))
    runtime.host.context.send_message.return_value = True
    assert await runtime.send_message(PRIVATE, "成功发出但存档失败")
    row = runtime.store.list("debug_records")[0]
    assert row["status"] == "sent" and row["response"]["history_status"] == "failed"


async def test_tool_followup_model_and_external_internal_call_is_not_captured(world):
    runtime, _, provider = world
    provider.answers = [
        LLMResponse(
            role="assistant",
            tools_call_name=["lookup"],
            tools_call_args=[{"query": "数学"}],
            tools_call_ids=["call1"],
        ),
        LLMResponse(role="assistant", completion_text="third party internal"),
        LLMResponse(role="assistant", completion_text="查到了"),
    ]

    class Executor:
        async def execute(self, **kwargs):
            await provider.text_chat(prompt="third party private inner prompt")
            yield CallToolResult(content=[TextContent(type="text", text="找到数学资料")])

    tool = FunctionTool(
        name="lookup",
        description="Search",
        parameters={"type": "object", "properties": {"query": {"type": "string"}}},
    )
    event = Event()
    runner = await runner_for(
        world,
        event,
        ProviderRequest(prompt="查数学", system_prompt="Student", func_tool=ToolSet([tool])),
        Executor(),
    )
    await consume(runner)
    records = runtime.store.list("debug_records")
    calls = [r for r in records if r["task"] == "reply.model"]
    assert len(calls) == 2 and len(provider.calls) == 3
    assert "third party private inner prompt" not in json.dumps(records, ensure_ascii=False)
    assert "找到数学资料" in json.dumps(calls, ensure_ascii=False)
    assert next(r for r in records if r["task"] == "reply.tool")["status"] == "success"


async def test_concurrent_runs_do_not_capture_unmanaged_shared_provider(world):
    runtime, _, provider = world
    first = await runner_for(world, Event(PRIVATE, "第一轮"))
    second = await runner_for(world, Event(GROUP, "第二轮"))
    await asyncio.gather(
        consume(first), consume(second), provider.text_chat(prompt="unmanaged private data")
    )
    calls = [r for r in runtime.store.list("debug_records") if r["task"] == "reply.model"]
    assert len(calls) == 2 and len({r["turn_id"] for r in calls}) == 2
    assert "unmanaged private data" not in json.dumps(calls, ensure_ascii=False)


async def test_stream_partial_send_failure_and_unload(world):
    runtime, _, provider = world
    original_step, original_call = ToolLoopAgentRunner.step, provider.text_chat
    event = Event()
    original_send = event.send
    runner = await runner_for(world, event, streaming=True)
    await consume(runner)
    await event.send(MessageChain([Plain("第一段")]))
    event.fail_send = True
    with pytest.raises(OSError):
        await event.send(MessageChain([Plain("第二段")]))
    records = runtime.store.list("debug_records")
    assert next(r for r in records if r["task"] == "chat.turn")["status"] == "partial"
    assert (
        next(r for r in records if r["task"] == "reply.model")["response"]["final"][
            "completion_text"
        ]
        == "你好呀"
    )
    runtime.chat.close()
    assert ToolLoopAgentRunner.step is original_step and provider.text_chat == original_call
    assert event.send == original_send


async def test_cancel_model_and_disable_audit_do_not_leave_wrappers(world):
    runtime, _, provider = world
    original_call = provider.text_chat
    event = Event()
    runner = await runner_for(world, event)
    provider.block = asyncio.Event()
    task = asyncio.create_task(consume(runner))
    await provider.entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (
        next(r for r in runtime.store.list("debug_records") if r["task"] == "reply.model")["status"]
        == "cancelled"
    )
    await runtime.update_settings({"modules": {"debug": False}})
    assert provider.text_chat == original_call
    await runtime.update_settings({"modules": {"debug": True}})
    assert provider.text_chat != original_call


async def test_turn_retention_and_clear_keep_complete_groups(world):
    runtime, _, _ = world
    await runtime.update_settings({"debug": {"retain_per_category": 2}})
    traces = []
    for _ in range(3):
        event = Event()
        await runtime.chat.observe(event)
        trace = runtime.chat.ensure_trace(event)
        traces.append(trace)
        for _ in range(15):
            runtime.chat.record(trace, "reply.model", {}, status="success")
    rows = runtime.store.list("debug_records")
    assert {r["turn_id"] for r in rows} == {traces[1]["id"], traces[2]["id"]}
    assert len([r for r in rows if r["task"] == "reply.model"]) == 30
    runtime.debug.clear("reply.model")
    runtime.chat.ensure_trace(event)
    runtime.chat.record(traces[2], "reply.send", {}, status="sent")
    assert not runtime.store.list("debug_records")


@pytest.mark.parametrize("scope", [GROUP, PRIVATE])
@pytest.mark.parametrize("reply_enabled", [False, True])
async def test_observations_do_not_record_or_evict_model_turns(world, scope, reply_enabled):
    runtime, _, provider = world
    runner = await runner_for(world, Event(GROUP, "请回复这条消息"))
    await consume(runner)
    await runtime.update_settings(
        {
            "debug": {"retain_per_category": 1},
            "modules": {"reply": reply_enabled, "interjection": False},
        }
    )
    before = runtime.store.list("debug_records")
    for index in range(30):
        event = Event(scope, f"普通消息 {index}")
        await runtime.chat.observe(event)
    assert runtime.store.list("debug_records") == before
    assert len(provider.calls) == 1
    trace = event.get_extra("living_world_trace")
    assert trace["root"] is None and not trace["debug_started"]
    status = (await runtime.chat.inspect(scope))["context_status"]["last_attempt"]
    assert status["turn_id"] == ""
    if scope == GROUP:
        window = (await runtime.chat.history(scope))["messages"]
        assert len(window) == 24 and window[-1]["text"] == "普通消息 29"


async def test_observation_promotes_once_and_keeps_received_snapshot(world):
    runtime, _, provider = world
    event = Event(GROUP, "原始群友消息")
    await runtime.chat.observe(event)
    assert not runtime.debug.views()
    trace = event.get_extra("living_world_trace")
    event.message_str = "宿主修改后的输入"
    runner = await runner_for(world, event)
    await consume(runner)
    runtime.chat.ensure_trace(event)
    view = runtime.debug.views()[0]
    assert view["id"] == trace["id"]
    assert view["sources"][0]["content"] == "原始群友消息"
    rows = runtime.store.list("debug_records")
    assert sum(r["task"] == "chat.turn" for r in rows) == 1
    assert sum(r["task"] == "chat.route" for r in rows) == 1
    assert len(provider.calls) == 1
    assert view["adopted"] and view["calls"]


@pytest.mark.parametrize("failure", [False, True])
async def test_observed_event_real_send_is_retained_without_model(world, failure):
    runtime, _, provider = world
    event = Event(GROUP)
    await runtime.chat.observe(event)
    event.fail_send = failure
    message = MessageChain([Plain("真实发送测试")])
    if failure:
        with pytest.raises(OSError, match="transport failure"):
            await event.send(message)
    else:
        await event.send(message)
    runtime.debug.trim()
    view = runtime.debug.views()[0]
    assert len(view["sends"]) == 1 and not view["calls"]
    assert view["sends"][0]["status"] == ("unknown" if failure else "sent")
    assert not provider.calls


async def test_disabled_debug_start_is_not_recreated_after_enable(world):
    runtime, _, _ = world
    event = Event(GROUP)
    await runtime.chat.observe(event)
    await runtime.update_settings({"modules": {"debug": False}})
    await runtime.chat.augment(event, ProviderRequest(prompt=event.message_str))
    await runtime.update_settings({"modules": {"debug": True}})
    runtime.chat.ensure_trace(event)
    await event.send(MessageChain([Plain("调试关闭期间开始的回复")]))
    assert not runtime.debug.views()
    assert len(event.sent) == 1


async def test_context_failure_restores_original_and_disabled_reply_does_not_inject(world):
    runtime, _, _ = world
    event = Event(GROUP)
    req = ProviderRequest(
        prompt="你好", system_prompt="Original", contexts=[{"role": "user", "content": "old"}]
    )
    original = copy.deepcopy(req)
    runtime.context_text = AsyncMock(side_effect=ValueError("broken memory"))
    await runtime.chat.augment(event, req)
    assert req == original
    assert (
        next(r for r in runtime.store.list("debug_records") if r["task"] == "chat.context")[
            "status"
        ]
        == "failed"
    )
    await runtime.update_settings({"modules": {"reply": False}})
    await runtime.chat.augment(event, req)
    assert req == original


async def test_group_observation_bounds_quotes_and_persona_isolation(world):
    runtime, _, _ = world
    text = _text(
        [
            {
                "type": "Reply",
                "id": "quoted-message",
                "sender_nickname": "小王",
                "message_str": "上数学课",
            },
            {"type": "At", "qq": "43", "name": "小王"},
            {"type": "Plain", "text": "我也想讨论"},
        ]
    )
    assert "quoted-message" in text and "小王" in text and "43" in text and "上数学课" in text
    await runtime.update_settings({"modules": {"reply": False, "interjection": False}})
    for i in range(30):
        await runtime.chat.observe(Event(GROUP, f"成员消息 {i}"))
    window = (await runtime.chat.history(GROUP))["messages"]
    assert len(window) == 24 and window[0]["text"] == "成员消息 6"
    assert not (await runtime.chat.inspect(GROUP))["allowed"]
    await runtime.chat.observe(Event(GROUP, "长消息" * 6000))
    window = (await runtime.chat.history(GROUP))["messages"]
    assert sum(len(row["text"]) for row in window) <= 12000
    await runtime.update_settings({"persona_id": "another"})
    assert not (await runtime.chat.history(GROUP))["messages"]
    event = Event(GROUP, "人格不匹配的消息")
    await runtime.chat.observe(event)
    status = (await runtime.chat.inspect(GROUP))["context_status"]["last_attempt"]
    assert status["status"] == "skipped" and "人格不匹配" in status["reason"]
    assert not runtime.store.list("debug_records")
    assert not (await runtime.chat.history(GROUP))["messages"]


@pytest.mark.parametrize("begin", [False, True])
async def test_unload_does_not_discard_group_history_before_or_after_begin(world, begin):
    runtime, manager, _ = world
    cid = await manager.new_conversation(GROUP, persona_id="student")
    history = [{"role": "user", "content": "必须保留的原始历史"}]
    req = ProviderRequest(prompt="新消息", contexts=history, conversation=manager.rows[cid])
    event = Event(GROUP)
    runner = await runner_for(world, event, req)
    if begin:
        runtime.chat.agent_begin(event, runner.run_context)
        assert "必须保留" not in json.dumps(
            json_value(runner.run_context.messages), ensure_ascii=False
        )
    runtime.chat.close()
    assert "必须保留" in json.dumps(json_value(runner.run_context.messages), ensure_ascii=False)
    assert req.contexts == history


async def test_changed_host_history_falls_back_without_duplicate_group_context(world):
    runtime, _, provider = world
    event = Event(GROUP)
    req = ProviderRequest(prompt="新消息", contexts=[{"role": "user", "content": "原历史"}])
    runner = await runner_for(world, event, req)
    first = next(m for m in runner.run_context.messages if m.role == "user")
    first.content = "其他插件修改过的历史"
    await consume(runner)
    actual = json.dumps(provider.calls, ensure_ascii=False)
    assert "其他插件修改过的历史" in actual and DYNAMIC_MARKER not in actual
    assert not event.get_extra("living_world_reply")
    assert (
        next(r for r in runtime.store.list("debug_records") if r["task"] == "chat.history_replace")[
            "status"
        ]
        == "failed"
    )


async def test_debug_storage_and_group_observation_failures_do_not_break_send(world, monkeypatch):
    runtime, _, provider = world
    event = Event(GROUP)
    runner = await runner_for(world, event)
    original_put = runtime.store.put

    def fail_debug(namespace, *args, **kwargs):
        if namespace in {"debug_records", "group_context"}:
            raise OSError("test debug storage failure")
        return original_put(namespace, *args, **kwargs)

    monkeypatch.setattr(runtime.store, "put", fail_debug)
    await consume(runner)
    await event.send(MessageChain([Plain("实际发出的一条消息")]))
    assert len(event.sent) == 1 and len(provider.calls) == 1
    assert await runtime.send_message(GROUP, "后台发送成功不受观察存储影响")


async def test_background_call_captures_provider_arguments(world):
    runtime, _, provider = world
    runtime.chat.install()

    async def llm_generate(chat_provider_id, **kwargs):
        return await provider.text_chat(**kwargs)

    runtime.host.context.llm_generate = llm_generate
    assert await runtime.generate("life", "后台活动请求", task="life.test") == "你好呀"
    rows = runtime.store.list("debug_records")
    actual = next(r for r in rows if r["task"] == "provider.life.test")
    outer = next(r for r in rows if r["task"] == "life.test")
    assert actual["parent_id"] == outer["id"] and not actual["turn_id"]
    assert actual["request"]["arguments"]["prompt"] == provider.calls[0]["prompt"]
