"""Automatic life material is compact, dated, scoped and separate from raw evidence."""

import copy
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from living_world.context import (
    clean_life_text,
    context_from_data,
    group_messages_text,
    observation_text,
    prepare_life_record,
    record_text,
)
from living_world.runtime import Runtime
from test_runtime import FakeHost, GROUP, PRIVATE

NOW = datetime(2026, 9, 9, 19, tzinfo=ZoneInfo("Asia/Shanghai"))
URL = "https://example.test/story?first=1&second=2"


def event(key="life:first", text="自由活动与准备休息。", at=None, scope="global", **extra):
    return {
        "id": key,
        "text": text,
        "source": "fiction",
        "kind": "event",
        "scope": scope,
        "created_at": (at or NOW.replace(hour=18)).isoformat(),
        **extra,
    }


def material(events=(), memories=()):
    return {
        "current_time": NOW.isoformat(),
        "timezone": "Asia/Shanghai",
        "schedule": {"status": "missing", "activities": []},
        "experiences": list(events),
        "memories": list(memories),
        "observations": [],
    }


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            f"中文正文\n阅读依据：search_results\n来源：{URL}\nhttps://example.test/another",
            "中文正文",
        ),
        (f"中文正文。来源：{URL}", "中文正文。"),
        ("第一段。\n\n第二段。", "第一段。\n\n第二段。"),
        (f"看看[观测指南]({URL})。", "看看观测指南。"),
        ('[指南](https://example.test/a_(b)?q=(c) "标题")', "指南"),
        ("参考[指南][s]\n[s]: https://example.test/a", "参考指南"),
        ("<https://example.test/a>\nwww.example.test/a", ""),
        (f"[{URL}]({URL})", ""),
        (
            "  - 经历，角色虚构经历：角色虚构经历：角色虚构生活：\n近期经历】\n"
            "- 角色虚构经历：角色虚构生活：自由活动与准备休息。\n"
            "- 角色虚构经历：角色虚构生活：",
            "自由活动与准备休息。",
        ),
    ],
)
def test_material_cleanup_preserves_prose(raw, expected):
    assert clean_life_text(raw) == expected
    assert clean_life_text(expected) == expected


def test_today_events_deduplicate_memory_lineage_without_rewriting_records():
    first = event(text="角色虚构生活：自由活动与准备休息。")
    memory = {
        **first,
        "id": "event:life:first",
        "text": "角色虚构经历：" + first["text"],
        "created_at": NOW.isoformat(),
    }
    older = event("life:old", "昨晚散步。", NOW - timedelta(days=1))
    timeless = {**event("life:missing", "日期未知。"), "created_at": "invalid"}
    data = material([first, older, timeless], [memory, {**older, "id": "event:life:old"}])
    before = copy.deepcopy(data)
    bundle = context_from_data(data)
    assert bundle["text"].count("角色经历（18：00）：自由活动与准备休息。") == 1
    assert "角色虚构日常" in bundle["text"]
    assert "昨晚散步" not in bundle["text"] and "日期未知" not in bundle["text"]
    assert "角色虚构经历：" not in bundle["text"]
    assert data == before
    assert all(row["content"] in bundle["text"] for row in bundle["sources"])


def test_legacy_exact_copy_and_distinct_occurrences():
    first = event()
    distinct = event("life:second", at=NOW.replace(hour=17))
    simultaneous = event("life:third")
    unlinked = {**first, "id": "old-memory", "source_event_id": ""}
    bundle = context_from_data(material([first, distinct, simultaneous], [unlinked]))
    assert bundle["text"].count("自由活动与准备休息。") == 3
    assert bundle["text"].index("18：00") < bundle["text"].index("17：00")
    private_copy = {**unlinked, "scope": PRIVATE}
    assert (
        context_from_data(material([first], [private_copy]))["text"].count("自由活动与准备休息。")
        == 2
    )


def test_character_timezone_midnight_and_dst():
    midnight = NOW.replace(hour=0, minute=5)
    yesterday_utc = {**event(), "created_at": "2026-09-08T16:01:00Z"}
    assert prepare_life_record(yesterday_utc, midnight) is not None
    assert record_text(prepare_life_record(yesterday_utc, midnight), midnight).startswith(
        "角色经历（00：01）："
    )
    assert prepare_life_record({**event(), "created_at": "2026-09-08T15:59:00Z"}, midnight) is None
    next_day = NOW + timedelta(days=1)
    assert prepare_life_record(event(), next_day) is None
    autumn = datetime(2026, 11, 1, 12, tzinfo=ZoneInfo("America/New_York"))
    data = material([{**event(), "created_at": "2026-11-01T04:30:00Z"}])
    data.update(current_time=autumn.isoformat(), timezone="America/New_York")
    assert "角色经历（00：30）" in context_from_data(data)["text"]


