"""Reversible, run-scoped observation of AstrBot provider calls."""

import asyncio
import contextvars
import functools
import logging

from .debug import json_value, response_value
from .wire import HTTPAudit

logger = logging.getLogger(__name__)
_RUN = contextvars.ContextVar("living_world_provider_run", default=None)
_TOOL_DEPTH = contextvars.ContextVar("living_world_tool_depth", default=0)
_PROVIDER_DEPTH = contextvars.ContextVar("living_world_provider_depth", default=0)
_CALL = contextvars.ContextVar("living_world_http_call", default=None)


def pause_tools():
    return _TOOL_DEPTH.set(_TOOL_DEPTH.get() + 1)


def resume_tools(token):
    _TOOL_DEPTH.reset(token)


class ProviderAudit:
    """Only marked runner steps and explicit background calls are observed."""

    def __init__(self, runtime):
        self.runtime = runtime
        self.active = True
        self.patches = []
        self.providers = set()
        self.available = False
        self.http = HTTPAudit(self, self._current_call)

    def _current_call(self):
        run, call = _RUN.get(), _CALL.get()
        if run and run.get("owner") is self and call and not _TOOL_DEPTH.get():
            return call
        return None

    def install(self):
        if self.active and self.available:
            return
        self.active = True
        from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner

        original = ToolLoopAgentRunner.step
        owner = self

        @functools.wraps(original)
        async def step(runner, *args, **kwargs):
            event = getattr(
                getattr(getattr(runner, "run_context", None), "context", None), "event", None
            )
            trace = event.get_extra("living_world_trace") if event else None
            selected = bool(
                owner.active
                and trace
                and trace.get("owner") is owner.runtime.chat
                and trace.get("managed")
            )
            if selected:
                owner.refresh(
                    [getattr(runner, "provider", None), *getattr(runner, "fallback_providers", [])]
                )
            iterator = original(runner, *args, **kwargs)
            tool_depth = 0
            try:
                while True:
                    # Never leave ContextVars set while yielding to the host pipeline.
                    token = _RUN.set({"owner": owner, "trace": trace} if selected else None)
                    depth_token = _TOOL_DEPTH.set(tool_depth)
                    try:
                        result = await anext(iterator)
                    except StopAsyncIteration:
                        return
                    finally:
                        tool_depth = _TOOL_DEPTH.get()
                        _TOOL_DEPTH.reset(depth_token)
                        _RUN.reset(token)
                    yield result
            except BaseException as exc:
                if selected and owner.active:
                    owner.runtime.chat.runner_failed(event, runner, exc)
                raise
            finally:
                await iterator.aclose()

        self._patch(ToolLoopAgentRunner, "step", step)
        self.available = True
        self.refresh()

    def _patch(self, obj, name, replacement):
        existed = name in vars(obj)
        previous = vars(obj).get(name)
        setattr(obj, name, replacement)
        self.patches.append((obj, name, replacement, existed, previous))

    def refresh(self, extra=()):
        if not self.active:
            return
        context = getattr(self.runtime.host, "context", None)
        providers = list(extra)
        if context and hasattr(context, "get_all_providers"):
            try:
                providers.extend(context.get_all_providers())
            except Exception:
                logger.warning("Provider audit discovery failed", exc_info=True)
        for provider in providers:
            if provider is None or id(provider) in self.providers:
                continue
            for name, streaming in (("text_chat", False), ("text_chat_stream", True)):
                original = getattr(provider, name, None)
                if callable(original):
                    wrapper = (
                        self._stream_wrapper(provider, original)
                        if streaming
                        else self._call_wrapper(provider, original)
                    )
                    try:
                        self._patch(provider, name, wrapper)
                    except Exception:
                        logger.warning(
                            "Provider audit method is not writable: %s", name, exc_info=True
                        )
            self.providers.add(id(provider))

    def _begin(self, provider, method, args, kwargs):
        run = _RUN.get()
        if (
            not self.active
            or not self.runtime.enabled("debug")
            or not run
            or run.get("owner") is not self
            or _TOOL_DEPTH.get()
            or _PROVIDER_DEPTH.get()
        ):
            return None
        trace = run.get("trace")
        if trace and not self.runtime.chat.trace_exists(trace):
            return None
        if not trace and (
            not run.get("parent_id")
            or not self.runtime.store.get("debug_records", run["parent_id"])
        ):
            return None
        meta = provider.meta()
        request = {
            "provider_id": getattr(meta, "id", ""),
            "model": kwargs.get("model") or getattr(meta, "model", ""),
            "method": method,
            "arguments": json_value(kwargs),
            "positional_arguments": json_value(args),
        }
        boundary = (
            "AstrBot Provider 方法的实际入参与返回；包含工具后的再次调用，不包含 SDK 内部 HTTP 重试"
        )
        if trace:
            return self.runtime.chat.record(
                trace,
                "provider." + run["task"] if run.get("task") else "reply.model",
                request,
                kind="model",
                boundary=boundary,
                module=run.get("module", "reply"),
                parent_id=run.get("parent_id"),
            )
        return self.runtime.debug.begin(
            "provider." + run["task"],
            request,
            scope=run["scope"],
            module=run["module"],
            parent_id=run.get("parent_id", ""),
            boundary=boundary,
        )

    def _safe_begin(self, *args):
        try:
            entry = self._begin(*args)
            if entry:
                try:
                    ready = self.http.bind(args[0])
                    entry.update(http_capture="ready" if ready else "unsupported", http_calls=[])
                except Exception:
                    logger.warning("HTTP client audit binding failed", exc_info=True)
                    entry.update(http_capture="error", http_calls=[])
                self.runtime.debug.patch(entry, http_capture=entry["http_capture"], http_calls=[])
            return entry
        except Exception:
            logger.warning("Provider audit request capture failed", exc_info=True)
            return None

    def _finish(self, entry, response=None, status="success", error=""):
        try:
            self.http.finish_call(entry, status, error)
            self.runtime.debug.finish(entry, response, status=status, error=error)
        except Exception:
            logger.warning("Provider audit response capture failed", exc_info=True)

    def _call_wrapper(self, provider, original):
        @functools.wraps(original)
        async def call(*args, **kwargs):
            entry = self._safe_begin(provider, "text_chat", args, kwargs)
            token = _PROVIDER_DEPTH.set(_PROVIDER_DEPTH.get() + 1)
            call_token = _CALL.set({"entry": entry} if entry else _CALL.get())
            try:
                result = await original(*args, **kwargs)
                if entry:
                    try:
                        value = response_value(result)
                    except Exception:
                        logger.warning("Provider response serialization failed", exc_info=True)
                        value = {"captured": False, "type": type(result).__name__}
                    self._finish(
                        entry,
                        value,
                        "failed" if getattr(result, "role", "") == "err" else "success",
                    )
                return result
            except BaseException as exc:
                self._finish(
                    entry,
                    status="cancelled" if isinstance(exc, asyncio.CancelledError) else "failed",
                    error=str(exc) or type(exc).__name__,
                )
                raise
            finally:
                _CALL.reset(call_token)
                _PROVIDER_DEPTH.reset(token)

        return call

    def _stream_wrapper(self, provider, original):
        @functools.wraps(original)
        async def stream(*args, **kwargs):
            entry = self._safe_begin(provider, "text_chat_stream", args, kwargs)
            chunks, final = [], None
            iterator = original(*args, **kwargs)
            try:
                while True:
                    token = _PROVIDER_DEPTH.set(_PROVIDER_DEPTH.get() + 1)
                    call_token = _CALL.set({"entry": entry} if entry else _CALL.get())
                    try:
                        result = await anext(iterator)
                    except StopAsyncIteration:
                        break
                    finally:
                        _CALL.reset(call_token)
                        _PROVIDER_DEPTH.reset(token)
                    if entry:
                        try:
                            value = response_value(result)
                        except Exception:
                            logger.warning("Streaming response serialization failed", exc_info=True)
                            value = {"captured": False, "type": type(result).__name__}
                        if getattr(result, "is_chunk", False):
                            chunks.append(value)
                        else:
                            final = value
                            self._finish(
                                entry,
                                {"chunks": chunks, "final": final},
                                "failed" if final.get("role") == "err" else "success",
                            )
                    yield result
                status = (
                    "failed"
                    if isinstance(final, dict) and final.get("role") == "err"
                    else "success"
                    if final is not None
                    else "partial"
                )
                self._finish(entry, {"chunks": chunks, "final": final}, status)
            except BaseException as exc:
                if final is None or not isinstance(exc, GeneratorExit):
                    self._finish(
                        entry,
                        {"chunks": chunks, "final": final},
                        "cancelled"
                        if isinstance(exc, (asyncio.CancelledError, GeneratorExit))
                        else "failed",
                        str(exc) or type(exc).__name__,
                    )
                raise
            finally:
                await iterator.aclose()

        return stream

    def current_trace(self):
        run = _RUN.get()
        trace = run.get("trace") if run and run.get("owner") is self else None
        return trace if self.runtime.chat.trace_exists(trace) else None

    def relation(self):
        trace = self.current_trace()
        return {"turn_id": trace["id"], "parent_id": trace["root"]["id"]} if trace else {}

    async def background(self, request, parent, awaitable):
        self.refresh()
        token = _RUN.set(
            {
                "owner": self,
                "task": request["task"],
                "scope": request["scope"],
                "module": request["module"],
                "parent_id": parent["id"] if parent else "",
                "trace": self.current_trace(),
            }
        )
        # Owned tools may themselves make explicit Living World background calls.
        pause = _TOOL_DEPTH.set(0)
        try:
            return await awaitable
        finally:
            _TOOL_DEPTH.reset(pause)
            _RUN.reset(token)

    def close(self):
        self.http.close()
        self.active = False
        self.available = False
        for obj, name, replacement, existed, previous in reversed(self.patches):
            if getattr(obj, name, None) is replacement:
                if existed:
                    setattr(obj, name, previous)
                else:
                    delattr(obj, name)
        self.patches.clear()
        self.providers.clear()
