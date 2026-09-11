"""QQ profile labels use real names without changing memory attribution."""

import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from living_world.profile_names import named_profiles
from living_world.host import AstrBotHost
from living_world.runtime import Runtime
from living_world.social import SocialService
from test_runtime import FakeHost


def runtime_with_names(lookup=None, sessions=None):
    runtime = SimpleNamespace(
        host=SimpleNamespace(target_name=lookup or AsyncMock(return_value="小明3号")),
        settings={"sessions": sessions or []},
    )
    runtime.social = SocialService(runtime)
    return runtime


def profile(number="773896729"):
    return {
        "id": "person:qq:" + number,
        "owner": "person",
        "person_id": "qq:" + number,
        "name": "qq:" + number,
    }


def memory(number="773896729", scope="qq:GroupMessage:1057309504"):
    return {"id": "memory", "person_id": "qq:" + number, "scope": scope, "text": "已存在的记忆"}


async def test_group_member_name_uses_person_api_on_origin_connection_and_cache():
    runtime = runtime_with_names(
        sessions=[{"umo": "qq:GroupMessage:1057309504", "display_name": "群名字不能当人名"}]
    )
    profiles, memories = [profile()], [memory()]
    before = copy.deepcopy((profiles, memories))
    for _ in range(2):
        result = await named_profiles(runtime, profiles, memories)
        assert result[0]["name"] == "小明3号"
        assert result[0]["id"] == profiles[0]["id"]
    runtime.host.target_name.assert_awaited_once_with("qq:FriendMessage:773896729")
    assert (profiles, memories) == before


async def test_manual_private_name_wins_without_changing_self_profile():
    runtime = runtime_with_names(
        sessions=[{"umo": "qq:FriendMessage:773896729", "display_name": "老大2号"}]
    )
    own = {"id": "self:keke", "owner": "self", "name": "可可"}
    result = await named_profiles(runtime, [profile(), own], [memory()])
    assert result[0]["name"] == "老大2号" and result[1] == own
    runtime.host.target_name.assert_not_awaited()


async def test_failures_are_cached_and_do_not_disguise_id_as_nickname():
    runtime = runtime_with_names(AsyncMock(side_effect=RuntimeError("disconnected")))
    for _ in range(2):
        result = await named_profiles(runtime, [profile()], [memory()])
        assert result[0]["name"] == "未获取昵称"
    assert runtime.host.target_name.await_count == 1
    result = await named_profiles(runtime, [{**profile(), "name": "已知名字"}], [memory()])
    assert result[0]["name"] == "已知名字"


async def test_distinct_connections_never_share_names_and_unknown_connection_is_not_guessed():
    lookup = AsyncMock(side_effect=lambda scope: "名字：" + scope.split(":")[0])
    runtime = runtime_with_names(
        lookup, sessions=[{"umo": "a:GroupMessage:1"}, {"umo": "b:GroupMessage:2"}]
    )
    first = await named_profiles(runtime, [profile()], [memory(scope="a:GroupMessage:1")])
    second = await named_profiles(runtime, [profile()], [memory(scope="b:GroupMessage:2")])
    unknown = await named_profiles(runtime, [profile("42")], [])
    assert first[0]["name"] == "名字：a" and second[0]["name"] == "名字：b"
    assert unknown[0]["name"] == "未获取昵称" and lookup.await_count == 2


async def test_global_profile_uses_single_configured_connection_and_retains_numeric_name():
    runtime = runtime_with_names(
        AsyncMock(return_value="773896729"), [{"umo": "qq:GroupMessage:1"}]
    )
    result = await named_profiles(runtime, [profile()], [])
    assert result[0]["name"] == "773896729" and result[0]["name_status"] == "resolved"


async def test_whole_page_timeout_is_bounded_and_failed_lookups_back_off(monkeypatch):
    cancelled = []

    async def lookup(scope):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(scope)

    runtime = runtime_with_names(AsyncMock(side_effect=lookup))
    actual_wait = asyncio.wait

    async def short_wait(tasks, *, timeout):
        assert timeout == 2
        return await actual_wait(tasks, timeout=0.02)

    monkeypatch.setattr("living_world.profile_names.asyncio.wait", short_wait)
    await named_profiles(runtime, [profile()], [memory()])
    await named_profiles(runtime, [profile()], [memory()])
    assert len(cancelled) == runtime.host.target_name.await_count == 1


async def test_profile_names_are_loaded_separately_without_rewriting_memory(tmp_path):
    host = FakeHost()
    host.target_name = AsyncMock(return_value="小明3号")
    runtime = Runtime(tmp_path / "names.sqlite", host)
    runtime.kick_memory = lambda: None
    try:
        await runtime.update_settings({"persona_id": "student"})
        row = runtime.memory.remember(
            "可可的旧迁移记录暂时保留", person_id="773896729", scope="qq:GroupMessage:1057309504"
        )
        before = copy.deepcopy(row)
        snapshot = await runtime.snapshot()
        assert "memories" not in snapshot and "memory_profiles" not in snapshot
        assert snapshot["memory_count"] == 1
        host.target_name.assert_not_awaited()
        result = await runtime.page_action({"action": "memory.names", "ids": [row["identity"]]})
        assert result["items"][0]["name"] == "小明3号"
        assert runtime.store.get("memories", row["id"]) == before
    finally:
        await runtime.stop()


@pytest.mark.parametrize("first", [RuntimeError("not a friend"), {"nickname": ""}])
async def test_host_reads_member_nickname_when_stranger_info_unavailable(first):
    client = SimpleNamespace(
        call_action=AsyncMock(side_effect=[first, {"nickname": "群友昵称3号", "card": "群名片"}])
    )
    platform = SimpleNamespace(
        meta=lambda: SimpleNamespace(name="aiocqhttp"), get_client=lambda: client
    )
    host = AstrBotHost(
        SimpleNamespace(
            get_platform_inst=lambda connection: platform if connection == "qq" else None
        )
    )
    assert (
        await host.person_name("qq:GroupMessage:773896729_1057309504", "773896729") == "群友昵称3号"
    )
    assert [call.args[0] for call in client.call_action.await_args_list] == [
        "get_stranger_info",
        "get_group_member_info",
    ]
    assert client.call_action.await_args_list[-1].kwargs == {
        "group_id": 1057309504,
        "user_id": 773896729,
    }


async def test_profile_uses_host_person_lookup_with_original_group():
    runtime = runtime_with_names()
    runtime.host.person_name = AsyncMock(return_value="群友昵称")
    result = await named_profiles(runtime, [profile()], [memory()])
    assert result[0]["name"] == "群友昵称"
    runtime.host.person_name.assert_awaited_once_with("qq:GroupMessage:1057309504", "773896729")
    runtime.host.target_name.assert_not_awaited()