def test_missing_dates_do_not_remove_knowledge_profiles_or_promises():
    rows = [
        {"text": "天文知识", "kind": "knowledge", "source": "fiction"},
        {"text": "喜欢数学", "kind": "event", "profile": True, "source": "chat"},
        {"text": "下周一起复习", "kind": "event", "source": "chat"},
    ]
    text = context_from_data(material(memories=rows))["text"]
    assert all(row["text"] in text for row in rows)


def test_cross_scope_origin_is_not_read_or_used_as_time():
    row = {**event(), "id": "event:other", "created_at": "unknown", "scope": GROUP}
    secret = event("other", "私人约定", scope=PRIVATE)
    assert prepare_life_record(row, NOW, memory=True, event_lookup=lambda _: secret) is None


def test_legacy_missing_type_and_invalid_origin_timestamp_fallback():
    row = {
        **event(),
        "id": "event:old",
        "source": "",
        "text": "角色虚构生活：散步。",
        "created_at": NOW.isoformat(),
    }
    origin = {**event("old"), "occurred_at": "invalid"}
    projected = prepare_life_record(row, NOW, memory=True, event_lookup=lambda _: origin)
    assert record_text(projected, NOW, memory=True) == "角色经历（18：00）：散步。"
    assert record_text(prepare_life_record(projected, NOW), NOW) == "角色经历（18：00）：散步。"
    row["occurred_at"] = NOW.replace(hour=17).isoformat()
    assert "17：00" in record_text(
        prepare_life_record(row, NOW, memory=True, event_lookup=lambda _: origin), NOW
    )


def test_legacy_title_only_boundary_survives_source_footer_removal():
    row = {
        **event(),
        "source": "news",
        "text": f"已读取来源资料：天文新闻\n阅读依据：title_only\n来源：{URL}",
    }
    text = record_text(prepare_life_record(row, NOW), NOW)
    assert "仅标题" in text and "天文新闻" in text
    assert "title_only" not in text and "阅读依据：" not in text and URL not in text


def test_observation_fallback_and_raw_chat_links_are_separate():
    raw = f"搜索所得（不代表观看过）：\n中文摘要。\n阅读依据：search_results\n来源：{URL}"
    row = {"text": raw, "reading_basis": "search_results", "sources": [URL]}
    projected = observation_text(row)
    assert "搜索结果" in projected and "中文摘要" in projected
    assert URL not in projected and "阅读依据：" not in projected
    assert row["text"] == raw and row["sources"] == [URL]
    assert URL in group_messages_text([{"sender_name": "朋友", "text": f"看看 {URL}"}])


@pytest.fixture
async def world(tmp_path):
    runtime = Runtime(tmp_path / "material.sqlite", FakeHost())
    await runtime.update_settings(
        {"persona_id": "student", "sessions": [{"umo": PRIVATE}, {"umo": GROUP}]}
    )
    runtime.life._now = lambda value=None: value or NOW
    yield runtime
    await runtime.stop()


async def test_sqlite_event_time_dedup_recall_and_restart(world):
    first = world.record_event("角色虚构生活：自由活动。", source="fiction", key="life:first")
    memory = world.store.get("memories", "event:life:first")
    assert memory["text"] == first["text"] == "自由活动。"
    assert memory["source_event_id"] == first["id"]
    assert memory["occurred_at"] == first["occurred_at"] == NOW.isoformat()
    assert world.record_event("不应覆盖", source="fiction", key="life:first") == first
    assert world.store.get("memories", "event:life:first") == memory
    bundle = await world.context_bundle(PRIVATE, reinforce=False)
    assert bundle["text"].count("角色经历（19：00）：自由活动。") == 1
    world.life._now = lambda value=None: value or NOW + timedelta(days=1)
    assert "自由活动。" not in (await world.context_bundle(PRIVATE, reinforce=False))["text"]
    assert world.store.get("events", first["id"]) == first
    reopened = Runtime(world.store.db.execute("PRAGMA database_list").fetchone()[2], FakeHost())
    try:
        reopened.life._now = lambda value=None: value or NOW + timedelta(days=1)
        assert reopened.record_event("重启不覆盖", source="fiction", key=first["id"]) == first
        assert reopened.store.get("memories", memory["id"])["occurred_at"] == memory["occurred_at"]
    finally:
        await reopened.stop()


