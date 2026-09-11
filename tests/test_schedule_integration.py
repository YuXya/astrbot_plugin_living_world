"""Exercise the real schedule/source/social pipeline with only external I/O replaced."""

import asyncio
import copy
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from living_world.runtime import Runtime
from test_life import action_counts

GROUP = "qq:GroupMessage:100"
ACTUAL_GROUP = "qq:GroupMessage:42_100"
PRIVATE = "qq:FriendMessage:42"
ZONE = ZoneInfo("Asia/Shanghai")
DAY = datetime(2030, 9, 5, tzinfo=ZONE)
NEWS_FACT = "天文台宣布周六举办公开观测活动"
SEARCH_FACT = "城市天文馆周六开放至晚上九点"
PRIVATE_FACT = "未公开的私人惊喜礼物计划"
FEED = "https://example.test/feed.xml"
ARTICLE = "https://example.test/observatory"


def generated_day():
    activities = []
    for index in range(10):
        start = DAY.replace(hour=9) + timedelta(hours=index)
        activities.append(
            {
                "start": start.isoformat(),
                "end": (start + timedelta(hours=1)).isoformat(),
                "title": "数学课与课间见闻" if index == 0 else f"日常活动 {index + 1}",
                "content": "继续当天生活，不把尚未执行的行动记成经历。",
                "location": "教室",
                "sleep_state": "清醒",
            }
        )
    return {"activities": activities}


class ExternalHost:
    def __init__(self):
        self.requests = []
        self.timeline = []
        self.sent = []
        self.history_scopes = []
        self.http_urls = []
        self.searches = []
        self.detail_row = generated_day()["activities"][0]
        self.detail_kinds = ("news", "search", "social")

    async def persona(self, persona_id):
        assert persona_id == "student"
        return "你是喜欢科学、每天上学的小夏。"

    async def session_persona(self, scope):
        return "student" if scope in {GROUP, ACTUAL_GROUP, PRIVATE} else "other"

    async def describe_model(self, provider_id, scope):
        return {"provider_id": provider_id or "test-model", "model": "fixture-model"}

    async def generate_request(self, request):
        self.requests.append(copy.deepcopy(request))
        task = request["task"]
        self.timeline.append(task)
        if task == "life.plan":
            response = generated_day()
        elif task == "life.detail":
            start = datetime.fromisoformat(self.detail_row["start"])
            response = {
                "description": "数学课间看了看窗外。",
                "incident": "忘带笔，先借一支。",
                "mood": "好奇",
                "actions": {
                    kind: {
                        "enabled": kind in self.detail_kinds,
                        "intent": {
                            "news": "了解今天感兴趣的科学新闻",
                            "search": "搜索天文馆的开放时间",
                            "social": "数学课有些无聊，去群里聊聊新见闻",
                        }[kind]
                        if kind in self.detail_kinds
                        else "",
                        "reason": "当前活动有具体兴趣"
                        if kind in self.detail_kinds
                        else "当前不需要",
                        "at": (start + timedelta(minutes=index)).isoformat()
                        if kind in self.detail_kinds
                        else None,
                    }
                    for index, kind in enumerate(("news", "search", "social"))
                },
            }
        elif task == "life.revise":
            response = {"updates": []}
        elif task == "news.select":
            assert ARTICLE in request["prompt"]
            response = {"index": 0, "reason": "喜欢天文，想了解公开观测活动"}
        elif task == "news.reflect":
            assert NEWS_FACT in request["prompt"]
            response = {
                "factual_summary": NEWS_FACT,
                "impression": "想听听同学是否也对观星感兴趣。",
            }
        elif task == "search.topic":
            response = {"query": "城市天文馆 开放时间", "reason": "刚看到观测活动，查一下周末安排"}
        elif task == "search.reflect":
            assert SEARCH_FACT in request["prompt"]
            response = {"factual_summary": SEARCH_FACT, "impression": "晚上也有机会去看看。"}
        elif task == "social.message":
            assert request["scope"] == ACTUAL_GROUP
            assert "群里在讨论函数" in request["prompt"]
            assert PRIVATE_FACT not in request["prompt"]
            response = "这道函数题先缓缓，有人周末想一起去天文馆吗？"
        elif task == "memory.query":
            response = {"keywords": ["天文台", "天文馆", "公开观测", "开放"]}
        elif task == "memory.reflect":
            materials = request["dynamic_context"]["materials"]
            response = {
                "memories": [
                    {
                        "judgment": fact,
                        "evidence": fact,
                        "attribute": "事实属性",
                        "owner": "self",
                        "stable": False,
                        "tags": ["天文"],
                    }
                    for fact in (NEWS_FACT, SEARCH_FACT)
                    if any(fact in material["text"] for material in materials)
                ]
            }
        elif task == "memory.feedback":
            response = {"feedback": []}
        else:
            raise AssertionError(f"Unexpected model task: {task}")
        text = response if isinstance(response, str) else json.dumps(response, ensure_ascii=False)
        return text, {"input_tokens": 10, "output_tokens": 10}, {"completion_text": text}

    async def search(self, query, scope):
        self.timeline.append("search.tool")
        self.searches.append((query, scope))
        return f"{SEARCH_FACT}。来源：https://example.test/museum-hours"

    async def history(self, scope):
        self.history_scopes.append(scope)
        assert scope == ACTUAL_GROUP
        return "小明：群里在讨论函数，最后一道题怎么做？"

    async def send(self, scope, text):
        self.timeline.append("qq.send")
        self.sent.append((scope, text))
        return True

    def group_history_enabled(self, scope):
        return True

    def host_interjection_enabled(self, scope):
        return False

    async def http(self, url, headers=None):
        self.http_urls.append(url)
        if url == FEED:
            self.timeline.append("news.feed")
            return (
                "<rss><channel><item><title>周末天文观测活动</title>"
                f"<link>{ARTICLE}</link><description>{NEWS_FACT}</description>"
                "</item></channel></rss>"
            ).encode()
        assert url == ARTICLE, "No unmocked network access is allowed"
        self.timeline.append("news.article")
        return (
            f"<html><article><h1>公开观测活动</h1><p>{NEWS_FACT}。"
            + "参加者可以在工作人员引导下观察夜空，活动以现场天气为准。" * 5
            + "</p></article></html>"
        ).encode()


