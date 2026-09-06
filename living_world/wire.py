"""Observe the existing SDK HTTP exchange without replacing its transport."""

import asyncio
import functools
import json
import logging
import re
import time
import uuid
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

logger = logging.getLogger(__name__)
HIDDEN = "[认证信息已隐藏]"
AUTH_NAMES = {
    "authorization",
    "proxy-authorization",
    "api-key",
    "api_key",
    "apikey",
    "key",
    "x-api-key",
    "access_token",
    "refresh_token",
    "client_secret",
    "cookie",
    "set-cookie",
}


def sanitize_text(text, secrets=()):
    """Preserve body formatting except credential values, including escaped values."""
    for value in sorted(set(secrets), key=len, reverse=True):
        if value:
            text = text.replace(json.dumps(value, ensure_ascii=True)[1:-1], HIDDEN)
            text = text.replace(json.dumps(value, ensure_ascii=False)[1:-1], HIDDEN)
            text = text.replace(value, HIDDEN)
    return text


def safe_url(url):
    parts = urlsplit(str(url))
    host = parts.netloc.rsplit("@", 1)[-1]
    query = [(k, HIDDEN if k.lower() in AUTH_NAMES else v) for k, v in parse_qsl(parts.query)]
    return urlunsplit((parts.scheme, host, parts.path, urlencode(query), ""))


def sse_events(text):
    events = []
    for data in sse_data(text):
        if data and data != "[DONE]":
            try:
                events.append(json.loads(data))
            except (ValueError, TypeError):
                pass
    return events


def sse_data(text):
    # An unterminated event is incomplete even when its last bytes look like JSON.
    for block in re.split(r"\r?\n\r?\n", text)[:-1]:
        data = "\n".join(
            line[5:].lstrip(" ") for line in block.splitlines() if line.startswith("data:")
        )
        yield data


