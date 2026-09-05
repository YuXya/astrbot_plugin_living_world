import copy
import json

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
            "weather": {"url": "", "location": "香港"},
            "bilibili": {"plugin_name": "astrbot_plugin_bilibili_ai_bot", "recent_limit": 5},
        }
        self.disabled = set()
        self.calls = []

    def enabled(self, module):
        return module not in self.disabled

    async def generate(self, module, prompt, scope="global"):
        self.calls.append((module, prompt, scope))
        return "从来源获得的简短见闻。"


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
    assert result["sources"] == ["https://news.test/math", "https://atom.test/weather"]
    assert result["errors"] == [{"source_index": 2, "error": "OSError"}]
    assert "Math news" in result["raw_text"] and "Weather research" in result["raw_text"]
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
async def test_weather_configurable_json_location_and_secret_source_redaction():
    runtime = Runtime()
    runtime.settings["weather"]["url"] = "https://weather.test/{location}?key=secret-value&units=c"
    service = SourceService(runtime)
    urls = []

    async def request(url):
        urls.append(url)
        return json.dumps({"temperature": 29, "condition": "晴"}).encode()

    service._request = request
    result = await service.explore("weather", scope="private-a")
    assert result["status"] == "success" and result["kind"] == "weather"
    assert "%E9%A6%99%E6%B8%AF" in urls[0]
    assert "secret-value" in urls[0]
    assert "secret-value" not in result["source"]
    assert "secret-value" not in result["text"]
    assert result["scope"] == "private-a"


@pytest.mark.asyncio
async def test_weather_provider_error_not_recorded_as_real_weather():
    runtime = Runtime()
    runtime.settings["weather"]["url"] = "https://weather.test/current"
    service = SourceService(runtime)

    async def request(url):
        return b'{"success":false,"error":"invalid token"}'

    service._request = request
    result = await service.explore("weather")
    assert result["status"] == "failed"
    assert runtime.store.list("observations") == []


@pytest.mark.asyncio
async def test_search_respects_configured_argument_and_scope():
    runtime = Runtime()
    runtime.settings["search"] = {"tool_name": "readonly_search", "query_argument": "keyword"}
    result = await SourceService(runtime).explore("search", "私下讨论的数学题", scope="private-a")
    assert runtime.host.calls == [
        ("readonly_search", {"keyword": "私下讨论的数学题"}, "private-a", None)
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

    async def generate(*args, **kwargs):
        raise RuntimeError("model unavailable")

    runtime.generate = generate
    result = await SourceService(runtime).explore("search", "数学")
    assert result["status"] == "success"
    assert runtime.host.reply in result["text"]
    assert result["raw_text"] == runtime.host.reply


@pytest.mark.asyncio
async def test_disabling_during_summary_stops_successful_observation_write():
    runtime = Runtime()

    async def generate(*args, **kwargs):
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

    async def allowed(scope):
        return permitted

    async def generate(*args, **kwargs):
        nonlocal permitted
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
