"""Verify editable group reply guidance at the real runner's user-message tail."""

import copy
import json

import pytest
from astrbot.api.provider import ProviderRequest
from astrbot.core.agent.message import ImageURLPart, TextPart, dump_messages_with_checkpoints
from test_chat import GROUP, PRIVATE, Event, consume, runner_for

from living_world.chat import DYNAMIC_MARKER, GROUP_REPLY_HEADING
from living_world.config import DEFAULT_GROUP_REPLY_PROMPT, settings_from
from living_world.runtime import Runtime

pytest_plugins = ("test_chat",)


def latest_user(call):
    return next(message for message in reversed(call["contexts"]) if message["role"] == "user")


@pytest.mark.parametrize("debug", [True, False])
async def test_group_prompt_is_last_after_late_parts_and_media_without_history_writes(world, debug):
    runtime, _, provider = world
    await runtime.update_settings({"modules": {"debug": debug}})
    event = Event(GROUP, "今天怎么样？")
    req = ProviderRequest(prompt=event.message_str, system_prompt="保持指定人格")
    runner = await runner_for(world, event, req)
    before = len(req.extra_user_content_parts)
    await runtime.chat.augment(event, req)
    assert len(req.extra_user_content_parts) == before
    # Other request/agent hooks may append content after our augmentation.
    late = TextPart(text="其他插件稍后加入的资料")
    media = ImageURLPart(image_url={"url": "https://example.test/reference.png"})
    req.extra_user_content_parts.append(late)
    runner.run_context.messages[-1].content.extend([late, media])
    runtime.chat.agent_begin(event, runner.run_context)
    runtime.chat.agent_begin(event, runner.run_context)
    await consume(runner)

    user = latest_user(provider.calls[0])
    final = user["content"][-1]["text"]
    assert final.endswith(GROUP_REPLY_HEADING + "\n" + DEFAULT_GROUP_REPLY_PROMPT)
    assert final.index("</living_world_context>") < final.index(GROUP_REPLY_HEADING)
    assert user["content"][-2]["type"] == "image_url"
    assert sum(GROUP_REPLY_HEADING in part.get("text", "") for part in user["content"]) == 1
    assert req.extra_user_content_parts[-1].text == final
    assert req.prompt == event.message_str and GROUP_REPLY_HEADING not in req.system_prompt
    saved = json.dumps(
        dump_messages_with_checkpoints(runner.run_context.messages), ensure_ascii=False
    )
    assert GROUP_REPLY_HEADING not in saved and DYNAMIC_MARKER not in saved
    assert event.message_str in saved and late.text in saved and "reference.png" in saved
    if debug:
        view = runtime.debug.views()[0]
        source = next(row for row in view["sources"] if row["title"] == "本轮群聊回复要求")
        assert source["content"] == GROUP_REPLY_HEADING + "\n" + DEFAULT_GROUP_REPLY_PROMPT
        assert "user 消息最后" in source["placement"]
        assert view["injected_text"].endswith(final)


async def test_group_prompt_edit_affects_next_turn_and_survives_restart(world, tmp_path):
    runtime, _, provider = world
    first = await runner_for(world, Event(GROUP, "第一轮"))
    custom = "只回答一个短句。\n保留中文与换行：<示例>。"
    await runtime.update_settings({"reply": {"group_prompt": custom}})
    await consume(first)
    assert latest_user(provider.calls[-1])["content"][-1]["text"].endswith(
        DEFAULT_GROUP_REPLY_PROMPT
    )
    second = await runner_for(world, Event(GROUP, "第二轮"))
    await consume(second)
    assert latest_user(provider.calls[-1])["content"][-1]["text"].endswith(custom)
    assert runtime.store.get("settings", "current")["reply"]["group_prompt"] == custom
    await runtime.stop()
    reopened = Runtime(tmp_path / "world.sqlite", runtime.host)
    try:
        assert reopened.settings["reply"]["group_prompt"] == custom
    finally:
        await reopened.stop()


@pytest.mark.parametrize(
    "scope,prompt,enabled",
    [(PRIVATE, "自定义群聊规则", True), (GROUP, " \n", True), (GROUP, "自定义群聊规则", False)],
)
async def test_group_guidance_does_not_leak_into_other_paths(world, scope, prompt, enabled):
    runtime, _, provider = world
    await runtime.update_settings(
        {"reply": {"group_prompt": prompt}, "modules": {"reply": enabled}}
    )
    runner = await runner_for(world, Event(scope))
    await consume(runner)
    assert GROUP_REPLY_HEADING not in json.dumps(provider.calls, ensure_ascii=False)
    assert (
        GROUP_REPLY_HEADING
        not in (await runtime.prepare_request("life.test", "life", "大纲", {}))["prompt"]
    )


async def test_history_fallback_removes_group_guidance_together_with_our_context(world):
    runtime, _, provider = world
    runner = await runner_for(
        world,
        Event(GROUP),
        ProviderRequest(prompt="新消息", contexts=[{"role": "user", "content": "原历史"}]),
    )
    next(
        message for message in runner.run_context.messages if message.role == "user"
    ).content = "其他插件改过的历史"
    await consume(runner)
    actual = json.dumps(provider.calls, ensure_ascii=False)
    assert GROUP_REPLY_HEADING not in actual and DYNAMIC_MARKER not in actual
    assert "其他插件改过的历史" in actual
    assert not runner.run_context.context.event.get_extra("living_world_reply")


@pytest.mark.parametrize(
    "invalid", [None, 5, [], {}, "x" * 8001], ids=["null", "number", "list", "object", "too_long"]
)
async def test_invalid_group_prompt_does_not_change_persisted_settings(world, invalid):
    runtime, _, _ = world
    previous = copy.deepcopy(runtime.settings)
    with pytest.raises((TypeError, ValueError)):
        await runtime.update_settings({"reply": {"group_prompt": invalid}})
    assert runtime.settings == previous
    assert runtime.store.get("settings", "current") == previous


def test_old_settings_gain_default_without_overwriting_custom_or_empty_prompt():
    assert (
        settings_from({"character": {"world": "旧世界"}})["reply"]["group_prompt"]
        == DEFAULT_GROUP_REPLY_PROMPT
    )
    for value in ("自定义", ""):
        assert settings_from({"reply": {"group_prompt": value}})["reply"]["group_prompt"] == value
