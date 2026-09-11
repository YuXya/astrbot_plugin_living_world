import copy
import json
from types import SimpleNamespace
from datetime import datetime

import pytest

from living_world.sources import MAX_BYTES, SourceService, source_url, validate_url

RSS = b"""<?xml version="1.0"?><rss><channel><item><title>Math news</title><link>https://news.test/math</link><description><![CDATA[<p>A new result.</p>]]></description><pubDate>Sat, 05 Sep 2026 10:00:00 +0800</pubDate></item></channel></rss>"""
ATOM = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Weather research</title><link href="/weather"/><summary>Forecast study.</summary><updated>2026-09-05T10:00:00Z</updated></entry></feed>"""
BV = "BV1xx411x7xx"


class Store:
    def __init__(self):
        self.data = {}

    def put(self, namespace, key, value):
        self.data.setdefault(namespace, {})[key] = copy.deepcopy(value)

    def list(self, namespace):
        return list(copy.deepcopy(self.data.get(namespace, {})).values())

    def claim(self, namespace, key, value):
        if key in self.data.get(namespace, {}):
            return False
        self.put(namespace, key, value)
        return True

    def get(self, namespace, key, default=None):
        return copy.deepcopy(self.data.get(namespace, {}).get(key, default))


class MemoryAPI:
    api_version = 3

    def __init__(self):
        self.calls = []
        self.records = [{"memory_type": "video", "text": "公开视频分析"}]

    def get_recent_memories(self, **kwargs):
        self.calls.append(kwargs)
        return self.records


class Host:
    def __init__(self):
        self.api = MemoryAPI()
        self.calls = []
        self.reply = "搜索结果 https://search.test/result"

    async def call_tool(self, name, arguments, scope, plugin_name=None):
        self.calls.append((name, arguments, scope, plugin_name))
        return self.reply

    async def search(self, query, scope):
        self.calls.append(("astrbot_search", {"query": query}, scope, None))
        return self.reply

    def bilibili_api(self, plugin_name):
        if self.api is None:
            raise ValueError("unavailable")
        return self.api


class Runtime:
    def __init__(self):
        self.store = Store()
        self.host = Host()
        self.settings = {
            "news": {"feeds": [], "limit": 5},
            "search": {"tool_name": "search_web", "query_argument": "query"},
            "weather": {
                "api_host": "",
                "auth_mode": "api_key",
                "credential": "",
                "location": "香港",
            },
            "bilibili": {"plugin_name": "astrbot_plugin_bilibili_ai_bot", "recent_limit": 5},
            "daily_digest": {
                "sources": [
                    {
                        "id": "heya",
                        "name": "黑鸦 Heya",
                        "uid": "3706929260006322",
                        "time": "12:00",
                        "keywords": "日报 早报",
                        "enabled": True,
                    }
                ]
            },
        }
        self.disabled = set()
        self.calls = []
        self.audits = []
        self.memory_jobs = []
        self.memory = SimpleNamespace(enqueue_material=self.enqueue_material)

    def enqueue_material(self, text, **metadata):
        self.memory_jobs.append({"text": text, **metadata})

    def kick_memory(self):
        pass

    def enabled(self, module):
        return module not in self.disabled

    async def generate(self, module, prompt, scope="global"):
        self.calls.append((module, prompt, scope))
        if "从候选新闻" in prompt:
            return '{"index":0,"reason":"数学课相关"}'
        if "一次网页搜索" in prompt:
            query = json.loads(prompt.split("\n", 1)[1])["activity_or_question"]
            return json.dumps({"query": query or "数学", "reason": "活动相关"})
        return '{"factual_summary":"来源中的实际资料。","impression":"这个发现让我好奇。"}'

    def audit_external(self, task, request, result, **kwargs):
        self.audits.append({"task": task, "request": request, "result": result, **kwargs})


@pytest.mark.asyncio
async def test_rss_atom_and_failed_feed_are_isolated_and_keep_sources():
    runtime = Runtime()
    runtime.settings["news"]["feeds"] = [
        "https://news.test/rss",
        "https://atom.test/feed",
        "https://broken.test/feed",
    ]
    service = SourceService(runtime)

    async def request(url):
        if "broken" in url:
            raise OSError("unreachable")
        return RSS if "rss" in url else ATOM

    service._request = request
    result = await service.explore("news", scope="private-a")
    assert result["status"] == "success"
    assert result["scope"] == "private-a"
    assert result["sources"] == ["https://news.test/math"]
    assert result["errors"] == [{"source_index": 2, "error": "OSError"}]
    assert "Math news" in result["raw_text"]
    assert len(result["candidates"]) == 2
    assert result["selection_reason"] == "数学课相关"
    assert all(url in result["text"] for url in result["sources"])


@pytest.mark.asyncio
async def test_all_feed_failures_do_not_create_observation():
    runtime = Runtime()
    runtime.settings["news"]["feeds"] = ["https://broken.test/feed"]
    service = SourceService(runtime)

    async def request(url):
        raise TimeoutError

    service._request = request
    result = await service.explore("news")
    assert result["status"] == "failed"
    assert runtime.store.list("observations") == []
    assert runtime.calls == []


@pytest.mark.asyncio
async def test_xml_external_entities_are_rejected():
    runtime = Runtime()
    runtime.settings["news"]["feeds"] = ["https://news.test/feed"]
    service = SourceService(runtime)

    async def request(url):
        return b'<!DOCTYPE root [<!ENTITY secret SYSTEM "file:///secrets">]><rss><channel><item><title>&secret;</title></item></channel></rss>'

    service._request = request
    assert (await service.explore("news"))["status"] == "failed"
    assert runtime.store.list("observations") == []


@pytest.mark.asyncio
async def test_weather_fixed_qweather_location_and_secret_source_redaction():
    runtime = Runtime()
    runtime.settings["weather"].update({"api_host": "weather.test", "credential": "secret-value"})
    service = SourceService(runtime)
    urls = []

    async def request(url, headers=None):
        urls.append((url, headers))
        if "/geo/" in url:
            return json.dumps(
                {"code": "200", "location": [{"name": "香港", "lat": "22.3", "lon": "114.17"}]}
            ).encode()
        return json.dumps(
            {
                "temperature": {"value": 29, "unit": "°C"},
                "condition": {"text": "晴"},
                "metadata": {"attributions": ["https://qweather.com/"]},
            }
        ).encode()

    service._request = request
    result = await service.explore("weather", scope="private-a")
    assert result["status"] == "success" and result["kind"] == "weather"
    assert "%E9%A6%99%E6%B8%AF" in urls[0][0]
    assert urls[0][1] == {"X-QW-Api-Key": "secret-value"}
    assert "/weather/v1/current/22.30/114.17" in urls[1][0]
    assert "secret-value" not in result["source"]
    assert "secret-value" not in result["text"]
    assert result["scope"] == "private-a"
    assert "secret-value" not in json.dumps(runtime.audits)
    assert result["impression"] == ""
    assert "29°C" in result["factual_summary"]


@pytest.mark.asyncio
async def test_weather_provider_error_not_recorded_as_real_weather():
    runtime = Runtime()
    runtime.settings["weather"].update(
        {"api_host": "weather.test", "credential": "secret", "location": "114.17,22.3"}
    )
    service = SourceService(runtime)

    async def request(url, headers=None):
        return b'{"success":false,"error":"invalid token"}'

    service._request = request
    result = await service.explore("weather")
    assert result["status"] == "failed"
    assert runtime.store.list("observations") == []


@pytest.mark.asyncio
async def test_search_automatically_uses_astrbot_and_keeps_scope():
    runtime = Runtime()
    runtime.settings["search"] = {"tool_name": "readonly_search", "query_argument": "keyword"}
    result = await SourceService(runtime).explore("search", "私下讨论的数学题", scope="private-a")
    assert runtime.host.calls == [
        ("astrbot_search", {"query": "私下讨论的数学题"}, "private-a", None)
    ]
    assert result["scope"] == "private-a"
    assert result["kind"] == "searched"
    assert "不代表观看过" in result["text"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply",
    ["搜索失败：连接超时", '{"status":"failed","message":"timeout"}', "Error: unavailable", ""],
)
async def test_tool_failure_text_is_not_success(reply):
    runtime = Runtime()
    runtime.host.reply = reply
    assert (await SourceService(runtime).explore("search", "数学"))["status"] == "failed"
    assert runtime.store.list("observations") == []


@pytest.mark.asyncio
async def test_bilibili_search_does_not_invoke_watch_or_interaction():
    runtime = Runtime()
    runtime.host.reply = f"视频搜索「数学」:\n《数学》| {BV} | https://www.bilibili.com/video/{BV}"
    result = await SourceService(runtime).explore("bilibili", "数学")
    assert result["status"] == "success" and result["kind"] == "searched"
    assert runtime.host.calls == [
        (
            "search_bilibili",
            {"keyword": "数学", "search_type": "video"},
            "global",
            "astrbot_plugin_bilibili_ai_bot",
        )
    ]
    assert "尚未观看" in result["raw_text"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prefix,expected",
    [("[已看完视频]", "watched"), ("[已从记忆读取视频]", "read"), ("[曾经看过这个视频]", "read")],
)
async def test_bilibili_watch_confirmation_distinguishes_cached_evidence(prefix, expected):
    runtime = Runtime()
    runtime.host.reply = f"{prefix}\n标题：数学\n链接：https://www.bilibili.com/video/{BV}\n（如需互动可调用 bili_action：点赞/投币）"
    result = await SourceService(runtime).explore("bilibili_watch", BV)
    assert result["status"] == "success" and result["kind"] == expected
    assert result["from_memory"] == (expected == "read")
    assert "bili_action" not in result["raw_text"]
    assert runtime.host.calls[0][0] == "watch_video"
    assert len(runtime.host.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply",
    [
        "找不到视频 BV123",
        "获取视频信息失败 BV123",
        "看视频时出错了: network error",
        "已在后台触发一次主动看B站视频流程。",
        "未知格式但好像成功",
    ],
)
async def test_unconfirmed_bilibili_watch_never_records_completion(reply):
    runtime = Runtime()
    runtime.host.reply = reply
    result = await SourceService(runtime).explore("bilibili_watch", BV)
    assert result["status"] == "failed"
    assert runtime.store.list("observations") == []


@pytest.mark.asyncio
async def test_bilibili_recent_only_requests_public_video_memory_api():
    runtime = Runtime()
    runtime.host.api.records.append({"memory_type": "chat", "text": "私人聊天不能进来"})
    result = await SourceService(runtime).explore("bilibili_recent", scope="private-a")
    assert result["status"] == "success" and result["kind"] == "read"
    assert "私人聊天不能进来" not in result["raw_text"]
    assert runtime.host.api.calls == [
        {
            "memory_types": {"video"},
            "reader_scope": "bili_comment",
            "source": "bilibili",
            "limit": 5,
        }
    ]
    assert runtime.host.calls == []


@pytest.mark.asyncio
async def test_public_memory_failure_is_audited_without_success_observation():
    runtime = Runtime()

    def unavailable(**kwargs):
        raise TimeoutError

    runtime.host.api.get_recent_memories = unavailable
    result = await SourceService(runtime).explore("bilibili_recent")
    assert result["status"] == "failed"
    assert runtime.audits[0]["task"] == "bilibili.public_memory"
    assert runtime.audits[0]["result"]["status"] == "failed"
    assert runtime.store.list("observations") == []


@pytest.mark.asyncio
async def test_empty_structured_search_results_do_not_create_impressions():
    runtime = Runtime()
    runtime.host.reply = '{"results":[]}'
    result = await SourceService(runtime).explore("search", "数学")
    assert result["reason"] == "no_results"
    assert len(runtime.calls) == 1
    assert runtime.store.list("observations") == []


@pytest.mark.asyncio
async def test_missing_bilibili_dependency_does_not_disable_search():
    runtime = Runtime()
    runtime.host.api = None
    service = SourceService(runtime)
    result = await service.explore("bilibili", "数学")
    assert result["reason"] == "dependency_unavailable"
    assert (await service.explore("search", "数学"))["status"] == "success"


@pytest.mark.asyncio
async def test_model_failure_preserves_actual_raw_evidence():
    runtime = Runtime()
    original = runtime.generate

    async def generate(module, prompt, **kwargs):
        if "一次网页搜索" in prompt:
            return await original(module, prompt, **kwargs)
        raise RuntimeError("model unavailable")

    runtime.generate = generate
    result = await SourceService(runtime).explore("search", "数学")
    assert result["status"] == "partial"
    assert runtime.host.reply in result["text"]
    assert result["raw_text"] == runtime.host.reply
    assert result["impression"] == ""
    assert result["reflection_status"] == "failed"


@pytest.mark.asyncio
async def test_disabling_during_summary_stops_successful_observation_write():
    runtime = Runtime()
    original = runtime.generate

    async def generate(module, prompt, **kwargs):
        if "一次网页搜索" in prompt:
            return await original(module, prompt, **kwargs)
        runtime.disabled.add("search")
        return "摘要"

    runtime.generate = generate
    result = await SourceService(runtime).explore("search", "数学")
    assert result["status"] == "skipped"
    assert runtime.store.list("observations") == []


@pytest.mark.asyncio
async def test_binding_change_during_summary_cannot_fall_back_to_stored_evidence():
    runtime = Runtime()
    permitted = True
    original = runtime.generate

    async def allowed(scope):
        return permitted

    async def generate(module, prompt, **kwargs):
        nonlocal permitted
        if "一次网页搜索" in prompt:
            return await original(module, prompt, **kwargs)
        permitted = False
        raise ValueError("persona changed")

    runtime.scope_allowed = allowed
    runtime.generate = generate
    result = await SourceService(runtime).explore("search", "数学", scope="private-a")
    assert result["status"] == "skipped"
    assert runtime.store.list("observations") == []


class FakeContent:
    def __init__(self, chunks):
        self.chunks = chunks

    async def iter_chunked(self, size):
        for chunk in self.chunks:
            yield chunk


class FakeResponse:
    url = "https://example.test/data"

    def __init__(self, chunks, content_length=None):
        self.content = FakeContent(chunks)
        self.content_length = content_length

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def raise_for_status(self):
        return None


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.closed = False

    def get(self, url):
        return self.response

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_chunked_http_body_is_bounded_and_session_closes():
    service = SourceService(Runtime())
    session = FakeSession(FakeResponse([b"a" * MAX_BYTES, b"b"]))
    service._session = session
    with pytest.raises(ValueError, match="2 MB"):
        await service._request("https://example.test/data")
    await service.close()
    assert session.closed
    assert (await service.explore("search", "query"))["reason"] == "service_closed"


@pytest.mark.asyncio
async def test_content_length_is_rejected_before_reading_body():
    service = SourceService(Runtime())
    service._session = FakeSession(FakeResponse([], content_length=MAX_BYTES + 1))
    with pytest.raises(ValueError, match="2 MB"):
        await service._request("https://example.test/data")


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://test/data",
        "data:text/plain,hi",
        "https:///nohost",
        "https://user:password@test/data",
    ],
)
def test_unsupported_urls_are_rejected(url):
    with pytest.raises(ValueError):
        validate_url(url)


def test_source_link_removes_credentials_but_preserves_resource():
    assert (
        source_url("https://example.test/weather?location=hk&api_key=secret#part")
        == "https://example.test/weather?location=hk"
    )


@pytest.mark.asyncio
async def test_news_has_ai_selection_then_actual_read_then_separate_impression():
    runtime = Runtime()
    runtime.settings["news"] = {
        "sources": [
            {"id": "test", "name": "Test", "url": "https://news.test/feed", "enabled": True}
        ],
        "limit": 5,
    }
    service = SourceService(runtime)
    requests = []

    async def request(url):
        requests.append(url)
        if url.endswith("/feed"):
            return RSS
        return b"<html><script>SECRET SCRIPT INSTRUCTION</script><article><p>Researchers obtained a mathematical result through a detailed proof and independent verification. The paper describes the assumptions and limitations of this finding.</p></article></html>"

    service._request = request
    result = await service.explore("news", "上数学课")
    assert requests == ["https://news.test/feed", "https://news.test/math"]
    assert result["reading_basis"] == "page_text"
    assert "SECRET SCRIPT" not in result["raw_text"]
    assert result["factual_summary"] != result["impression"]
    assert result["title"] == "Math news"
    assert len(runtime.calls) == 2
    assert [row["task"] for row in runtime.audits] == ["news.feed", "news.read"]


@pytest.mark.asyncio
async def test_invalid_news_selection_cannot_become_success():
    runtime = Runtime()
    runtime.settings["news"]["feeds"] = ["https://news.test/feed"]
    service = SourceService(runtime)

    async def request(url):
        return RSS

    async def generate(*args, **kwargs):
        return '{"index":99,"reason":"fake"}'

    service._request, runtime.generate = request, generate
    result = await service.explore("news")
    assert result["status"] == "failed"
    assert result["reason"] == "invalid_selection"
    assert runtime.store.list("observations") == []


@pytest.mark.asyncio
async def test_disabled_news_sources_are_not_requested():
    runtime = Runtime()
    runtime.settings["news"] = {
        "sources": [{"url": "https://disabled.test/feed", "enabled": False}]
    }
    service = SourceService(runtime)

    async def request(url):
        raise AssertionError("Disabled feeds must not run.")

    service._request = request
    assert (await service.explore("news"))["status"] == "skipped"


@pytest.mark.asyncio
async def test_weather_connection_test_does_not_write_life_data_or_call_model():
    runtime = Runtime()
    runtime.settings["weather"].update(
        {
            "api_host": "https://weather.test",
            "credential": "jwt-secret",
            "auth_mode": "jwt",
            "location": "114.17,22.3",
        }
    )
    service = SourceService(runtime)

    async def request(url, headers=None):
        assert headers == {"Authorization": "Bearer jwt-secret"}
        return b'{"condition":{"text":"Sunny"},"temperature":{"value":28,"unit":"C"}}'

    service._request = request
    assert (await service.test_weather())["status"] == "success"
    assert runtime.store.data == {}
    assert runtime.calls == []
    assert "jwt-secret" not in json.dumps(runtime.audits)


@pytest.mark.asyncio
async def test_weather_rejects_arbitrary_endpoint_and_http_credentials():
    runtime = Runtime()
    service = SourceService(runtime)
    for host in ["http://weather.test", "https://weather.test/custom?key=bad"]:
        runtime.settings["weather"].update({"api_host": host, "credential": "secret"})
        assert (await service.test_weather())["reason"] == "invalid_api_host"
    assert runtime.audits == []


@pytest.mark.asyncio
async def test_weather_refresh_cache_survives_restart_and_configuration_change():
    runtime = Runtime()
    runtime.settings["weather"].update(
        {"api_host": "weather.test", "credential": "secret", "location": "114.17,22.3"}
    )
    requests = []

    async def request(url, headers=None):
        requests.append(url)
        return b'{"condition":{"text":"Sunny"},"temperature":{"value":28,"unit":"C"}}'

    service = SourceService(runtime)
    service._request = request
    assert (await service.refresh_weather(datetime.fromisoformat("2026-09-05T12:00:00+08:00")))[
        "status"
    ] == "success"
    restarted = SourceService(runtime)
    restarted._request = request
    assert (await restarted.refresh_weather(datetime.fromisoformat("2026-09-05T13:00:00+08:00")))[
        "reason"
    ] == "cached"
    assert len(requests) == 1
    runtime.settings["weather"]["location"] = "114.17,23.0"
    assert (await restarted.refresh_weather(datetime.fromisoformat("2026-09-05T13:01:00+08:00")))[
        "status"
    ] == "success"
    assert len(requests) == 2
    assert runtime.calls == []


@pytest.mark.asyncio
async def test_weather_failure_has_fifteen_minute_backoff():
    runtime = Runtime()
    runtime.settings["weather"].update(
        {"api_host": "weather.test", "credential": "secret", "location": "114.17,22.3"}
    )
    service = SourceService(runtime)
    requests = []

    async def request(url, headers=None):
        requests.append(url)
        raise TimeoutError

    service._request = request
    assert (await service.refresh_weather(datetime.fromisoformat("2026-09-05T12:00:00+08:00")))[
        "status"
    ] == "failed"
    assert (await service.refresh_weather(datetime.fromisoformat("2026-09-05T12:01:00+08:00")))[
        "reason"
    ] == "failure_backoff"
    assert (await service.refresh_weather(datetime.fromisoformat("2026-09-05T12:15:00+08:00")))[
        "status"
    ] == "failed"
    assert len(requests) == 2
    assert runtime.store.list("observations") == []


@pytest.mark.asyncio
async def test_bilibili_plugin_name_is_fixed_despite_legacy_configuration():
    runtime = Runtime()
    runtime.settings["bilibili"]["plugin_name"] = "other_plugin"
    runtime.host.reply = f"视频搜索「数学」: {BV}"
    assert (await SourceService(runtime).explore("bilibili", "数学"))["status"] == "success"
    assert runtime.host.calls[0][-1] == "astrbot_plugin_bilibili_ai_bot"


def digest_fixture(runtime, now, *, uid=None, watched="[已看完视频]"):
    service = SourceService(runtime)

    async def tool(name, arguments, scope, plugin_name=None):
        runtime.host.calls.append((name, arguments, scope, plugin_name))
        if name == "search_bilibili":
            return f"视频搜索「日报」: {BV} https://www.bilibili.com/video/{BV}"
        return f"{watched}\n今日 AI 资讯视频分析。"

    async def info(bvid):
        return {
            "bvid": bvid,
            "title": "今日 AI 日报",
            "uid": uid or "3706929260006322",
            "published_at": now.timestamp(),
        }

    runtime.host.call_tool = tool
    service._video_metadata = info
    return service


@pytest.mark.asyncio
async def test_daily_digest_is_once_and_does_not_consume_activity_or_social_quota():
    runtime = Runtime()
    now = datetime.fromisoformat("2026-09-05T12:00:20+08:00")
    service = digest_fixture(runtime, now)
    await service.tick(now)
    await service.tick(datetime.fromisoformat("2026-09-05T12:01:20+08:00"))
    assert [call[0] for call in runtime.host.calls] == ["search_bilibili", "watch_video"]
    run = runtime.store.list("daily_digest_runs")[0]
    assert run["status"] == "success"
    assert run["result"]["module"] == "daily_digest"
    assert run["result"]["kind"] == "watched"
    assert set(runtime.store.data) == {"daily_digest_runs", "observations"}


@pytest.mark.asyncio
async def test_daily_digest_restart_does_not_catch_up_or_repeat_attempt():
    runtime = Runtime()
    now = datetime.fromisoformat("2026-09-05T15:00:00+08:00")
    service = digest_fixture(runtime, now)
    await service.tick(now)
    assert runtime.host.calls == []
    assert runtime.store.list("daily_digest_runs")[0]["reason"] == "missed_schedule"
    restarted = digest_fixture(runtime, now)
    await restarted.tick(datetime.fromisoformat("2026-09-05T12:00:10+08:00"))
    assert runtime.host.calls == []


@pytest.mark.asyncio
async def test_daily_digest_unconfirmed_author_never_calls_watch():
    runtime = Runtime()
    now = datetime.fromisoformat("2026-09-05T12:00:00+08:00")
    service = digest_fixture(runtime, now, uid="wrong_author")
    await service.tick(now)
    assert [call[0] for call in runtime.host.calls] == ["search_bilibili"]
    assert runtime.store.list("daily_digest_runs")[0]["reason"] == "unverified_author_or_date"
    assert runtime.store.list("observations") == []


@pytest.mark.asyncio
async def test_daily_digest_old_public_memory_is_not_new_watch():
    runtime = Runtime()
    now = datetime.fromisoformat("2026-09-05T12:00:00+08:00")
    service = digest_fixture(runtime, now, watched="[已从记忆读取视频]")
    await service.tick(now)
    observation = runtime.store.list("observations")[0]
    assert observation["kind"] == "read"
    assert observation["from_memory"] is True
    assert observation["reading_basis"] == "public_video_memory"


@pytest.mark.asyncio
async def test_daily_digest_disabled_slot_is_not_replayed_when_reenabled():
    runtime = Runtime()
    runtime.disabled.add("daily_digest")
    now = datetime.fromisoformat("2026-09-05T12:00:00+08:00")
    service = digest_fixture(runtime, now)
    await service.tick(now)
    runtime.disabled.clear()
    await service.tick(datetime.fromisoformat("2026-09-05T12:00:30+08:00"))
    assert runtime.host.calls == []
    assert runtime.store.list("daily_digest_runs")[0]["reason"] == "module_disabled"


@pytest.mark.asyncio
async def test_daily_digest_metadata_failure_is_explicit_and_isolated():
    runtime = Runtime()
    now = datetime.fromisoformat("2026-09-05T12:00:00+08:00")
    service = digest_fixture(runtime, now)

    async def unavailable(bvid):
        raise TimeoutError

    service._video_metadata = unavailable
    await service.tick(now)
    run = runtime.store.list("daily_digest_runs")[0]
    assert run["status"] == "skipped"
    assert run["result"]["verification_errors"] == [{"bvid": BV, "error": "TimeoutError"}]
    assert runtime.calls == []


@pytest.mark.asyncio
async def test_daily_digest_crossing_time_runs_without_exact_second_match():
    runtime = Runtime()
    now = datetime.fromisoformat("2026-09-05T12:01:00+08:00")
    service = digest_fixture(runtime, now)
    await service.tick(datetime.fromisoformat("2026-09-05T11:59:50+08:00"))
    await service.tick(now)
    assert runtime.store.list("daily_digest_runs")[0]["status"] == "success"
