"""Page edits return authoritative results without loading unrelated archives."""

import copy
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from living_world.runtime import Runtime
from test_runtime import FakeHost


def reject(*args, **kwargs):
    raise AssertionError("A local page edit must not load full state or call the host")


async def test_template_receipts_and_failed_writes(tmp_path, monkeypatch):
    runtime = Runtime(tmp_path / "page.sqlite", FakeHost())
    task = "memory.reflect"
    default = runtime.debug.get_default(task)
    try:
        with monkeypatch.context() as guard:
            for target, key in (
                (runtime, "snapshot"),
                (runtime.store, "list"),
                (runtime.debug, "trim"),
                (runtime.host, "generate_request"),
                (runtime.host, "catalogs"),
                (runtime.host, "send"),
            ):
                guard.setattr(target, key, reject)
            saved = await runtime.page_action(
                {"action": "save_template", "task": task, "template": "新模板"}
            )
            assert saved["template"] == "新模板"
            assert runtime.store.get("prompt_templates", task)["template"] == "新模板"
            with monkeypatch.context() as failed:
                failed.setattr(runtime.store, "put", reject)
                with pytest.raises(AssertionError):
                    await runtime.page_action({"action": "reset_template", "task": task})
            assert runtime.store.get("prompt_templates", task)["template"] == "新模板"
            reset = await runtime.page_action({"action": "reset_template", "task": task})
            assert reset["template"] == default
            assert runtime.store.get("prompt_templates", task)["template"] == default
    finally:
        await runtime.stop()


@pytest.mark.parametrize("batch", [False, True])
async def test_activity_page_receipt_only_reads_schedule(tmp_path, monkeypatch, batch):
    runtime = Runtime(tmp_path / "page.sqlite", FakeHost())
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    day = now.date() + timedelta(days=1)
    row = runtime.life._make_activity(
        {
            "title": "旧大纲",
            "start": "09:00",
            "end": "10:00",
            "content": "沿河散步",
            "location": "河边",
            "sleep_state": "清醒",
        },
        day,
        "global",
        "future",
    )
    row.update(detailed=True, description="旧细化正文", detail_version="before")
    runtime.store.put("activities", row["id"], row)
    original = runtime.store.list
    read_kinds = []

    def schedule_only(kind):
        assert kind in {"activities", "life_retired_activities", "life_detail_history"}
        read_kinds.append(kind)
        return original(kind)

    try:
        patch = {"title": "新大纲"}
        data = (
            {"action": "update_activities", "updates": [{"id": row["id"], "changes": patch}]}
            if batch
            else {"action": "update_activity", "id": row["id"], "patch": patch}
        )
        with monkeypatch.context() as guard:
            guard.setattr(runtime.store, "list", schedule_only)
            guard.setattr(runtime, "snapshot", reject)
            guard.setattr(runtime.host, "generate_request", reject)
            guard.setattr(runtime.host, "catalogs", reject)
            receipt = await runtime.page_action(data)
        assert set(receipt["page_state"]) == {"activities", "detail_history"}
        changed = receipt["result"][0] if batch else receipt["result"]
        assert changed["title"] == "新大纲" and not changed["detailed"]
        assert receipt["page_state"]["activities"] == [changed]
        assert receipt["page_state"]["detail_history"] == runtime.store.list("life_detail_history")
        assert receipt["page_state"]["detail_history"][0]["activity"]["description"] == "旧细化正文"
        before = copy.deepcopy(runtime.store.export())
        data = {
            "action": "update_activities",
            "updates": [{"id": row["id"], "changes": {"start": "2030-01-01"}}],
        }
        with pytest.raises(ValueError):
            await runtime.page_action(data)
        assert runtime.store.export() == before
        assert read_kinds and not runtime.host.calls and not runtime.host.sent
    finally:
        await runtime.stop()