def attach_external_io(runtime, host, clock):
    runtime.life._now = lambda now=None: now or clock[0]
    runtime.social._now = lambda: clock[0]
    runtime.sources._request = host.http


async def drain(runtime):
    while runtime.background:
        await asyncio.gather(*list(runtime.background))


async def configured_runtime(path, host, clock):
    runtime = Runtime(path, host)
    attach_external_io(runtime, host, clock)
    await runtime.update_settings(
        {
            "persona_id": "student",
            "modules": {"news": True, "search": True, "proactive": True},
            "models": {"default": "test-model"},
            "sessions": [{"umo": GROUP, "enabled": True, "weight": 1}],
            "social": {"cooldown_minutes": 0},
            "news": {
                "sources": [
                    {"id": "fixture-news", "name": "Fixture news", "url": FEED, "enabled": True}
                ]
            },
        }
    )
    for identifier in ("loneliness", "energy"):
        config = copy.deepcopy(runtime.drives.snapshot()["meters"][identifier]["config"])
        config["growth_per_hour"] = 0
        runtime.drives.save_settings(identifier, config)
    runtime.drives.set_value("loneliness", 50)
    runtime.drives.set_value("energy", 70)
    runtime.note_scope(ACTUAL_GROUP)
    runtime.memory.remember(PRIVATE_FACT, scope=PRIVATE)
    return runtime


