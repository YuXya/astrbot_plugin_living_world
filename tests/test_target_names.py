"""Read-only destination names, failure bounds, and connection-scoped caching."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from living_world.host import AstrBotHost
from living_world.social import SocialService

PRIVATE = "qq:FriendMessage:773896729"
GROUP = "qq:GroupMessage:111_987654321"


def service(host=None, sessions=None):
    runtime = SimpleNamespace(
        host=host or SimpleNamespace(target_name=AsyncMock(return_value="自动名字")),
        settings={"sessions": sessions or []},
    )
    return SocialService(runtime)


@pytest.mark.parametrize(
    ("scope", "label", "kind", "audience"),
    [
        (PRIVATE, "目标名字", "一对一私聊", "这位私聊对象"),
        (GROUP, "群聊名字", "QQ群聊", "整个群"),
    ],
)
async def test_manual_name_wins_and_keeps_legal_digits(scope, label, kind, audience):
    social = service(sessions=[{"umo": scope, "display_name": "  高一3班  "}])
    result = await social.recipient_context(scope)
    assert f"{label}：高一3班" in result
    assert f"会话类型：{kind}" in result
    assert audience in result
    assert "773896729" not in result and "987654321" not in result
    assert "目标QQ号" not in result and "目标群号" not in result
    social.runtime.host.target_name.assert_not_awaited()


async def test_empty_manual_name_uses_host_and_global_never_resolves():
    social = service(sessions=[{"umo": PRIVATE, "display_name": "   "}])
    assert "目标名字：自动名字" in await social.recipient_context(PRIVATE)
    social.runtime.host.target_name.assert_awaited_once_with(PRIVATE)
    assert "尚未选择聊天对象" in await social.recipient_context("global")
    assert social.runtime.host.target_name.await_count == 1


async def test_aliases_share_cache_but_connections_and_target_kinds_do_not(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("living_world.social.monotonic", lambda: now[0])
    social = service()
    assert "自动名字" in await social.recipient_context(GROUP)
    await social.recipient_context("qq:GroupMessage:222_987654321")
    assert social.runtime.host.target_name.await_count == 1
    await social.recipient_context("other-qq:GroupMessage:987654321")
    await social.recipient_context("qq:FriendMessage:987654321")
    assert social.runtime.host.target_name.await_count == 3
    now[0] = 3699.0
    await social.recipient_context(GROUP)
    assert social.runtime.host.target_name.await_count == 3
    now[0] = 3700.0
    await social.recipient_context(GROUP)
    assert social.runtime.host.target_name.await_count == 4


@pytest.mark.parametrize("outcome", ["", "  ", None, 773896729, RuntimeError("offline")])
async def test_failed_or_invalid_lookup_never_falls_back_to_number(outcome, monkeypatch):
    now = [100.0]
    monkeypatch.setattr("living_world.social.monotonic", lambda: now[0])
    lookup = AsyncMock()
    if isinstance(outcome, Exception):
        lookup.side_effect = outcome
    else:
        lookup.return_value = outcome
    social = service(host=SimpleNamespace(target_name=lookup))
    for moment in (100.0, 159.0):
        now[0] = moment
        result = await social.recipient_context(PRIVATE)
        assert "目标名字：未设置名字" in result
        assert "773896729" not in result
    assert lookup.await_count == 1
    now[0] = 160.0
    await social.recipient_context(PRIVATE)
    assert lookup.await_count == 2


async def test_timeout_is_two_seconds_and_cancels_name_lookup(monkeypatch):
    cancelled = asyncio.Event()
    original_wait_for = asyncio.wait_for
    timeouts = []

    async def lookup(_):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def short_wait_for(awaitable, timeout):
        timeouts.append(timeout)
        return await original_wait_for(awaitable, timeout=0.01)

    monkeypatch.setattr("living_world.social.asyncio.wait_for", short_wait_for)
    social = service(host=SimpleNamespace(target_name=lookup))
    assert "未设置名字" in await social.recipient_context(PRIVATE)
    assert cancelled.is_set()
    assert timeouts == [2]
    await social.recipient_context(PRIVATE)
    assert timeouts == [2]


async def test_manual_edits_take_effect_without_waiting_for_cached_name():
    social = service()
    await social.recipient_context(PRIVATE)
    social.runtime.settings["sessions"] = [
        None,
        {"umo": "invalid"},
        {"umo": PRIVATE, "display_name": "新称呼"},
    ]
    assert "目标名字：新称呼" in await social.recipient_context(PRIVATE)
    assert social.runtime.host.target_name.await_count == 1


@pytest.mark.parametrize(
    ("scope", "response", "expected", "action", "arguments"),
    [
        (PRIVATE, {"nickname": "好友2号"}, "好友2号", "get_stranger_info", {"user_id": 773896729}),
        (GROUP, {"group_name": "同学群"}, "同学群", "get_group_info", {"group_id": 987654321}),
        (GROUP, {"nickname": "最近发言的成员"}, "", "get_group_info", {"group_id": 987654321}),
        (GROUP, {"group_name": 987654321}, "", "get_group_info", {"group_id": 987654321}),
        (PRIVATE, {"user_id": 773896729}, "", "get_stranger_info", {"user_id": 773896729}),
    ],
)
async def test_host_calls_only_destination_name_api(scope, response, expected, action, arguments):
    client = SimpleNamespace(call_action=AsyncMock(return_value=response))
    platform = SimpleNamespace(
        meta=lambda: SimpleNamespace(name="aiocqhttp"), get_client=lambda: client
    )
    lookups = []

    def get_platform(platform_id):
        lookups.append(platform_id)
        return platform

    host = AstrBotHost(SimpleNamespace(get_platform_inst=get_platform))
    assert await host.target_name(scope) == expected
    assert lookups == ["qq"]
    client.call_action.assert_awaited_once_with(action, **arguments)


@pytest.mark.parametrize("platform", [None, SimpleNamespace(meta=lambda: SimpleNamespace(name="other"))])
async def test_missing_or_non_onebot_connection_has_no_name(platform):
    host = AstrBotHost(SimpleNamespace(get_platform_inst=lambda _: platform))
    assert await host.target_name(PRIVATE) == ""


async def test_missing_name_capability_remains_compatible_with_alternative_hosts():
    social = service(host=SimpleNamespace())
    assert "未设置名字" in await social.recipient_context(PRIVATE)


async def test_cancellation_propagates_without_saving_a_failure():
    social = service(host=SimpleNamespace(target_name=AsyncMock(side_effect=asyncio.CancelledError)))
    with pytest.raises(asyncio.CancelledError):
        await social.recipient_context(PRIVATE)
    assert social._target_names == {}
