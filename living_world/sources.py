"""Read-only, configurable external observations with explicit provenance."""

from __future__ import annotations

import asyncio
import hashlib
import html
import inspect
import json
import re
import time
import uuid
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import aiohttp
from defusedxml import ElementTree

from .life import character_timezone, parse_json, scope_allowed
from .prompts import PROMPTS

MAX_BYTES = 2 * 1024 * 1024
MAX_TEXT = 20000
BV_PATTERN = re.compile(r"\bBV[0-9A-Za-z]{10}\b")
BILIBILI_PLUGIN = "astrbot_plugin_bilibili_ai_bot"
SECRET_KEYS = frozenset(
    {"key", "api_key", "apikey", "token", "access_token", "password", "secret", "appid"}
)


def validate_url(value: str) -> str:
    parts = urlsplit(str(value))
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        raise ValueError("Source URLs must use HTTP or HTTPS.")
    if parts.username is not None or parts.password is not None:
        raise ValueError("Source URLs must not contain embedded credentials.")
    return str(value)


def source_url(value: str) -> str:
    """Keep source links useful without exposing common API credential parameters."""
    parts = urlsplit(validate_url(value))
    query = urlencode(
        [
            (key, item)
            for key, item in parse_qsl(parts.query, keep_blank_values=True)
            if key.lower() not in SECRET_KEYS
        ]
    )
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def plain_text(value: str) -> str:
    return html.unescape(re.sub(r"<[^>]*>", " ", str(value))).strip()


class ArticleText(HTMLParser):
    """Extract visible prose without running page scripts or fetching assets."""

    def __init__(self):
        super().__init__()
        self.hidden = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "nav", "footer", "header", "noscript"}:
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style", "nav", "footer", "header", "noscript"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden and data.strip():
            self.parts.append(data.strip())