@pytest.mark.asyncio
async def test_real_pipeline_orders_overlapping_actions_and_does_not_replay(tmp_path):
    host, clock = ExternalHost(), [DAY.replace(hour=6)]
    path = tmp_path / "living-world.sqlite"
    runtime = await configured_runtime(path, host, clock)
    try:
        await runtime.life.tick()
        await drain(runtime)
        plan = runtime.life.list_activities()
        assert len(plan) == 10
        assert [
            sum(item["actions"][kind]["enabled"] for item in plan)
            for kind in ("news", "search", "social")
        ] == [0, 0, 0]
        assert len([request for request in host.requests if request["task"] == "life.plan"]) == 1
        assert not host.sent and not host.http_urls

        clock[0] = DAY.replace(hour=8, minute=50)
        await runtime.life.tick()
        await drain(runtime)
        assert len([request for request in host.requests if request["task"] == "life.detail"]) == 1
        assert all(
            value["pending"] == 1 and value["started"] == 0
            for value in action_counts(runtime.life).values()
        )
        assert not host.sent and not host.http_urls
        clock[0] = DAY.replace(hour=9, minute=2)
        await runtime.life.tick()
        await drain(runtime)
        first = min(runtime.life.list_activities(), key=lambda item: item["start"])
        assert {
            kind: action["execution"]["status"] for kind, action in first["actions"].items()
        } == {"news": "success", "search": "success", "social": "success"}
        expected = [
            "news.feed",
            "news.select",
            "news.article",
            "news.reflect",
            "search.topic",
            "search.tool",
            "search.reflect",
            "social.message",
            "qq.send",
        ]
        positions = [host.timeline.index(event) for event in expected]
        assert positions == sorted(positions), host.timeline
        assert len(host.sent) == 1 and host.sent[0][0] == ACTUAL_GROUP
        assert host.history_scopes == [ACTUAL_GROUP]
        assert PRIVATE_FACT not in host.sent[0][1]
        observations = runtime.store.list("observations")
        assert {item["module"] for item in observations} == {"news", "search"}
        assert all(
            item["factual_summary"] and item["impression"] and item["sources"]
            for item in observations
        )
        # Extraction is background work; subsequent turns see the completed bank.
        recalled = await runtime.context_bundle(ACTUAL_GROUP, selection=["memory.recent"])
        assert NEWS_FACT in recalled["text"] and SEARCH_FACT in recalled["text"]
        assert len(runtime.store.list("actions")) == 3
        assert runtime.drives.snapshot()["meters"]["energy"]["value"] == 50
        assert runtime.drives.snapshot()["meters"]["loneliness"]["value"] == 40
        assert all(
            value["started"] == 1 and value["pending"] == 0
            for value in action_counts(runtime.life).values()
        )
        archive = runtime.store.get("life_days", f"{DAY.date()}:global")
        assert archive["raw_json"] and archive["full_request"]["task"] == "life.plan"

        # A repeat tick has no newly eligible action and must not retry prior work.
        counts = (len(host.sent), len(host.http_urls), len(host.searches))
        await runtime.life.tick()
        await drain(runtime)
        assert (len(host.sent), len(host.http_urls), len(host.searches)) == counts
        for index, kind in ((4, "news"), (5, "search"), (6, "social")):
            host.detail_row, host.detail_kinds = plan[index], (kind,)
            await runtime.life.detail(plan[index]["id"])
    finally:
        await runtime.stop()

    # Restart after additional planned actions have expired. No catch-up burst is allowed.
    clock[0] = DAY.replace(hour=15, minute=30)
    runtime = Runtime(path, host)
    attach_external_io(runtime, host, clock)
    try:
        await runtime.life.tick()
        await drain(runtime)
        assert len(host.sent) == 1 and len(host.http_urls) == 2 and len(host.searches) == 1
        assert len(runtime.store.list("actions")) == 3
        assert runtime.drives.snapshot()["meters"]["energy"]["value"] == 50
        assert runtime.drives.snapshot()["meters"]["loneliness"]["value"] == 40
        assert len([request for request in host.requests if request["task"] == "life.plan"]) == 1
        for index, kind in ((4, "news"), (5, "search"), (6, "social")):
            item = sorted(runtime.life.list_activities(), key=lambda row: row["start"])[index]
            assert item["actions"][kind]["execution"]["status"] == "skipped"
            assert item["actions"][kind]["execution"]["reason"] == "overdue_after_restart"
        assert len(runtime.store.list("observations")) == 2
        assert all(
            value["started"] == 1 and value["pending"] == 0
            for value in action_counts(runtime.life).values()
        )
    finally:
        await runtime.stop()