def reading_view(body, response_type="json"):
    """Derive a reading view; never use it as the captured response body."""
    result = {"text": "", "reasoning": "", "tool_calls": [], "usage": {}, "structured": None}
    if not body:
        return result
    try:
        values = sse_events(body) if response_type == "sse" else [json.loads(body)]
    except (ValueError, TypeError):
        return result
    tools = {}
    for value in values:
        if not isinstance(value, dict):
            continue
        # Responses streams provide an authoritative final object at completion.
        response = value.get("response")
        if isinstance(response, dict) and response.get("output"):
            value = response
            result.update(text="", reasoning="", tool_calls=[])
            tools.clear()
        if value.get("usage"):
            result["usage"] = value["usage"]
        for choice in value.get("choices") or []:
            if not isinstance(choice, dict):
                continue
            if choice.get("index", 0) != 0:
                continue
            message = choice.get("message") or choice.get("delta") or {}
            content = message.get("content")
            if isinstance(content, str):
                result["text"] += content
            elif isinstance(content, list):
                result["text"] += "\n".join(
                    p.get("text", "") for p in content if isinstance(p, dict)
                )
            result["reasoning"] += (
                message.get("reasoning_content") or message.get("reasoning") or ""
            )
            for tool in message.get("tool_calls", []):
                key = tool.get("index", tool.get("id", len(tools)))
                item = tools.setdefault(
                    key, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
                )
                if tool.get("id"):
                    item["id"] = tool["id"]
                fn = tool.get("function") or {}
                for field in ("name", "arguments"):
                    item["function"][field] += fn.get(field) or ""
        for output in value.get("output") or []:
            if not isinstance(output, dict):
                continue
            if output.get("type") == "function_call":
                tools[output.get("call_id", len(tools))] = output
            for part in output.get("content", []):
                if part.get("type") == "output_text":
                    result["text"] += part.get("text", "")
            for part in output.get("summary", []):
                if part.get("text"):
                    result["reasoning"] += part["text"]
        kind = value.get("type", "")
        if kind == "response.output_text.delta":
            result["text"] += value.get("delta", "")
        elif kind in {"response.reasoning_text.delta", "response.reasoning_summary_text.delta"}:
            result["reasoning"] += value.get("delta", "")
        elif (
            kind == "response.output_item.done"
            and value.get("item", {}).get("type") == "function_call"
        ):
            item = value["item"]
            tools[item.get("call_id", len(tools))] = item
        if value.get("error"):
            result["error"] = value["error"]
    result["tool_calls"] = list(tools.values())
    candidate = result["text"].strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*|\s*```$", "", candidate)
    try:
        result["structured"] = json.loads(candidate)
    except (ValueError, TypeError):
        pass
    return result


class HTTPAudit:
    def __init__(self, owner, current_call):
        self.owner = owner
        self.current_call = current_call
        self.clients = set()
        self.pending = {}

    def bind(self, provider):
        """Bind each concrete HTTP client, including clients replaced after key changes."""
        sdk = getattr(provider, "client", None)
        if not sdk or not any(c.__module__.startswith("openai.") for c in type(sdk).__mro__):
            return False
        client = getattr(sdk, "_client", None)
        original = getattr(client, "_send_single_request", None)
        if not callable(original):
            return False
        if id(client) in self.clients:
            return True

        @functools.wraps(original)
        async def send(request):
            exchange = self.begin(request)
            try:
                response = await original(request)
            except BaseException as exc:
                if exchange:
                    self.finish(
                        exchange,
                        status="cancelled" if isinstance(exc, asyncio.CancelledError) else "failed",
                        error=str(exc) or type(exc).__name__,
                    )
                raise
            if exchange:
                try:
                    self.observe_response(exchange, response)
                except Exception:
                    logger.warning("HTTP response capture unavailable", exc_info=True)
                    self.finish(
                        exchange, status="capture_failed", error="响应原文捕获失败；宿主继续处理"
                    )
            return response

        self.owner._patch(client, "_send_single_request", send)
        self.clients.add(id(client))
        return True

    def begin(self, request):
        try:
            active = self.current_call()
            if not active or not self.owner.active or not self.owner.runtime.enabled("debug"):
                return None
            entry = active["entry"]
            if not self.owner.runtime.store.get("debug_records", entry["id"]):
                return None
            path = request.url.path.rstrip("/")
            if request.method != "POST" or not path.endswith(("/chat/completions", "/responses")):
                return None
            secrets = []
            for key, value in request.headers.items():
                if key.lower() in AUTH_NAMES:
                    secrets.append(value)
                    if value.lower().startswith("bearer "):
                        secrets.append(value[7:])
            for key, value in parse_qsl(urlsplit(str(request.url)).query):
                if key.lower() in AUTH_NAMES:
                    secrets.append(value)
            call = {
                "id": uuid.uuid4().hex,
                "record_id": entry["id"],
                "provider_id": entry["request"].get("provider_id", ""),
                "model": entry["request"].get("model", ""),
                "method": request.method,
                "url": safe_url(request.url),
                "status": "running",
                "created_at": time.time(),
                "error": "",
                "request_body": None,
                "response_body": None,
                "response_type": "json",
                "capture_status": "captured",
                "http_status": None,
            }
            try:
                call["request_body"] = sanitize_text(request.content.decode("utf-8"), secrets)
                payload = json.loads(call["request_body"])
                call["model"] = payload.get("model", call["model"])
            except Exception:  # noqa: BLE001 - A diagnostic read must never block transmission.
                call.update(
                    capture_status="request_failed", error="请求正文不可读取，未使用中间参数代替"
                )
            entry.setdefault("http_calls", []).append(call)
            entry["http_capture"] = "captured"
            exchange = {
                "entry": entry,
                "call": call,
                "chunks": [],
                "secrets": secrets,
                "done": False,
            }
            self.pending[call["id"]] = exchange
            self.save(exchange)
            return exchange
        except Exception:
            logger.warning("HTTP request capture failed", exc_info=True)
            return None

    def save(self, exchange):
        if self.owner.active:
            entry = exchange["entry"]
            self.owner.runtime.debug.patch(
                entry,
                http_capture=entry.get("http_capture"),
                http_calls=entry.get("http_calls", []),
            )

    def finish(self, exchange, status=None, error=""):
        try:
            if exchange["done"]:
                return
            exchange["done"] = True
            self.pending.pop(exchange["call"]["id"], None)
            call = exchange["call"]
            if exchange["chunks"]:
                body = b"".join(exchange["chunks"]).decode("utf-8", errors="replace")
                call["response_body"] = sanitize_text(body, exchange["secrets"])
            body = call["response_body"] or ""
            events = (
                [e for e in sse_events(body) if isinstance(e, dict)]
                if call["response_type"] == "sse"
                else []
            )
            event_types = {e.get("type") for e in events}
            terminal = (
                any(data == "[DONE]" for data in sse_data(body))
                or "response.completed" in event_types
            )
            call["status"] = status or (
                "failed"
                if (call["http_status"] or 0) >= 400 or "response.failed" in event_types
                else "partial"
                if call["response_type"] == "sse"
                and (not terminal or "response.incomplete" in event_types)
                else "success"
            )
            call["error"] = sanitize_text(error, exchange["secrets"]) or call["error"]
            call["finished_at"] = time.time()
            # Save evidence independently from best-effort display parsing.
            self.save(exchange)
            exchange["chunks"].clear()
            try:
                call["reading"] = reading_view(call["response_body"], call["response_type"])
                for event in events:
                    if event.get("type") in {"response.failed", "response.incomplete"}:
                        call["reading"]["error"] = (
                            event.get("response", {}).get("error")
                            or event.get("response", {}).get("incomplete_details")
                            or event.get("type")
                        )
            except Exception:
                logger.warning(
                    "HTTP response reading view failed; raw body retained", exc_info=True
                )
                call["reading"] = {"error": "阅读版解析失败；请查看第三项保留的接口原文"}
            self.save(exchange)
        except Exception:
            logger.warning("HTTP capture persistence failed", exc_info=True)

    def finish_call(self, entry, status="success", error=""):
        if entry:
            for exchange in list(self.pending.values()):
                if exchange["entry"]["id"] == entry["id"]:
                    self.finish(
                        exchange, status="cancelled" if status == "cancelled" else None, error=error
                    )

    def close(self):
        for exchange in list(self.pending.values()):
            self.finish(
                exchange, status="partial", error="调试记录或插件已关闭；仅保留关闭前已收到的原文"
            )
            exchange["done"] = True
        self.pending.clear()
        self.clients.clear()

    def observe_response(self, exchange, response):
        call = exchange["call"]
        content_type = response.headers.get("content-type", "")
        call.update(
            http_status=response.status_code,
            response_type="sse"
            if "event-stream" in content_type
            else "json"
            if "json" in content_type
            else "text",
        )
        # Mock/cached responses may have already been read by their transport.
        if response.is_stream_consumed:
            exchange["chunks"].append(response.content)
            self.finish(exchange)
            return
        original_bytes, original_close = response.aiter_bytes, response.aclose

        async def aiter_bytes(*args, **kwargs):
            exchange["iterating"] = True
            try:
                async for chunk in original_bytes(*args, **kwargs):
                    if self.owner.active and not exchange["done"]:
                        exchange["chunks"].append(chunk)
                    yield chunk
                self.finish(exchange)
            except BaseException as exc:
                self.finish(
                    exchange,
                    status=None
                    if isinstance(exc, GeneratorExit)
                    else "cancelled"
                    if isinstance(exc, asyncio.CancelledError)
                    else "partial",
                    error="" if isinstance(exc, GeneratorExit) else str(exc) or type(exc).__name__,
                )
                raise
            finally:
                exchange["iterating"] = False

        async def aclose():
            try:
                return await original_close()
            finally:
                if not exchange.get("iterating"):
                    self.finish(exchange)

        # Observe decoded HTTP entity bytes; the SDK receives each byte unchanged.
        response.aiter_bytes = aiter_bytes
        response.aclose = aclose
        self.save(exchange)
