"""AstrBot 4.28 integration with explicit persona and transport boundaries."""

import json
import uuid


class AstrBotHost:
    def __init__(self, context):
        self.context = context

    async def persona(self, persona_id):
        persona = self.context.persona_manager.get_persona_v3_by_id(persona_id)
        if not persona:
            raise ValueError("绑定的人格不存在")
        return persona.get("prompt", "")

    def platform(self, scope):
        platform_id = scope.split(":", 1)[0]
        return self.context.get_platform_inst(platform_id)

    async def session_persona(self, scope):
        platform = self.platform(scope)
        if not platform or platform.meta().name != "aiocqhttp":
            return ""
        manager = self.context.conversation_manager
        cid = await manager.get_curr_conversation_id(scope)
        conversation = await manager.get_conversation(scope, cid) if cid else None
        selected, _, _, _ = await self.context.persona_manager.resolve_selected_persona(
            umo=scope,
            conversation_persona_id=getattr(conversation, "persona_id", None),
            platform_name=platform.meta().name,
        )
        return selected or ""

    async def generate(self, provider_id, prompt, system, scope):
        provider_id = provider_id or await self.context.get_current_chat_provider_id(
            scope if scope != "global" else ""
        )
        response = await self.context.llm_generate(
            chat_provider_id=provider_id, prompt=prompt, system_prompt=system
        )
        text = response.completion_text
        if not text or not text.strip():
            raise ValueError("模型没有返回文本")
        usage = getattr(response, "usage", None)
        if hasattr(usage, "model_dump"):
            usage = usage.model_dump()
        if not isinstance(usage, dict):
            usage = {}
        return text, {"provider": provider_id, **usage}

    async def history(self, scope, limit=24):
        if ":GroupMessage:" in scope:
            if not self.group_history_enabled(scope):
                return ""
            rows = await self.context.message_history_manager.get(
                scope.split(":", 1)[0], scope, page_size=limit
            )
            lines = []
            for row in rows:
                content = row.content if isinstance(row.content, dict) else {}
                parts = content.get("message", [])
                value = " ".join(str(p.get("text", "")) for p in parts if isinstance(p, dict))
                lines.append(
                    f"{row.sender_name or row.sender_id or content.get('type', '')}: {value}"
                )
            return "\n".join(lines)[-12000:]
        manager = self.context.conversation_manager
        cid = await manager.get_curr_conversation_id(scope)
        conversation = await manager.get_conversation(scope, cid) if cid else None
        if not conversation:
            return ""
        history = conversation.history
        history = json.loads(history) if isinstance(history, str) else history
        return "\n".join(
            f"{row.get('role', '')}: {row.get('content', '')}" for row in (history or [])[-limit:]
        )[-12000:]

    def group_history_enabled(self, scope):
        return bool(
            self.context.get_config(scope)
            .get("provider_ltm_settings", {})
            .get("group_message_history_enable", False)
        )

    def host_interjection_enabled(self, scope):
        return bool(
            self.context.get_config(scope)
            .get("provider_ltm_settings", {})
            .get("active_reply", {})
            .get("enable", False)
        )

    async def send(self, scope, text):
        from astrbot.api.event import MessageChain
        from astrbot.api.message_components import Plain

        return await self.context.send_message(scope, MessageChain([Plain(text)]))

    def tool(self, name, plugin_name=None):
        manager = self.context.get_llm_tool_manager()
        tool = manager.get_func(name)
        if not tool or not tool.active:
            raise ValueError(f"工具不可用：{name}")
        if getattr(tool, "is_background_task", False):
            raise ValueError("不支持脱离当前执行周期的后台工具")
        if plugin_name:
            star = self.context.get_registered_star(plugin_name)
            if not star or not star.activated or not star.star_cls:
                raise ValueError(f"依赖插件未启用：{plugin_name}")
            owner = str(getattr(tool, "handler_module_path", "") or "")
            root = str(star.module_path or "").rsplit(".", 1)[0]
            if not root or not (owner == root or owner.startswith(root + ".")):
                raise ValueError("工具来源与依赖插件不匹配")
        return tool

    async def call_tool(self, name, arguments, scope, plugin_name=None):
        from astrbot.core.astr_agent_context import AgentContextWrapper, AstrAgentContext
        from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor
        from astrbot.core.platform.astr_message_event import AstrMessageEvent
        from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
        from astrbot.core.platform.message_session import MessageSession
        from astrbot.core.platform.platform_metadata import PlatformMetadata

        class BackgroundEvent(AstrMessageEvent):
            async def send(self, message):
                raise RuntimeError("Background tools cannot send messages directly")

        tool = self.tool(name, plugin_name)
        session = MessageSession.from_str(
            scope if scope != "global" else "living-world:FriendMessage:background"
        )
        message = AstrBotMessage()
        message.type = session.message_type
        message.self_id = "living-world"
        message.session_id = session.session_id
        message.message_id = uuid.uuid4().hex
        message.sender = MessageMember("living-world", "Living World")
        message.message = []
        message.message_str = "Living World background observation"
        message.raw_message = {}
        if ":GroupMessage:" in scope:
            message.group_id = session.session_id.split("_")[-1]
        event = BackgroundEvent(
            message.message_str,
            message,
            PlatformMetadata("aiocqhttp", "Living World background", session.platform_id),
            session.session_id,
        )
        wrapper = AgentContextWrapper(
            context=AstrAgentContext(context=self.context, event=event), tool_call_timeout=120
        )
        chunks = []
        async for result in FunctionToolExecutor.execute(tool, wrapper, **arguments):
            if getattr(result, "isError", False):
                raise ValueError("外部工具返回错误")
            for block in getattr(result, "content", []):
                if getattr(block, "type", "") == "text":
                    chunks.append(block.text)
        if not chunks:
            raise ValueError("工具没有返回可用文本")
        return "\n".join(chunks)[:20000]

    def bilibili_api(self, plugin_name):
        star = self.context.get_registered_star(plugin_name)
        if not star or not star.activated or not star.star_cls:
            raise ValueError("B 站依赖插件未启用")
        api = getattr(star.star_cls, "memory_api", None)
        if not api or getattr(api, "api_version", 0) < 3:
            raise ValueError("B 站插件需要公开记忆 API v3")
        return api

    async def catalogs(self, sessions):
        personas = [{"id": "default", "name": "default"}]
        personas.extend(
            {"id": p["name"], "name": p["name"]}
            for p in self.context.persona_manager.personas_v3
            if p["name"] != "default"
        )
        providers = [
            {"id": p.meta().id, "name": p.meta().id} for p in self.context.get_all_providers()
        ]
        result = []
        for session in sessions:
            try:
                persona_id = await self.session_persona(session["umo"])
            except Exception:  # noqa: BLE001 - One missing session must not break the admin page.
                persona_id = ""
            result.append(
                {"umo": session["umo"], "title": session["umo"], "persona_id": persona_id}
            )
        return {"personas": personas, "providers": providers, "sessions": result}