async def test_event_and_derived_memory_rollback_together(world, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("memory write failed")

    monkeypatch.setattr(world.memory, "remember", fail)
    with pytest.raises(RuntimeError, match="memory write failed"):
        world.record_event("活动正文", source="fiction", key="rollback")
    assert world.store.get("events", "rollback") is None
    assert world.store.get("memories", "event:rollback") is None


async def test_old_fiction_is_filtered_before_recall_limit_without_strengthening(world):
    for index in range(15):
        world.record_event(
            f"昨天重复的关键词 {index}",
            source="fiction",
            key=f"old:{index}",
            occurred_at=(NOW - timedelta(days=1)).isoformat(),
        )
    wanted = world.memory.remember("关键词的长期知识", kind="knowledge")
    previous = world.store.get("memories", "event:old:0")
    selected = world.memory.recall("关键词", limit=1, context_now=NOW)
    assert selected[0]["id"] == wanted["id"]
    assert world.store.get("memories", previous["id"]) == previous


async def test_legacy_origin_time_beats_recent_memory_creation_and_private_stays_private(world):
    raw = event("legacy", "角色虚构生活：昨天的生活", NOW - timedelta(days=1))
    world.store.put("events", "legacy", raw)
    memory = world.memory.remember(
        "角色虚构经历：" + raw["text"], source="fiction", key="event:legacy"
    )
    world.record_event("只在私聊中的经历", scope=PRIVATE, source="fiction", key="private")
    private = (await world.context_bundle(PRIVATE, reinforce=False))["text"]
    public = (await world.context_bundle(GROUP, reinforce=False))["text"]
    assert "昨天的生活" not in private + public
    assert "只在私聊中的经历" in private and "只在私聊中的经历" not in public
    assert world.store.get("memories", memory["id"]) == memory
    assert world.store.get("events", "legacy") == raw


async def test_detail_plan_preview_and_source_evidence_use_correct_material(world):
    raw = f"搜索所得：中文摘要\n角色感想：想了解天文\n阅读依据：search_results\n来源：{URL}"
    world.record_event(raw, source="search", kind="search", key="search:one")
    world.record_event("今天散步", source="fiction", key="today")
    world.record_event(
        "昨天散步",
        source="fiction",
        key="yesterday",
        occurred_at=(NOW - timedelta(days=1)).isoformat(),
    )
    world.memory.remember(f"天文知识，[观测指南]({URL})", kind="knowledge")
    activity = {
        "id": "future",
        "date": str(NOW.date()),
        "start": (NOW + timedelta(minutes=10)).isoformat(),
        "end": (NOW + timedelta(hours=1)).isoformat(),
        "scope": "global",
        "status": "planned",
        "title": "晚间散步",
    }
    world.store.put("activities", activity["id"], activity)
    detail = world.life.detail_request(activity)
    payload = json.dumps(detail["context"], ensure_ascii=False)
    assert payload.count("中文摘要") == 1
    assert "今天散步" in payload and "角色经历（19：00）" in payload
    assert "昨天散步" not in payload and URL not in payload and "search_results" not in payload
    plan = json.dumps(world.life.plan_request()["context"], ensure_ascii=False)
    assert URL not in plan and "昨天散步" not in plan and "今天散步" in plan
    preview = await world.build_test_request("life.detail")
    assert preview["dynamic_context"] == detail["context"]
    assert URL not in preview["prompt"]
    reflected = await world.prepare_request(
        "search.reflect",
        "search",
        "根据证据总结",
        {
            "external_data": raw,
            "sources": [URL],
            "reading_basis": "search_results",
            "context": await world.context_text(reinforce=False),
        },
        "global",
    )
    assert URL in reflected["prompt"]
    assert "阅读依据：search_results" in reflected["dynamic_context"]["external_data"]
    assert URL not in reflected["dynamic_context"]["context"]
    assert world.store.get("events", "search:one")["text"] == raw