class SourceService:
    def __init__(self, runtime):
        self.runtime = runtime
        self._session = None
        self._closed = False
        self._http_slots = asyncio.Semaphore(4)
        self._last_tick = None
        self._tick_lock = asyncio.Lock()
        self._weather_lock = asyncio.Lock()

    async def close(self) -> None:
        self._closed = True
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    async def _request(self, url: str, headers: dict | None = None) -> bytes:
        validate_url(url)
        if self._closed:
            raise RuntimeError("The source service has been closed.")
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=20),
                headers={"User-Agent": "AstrBot-LivingWorld/0.2"},
            )
        options = {"headers": headers, "allow_redirects": False} if headers else {}
        async with self._http_slots, self._session.get(url, **options) as response:
            response.raise_for_status()
            if headers and getattr(response, "status", 200) >= 300:
                raise ValueError("Authenticated API redirects are not followed.")
            validate_url(str(response.url))
            if response.content_length is not None and response.content_length > MAX_BYTES:
                raise ValueError("Source response exceeds the 2 MB limit.")
            chunks = []
            size = 0
            async for chunk in response.content.iter_chunked(65536):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValueError("Source response exceeds the 2 MB limit.")
                chunks.append(chunk)
            return b"".join(chunks)

    def _audit(self, task, request, result, scope="global", boundary="external_http"):
        audit = getattr(self.runtime, "audit_external", None)
        if audit:
            audit(task, request, result, scope=scope, boundary=boundary)

    async def _fetch(self, task, url, scope="global", headers=None):
        request = {"method": "GET", "url": source_url(url)}
        try:
            data = await (self._request(url, headers=headers) if headers else self._request(url))
        except BaseException as exc:
            self._audit(task, request, {"status": "failed", "error": type(exc).__name__}, scope)
            raise
        self._audit(
            task, request, {"status": "success", "body": data.decode("utf-8", "replace")}, scope
        )
        return data

    async def _complete(self, task, module, template, context, scope):
        complete = getattr(self.runtime, "complete", None)
        if complete:
            return await complete(task, module, template, context, scope=scope)
        return await self.runtime.generate(
            module, template + "\n" + json.dumps(context, ensure_ascii=False), scope=scope
        )

    async def _context(self, scope, query):
        get_context = getattr(self.runtime, "context_text", None)
        result = get_context(scope, query=query) if get_context else ""
        return await result if inspect.isawaitable(result) else result

    async def _allowed(self, module, scope):
        return (
            not self._closed
            and self.runtime.enabled(module)
            and await scope_allowed(self.runtime, scope)
        )

    async def _call_tool(self, name, arguments, scope, plugin_name=None):
        caller = getattr(self.runtime, "call_tool", self.runtime.host.call_tool)
        return await caller(name, arguments, scope, plugin_name=plugin_name)

    @staticmethod
    def _skip(reason: str, text: str = "") -> dict:
        return {"status": "skipped", "text": text, "reason": reason}

    @staticmethod
    def _fail(reason: str, text: str = "") -> dict:
        return {"status": "failed", "text": text, "reason": reason}

    @staticmethod
    def _links(text: str) -> list[str]:
        links = []
        for match in re.findall(r"https?://[^\s<>\"\]\)）]+", text):
            try:
                link = source_url(match.rstrip(".,;，。；"))
            except ValueError:
                continue
            if link not in links:
                links.append(link)
        return links[:30]

    @staticmethod
    def _tool_error(text: str) -> str:
        stripped = text.strip()
        if not stripped:
            return "empty_result"
        try:
            data = json.loads(stripped)
        except (json.JSONDecodeError, TypeError):
            data = None
        if isinstance(data, dict) and (
            data.get("error")
            or data.get("success") is False
            or data.get("status") in {"error", "failed"}
        ):
            return "tool_failed"
        if isinstance(data, dict) and data.get("results") == []:
            return "no_results"
        if re.match(
            r"^(?:⚠|❌|错误[:：]|error\b|exception\b|搜索失败|调用失败|请求失败|未配置|未登录|请先|请提供|找不到视频|获取视频信息失败|看视频时出错了|没有找到要观看)",
            stripped,
            re.IGNORECASE,
        ):
            return "tool_failed"
        if re.match(
            r"^(?:没搜到|没有找到|未找到|无搜索结果|no results\b|no matching\b)",
            stripped,
            re.IGNORECASE,
        ):
            return "no_results"
        return ""

    async def _feed(self, url: str, limit: int, scope="global") -> list[dict]:
        data = await self._fetch("news.feed", validate_url(url), scope)
        if len(data) > MAX_BYTES:
            raise ValueError("Source response exceeds the 2 MB limit.")
        root = ElementTree.fromstring(data)
        articles = []
        for node in root.iter():
            if str(node.tag).rsplit("}", 1)[-1] not in {"item", "entry"}:
                continue
            fields = {}
            link = ""
            for child in node:
                name = str(child.tag).rsplit("}", 1)[-1]
                value = plain_text("".join(child.itertext()))
                if name == "link":
                    candidate = child.get("href") or value
                    if not link or child.get("rel", "alternate") == "alternate":
                        link = candidate
                elif name not in fields:
                    fields[name] = value
            title = fields.get("title", "").strip()
            if not title:
                continue
            try:
                article_url = source_url(urljoin(url, link)) if link else source_url(url)
            except ValueError:
                article_url = source_url(url)
            articles.append(
                {
                    "title": title[:500],
                    "text": (
                        fields.get("description")
                        or fields.get("summary")
                        or fields.get("content")
                        or ""
                    )[:3000],
                    "url": article_url,
                    "feed": source_url(url),
                    "published": fields.get("pubDate")
                    or fields.get("published")
                    or fields.get("updated")
                    or "",
                }
            )
            if len(articles) >= limit:
                break
        return articles

    async def _news(self, query: str, scope="global") -> dict:
        settings = self.runtime.settings.get("news", {})
        feeds = [item["url"] for item in settings.get("sources", []) if item.get("enabled", True)]
        if "sources" not in settings:
            feeds = settings.get("feeds", [])
        if not feeds:
            return self._skip("not_configured", "尚未配置新闻 RSS/Atom 来源。")
        limit = max(1, min(30, int(settings.get("limit", 5))))
        urls = list(dict.fromkeys(str(feed) for feed in feeds))[:20]
        results = await asyncio.gather(
            *(self._feed(url, limit, scope) for url in urls), return_exceptions=True
        )
        articles, errors, seen = [], [], set()
        for index, result in enumerate(results):
            if isinstance(result, BaseException):
                if isinstance(result, asyncio.CancelledError):
                    raise result
                errors.append({"source_index": index, "error": type(result).__name__})
                continue
            for article in result:
                key = (article["url"], article["title"])
                if key not in seen:
                    seen.add(key)
                    articles.append(article)
        if query:
            matching = [
                article
                for article in articles
                if query.lower() in (article["title"] + article["text"]).lower()
            ]
            if matching:
                articles = matching
        if not articles:
            return (
                self._fail("all_sources_failed", "新闻来源均不可用。")
                if len(errors) == len(urls)
                else self._skip("no_results", "新闻来源未返回可读条目。")
            )
        if not await self._allowed("news", scope):
            return self._skip("module_disabled")
        selection = parse_json(
            await self._complete(
                "news.select",
                "news",
                PROMPTS["news.select"],
                {
                    "activity": query,
                    "context": await self._context(scope, query),
                    "candidates": articles,
                },
                scope,
            )
        )
        index = selection.get("index") if isinstance(selection, dict) else None
        if type(index) is not int or not 0 <= index < len(articles):
            return self._fail("invalid_selection", "模型没有选出有效新闻，未记录阅读成功。")
        selected = articles[index]
        basis = "feed_summary" if selected["text"] else "title_only"
        evidence = selected["text"] or selected["title"]
        if not await self._allowed("news", scope):
            return self._skip("module_disabled")
        try:
            page = await self._fetch("news.read", selected["url"], scope)
            parser = ArticleText()
            parser.feed(page.decode("utf-8", "replace"))
            body = "\n".join(parser.parts).strip()
            if len(body) >= 80:
                evidence, basis = body[:MAX_TEXT], "page_text"
        except Exception as exc:  # noqa: BLE001 - A feed summary remains valid evidence.
            errors.append({"stage": "article", "error": type(exc).__name__})
        raw = f"{selected['title']}\n{evidence}\n发布时间：{selected['published']}"
        return {
            "status": "success",
            "raw_text": raw[:MAX_TEXT],
            "sources": [selected["url"]],
            "kind": "read",
            "title": selected["title"],
            "selection_reason": str(selection.get("reason", "")),
            "reading_basis": basis,
            "items": [selected],
            "candidates": articles,
            "errors": errors,
        }

    async def _search(self, query: str, scope: str) -> dict:
        topic = parse_json(
            await self._complete(
                "search.topic",
                "search",
                PROMPTS["search.topic"],
                {"activity_or_question": query, "context": await self._context(scope, query)},
                scope,
            )
        )
        selected = str(topic.get("query", "")).strip() if isinstance(topic, dict) else ""
        if not selected:
            return self._fail("invalid_search_topic", "模型没有返回可用搜索词，未进行搜索。")
        if not await self._allowed("search", scope):
            return self._skip("module_disabled")
        search = getattr(self.runtime.host, "search", None)
        if search is None:
            return self._skip("not_configured", "AstrBot 网页搜索服务不可用。")
        try:
            raw = await search(selected, scope)
        except BaseException as exc:
            self._audit(
                "search.tool",
                {"query": selected},
                {"status": "failed", "error": type(exc).__name__},
                scope,
                "astrbot_search",
            )
            raise
        self._audit("search.tool", {"query": selected}, raw, scope, "astrbot_search")
        if isinstance(raw, dict):
            if raw.get("status") in {"failed", "skipped"}:
                return raw
            raw = raw.get("text", raw.get("result", json.dumps(raw, ensure_ascii=False)))
        raw = str(raw or "")
        error = self._tool_error(raw)
        if error:
            return self._skip(error, raw) if error == "no_results" else self._fail(error, raw)
        return {
            "status": "success",
            "raw_text": raw,
            "sources": self._links(raw) or ["astrbot:web-search"],
            "kind": "searched",
            "title": selected,
            "query": selected,
            "selection_reason": str(topic.get("reason", "")),
            "reading_basis": "search_results",
        }

    async def _weather(self, query: str, scope="global") -> dict:
        settings = self.runtime.settings.get("weather", {})
        host = str(settings.get("api_host", "")).strip()
        credential = str(settings.get("credential", "")).strip()
        location = query.strip() or str(settings.get("location", "")).strip()
        if not host or not credential:
            return self._skip("not_configured", "请配置和风天气的 API Host 与认证凭据。")
        if not location:
            return self._skip("missing_location", "请填写城市名称、城市 ID 或经度,纬度。")
        origin = host if "://" in host else "https://" + host
        parts = urlsplit(validate_url(origin))
        if parts.scheme != "https" or parts.path not in {"", "/"} or parts.query or parts.fragment:
            return self._fail("invalid_api_host", "API Host 应为 HTTPS 域名，不含接口路径。")
        origin = f"https://{parts.netloc}"
        mode = settings.get("auth_mode", "api_key")
        if mode not in {"api_key", "jwt"}:
            return self._fail("invalid_auth_mode")
        headers = (
            {"Authorization": f"Bearer {credential}"}
            if mode == "jwt"
            else {"X-QW-Api-Key": credential}
        )
        coordinates = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*", location)
        if coordinates:
            longitude, latitude = map(float, coordinates.groups())
        else:
            geo_url = (
                origin
                + "/geo/v2/city/lookup?"
                + urlencode({"location": location, "number": 1, "lang": "zh"})
            )
            geo = json.loads(await self._fetch("weather.location", geo_url, scope, headers=headers))
            if (
                not isinstance(geo, dict)
                or str(geo.get("code")) != "200"
                or not geo.get("location")
            ):
                return self._fail("location_not_found", "和风天气未能解析该地点。")
            city = geo["location"][0]
            latitude, longitude = float(city["lat"]), float(city["lon"])
            location = str(city.get("name") or location)
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            return self._fail("invalid_coordinates")
        url = f"{origin}/weather/v1/current/{latitude:.2f}/{longitude:.2f}?lang=zh&localTime=true"
        body = json.loads(await self._fetch("weather.current", url, scope, headers=headers))
        if (
            not isinstance(body, dict)
            or not isinstance(body.get("condition"), dict)
            or not isinstance(body.get("temperature"), dict)
        ):
            return self._fail("weather_provider_error", "和风天气未返回有效实时天气。")
        temperature = body["temperature"]
        if temperature.get("value") is None:
            return self._fail("invalid_weather", "和风天气没有返回温度数据。")
        summary = f"{location}：{body['condition'].get('text', '未知天气')}，{temperature['value']}{temperature.get('unit', '°C')}"
        feels = body.get("feelsLike", {})
        if feels.get("value") is not None:
            summary += f"，体感 {feels['value']}{feels.get('unit', '°C')}"
        attributions = body.get("metadata", {}).get("attributions", [])
        sources = [source_url(url)] + [
            source_url(item)
            for item in attributions
            if isinstance(item, str) and item.startswith(("http://", "https://"))
        ]
        return {
            "status": "success",
            "raw_text": json.dumps(body, ensure_ascii=False),
            "sources": sources,
            "kind": "weather",
            "location": location,
            "title": "和风天气 · " + location,
            "factual_summary": summary,
            "impression": "",
            "reading_basis": "qweather_current",
            "weather": body,
        }

    async def test_weather(self) -> dict:
        if not await self._allowed("weather", "global"):
            return self._skip("module_disabled", "请先启用天气模块并绑定人格。")
        try:
            return await self._weather("")
        except Exception as exc:  # noqa: BLE001 - Connection tests return diagnostics only.
            return self._fail(type(exc).__name__, "和风天气连接失败，请检查地点、API Host 与凭据。")

    async def refresh_weather(self, now: datetime | None = None) -> dict:
        """Refresh factual state every 90 minutes, backing off failures for 15 minutes."""
        if not await self._allowed("weather", "global"):
            return self._skip("module_disabled")
        tz = character_timezone(self.runtime.settings)
        now = now or datetime.now(tz)
        now = now.replace(tzinfo=tz) if now.tzinfo is None else now.astimezone(tz)
        fingerprint = hashlib.sha256(
            json.dumps(self.runtime.settings.get("weather", {}), sort_keys=True).encode()
        ).hexdigest()
        async with self._weather_lock:
            previous = self.runtime.store.get("weather_refresh", "current", {}) or {}
            interval = 90 * 60 if previous.get("status") == "success" else 15 * 60
            elapsed = now.timestamp() - float(previous.get("attempted_at", 0))
            if previous.get("settings_fingerprint") == fingerprint and elapsed < interval:
                return self._skip(
                    "cached" if previous.get("status") == "success" else "failure_backoff"
                )
            checkpoint = {
                "attempted_at": now.timestamp(),
                "settings_fingerprint": fingerprint,
                "status": "running",
            }
            self.runtime.store.put("weather_refresh", "current", checkpoint)
            try:
                result = await self.explore("weather")
                checkpoint.update(
                    {
                        "status": result["status"],
                        "reason": result.get("reason", ""),
                        "observation_id": result.get("id"),
                    }
                )
            except asyncio.CancelledError:
                checkpoint.update({"status": "skipped", "reason": "interrupted"})
                self.runtime.store.put("weather_refresh", "current", checkpoint)
                raise
            self.runtime.store.put("weather_refresh", "current", checkpoint)
            return result

    async def _bilibili(self, kind: str, query: str, scope: str) -> dict:
        settings = self.runtime.settings.get("bilibili", {})
        plugin = BILIBILI_PLUGIN
        try:
            api = self.runtime.host.bilibili_api(plugin)
            if inspect.isawaitable(api):
                api = await api
        except (ValueError, LookupError, AttributeError):
            return self._skip("dependency_unavailable", "B 站依赖插件或公开记忆 API v3 不可用。")
        if not api or getattr(api, "api_version", 0) < 3:
            return self._skip("dependency_unavailable", "B 站插件需要公开记忆 API v3。")
        if kind == "bilibili_recent":
            limit = max(1, min(30, int(settings.get("recent_limit", 5))))
            arguments = {
                "source": "bilibili",
                "memory_types": ["video"],
                "reader_scope": "bili_comment",
                "limit": limit,
            }
            try:
                records = api.get_recent_memories(
                    memory_types={"video"},
                    reader_scope="bili_comment",
                    source="bilibili",
                    limit=limit,
                )
                if inspect.isawaitable(records):
                    records = await records
            except BaseException as exc:
                self._audit(
                    "bilibili.public_memory",
                    arguments,
                    {"status": "failed", "error": type(exc).__name__},
                    scope,
                    "third_party_public_memory",
                )
                raise
            self._audit(
                "bilibili.public_memory",
                arguments,
                records,
                scope,
                "third_party_public_memory",
            )
            records = [
                record
                for record in records
                if isinstance(record, dict)
                and record.get("memory_type") == "video"
                and record.get("text")
            ][:limit]
            if not records:
                return self._skip("no_results", "没有可读取的公开视频记忆。")
            raw = "已读取 B 站插件的公开视频记忆（不代表今天新观看）：\n" + "\n".join(
                str(record["text"]) for record in records
            )
            return {
                "status": "success",
                "raw_text": raw[:MAX_TEXT],
                "sources": self._links(raw) or [f"plugin:{plugin}:public-video-memory"],
                "kind": "read",
                "from_memory": True,
                "title": "B 站公开视频记忆",
                "reading_basis": "public_video_memory",
            }
        if not query.strip():
            return self._skip("missing_query", "B 站搜索需要关键词，观看需要 BV 号。")
        if kind == "bilibili":
            raw = await self._call_tool(
                "search_bilibili",
                {"keyword": query, "search_type": "video"},
                scope,
                plugin_name=plugin,
            )
            raw = str(raw or "")[:MAX_TEXT]
            error = self._tool_error(raw)
            if error:
                return self._skip(error, raw) if error == "no_results" else self._fail(error, raw)
            if not raw.lstrip().startswith("视频搜索") or not BV_PATTERN.search(raw):
                return self._fail(
                    "unconfirmed_search_result", "B 站工具没有返回可确认的视频搜索结果。"
                )
            return {
                "status": "success",
                "raw_text": "B 站视频搜索结果（仅搜索，尚未观看）：\n" + raw,
                "sources": self._links(raw),
                "kind": "searched",
                "title": query,
                "reading_basis": "video_search_results",
            }
        bvid = BV_PATTERN.fullmatch(query.strip())
        if not bvid:
            return self._skip("invalid_bvid", "观看需要完整 BV 号。")
        raw = await self._call_tool(
            "watch_video", {"bvid": bvid.group()}, scope, plugin_name=plugin
        )
        raw = str(raw or "")[:MAX_TEXT]
        # The dependency appends an invitation to account writes; it is not evidence.
        raw = re.sub(r"\n?[（(]如需互动可调用 bili_action[^\n]*", "", raw).strip()
        if raw.startswith("[已看完视频]"):
            result_kind, from_memory = "watched", False
        elif raw.startswith(("[已从记忆读取视频]", "[曾经看过这个视频]")):
            result_kind, from_memory = "read", True
        else:
            return self._fail("unconfirmed_watch_result", raw or "B 站工具未确认观看完成。")
        return {
            "status": "success",
            "raw_text": raw,
            "sources": self._links(raw) or [f"https://www.bilibili.com/video/{bvid.group()}"],
            "kind": result_kind,
            "from_memory": from_memory,
            "title": bvid.group(),
            "reading_basis": "public_video_memory" if from_memory else "video_analysis",
        }

    async def _summarize(self, module: str, data: dict, scope: str) -> dict:
        if module == "weather":
            return {
                "factual_summary": data["factual_summary"],
                "impression": "",
                "reflection_status": "not_requested",
            }
        raw = data["raw_text"][:MAX_TEXT]
        try:
            response = await self._complete(
                f"{module}.reflect",
                module,
                PROMPTS["news.reflect"],
                {
                    "evidence_kind": data["kind"],
                    "reading_basis": data.get("reading_basis"),
                    "external_data": raw,
                    "sources": data["sources"],
                    "selection_reason": data.get("selection_reason", ""),
                    "context": await self._context(scope, data.get("title", "")),
                },
                scope,
            )
            summary = parse_json(response)
            if (
                not isinstance(summary, dict)
                or not isinstance(summary.get("factual_summary"), str)
                or not summary["factual_summary"].strip()
                or not isinstance(summary.get("impression"), str)
            ):
                raise ValueError("Invalid reflection JSON.")
            return {
                "factual_summary": summary["factual_summary"],
                "impression": summary["impression"],
                "reflection_status": "success",
            }
        except Exception:  # noqa: BLE001 - A model outage must not erase successfully obtained evidence.
            return {"factual_summary": raw, "impression": "", "reflection_status": "failed"}

    def _save_observation(self, module, data, reflection, scope, **extra):
        labels = {
            "read": "已读取来源资料",
            "searched": "搜索所得（不代表观看过）",
            "watched": "依赖插件已确认完成视频分析",
            "weather": "天气接口资料",
        }
        rendered = f"{labels[data['kind']]}：\n{reflection['factual_summary']}"
        if reflection.get("impression"):
            rendered += "\n角色感想：" + reflection["impression"]
        rendered += "\n阅读依据：" + data.get("reading_basis", "unknown")
        rendered += "\n来源：" + "\n".join(data["sources"])
        record = {
            **data,
            **reflection,
            **extra,
            "id": uuid.uuid4().hex,
            "text": rendered,
            "module": module,
            "scope": scope,
            "source": data["sources"][0] if data["sources"] else module,
            "created_at": time.time(),
            "from_memory": data.get("from_memory", False),
            "title": data.get("title", ""),
            "selection_reason": data.get("selection_reason", ""),
        }
        if reflection.get("reflection_status") == "failed":
            record.update({"status": "partial", "reason": "reflection_failed"})
        self.runtime.store.put("observations", record["id"], record)
        return record

    async def explore(self, kind: str, query: str = "", scope: str = "global") -> dict:
        module = "bilibili" if kind in {"bilibili", "bilibili_watch", "bilibili_recent"} else kind
        if module not in {"news", "search", "weather", "bilibili"}:
            return self._fail("unknown_source", "未知外部来源。")
        if self._closed:
            return self._skip("service_closed")
        if not self.runtime.enabled(module):
            return self._skip("module_disabled")
        if not await scope_allowed(self.runtime, scope):
            return self._skip("scope_not_allowed")
        try:
            if module == "news":
                data = await self._news(str(query), scope)
            elif module == "weather":
                data = await self._weather(str(query), scope)
            elif module == "search":
                data = await self._search(str(query), scope)
            else:
                data = await self._bilibili(kind, str(query), scope)
            if data["status"] != "success":
                return data
            if (
                self._closed
                or not self.runtime.enabled(module)
                or not await scope_allowed(self.runtime, scope)
            ):
                return self._skip("module_disabled")
            reflection = await self._summarize(module, data, scope)
            if (
                self._closed
                or not self.runtime.enabled(module)
                or not await scope_allowed(self.runtime, scope)
            ):
                return self._skip("module_disabled")
            record = self._save_observation(module, data, reflection, scope)
            return {"status": "success", **record, "errors": data.get("errors", [])}
        except Exception as exc:  # noqa: BLE001 - Source and dependency failures are isolated to this action.
            return self._fail(type(exc).__name__, "外部来源调用失败，未记录成功见闻。")

    async def _video_metadata(self, bvid: str) -> dict:
        if not BV_PATTERN.fullmatch(bvid):
            raise ValueError("A complete BVID is required.")
        url = "https://api.bilibili.com/x/web-interface/view?" + urlencode({"bvid": bvid})
        body = json.loads(await self._fetch("daily_digest.video_info", url))
        if (
            not isinstance(body, dict)
            or body.get("code") != 0
            or not isinstance(body.get("data"), dict)
        ):
            raise ValueError("Video metadata was not confirmed.")
        data = body["data"]
        return {
            "bvid": str(data.get("bvid", "")),
            "title": str(data.get("title", "")),
            "uid": str(data.get("owner", {}).get("mid", "")),
            "published_at": data.get("pubdate"),
        }

    async def _digest(self, source: dict, now: datetime) -> dict:
        if not await self._allowed("daily_digest", "global") or not self.runtime.enabled(
            "bilibili"
        ):
            return self._skip("module_disabled", "AI 日报依赖日报模块与 B 站模块同时启用。")
        keywords = source.get("keywords", "日报 早报")
        if isinstance(keywords, list):
            keywords = " ".join(str(item) for item in keywords)
        query = f"{source['name']} {keywords} {now:%Y-%m-%d}"
        search = await self._bilibili("bilibili", query, "global")
        if search["status"] != "success":
            return search
        candidates = list(dict.fromkeys(BV_PATTERN.findall(search["raw_text"])))[:10]
        verified, failures = [], []
        for bvid in candidates:
            if not await self._allowed("daily_digest", "global") or not self.runtime.enabled(
                "bilibili"
            ):
                return self._skip("module_disabled")
            try:
                info = await self._video_metadata(bvid)
                published = datetime.fromtimestamp(float(info["published_at"]), now.tzinfo)
                title_matches = any(word in info["title"] for word in str(keywords).split())
                if (
                    info["bvid"] == bvid
                    and info["uid"] == str(source["uid"])
                    and published.date() == now.date()
                    and title_matches
                ):
                    verified.append(info)
            except Exception as exc:  # noqa: BLE001 - A failed verification must not authorize watching.
                failures.append({"bvid": bvid, "error": type(exc).__name__})
        if not verified:
            return {
                **self._skip(
                    "unverified_author_or_date", "未找到能够确认作者与今日发布日期的日报，未观看。"
                ),
                "candidates": candidates,
                "verification_errors": failures,
            }
        video = max(verified, key=lambda item: float(item["published_at"]))
        if not await self._allowed("daily_digest", "global") or not self.runtime.enabled(
            "bilibili"
        ):
            return self._skip("module_disabled")
        data = await self._bilibili("bilibili_watch", video["bvid"], "global")
        if data["status"] != "success":
            return data
        data.update(
            {
                "title": video["title"],
                "selection_reason": f"独立定时读取 {source['name']} 的今日 AI 日报；已核对作者 UID 和发布日期。",
                "video": video,
                "digest_source_id": source["id"],
            }
        )
        if not await self._allowed("daily_digest", "global") or not self.runtime.enabled(
            "bilibili"
        ):
            return self._skip("module_disabled")
        reflection = await self._summarize("daily_digest", data, "global")
        if not await self._allowed("daily_digest", "global") or not self.runtime.enabled(
            "bilibili"
        ):
            return self._skip("module_disabled")
        record = self._save_observation("daily_digest", data, reflection, "global")
        record_event = getattr(self.runtime, "record_event", None)
        if record_event and record["status"] == "success":
            result = record_event(
                record["text"],
                "global",
                kind="read",
                source="daily_digest",
                key=f"digest:{now.date()}:{source['id']}",
            )
            if inspect.isawaitable(result):
                await result
        return record

    async def tick(self, now: datetime | None = None) -> None:
        """Run each due digest once; persist missed slots without restart catch-up."""
        if self._closed:
            return
        tz = character_timezone(self.runtime.settings)
        now = now or datetime.now(tz)
        now = now.replace(tzinfo=tz) if now.tzinfo is None else now.astimezone(tz)
        async with self._tick_lock:
            previous, self._last_tick = self._last_tick, now
            for source in self.runtime.settings.get("daily_digest", {}).get("sources", []):
                if not source.get("enabled", True):
                    continue
                hour, minute = map(int, source["time"].split(":"))
                due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if due > now:
                    continue
                key = f"{now.date()}:{source['id']}"
                crossed = previous is not None and previous <= due <= now
                in_minute = due <= now and (now - due).total_seconds() < 60
                stale_minutes = float(
                    self.runtime.settings.get("life", {}).get("stale_action_minutes", 10)
                )
                timely = (crossed or in_minute) and (
                    now - due
                ).total_seconds() <= stale_minutes * 60
                run = {
                    "id": key,
                    "source_id": source["id"],
                    "source_name": source["name"],
                    "date": str(now.date()),
                    "scheduled_at": due.isoformat(),
                    "started_at": now.isoformat(),
                    "status": "running" if timely else "skipped",
                    "reason": "" if timely else "missed_schedule",
                }
                if not self.runtime.store.claim("daily_digest_runs", key, run):
                    continue
                if not timely:
                    continue
                try:
                    if not await self._allowed(
                        "daily_digest", "global"
                    ) or not self.runtime.enabled("bilibili"):
                        result = self._skip("module_disabled")
                    else:
                        runner = getattr(self.runtime, "run", None)
                        result = (
                            await runner("daily_digest", self._digest(source, now))
                            if runner
                            else await self._digest(source, now)
                        )
                    run.update(
                        {
                            "status": result["status"],
                            "reason": result.get("reason", ""),
                            "result": result,
                            "observation_id": result.get("id"),
                        }
                    )
                except asyncio.CancelledError:
                    run.update({"status": "skipped", "reason": "interrupted_no_retry"})
                    self.runtime.store.put("daily_digest_runs", key, run)
                    raise
                except Exception as exc:  # noqa: BLE001 - One digest cannot stop another source.
                    run.update({"status": "failed", "reason": type(exc).__name__})
                self.runtime.store.put("daily_digest_runs", key, run)
