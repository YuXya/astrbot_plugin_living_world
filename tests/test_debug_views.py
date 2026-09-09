"""User-visible raw exports and retention of whole diagnostic calls."""

import json

import pytest
from test_debug import runtime as runtime_fixture

from living_world.debug_views import build_views

runtime = runtime_fixture


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
    assert "【今日日程】" in request["prompt"]
    assert "history_count" not in request["prompt"]
    assert "轻量状态" not in request["system_prompt"]
    assert any(row["title"] == "今日日程" for row in request["sources"])
    assert request["prompt"] == request["template"] + request["injected_text"]