@pytest.mark.asyncio
async def test_disabled_action_modules_skip_flags_without_external_io_or_later_replay(tmp_path):
    host, clock = ExternalHost(), [DAY.replace(hour=6)]
    runtime = await configured_runtime(tmp_path / "living-world.sqlite", host, clock)
    try:
        await runtime.life.tick()
        await drain(runtime)
        clock[0] = DAY.replace(hour=8, minute=50)
        await runtime.life.tick()
        await drain(runtime)
        await runtime.update_settings(
            {"modules": {"news": False, "search": False, "proactive": False}}
        )
        disabled_at = clock[0]
        clock[0] = DAY.replace(hour=9, minute=2)
        await runtime.life.tick()
        await drain(runtime)
        first = min(runtime.life.list_activities(), key=lambda item: item["start"])
        for action in first["actions"].values():
            execution = action["execution"]
            assert execution["status"] == "skipped"
            assert execution["reason"] == "module_disabled"
            elapsed = (
                datetime.fromisoformat(execution["finished_at"]) - disabled_at
            ).total_seconds()
            assert 0 <= elapsed < 5
        assert first["status"] == "running" and runtime.enabled("life")
        assert not host.sent and not host.http_urls and not host.searches
        assert not runtime.store.list("observations") and not runtime.store.list("actions")
        assert all(
            value["started"] == 0 and value["pending"] == 0
            for value in action_counts(runtime.life).values()
        )
        assert runtime.store.list("memories"), "Disabling behavior must retain business data"

        await runtime.update_settings(
            {"modules": {"news": True, "search": True, "proactive": True}}
        )
        clock[0] += timedelta(minutes=1)
        await runtime.life.tick()
        await drain(runtime)
        assert not host.sent and not host.http_urls and not host.searches
        assert len([request for request in host.requests if request["task"] == "life.plan"]) == 1
    finally:
        await runtime.stop()


class ActionHost(ExternalHost):
    """Keep generated messages independent of source successes for action boundary tests."""

    def __init__(self):
        super().__init__()
        self.fail_social = False

    async def session_persona(self, scope):
        return "student" if scope in {GROUP, ACTUAL_GROUP, "qq:GroupMessage:200"} else "other"

    async def history(self, scope):
        self.history_scopes.append(scope)
        return "群友在聊今天的课程。"

    async def generate_request(self, request):
        if request["task"] != "social.message":
            return await super().generate_request(request)
        self.requests.append(copy.deepcopy(request))
        self.timeline.append("social.message")
        if self.fail_social:
            raise OSError("Fixture model unavailable")
        text = "课间休息了，大家今天学得怎么样？"
        return text, {}, {"completion_text": text}


async def prepare_first_detail(runtime, host, clock, kinds):
    await runtime.life.tick()
    await drain(runtime)
    host.detail_kinds = kinds
    clock[0] = DAY.replace(hour=8, minute=50)
    await runtime.life.tick()
    await drain(runtime)
    assert all(value["started"] == 0 for value in action_counts(runtime.life).values())
    clock[0] = DAY.replace(hour=9, minute=2)


@pytest.mark.parametrize("fail_social", [False, True])
async def test_one_selected_target_consumes_one_attempt_even_on_failure(tmp_path, fail_social):
    host, clock = ActionHost(), [DAY.replace(hour=6)]
    host.fail_social = fail_social
    runtime = await configured_runtime(tmp_path / "world.sqlite", host, clock)
    try:
        await runtime.update_settings(
            {
                "sessions": [
                    {"umo": GROUP, "enabled": True, "weight": 1},
                    {"umo": "qq:GroupMessage:200", "enabled": True, "weight": 1},
                ],
                "social": {"target_count": 2},
            }
        )
        await prepare_first_detail(runtime, host, clock, ("social",))
        await runtime.life.tick()
        await drain(runtime)
        assert len([r for r in host.requests if r["task"] == "social.message"]) == 1
        assert len(host.sent) == (0 if fail_social else 1)
        assert action_counts(runtime.life)["social"]["started"] == 1
        assert action_counts(runtime.life)["social"]["pending"] == 0
        assert len(runtime.store.list("life_action_usage")) == 1
        assert runtime.drives.snapshot()["meters"]["loneliness"]["value"] == 40
        assert runtime.drives.snapshot()["meters"]["energy"]["value"] == 70
        await runtime.life.tick()
        assert len([r for r in host.requests if r["task"] == "social.message"]) == 1
    finally:
        await runtime.stop()


@pytest.mark.parametrize("blocking", ["cooldown", "no_targets", "changed_before_generation"])
async def test_chat_skipped_before_message_generation_does_not_debit_loneliness(tmp_path, blocking):
    host, clock = ActionHost(), [DAY.replace(hour=6)]
    runtime = await configured_runtime(tmp_path / "world.sqlite", host, clock)
    try:
        await prepare_first_detail(runtime, host, clock, ("social",))
        if blocking == "no_targets":
            await runtime.update_settings({"sessions": []})
        else:
            checks = 0

            async def changed_control(scope, interjection=False):
                nonlocal checks
                checks += 1
                return "" if blocking == "changed_before_generation" and checks == 1 else "cooldown"

            runtime.social._control_reason = changed_control
        await runtime.life.tick()
        await drain(runtime)
        assert not any(r["task"] == "social.message" for r in host.requests)
        assert not host.sent
        assert action_counts(runtime.life)["social"]["started"] == 0
        assert action_counts(runtime.life)["social"]["pending"] == 0
        assert not runtime.store.list("life_action_usage")
        assert runtime.drives.snapshot()["meters"]["loneliness"]["value"] == 50
    finally:
        await runtime.stop()


