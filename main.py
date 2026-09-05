"""AstrBot entry point and authenticated Plugin Pages bridge."""

import asyncio
import json
import uuid

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.api.star import Context, Star, StarTools
from astrbot.api.web import error_response, json_response, request

from .living_world import __version__
from .living_world.host import AstrBotHost
from .living_world.runtime import Runtime

PLUGIN = "astrbot_plugin_living_world"
TOOL_MODULES = {
    "living_world_social": "proactive",
    "living_world_explore": None,
    "living_world_remember": "memory",
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
        yield event.plain_result(
            f"Living World {__version__} {'运行中' if running else '未启动'}。请在 AstrBot 插件页面管理人格、日程、记忆与模块。"
        )

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def observe(self, event: AstrMessageEvent):
        """Consider group interjections without waking the host model."""
        runtime = self.runtime
        if (
            not runtime
            or not runtime.enabled("interjection")
            or not event.get_group_id()
            or event.is_at_or_wake_command
        ):
            return
        scope = event.unified_msg_origin
        if not await runtime.scope_allowed(scope):
            return
        if event.get_sender_id() == event.get_self_id():
            return

        async def consider():
            await asyncio.sleep(1)
            if (
                not event.is_at_or_wake_command
                and not event.get_extra("living_world_reply")
                and not event._has_send_oper
            ):
                await runtime.social.interject(
                    scope,
                    event.message_str,
                    person_id="qq:" + event.get_sender_id(),
                    event_id=str(event.message_obj.message_id),
                )

        runtime.spawn("interjection", consider())

    @filter.on_llm_request()
    async def augment(self, event: AstrMessageEvent, req: ProviderRequest):
        """Supply only records visible in the current conversation."""
        runtime = self.runtime
        allowed = bool(runtime and await runtime.scope_allowed(event.unified_msg_origin))
        if req.func_tool:
            req.func_tool = type(req.func_tool)(list(req.func_tool.tools))
            for name, module in TOOL_MODULES.items():
                if (
                    not allowed
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
            return
        event.set_extra("living_world_reply", True)
        if runtime.enabled("reply"):
            context = await runtime.context_text(
                event.unified_msg_origin, "qq:" + event.get_sender_id(), event.message_str
            )
            character = runtime.settings["character"]
            req.system_prompt += (
                "\nLiving World 角色补充资料："
                + character["profile"]
                + "\n世界设定："
                + character["world"]
                + "\n当前场合可用资料（fiction 为角色虚构经历，计划不代表已发生）：\n"
                + context
            )

    @filter.on_llm_response()
    async def remember_reply(self, event: AstrMessageEvent, resp: LLMResponse):
        """Extract facts from the user's words, without storing a second chat log."""
        runtime = self.runtime
        if (
            not runtime
            or not runtime.enabled("memory")
            or not event.get_extra("living_world_reply")
        ):
            return
        if event.get_extra("living_world_reflected"):
            return
        event.set_extra("living_world_reflected", True)
        runtime.spawn(
            "memory",
            runtime.reflect_chat(
                event.message_str, event.unified_msg_origin, "qq:" + event.get_sender_id()
            ),
        )

    @filter.llm_tool(name="living_world_social")
    async def social_tool(self, event: AstrMessageEvent, reason: str):
        """Request a natural conversation through the configured social controls.

        Args:
            reason(string): The reason or topic for wanting to talk.
        """
        if not self.runtime:
            return "Living World unavailable"
        result = await self.runtime.execute_action(
            "social", {"reason": reason}, event.unified_msg_origin, uuid.uuid4().hex
        )
        return json.dumps(result, ensure_ascii=False)

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
        return json.dumps(result, ensure_ascii=False)

    @filter.llm_tool(name="living_world_remember")
    async def remember_tool(
        self, event: AstrMessageEvent, text: str, kind: str = "event", important: bool = False
    ):
        """Remember a fact or personal impression in the current conversation.

        Args:
            text(string): The fact or impression to remember; do not invent execution results.
            kind(string): knowledge, event, skill or emotional.
            important(boolean): Whether this memory should be protected from ordinary fading.
        """
        runtime = self.runtime
        if not runtime or not await runtime.scope_allowed(event.unified_msg_origin):
            return "Session is not managed"
        record = runtime.memory.remember(
            text,
            kind=kind,
            scope=event.unified_msg_origin,
            person_id="qq:" + event.get_sender_id(),
            important=important,
            source="explicit",
        )
        if record:
            runtime.spawn("life", runtime.revise(event.unified_msg_origin, "主动记忆更新"))
        return json.dumps(record, ensure_ascii=False)

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
