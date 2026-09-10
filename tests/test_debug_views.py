"""User-visible raw exports and retention of whole diagnostic calls."""

import json

import pytest
from test_debug import runtime as runtime_fixture

from living_world.debug import DebugService
from living_world.debug_views import build_views

runtime = runtime_fixture


def observation_rows(key, created_at=1):
    root = {
        "id": key,
        "capture_version": 2,
        "task": "chat.turn",
        "category": "chat.turn",
        "kind": "turn",
        "scope": "qq:GroupMessage:100",
        "turn_id": key,
        "created_at": created_at,
        "status": "observed",
        "request": {"text": "普通群友消息"},
        "response": {"reason": "尚未进入模型阶段；其他链路是否处理暂不确定"},
    }
    route = {
        **root,
        "id": key + "-route",
        "parent_id": key,
        "task": "chat.route",
        "category": "chat.route",
        "kind": "event",
        "status": "success",
        "request": {"allowed": True, "reason": "允许接入"},
        "response": {"reason": "允许接入"},
    }
    return [root, route]


async def test_old_observations_removed_before_retention_and_after_restore(runtime):
    await runtime.update_settings({"debug": {"retain_per_category": 1}})
    kept = runtime.debug.begin("chat.turn", {}, turn_id="attempt", kind="turn")
    child = runtime.debug.begin(
        "reply.model", {}, turn_id="attempt", parent_id=kept["id"], kind="model"
    )
    runtime.debug.finish(child, status="failed", error="connection failed before HTTP capture")
    runtime.debug.finish(kept, status="failed")
    baseline = runtime.store.list("debug_records")
    formal = {
        "group_context": {"messages": [{"text": "保留群观察"}]},
        "chat_context_status": {"last_injected": {"turn_id": "attempt"}},
        "memories": {"text": "保留记忆"},
        "actions": {"status": "sent"},
        "claims": {"status": "done"},
    }
    for namespace, value in formal.items():
        runtime.store.put(namespace, "keep", value)
    old_rows = [
        row
        for index in range(12)
        for row in observation_rows(f"observed-{index}", kept["created_at"] + index + 1)
    ]
    runtime.store.put_many("debug_records", [(row["id"], row) for row in old_rows])
    DebugService(runtime)
    assert runtime.store.list("debug_records") == baseline
    runtime.debug.trim()
    assert runtime.store.list("debug_records") == baseline
    for namespace, value in formal.items():
        assert runtime.store.get(namespace, "keep") == value
    await runtime.restore(
        {
            "format": "living-world",
            "version": 1,
            "settings": runtime.settings,
            "records": [
                {"namespace": "debug_records", "key": row["id"], "value": row} for row in old_rows
            ],
        }
    )
    assert runtime.store.list("debug_records") == baseline
    assert not runtime.enabled("debug")


@pytest.mark.parametrize(
    "evidence",
    [
        {"task": "reply.model", "kind": "model", "status": "failed"},
        {"task": "chat.context", "status": "failed"},
        {"task": "chat.error", "status": "cancelled"},
        {"task": "reply.send", "kind": "message", "status": "unknown"},
        {"task": "reply.tool", "kind": "tool"},
        {"http_capture": "unsupported"},
        {"http_calls": [{"request_body": "{}", "response_body": None}]},
        {"reply": {"completion_text": "旧回复"}},
        {"response": {"completion_text": "保留的返回"}},
        {"response": 42},
        {"task": "unknown.future.event"},
    ],
)
async def test_cleanup_keeps_attempts_and_unknown_records_without_complete_api(runtime, evidence):
    rows = observation_rows("keep")
    rows[1].update(evidence)
    runtime.store.put_many("debug_records", [(row["id"], row) for row in rows])
    runtime.debug.trim()
    assert {row["id"] for row in runtime.store.list("debug_records")} == {"keep", "keep-route"}


async def test_background_retention_keeps_complete_roots_and_clear_removes_children(
    runtime, monkeypatch
):
    monkeypatch.setattr("living_world.store.time.time", lambda: 1800000000.0)
    await runtime.update_settings({"debug": {"retain_per_category": 2}})
    for i in range(4):
        parent = runtime.debug.begin("news.reflect", {"index": i})
        for attempt in range(i + 1):
            child = runtime.debug.begin(
                "provider.news.reflect", {"attempt": attempt}, parent_id=parent["id"]
            )
            runtime.debug.patch(
                child,
                http_calls=[{"id": f"{i}-{attempt}", "request_body": "{}", "response_body": "{}"}],
            )
            runtime.debug.finish(child, {"completion_text": "ok"})
        runtime.debug.finish(parent, {"completion_text": "done"})
    views = runtime.debug.views()
    assert len(views) == 2
    assert {len(v["calls"]) for v in views} == {3, 4}
    assert all(runtime.store.get("debug_records", rid) for v in views for rid in v["record_ids"])
    runtime.debug.clear("provider.news.reflect")
    assert not runtime.store.list("debug_records")


async def test_raw_export_is_unwrapped_and_cleared_calls_do_not_reappear(runtime):
    row = runtime.debug.begin("provider.reply", {})
    request = '{ "messages": [{"role":"user","content":"你好\\n下一行"}], "model":"test" }'
    response = '{"unrecognized_vendor_field":{"keep":true},"choices":[]}'
    runtime.debug.patch(
        row,
        http_calls=[
            {
                "id": "wire-1",
                "request_body": request,
                "response_body": response,
                "response_type": "json",
            }
        ],
    )
    runtime.debug.finish(row, {"completion_text": "parsed"})
    assert runtime.debug.export_body("wire-1", "request")["body"] == request
    assert runtime.debug.export_body("wire-1", "response")["body"] == response
    assert json.loads(runtime.debug.export_body("wire-1", "request")["body"])["model"] == "test"
    runtime.debug.clear()
    runtime.debug.patch(row, http_calls=[{"id": "late"}])
    runtime.debug.finish(row, "late")
    assert not runtime.debug.views()
    with pytest.raises(ValueError, match="不存在"):
        runtime.debug.export_body("wire-1", "request")


def test_legacy_snapshot_never_becomes_api_body_and_day_adoption_requires_link():
    old = {
        "id": "old",
        "task": "life.plan",
        "request": {"prompt": "old prompt"},
        "response": {"completion_text": "old"},
        "created_at": 1,
    }
    views = build_views(
        [old], [{"full_request": {"prompt": "old prompt"}, "adopted_activities": ["unrelated"]}]
    )
    assert views[0]["legacy"] and not views[0]["calls"] and not views[0]["adopted"]
    assert views[0]["legacy_snapshot"]["prompt"] == "old prompt"
    matched = build_views(
        [old],
        [
            {
                "full_request": {"_debug_record_id": "old"},
                "adopted_activities": [{"title": "数学课"}],
                "status": "completed",
            }
        ],
    )
    assert matched[0]["adopted"][0]["content"]["activities"][0]["title"] == "数学课"


async def test_background_state_is_dynamic_and_life_snapshot_has_readable_sources(runtime):
    context = await runtime.context_text("global", reinforce=False)
    request = await runtime.prepare_request(
        "search.topic", "search", "选择选题", {"available_context": context}
    )
    assert "【日程与执行：今日日程】" in request["prompt"]
    assert "history_count" not in request["prompt"]
    assert "轻量状态" not in request["system_prompt"]
    assert any(row.get("block_id") == "schedule" for row in request["sources"])
    assert request["prompt"].startswith(request["template"] + "\n\n<living_world_context>")
    assert request["base_system_prompt"] in request["system_prompt"]
    assert "【角色补充资料】" not in request["prompt"]