@pytest.mark.parametrize("capability", ["news", "search", "search_unconfigured"])
async def test_missing_source_capability_does_not_start_or_charge_an_action(tmp_path, capability):
    host, clock = ActionHost(), [DAY.replace(hour=6)]
    runtime = await configured_runtime(tmp_path / "world.sqlite", host, clock)
    kind = "news" if capability == "news" else "search"
    try:
        await prepare_first_detail(runtime, host, clock, (kind,))
        if kind == "news":
            await runtime.update_settings({"news": {"sources": []}})
        elif capability == "search":
            host.search = None
        else:
            host.search_ready = lambda scope: False
        await runtime.life.tick()
        await drain(runtime)
        assert not host.sent and not host.searches and not host.http_urls
        assert not any(r["task"] in {"news.select", "search.topic"} for r in host.requests)
        assert action_counts(runtime.life)[kind]["started"] == 0
        assert action_counts(runtime.life)[kind]["pending"] == 0
        assert runtime.drives.snapshot()["meters"]["energy"]["value"] == 70
    finally:
        await runtime.stop()


async def test_manual_source_read_never_debits_drives_or_starts_schedule_actions(tmp_path):
    host, clock = ActionHost(), [DAY.replace(hour=6)]
    runtime = await configured_runtime(tmp_path / "manual.sqlite", host, clock)
    try:
        before = runtime.drives.snapshot()
        result = await runtime.action({"action": "explore", "source": "news", "query": "科学新闻"})
        assert result["status"] == "success" and host.http_urls
        assert runtime.drives.snapshot() == before
        assert not runtime.store.list("drive_debits")
        assert not runtime.store.list("life_action_usage")
        assert not host.sent
    finally:
        await runtime.stop()


async def test_source_failure_after_start_keeps_energy_debit_and_never_retries(tmp_path):
    host, clock = ActionHost(), [DAY.replace(hour=6)]
    runtime = await configured_runtime(tmp_path / "failure.sqlite", host, clock)
    calls = []
    try:
        await prepare_first_detail(runtime, host, clock, ("news",))

        async def failed_source(*args, **kwargs):
            calls.append(args)
            raise OSError("Source unavailable after the attempt started")

        runtime.sources._request = failed_source
        await runtime.life.tick()
        assert calls
        assert runtime.drives.snapshot()["meters"]["energy"]["value"] == 60
        assert runtime.drives.snapshot()["meters"]["loneliness"]["value"] == 50
        assert action_counts(runtime.life)["news"]["started"] == 1
        assert runtime.life.day_summary()["counts"]["news"]["failed"] == 1
        count = len(calls)
        await runtime.life.tick()
        assert len(calls) == count
        assert len(runtime.store.list("drive_debits")) == 1
    finally:
        await runtime.stop()


async def test_zero_energy_and_loneliness_do_not_block_scheduled_actions(tmp_path):
    host, clock = ExternalHost(), [DAY.replace(hour=6)]
    runtime = await configured_runtime(tmp_path / "zero.sqlite", host, clock)
    try:
        runtime.drives.set_value("loneliness", 0)
        runtime.drives.set_value("energy", 0)
        await prepare_first_detail(runtime, host, clock, ("news", "search", "social"))
        await runtime.life.tick()
        await drain(runtime)
        assert len(host.sent) == 1 and len(host.searches) == 1 and len(host.http_urls) == 2
        assert runtime.drives.snapshot()["meters"]["energy"]["value"] == 0
        assert runtime.drives.snapshot()["meters"]["loneliness"]["value"] == 0
        assert len(runtime.store.list("drive_debits")) == 3
        assert all(
            c["started"] == 1 and c["success"] == 1
            for c in runtime.life.day_summary()["counts"].values()
        )
    finally:
        await runtime.stop()
