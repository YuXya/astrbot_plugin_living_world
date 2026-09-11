"""Recent schedule windows and task selection preserve scoped source snapshots."""

import copy
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from living_world.context import context_from_data, memory_blocks, recent_schedule_rows
from living_world.context_usage import DEFAULT_USAGE
from living_world.layout import DEFAULT_SETTINGS
from living_world.memory import MemoryService
from test_life import Runtime, fixed_service


NOW = datetime(2026, 9, 11, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
PRIVATE = "qq:FriendMessage:42"


def activity(key, start, end, **changes):
    return {
        "id": key,
        "date": str(NOW.date()),
        "start": start,
        "end": end,
        "title": key,
        "content": f"{key}的完整大纲。",
        "description": f"{key}的完整细节。",
        "incident": f"{key}的小插曲。",
        "location": "学校",
        "sleep_state": "清醒",
        "scope": "global",
        "status": "planned",
        **changes,
    }


def schedule(rows=None):
    return {
        "date": str(NOW.date()),
        "status": "available",
        "activities": rows
        if rows is not None
        else [
            activity("早读", "08:00", "08:30", status="completed"),
            activity("第一节课", "08:30", "09:15", status="completed"),
            activity("大课间休息", "09:30", "09:50", status="completed"),
            activity("第三节课：美术", "09:50", "10:35", status="running"),
            activity("第四节课：体育", "10:35", "11:20"),
        ],
    }


def material(rows=None, **changes):
    return {
        "current_time": NOW.isoformat(),
        "timezone": "Asia/Shanghai",
        "schedule": schedule(rows),
        "experiences": [],
        "memories": [],
        "observations": [],
        **changes,
    }


def projected(data, **kwargs):
    return {row["block_id"]: row for row in context_from_data(data, **kwargs)["sources"]}


@pytest.mark.parametrize(
    ("clock", "expected"),
    [
        ("07:59", ["早读"]),
        ("08:00", ["早读", "第一节课"]),
        ("08:30", ["早读", "第一节课", "大课间休息"]),
        ("09:20", ["早读", "第一节课", "大课间休息"]),
        ("09:50", ["大课间休息", "第三节课：美术", "第四节课：体育"]),
        ("10:00", ["大课间休息", "第三节课：美术", "第四节课：体育"]),
        ("10:35", ["第三节课：美术", "第四节课：体育"]),
        ("11:20", ["第三节课：美术", "第四节课：体育"]),
        ("23:59", ["第三节课：美术", "第四节课：体育"]),
    ],
)
def test_recent_window_current_boundaries_gaps_and_day_edges(clock, expected):
    hour, minute = map(int, clock.split(":"))
    data = schedule()
    data["activities"].reverse()
    before = copy.deepcopy(data)
    actual = recent_schedule_rows(data, NOW.replace(hour=hour, minute=minute))
    assert [row["title"] for row in actual] == expected
    assert data == before


def test_both_schedule_blocks_keep_full_visible_prose_and_exact_status():
    data = material()
    data["schedule"]["activities"][3]["description"] = "水彩在纸上晕开。" * 300
    before = copy.deepcopy(data)
    blocks = projected(data)
    recent = blocks["schedule.recent"]["content"]
    complete = blocks["schedule"]["content"]
    assert recent.count("\n- ") == 2
    for row in data["schedule"]["activities"][2:]:
        for field in ("content", "description", "incident", "location", "sleep_state"):
            assert row[field] in recent and row[field] in complete
    assert "已结束" in recent and "进行中" in recent and "计划中，尚未发生" in recent
    assert "09:30—09:50" in recent
    assert "早读" not in recent and "早读" in complete
    assert blocks["schedule"]["title"].endswith("今日日程（完整）")
    assert blocks["schedule.recent"]["title"].endswith("今日日程（简版）")
    assert data == before


def test_recent_excludes_cancelled_retired_wrong_day_and_invalid_ranges():
    rows = [
        activity("失败", "08:00", "08:30", status="failed"),
        activity("跳过", "09:00", "09:30", status="skipped"),
        activity("未来", "11:00", "12:00"),
        activity("取消", "09:30", "10:10", status="cancelled"),
        activity("退役", "09:40", "10:20", status="retired"),
        activity("退役标记", "09:45", "10:25", retired=True),
        activity("昨日", "09:00", "10:00", date="2026-09-10"),
        activity("明日", "2026-09-12T09:00+08:00", "2026-09-12T10:00+08:00"),
        activity("坏时间", "bad", "bad"),
        activity("倒置范围", "2026-09-11 11:00+08:00", "2026-09-11 10:00+08:00"),
    ]
    assert [row["title"] for row in recent_schedule_rows(schedule(rows), NOW)] == [
        "失败",
        "跳过",
        "未来",
    ]
    text = projected(material(rows))["schedule.recent"]["content"]
    assert "执行失败" in text and "已跳过" in text


def test_recent_uses_character_timezone_and_never_fills_from_other_days():
    data = material(
        [activity("美术课", "2026-09-11T01:50:00Z", "2026-09-11T02:35:00Z")],
        current_time="2026-09-11T02:00:00Z",
    )
    assert "美术课" in projected(data)["schedule.recent"]["content"]
    yesterday = activity("昨夜睡眠", "2026-09-10T23:00+08:00", "2026-09-11T08:00+08:00")
    assert recent_schedule_rows(schedule([yesterday]), NOW.replace(hour=0)) == []
    tonight = activity("今夜睡眠", "23:00", "08:00")
    assert recent_schedule_rows(schedule([tonight]), NOW.replace(hour=23, minute=30)) == [tonight]
    assert recent_schedule_rows(schedule([tonight]), NOW + timedelta(days=1)) == []
    assert recent_schedule_rows({"status": "disabled", "activities": [tonight]}, NOW) == []
    assert recent_schedule_rows(schedule([]), NOW) == []


def test_v1_v2_projection_preserves_old_schedule_names_without_new_blocks():
    data = material()
    v1 = projected(data, legacy=True)
    assert "schedule.recent" not in v1 and v1["schedule"]["title"] == "今日日程"
    assert projected(data, version=1) == v1
    v2 = projected(data, version=2)
    assert "schedule.recent" not in v2
    assert v2["schedule"]["title"] == "日程与执行：今日日程"


def test_service_schedule_retains_scoped_full_prose_but_no_internal_overrides():
    runtime = Runtime()
    service = fixed_service(runtime, NOW)
    original = activity(
        "美术课",
        "09:50",
        "10:35",
        scope_overrides={
            PRIVATE: {"content": "私聊约定", "incident": "私聊的小插曲"},
            "qq:FriendMessage:99": {"content": "另一个私聊的秘密"},
        },
    )
    runtime.store.put("activities", original["id"], original)
    runtime.store.put("activities", "retired", {**original, "id": "retired", "title": "旧版"})
    runtime.store.put("life_retired_activities", "retired", {"id": "retired"})
    before = runtime.store.export()
    public = service.schedule_context()
    private = service.schedule_context(PRIVATE)
    assert len(public["activities"]) == len(private["activities"]) == 1
    for field in ("date", "content", "description", "incident"):
        assert public["activities"][0][field] == original[field]
    for block in projected(material(schedule=private)).values():
        if block["block_id"].startswith("schedule"):
            assert "私聊约定" in block["content"] and "私聊的小插曲" in block["content"]
            assert "另一个私聊" not in block["content"]
    for row in (public["activities"][0], private["activities"][0]):
        assert "scope_overrides" not in row and "scope" not in row
    assert runtime.store.export() == before
    runtime.store.close()


def test_unselected_experiences_and_memory_categories_do_not_suppress_selected_sources():
    origin = {"id": "event-a", "text": "同一事件", "source": "news", "kind": "event"}
    memory = {**origin, "id": "memory-a", "source_event_id": origin["id"]}
    other = {**memory, "id": "memory-b", "kind": "knowledge", "text": "实际勾选的知识"}
    data = material(
        experiences=[origin], memories=[memory, other], context_selection=["memory.knowledge"]
    )
    before = copy.deepcopy(data)
    blocks = projected(data)
    assert "实际勾选的知识" in blocks["memory.knowledge"]["content"]
    assert blocks["memory.knowledge"]["memory_ids"] == ["memory-b"]
    data["context_selection"] = ["memory.event"]
    assert "同一事件" in projected(data)["memory.event"]["content"]
    data["context_selection"] = ["experiences", "memory.event"]
    assert "memory.event" not in projected(data)
    data["context_selection"] = before["context_selection"]
    assert data == before
    assert "memory-b" not in blocks["memory.knowledge"]["content"]
    assert memory_blocks([other], NOW)[0]["memory_ids"] == ["memory-b"]


@pytest.mark.parametrize("task", ["life.plan", "life.revise", "life.detail"])
def test_life_memory_recall_filters_before_lineage_deduplication_and_does_not_reinforce(task):
    runtime = Runtime()
    runtime.settings["context_layout"] = copy.deepcopy(DEFAULT_SETTINGS)
    runtime.settings["context_layout"]["tasks"][task] = ["memory.knowledge"]
    runtime.memory = MemoryService(runtime)
    service = fixed_service(runtime, NOW)
    knowledge = runtime.memory.remember("采用的知识", kind="knowledge", source_event_id="same")
    runtime.memory.remember("未选情感", kind="emotional", source_event_id="same")
    before = runtime.store.export()
    result = service._memories("global", now=NOW, task=task)
    assert [row["id"] for row in result] == [knowledge["id"]]
    assert runtime.store.export() == before
    runtime.store.close()


def test_detail_unselected_action_records_do_not_remove_selected_memories():
    runtime = Runtime()
    runtime.settings["context_layout"] = copy.deepcopy(DEFAULT_SETTINGS)
    runtime.settings["context_layout"]["tasks"]["life.detail"] = ["memory.event"]
    runtime.memory = MemoryService(runtime)
    service = fixed_service(runtime, NOW)
    runtime.store.put("events", "e", {"id": "e", "text": "已读取的内容", "source": "news"})
    memory = runtime.memory.remember("已读取的内容", source="news", source_event_id="e")
    target = activity("之后的活动", "11:00", "12:00")
    result = service.detail_request(target)["context"]
    assert [row["id"] for row in result["相关记忆"]] == [memory["id"]]
    assert result["context_selection"] == ["memory.event"]
    runtime.settings["context_layout"]["tasks"]["life.detail"].append("task.actions")
    assert service.detail_request(target)["context"]["相关记忆"] == []
    runtime.store.close()


def test_detail_deduplicates_action_memory_before_applying_its_allowance():
    runtime = Runtime()
    runtime.settings["context_layout"] = copy.deepcopy(DEFAULT_SETTINGS)
    runtime.settings["context_layout"]["tasks"]["life.detail"] = ["memory.event", "task.actions"]
    runtime.settings["context_usage"] = copy.deepcopy(DEFAULT_USAGE)
    runtime.settings["context_usage"]["limits"]["memory.event"] = 1
    runtime.memory = MemoryService(runtime)
    service = fixed_service(runtime, NOW)
    replacement = runtime.memory.remember("另一条可用记忆")
    runtime.store.put("events", "e", {"id": "e", "text": "已读取的内容", "source": "news"})
    runtime.memory.remember("已读取的内容", source="news", source_event_id="e", important=True)
    target = activity("之后的活动", "11:00", "12:00")
    rows = service.detail_request(target)["context"]["相关记忆"]
    assert [row["id"] for row in rows] == [replacement["id"]]
    runtime.store.close()
