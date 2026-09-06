"""Prompt text is useful, provenance is exact, and storage fields stay out of it."""

import copy
import json
from datetime import datetime, timedelta

from astrbot.api.provider import ProviderRequest
from test_chat import GROUP, PRIVATE, Event, consume, runner_for

from living_world.chat import DYNAMIC_MARKER
from living_world.context import context_from_data, normalize_context

pytest_plugins = ("test_chat",)


def material():
    activity = {
        "id": "internal-activity-id",
        "start": "2026-09-06T12:00:00+08:00",
        "end": "2026-09-06T13:00:00+08:00",
        "title": "上数学课",
        "location": "教室",
        "sleep_state": "清醒",
        "description": "下课后讨论习题",
        "status": "running",
        "scope": PRIVATE,
        "actions": {"social": {"execution_id": "internal-action-id"}},
        "scope_overrides": {"other-private": {"description": "不应泄露的约定"}},
    }
    return {
        "current_time": "2026-09-06T12:15:00+08:00",
        "state": {
            "mood": "平静",
            "energy": 75,
            "location": "教室",
            "sleep_state": "清醒",
            "updated_at": "internal-update-time",
        },
        "schedule": {
            "status": "available",
            "notice": "计划不代表已经发生",
            "activities": [activity],
        },
        "activity": activity,
        "memories": [
            {
                "id": "internal-memory-id",
                "text": "昨天约好讨论习题",
                "kind": "event",
                "access_count": 22,
                "scope": PRIVATE,
            }
        ],
        "experiences": [{"id": "internal-event-id", "source": "fiction", "text": "忘带笔"}],
        "observations": [
            {
                "id": "internal-observation-id",
                "module": "bilibili",
                "title": "数学视频",
                "factual_summary": "介绍勾股定理",
                "impression": "想试试证明",
                "reading_basis": "public_video_memory",
                "sources": ["https://example.org/math"],
                "raw_text": "未选用的完整网页正文",
                "candidates": ["未选用候选"],
            }
        ],
    }


def test_readable_projection_keeps_evidence_and_excludes_storage_fields():
    data = material()
    before = copy.deepcopy(data)
    bundle = context_from_data(data)
    text = bundle["text"]
    assert data == before
    for expected in (
        "当前时间",
        "当前活动",
        "今日日程",
        "上数学课",
        "昨天约好讨论习题",
        "角色虚构经历",
        "忘带笔",
        "不是本次新观看",
        "https://example.org/math",
        "想试试证明",
    ):
        assert expected in text
    for unwanted in ("internal-", "access_count", "scope_overrides", PRIVATE, "未选用", "不应泄露"):
        assert unwanted not in text
    assert all(row["content"] in text for row in bundle["sources"])


def test_normalizer_changes_life_snapshot_only_and_keeps_task_schemas():
    data = {
        "context": json.dumps(material(), ensure_ascii=False),
        "activity": {"id": "required-edit-id", "actions": {"social": {"enabled": True}}},
        "schema": {"schedule": [{"id": "id required by task", "actions": "required"}]},
        "known": [{"id": "memory-update-target", "text": "旧记忆"}],
        "nested": [material()],
        "raw": "{not json}",
    }
    normalized, sources = normalize_context(data)
    assert "当前活动" in normalized["context"]
    assert "当前活动" in normalized["nested"][0]
    for key in ("activity", "schema", "known", "raw"):
        assert normalized[key] == data[key]
    assert sources and all(row["placement"].startswith("本轮动态资料：context") for row in sources)


async def test_chat_uses_chinese_context_and_exact_source_snapshot(world):
    runtime, manager, provider = world
    await runtime.update_settings({"character": {"profile": "喜欢数学的莉莉", "world": "校园生活"}})
    event = Event(PRIVATE, "数学")
    memory = runtime.memory.remember("数学课要带笔", scope=PRIVATE, person_id="qq:42")
    cid = await manager.new_conversation(PRIVATE)
    await manager.update_conversation(PRIVATE, cid, [{"role": "user", "content": "宿主较旧记录"}])
    history = [{"role": "user", "content": "本轮实际采用的历史"}]
    req = ProviderRequest(
        prompt=event.message_str,
        system_prompt="稳定人格",
        contexts=history,
        conversation=manager.rows[cid],
    )
    runner = await runner_for(world, event, req)
    await consume(runner)
    part = next(p for p in req.extra_user_content_parts if p.text.startswith(DYNAMIC_MARKER))
    entry = next(r for r in runtime.store.list("debug_records") if r["task"] == "chat.context")
    assert entry["request"]["injected_text"] == part.text
    sources = entry["request"]["sources"]
    historical = next(r for r in sources if r["title"] == "聊天历史")
    assert historical["content"] == history
    assert "本轮实际采用的历史" not in part.text
    assert "宿主较旧记录" not in json.dumps(sources, ensure_ascii=False)
    for unwanted in (
        "conversation_id",
        "history_status",
        "history_count",
        "current_speaker",
        memory["id"],
        cid,
    ):
        assert unwanted not in part.text
    assert "数学课要带笔" in part.text and "小明" in part.text
    assert "稳定人格" in req.system_prompt and "喜欢数学的莉莉" in req.system_prompt
    assert "喜欢数学的莉莉" not in part.text
    assert part.text in json.dumps(provider.calls[0], ensure_ascii=False).replace("\\n", "\n")
    assert runtime.store.get("memories", memory["id"])["access_count"] == 1


async def test_scoped_state_and_schedule_share_projection_without_private_leaks(world):
    runtime, _, _ = world
    now = datetime.fromisoformat("2026-09-06T12:00:00+08:00")
    runtime.life._now = lambda value=None: value or now
    activity = {
        "id": "activity",
        "date": str(now.date()),
        "start": now.isoformat(),
        "end": (now + timedelta(hours=1)).isoformat(),
        "title": "数学课",
        "status": "running",
        "scope": "global",
        "location": "教室",
        "scope_overrides": {
            PRIVATE: {"description": "和私聊对象约定复习", "location": "自习室"},
            "qq:FriendMessage:99": {"description": "另一个人的私人约定"},
        },
    }
    runtime.store.put("activities", "activity", activity)
    private = await runtime.context_bundle(PRIVATE)
    public = await runtime.context_bundle(GROUP)
    assert "和私聊对象约定复习" in private["text"] and "地点：自习室" in private["text"]
    assert "和私聊对象约定复习" not in public["text"]
    assert "另一个人的私人约定" not in private["text"] + public["text"]
    await runtime.update_settings({"modules": {"life": False}})
    disabled = await runtime.context_bundle(PRIVATE)
    assert "日程生活模块已关闭" in disabled["text"]
    assert "数学课" not in disabled["text"] and "自习室" not in disabled["text"]


async def test_group_text_excludes_record_metadata_but_preserves_quotes(world):
    runtime, _, _ = world
    runtime.chat.add_group_message(
        GROUP,
        {
            "id": "internal-message",
            "sender_id": "42",
            "sender_name": "小明",
            "text": "[引用老师: 带课本] 我记住了",
            "scope": GROUP,
            "conversation_id": "internal-conversation",
        },
    )
    event = Event(GROUP, "现在上课吗")
    req = ProviderRequest(prompt=event.message_str)
    await runtime.chat.augment(event, req)
    text = next(p.text for p in req.extra_user_content_parts if p.text.startswith(DYNAMIC_MARKER))
    assert "小明：[引用老师: 带课本] 我记住了" in text
    assert "internal-" not in text and "conversation_id" not in text and GROUP not in text
