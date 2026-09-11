"""AstrBot entry point and authenticated Plugin Pages bridge."""

import asyncio
import json
import logging

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.api.star import Context, Star, StarTools
from astrbot.api.web import error_response, json_response, request

from .living_world import __version__
from .living_world.debug import json_value
from .living_world.host import AstrBotHost
from .living_world.runtime import Runtime

PLUGIN = "astrbot_plugin_living_world"
logger = logging.getLogger(__name__)
TOOL_MODULES = {
    "living_world_social": "proactive",
    "living_world_explore": None,
    "living_world_remember": "memory",
    "living_world_recall": "memory",
}


class Main(Star):
    """Bind one persona to selected OneBot sessions and a persistent world."""

    def __init__(self, context: Context):
        super().__init__(context)
        self.runtime = None
        self._routes = []

    async def initialize(self):
        self.runtime = Runtime(
            StarTools.get_data_dir(PLUGIN) / "living_world.sqlite3", AstrBotHost(self.context)
        )
        self.runtime.chat.install()
        for route, method in (
            ("state", "GET"),
            ("settings", "POST"),
            ("action", "POST"),
            ("export", "GET"),
            ("import", "POST"),
        ):
            handler = self._api_handler(route)
            path = f"/{PLUGIN}/{route}"
            self.context.register_web_api(path, handler, [method], f"Living World {route}")
            self._routes.append((path, handler))
        await self.runtime.start()

    def _api_handler(self, route):
        async def handle():
            if not self.runtime or self.runtime.stopped:
                return error_response("插件已停用", status_code=503)
            try:
                if route == "state":
                    result = await self.runtime.snapshot()
                elif route == "export":
                    result = self.runtime.export()
                else:
                    if len(await request.body()) > 20 * 1024 * 1024:
                        return error_response("请求内容过大", status_code=413)
                    data = await request.json(default={})
                    if not isinstance(data, dict):
                        return error_response("请求必须为对象")
                    if route == "settings":
                        result = await self.runtime.update_settings(data)
                    elif route == "import":
                        result = await self.runtime.restore(data)
                    else:
                        result = await self.runtime.action(data)
                return json_response(result)
            except asyncio.CancelledError:
                return error_response("操作已取消：模块或接入配置发生变化", status_code=409)
            except Exception as exc:  # noqa: BLE001 - Isolate plugin API failures at the HTTP boundary.
                self.logger.warning("Living World page operation failed: %s", type(exc).__name__)
                return error_response(str(exc)[:300])

        return handle

    @filter.command("living_world")
    async def living_world(self, event: AstrMessageEvent):
        """Report health without exposing the character's private records."""
        running = self.runtime is not None and not self.runtime.stopped
        event.set_extra("living_world_owned_message", True)
        yield event.plain_result(
            f"Living World {__version__} {'运行中' if running else '未启动'}。请在 AstrBot 插件页面管理人格、日程、记忆与模块。"
        )

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def observe(self, event: AstrMessageEvent):
        """Observe selected private and group conversations without forcing a reply."""
        runtime = self.runtime
        if not runtime or runtime.stopped:
            return
        allowed = await runtime.chat.observe(event)
        if not allowed or not event.get_group_id():
            return
        if not runtime.enabled("interjection") or event.is_at_or_wake_command:
            return

        async def consider():
            await asyncio.sleep(1)
            if (
                not event.is_at_or_wake_command
                and not event.get_extra("living_world_reply")
                and not event._has_send_oper
            ):
                await runtime.social.interject(
                    event.unified_msg_origin,
                    event.message_str,
                    person_id="qq:" + event.get_sender_id(),
                    event_id=str(event.message_obj.message_id),
                )

        runtime.spawn("interjection", consider())

    @filter.on_llm_request()
    async def augment(self, event: AstrMessageEvent, req: ProviderRequest):
        """Supply only records visible in the current conversation."""
        runtime = self.runtime
        allowed = False
        if runtime and not getattr(runtime, "stopped", False):
            if hasattr(runtime, "chat") and runtime.chat.configured(event.unified_msg_origin):
                runtime.chat.ensure_trace(event)
            try:
                route = await runtime.scope_status(event.unified_msg_origin)
                allowed = route["allowed"]
                if hasattr(runtime, "chat"):
                    trace = event.get_extra("living_world_trace")
                    if trace:
                        runtime.chat.record(
                            trace, "chat.route", route, status="success" if allowed else "skipped"
                        )
                        if not allowed:
                            runtime.chat.note_context(trace, "skipped", route["reason"])
            except Exception as exc:  # noqa: BLE001 - Unavailable routing leaves the host request intact.
                if hasattr(runtime, "chat"):
                    trace = event.get_extra("living_world_trace")
                    if trace:
                        runtime.chat.record(
                            trace, "chat.route", {"error": str(exc)}, status="failed"
                        )
        if req.func_tool:
            req.func_tool = type(req.func_tool)(list(req.func_tool.tools))
            for name, module in TOOL_MODULES.items():
                if (
                    name == "living_world_social"
                    or not allowed
                    or (module and not runtime.enabled(module))
                    or (
                        module is None
                        and not any(
                            runtime.enabled(m) for m in ("news", "search", "weather", "bilibili")
                        )
                    )
                ):
                    req.func_tool.remove_tool(name)
        if not allowed:
            if runtime and hasattr(runtime, "chat"):
                trace = event.get_extra("living_world_trace")
                if trace:
                    runtime.chat.record(
                        trace,
                        "chat.route",
                        {"reason": "未通过会话接入检查；具体原因见本轮 chat.route"},
                        status="skipped",
                    )
            return
        runtime.note_scope(event.unified_msg_origin)
        await runtime.chat.augment(event, req)

    @filter.on_agent_begin(priority=-1000000)
    async def trace_agent_begin(self, event, run_context):
        if self.runtime:
            self.runtime.chat.agent_begin(event, run_context)

    @filter.on_agent_done(priority=1000000)
    async def restore_group_history(self, event, run_context, resp):
        if self.runtime:
            self.runtime.chat.restore_history(event, run_context)
            try:
                self.runtime.chat.protect_memory_history(event, run_context)
                await self.runtime.chat.finalize_memory(event, resp)
            except Exception:
                logger.warning("Completed chat could not be queued for memory", exc_info=True)

    @filter.on_using_llm_tool()
    async def trace_tool_start(self, event, tool, tool_args):
        if self.runtime and not self.runtime.stopped:
            self.runtime.chat.tool_start(event, tool, tool_args)

    @filter.on_llm_tool_respond()
    async def trace_tool_end(self, event, tool, tool_args, tool_result):
        if self.runtime and not self.runtime.stopped:
            self.runtime.chat.tool_end(event, tool, tool_args, tool_result)

    @filter.on_llm_response()
    async def remember_reply(self, event: AstrMessageEvent, resp: LLMResponse):
        """Record the response; the agent completion hook owns memory batching."""
        runtime = self.runtime
        if runtime and not runtime.stopped:
            runtime.chat.response(event, resp)

    async def social_tool(self, event: AstrMessageEvent, reason: str):
        """Keep legacy direct callers harmless after removing spontaneous contact."""
        return json.dumps(
            {"status": "skipped", "text": "主动聊天只由日程中的聊天标记触发"}, ensure_ascii=False
        )

    @filter.after_message_sent()
    async def record_reply_sent(self, event: AstrMessageEvent):
        """Record the callback separately; only event.send confirms transport success."""
        runtime = self.runtime
        if not runtime or runtime.stopped:
            return
        trace = event.get_extra("living_world_trace")
        if trace:
            runtime.chat.record(
                trace,
                "reply.sent_callback",
                {},
                status="observed",
                response={"sent": trace["sent"], "failed_or_unknown": trace["failed_sends"]},
                boundary="宿主发送阶段完成回调，不单独作为发送成功依据",
            )
        elif event.get_extra("living_world_owned_message"):
            entry = runtime.debug.begin(
                "command.send",
                {"message": json_value(event.get_result())},
                kind="message",
                scope=event.unified_msg_origin,
                boundary="宿主命令发送回调，传输详情未捕获",
            )
            runtime.debug.finish(entry, status="observed")

    @filter.llm_tool(name="living_world_explore")
    async def explore_tool(self, event: AstrMessageEvent, source: str, query: str):
        """Read an enabled external source and retain its provenance.

        Args:
            source(string): news, search, weather, bilibili, bilibili_watch or bilibili_recent.
            query(string): Search terms, or the BVID for a specific video to watch.
        """
        if not self.runtime or source not in {
            "news",
            "search",
            "weather",
            "bilibili",
            "bilibili_watch",
            "bilibili_recent",
        }:
            return "Unsupported source"
        result = await self.runtime.execute_action(
            source, {"query": query}, event.unified_msg_origin
        )
        self.runtime.audit_external(
            "tool.living_world_explore",
            {"source": source, "query": query},
            result,
            event.unified_msg_origin,
            "Living World 工具执行入参与结果",
        )
        return json.dumps(result, ensure_ascii=False)

    @filter.llm_tool(name="living_world_remember")
    async def remember_tool(
        self,
        event: AstrMessageEvent,
        text: str,
        attribute: str = "事实属性",
        tags: str = "",
        reasoning: str = "",
    ):
        """Remember a fact or personal impression in the current conversation.

        Args:
            text(string): The fact or impression to remember; do not invent execution results.
            attribute(string): 用户别名, 事实属性, 技能树, 关系图谱 or 活跃项目.
            tags(string): Short search tags separated by commas.
            reasoning(string): Supporting facts or source evidence, not private reasoning.
        """
        runtime = self.runtime
        if not runtime or not await runtime.scope_allowed(event.unified_msg_origin):
            return "Session is not managed"
        record = runtime.memory.remember(
            text,
            scope=event.unified_msg_origin,
            attribute=attribute,
            tags=[tag.strip() for tag in tags.replace("，", ",").split(",") if tag.strip()],
            reasoning=reasoning,
            source="explicit",
        )
        if record:
            runtime.spawn("life", runtime.revise(event.unified_msg_origin, "主动记忆更新"))
        runtime.audit_external(
            "tool.living_world_remember",
            {"text": text, "attribute": attribute, "tags": tags, "reasoning": reasoning},
            record,
            event.unified_msg_origin,
            "Living World 工具执行入参与结果",
        )
        return json.dumps(record, ensure_ascii=False)

    @filter.llm_tool(name="living_world_recall")
    async def recall_tool(self, event: AstrMessageEvent, query: str, limit: int = 10):
        """Search accessible memories and profiles using meaningful search words.

        Args:
            query(string): Search words, including alternative names when helpful.
            limit(int): Maximum returned memories, between 1 and 30.
        """
        runtime = self.runtime
        if (
            not runtime
            or not runtime.enabled("memory")
            or not await runtime.scope_allowed(event.unified_msg_origin)
        ):
            return "Session is not managed or memory is disabled"
        rows = runtime.memory.recall(
            query,
            scope=event.unified_msg_origin,
            person_id="qq:" + event.get_sender_id(),
            limit=max(1, min(30, int(limit))),
            reinforce=False,
        )
        result = {
            "memories": [
                {
                    key: row.get(key)
                    for key in (
                        "id",
                        "text",
                        "reasoning",
                        "attribute",
                        "tags",
                        "occurred_at",
                        "inferred",
                    )
                }
                for row in rows
            ]
        }
        trace = event.get_extra("living_world_trace")
        if trace and trace.get("managed"):
            trace.setdefault("memory_sources", []).append(
                {
                    "block_id": "memory.tool",
                    "memory_ids": [row["id"] for row in rows],
                    "memory_versions": {row["id"]: row.get("version", 1) for row in rows},
                    "memory_snapshots": json_value(rows),
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )
        runtime.audit_external(
            "tool.living_world_recall",
            {"query": query, "limit": limit},
            result,
            event.unified_msg_origin,
        )
        return json.dumps(result, ensure_ascii=False)

    async def terminate(self):
        for path, handler in self._routes:
            self.context.registered_web_apis[:] = [
                api
                for api in self.context.registered_web_apis
                if not (api[0] == path and api[1] is handler)
            ]
        self._routes.clear()
        if self.runtime:
            await self.runtime.stop()
