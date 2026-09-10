"""Conversation-scoped context assembly, observation and delivery traces."""

import asyncio
import copy
import hashlib
import logging
import time
import uuid
import weakref

from .context import group_messages_text, source_item
from .debug import diagnostic_write, json_value, response_value
from .instrumentation import ProviderAudit, pause_tools, resume_tools
from .layout import assemble, block, resolve_layout
from .context_catalog import BLOCK_NAMES
from .context_usage import usage_for
from .social import destination

logger = logging.getLogger(__name__)
DYNAMIC_MARKER = "<living_world_context>"
GROUP_REPLY_HEADING = f"【{BLOCK_NAMES['group_reply']}】"


def _text(parts):
    """Keep mentions, quotes and media markers without reading private media."""
    if isinstance(parts, str):
        return parts
    if isinstance(parts, dict):
        parts = parts.get("chain", [])
    result = []
    for part in parts or []:
        if not isinstance(part, dict):
            continue
        kind = str(part.get("type", "媒体")).lower()
        if kind == "reply":
            result.append(
                f"[引用 {part.get('id') or part.get('message_id', '')} "
                f"{part.get('sender_nickname') or part.get('sender_name', '')}: "
                f"{part.get('message_str') or part.get('text', '')}]"
            )
        elif kind == "at":
            result.append(
                f"[@{part.get('name') or ''}({part.get('qq') or part.get('user_id', '')})]"
            )
        else:
            result.append(str(part.get("text") or part.get("message_str") or f"[{kind}]"))
    return " ".join(result)


class ChatService:
    def __init__(self, runtime):
        self.runtime = runtime
        self.audit = ProviderAudit(runtime)
        self.events = []
        self.seeds = set()

    def install(self):
        if not self.runtime.enabled("debug"):
            return
        try:
            self.audit.install()
        except Exception:
            self.audit.close()
            self.runtime.errors["debug"] = "逐次 Provider 捕获不可用，已保留宿主请求与最终回复快照"
            logger.warning("Provider audit installation failed", exc_info=True)

    def configured(self, scope):
        try:
            target = destination(scope)
            return next(
                (
                    row
                    for row in self.runtime.settings["sessions"]
                    if destination(row["umo"]) == target
                ),
                None,
            )
        except (ValueError, KeyError):
            return None

    def resolve(self, scope):
        target = destination(scope)
        if scope == target:
            return self.runtime.store.get("session_contexts", target, {}).get("scope", scope)
        return scope

    def _key(self, scope):
        return self.runtime.settings["persona_id"] + "\0" + destination(scope)

    def _window(self, scope):
        return self.runtime.store.get("group_context", self._key(scope), {}).get("messages", [])

    def add_group_message(self, scope, row):
        if self.runtime.stopped:
            return
        rows = self._window(scope)
        row = json_value(row)
        if row.get("id") and any(item.get("id") == row["id"] for item in rows):
            return
        rows.append(row)
        rows = rows[-24:]
        budget, kept = 12000, []
        for item in reversed(rows):
            item = dict(item)
            item["text"] = str(item.get("text", ""))[-budget:]
            budget -= len(item["text"]) + len(item.get("sender_name", "")) + 40
            kept.append(item)
            if budget <= 0:
                break
        self.runtime.store.put(
            "group_context",
            self._key(scope),
            {
                "id": destination(scope),
                "persona_id": self.runtime.settings["persona_id"],
                "messages": list(reversed(kept)),
            },
        )

    async def seed_group(self, scope):
        key = self._key(scope)
        if key in self.seeds or self._window(scope):
            return
        if hasattr(self.runtime.host, "history_snapshot"):
            snapshot = await self.runtime.host.history_snapshot(scope)
            rows = snapshot["messages"]
        else:
            text = await self.runtime.host.history(scope)
            rows = [
                {
                    "id": uuid.uuid4().hex,
                    "sender_id": "",
                    "sender_name": "宿主群历史",
                    "text": line,
                    "scope": scope,
                    "time": "",
                }
                for line in text.splitlines()
            ]
        for row in rows:
            self.add_group_message(scope, row)
        self.seeds.add(key)

    async def history(self, scope, *, initialize=False):
        if ":GroupMessage:" in scope:
            if initialize:
                await self.seed_group(scope)
            rows = self._window(scope)
            return {
                "status": "found" if rows else "empty",
                "count": len(rows),
                "source": "Living World 群聊观察",
                "conversation_id": "",
                "messages": rows,
                "text": "\n".join(
                    f"{r.get('sender_name', '')}({r.get('sender_id', '')}): {r['text']}"
                    for r in rows
                )[-12000:],
            }
        if hasattr(self.runtime.host, "history_snapshot"):
            return await self.runtime.host.history_snapshot(scope)
        text = await self.runtime.host.history(scope)
        return {
            "status": "found" if text else "empty",
            "count": len(text.splitlines()),
            "source": "AstrBot 当前对话",
            "conversation_id": "",
            "messages": [],
            "text": text,
        }

    async def inspect(self, scope):
        actual = self.resolve(scope)
        route = await self.runtime.scope_status(actual, module="reply")
        result = {
            **route,
            "umo": scope,
            "actual_scope": actual,
            "platform_id": actual.split(":", 1)[0],
            "history_status": "empty",
            "history_count": 0,
            "checked_at": time.time(),
            "context_status": self.runtime.store.get(
                "chat_context_status", self._status_key(actual), {}
            ),
        }
        try:
            history = await self.history(actual)
            if (
                ":GroupMessage:" in actual
                and not history["count"]
                and hasattr(self.runtime.host, "history_snapshot")
            ):
                # A diagnostic read does not initialize the observation window.
                history = await self.runtime.host.history_snapshot(actual)
            result.update(
                history_status=history["status"],
                history_count=history["count"],
                history_source=history["source"],
                conversation_id=history["conversation_id"],
            )
        except Exception as exc:  # noqa: BLE001 - Diagnostics isolate missing host services.
            result.update(history_status="error", history_error="历史读取失败：" + str(exc)[:200])
        return result

    def _status_key(self, scope):
        return self.runtime.settings["persona_id"] + "\0" + scope

    def note_context(self, trace, status, reason):
        """Keep bounded status metadata, independently of disposable debug records."""
        if not trace or self.runtime.stopped:
            return
        try:
            key = trace["bound_persona"] + "\0" + trace["scope"]
            stored = self.runtime.store.get("chat_context_status", key, {})
            attempt = {
                "scope": trace["scope"],
                "turn_id": trace["id"] if trace.get("root") else "",
                "status": status,
                "reason": reason,
                "at": time.time(),
                "received_at": trace["received_at"],
                "conversation_id": str(
                    getattr(getattr(trace.get("request"), "conversation", None), "cid", "")
                ),
            }
            if trace["received_at"] >= stored.get("last_attempt", {}).get("received_at", 0):
                stored["last_attempt"] = attempt
            if status == "injected":
                stored["last_injected"] = attempt
            self.runtime.store.put("chat_context_status", key, stored)
        except Exception:
            logger.warning("Chat context status write failed", exc_info=True)

    def trace_exists(self, trace):
        try:
            return bool(
                trace
                and not self.runtime.stopped
                and self.runtime.enabled("debug")
                and trace.get("root")
                and self.runtime.store.get("debug_records", trace["root"]["id"])
            )
        except Exception:
            logger.warning("Debug trace lookup failed", exc_info=True)
            return False

    def record(
        self,
        trace,
        task,
        request,
        *,
        kind="event",
        boundary="Living World 当前聊天轮次",
        response=None,
        status=None,
        module="reply",
        parent_id=None,
    ):
        if trace and not trace.get("debug_started") and task in {"chat.route", "chat.history"}:
            if self.runtime.enabled("debug"):
                # Observation has at most one route and one history diagnostic.
                trace["observations"][task] = {
                    "request": json_value(request),
                    "response": json_value(response),
                    "status": status,
                }
            return None
        if not self.trace_exists(trace):
            return None
        entry = self.runtime.debug.begin(
            task,
            request,
            module=module,
            scope=trace["scope"],
            kind=kind,
            boundary=boundary,
            turn_id=trace["id"],
            parent_id=parent_id or trace["root"]["id"],
        )
        if status is not None:
            self.runtime.debug.finish(entry, response, status=status)
        return entry

    def ensure_trace(self, event, *, start_debug=True):
        trace = event.get_extra("living_world_trace")
        if trace and trace.get("owner") is self:
            if start_debug:
                self._start_trace(trace)
            return trace
        scope = event.unified_msg_origin
        message = getattr(event, "message_obj", None)
        trace = {
            "id": uuid.uuid4().hex,
            "received_at": time.time(),
            "bound_persona": self.runtime.settings["persona_id"],
            "owner": self,
            "scope": scope,
            "managed": False,
            "root": None,
            "debug_started": False,
            "observations": {},
            "received_message": {
                "message_id": str(getattr(message, "message_id", "")),
                "sender_id": event.get_sender_id(),
                "message": json_value(getattr(message, "message", [])),
                "text": getattr(event, "message_str", ""),
                "scope": scope,
            },
            "sent": 0,
            "failed_sends": 0,
            "tools": {},
            "restored": False,
        }
        event.set_extra("living_world_trace", trace)
        self.events = [ref for ref in self.events if ref() is not None]
        try:
            self.events.append(weakref.ref(event))
        except TypeError:
            self.events.append(lambda: event)
        self._wrap_send(event, trace)
        if start_debug:
            self._start_trace(trace)
        return trace

    @diagnostic_write
    def _start_trace(self, trace):
        """Persist only when model preparation or a real send starts, once per event."""
        if trace["debug_started"]:
            return
        # Cleared or disabled turns must never be recreated by later callbacks.
        trace["debug_started"] = True
        request = trace.pop("received_message")
        observations = trace.pop("observations")
        trace["root"] = self.runtime.debug.begin(
            "chat.turn",
            request,
            module="reply",
            scope=trace["scope"],
            kind="turn",
            turn_id=trace["id"],
            boundary="进入模型处理或实际发送的聊天轮次",
        )
        for task, entry in observations.items():
            self.record(trace, task, **entry)

    async def observe(self, event):
        scope = event.unified_msg_origin
        configured = self.configured(scope)
        if not configured or self.runtime.stopped or event.get_sender_id() == event.get_self_id():
            return False
        trace = self.ensure_trace(event, start_debug=False)
        self.runtime.note_scope(scope)
        try:
            route = await self.runtime.scope_status(scope)
            allowed = route["allowed"]
        except Exception as exc:  # noqa: BLE001 - A failed persona lookup must not block the host.
            self.record(trace, "chat.route", {"error": str(exc)}, status="failed")
            return False
        self.record(
            trace,
            "chat.route",
            {
                **route,
                "reply_enabled": self.runtime.enabled("reply"),
                "bound_persona": self.runtime.settings["persona_id"],
                "actual_scope": scope,
            },
            response={"reason": route["reason"]},
            status="success" if allowed else "skipped",
        )
        self.note_context(
            trace,
            "observed" if allowed and self.runtime.enabled("reply") else "skipped",
            route["reason"]
            if not allowed
            else "被动回复上下文模块已关闭；群观察仍独立工作"
            if not self.runtime.enabled("reply")
            else "已收到消息，等待宿主模型流程",
        )
        if not allowed or not event.get_group_id():
            return allowed
        try:
            await self.seed_group(scope)
        except Exception as exc:  # noqa: BLE001 - Live observations still work when host history fails.
            self.record(trace, "chat.history", {"error": str(exc)}, status="failed")
        parts = json_value(getattr(event.message_obj, "message", []))
        text = _text(parts) or getattr(event, "message_str", "")
        if text:
            self.add_group_message(
                scope,
                {
                    "id": str(getattr(event.message_obj, "message_id", ""))
                    or hashlib.sha256((scope + text).encode()).hexdigest(),
                    "sender_id": event.get_sender_id(),
                    "sender_name": event.get_sender_name(),
                    "text": text,
                    "scope": scope,
                    "time": time.time(),
                },
            )
        return allowed

    @staticmethod
    def _native_group_part(part):
        text = part.get("text", "") if isinstance(part, dict) else getattr(part, "text", "")
        return (
            text.strip().startswith("<system_reminder>")
            and text.strip().endswith("</system_reminder>")
            and "You are in a group chat." in text
            and "--- BEGIN CONTEXT---" in text
            and "--- END CONTEXT ---" in text
        )

    async def augment(self, event, req):
        from astrbot.core.agent.message import TextPart

        trace = self.ensure_trace(event)
        if trace.get("request") is req:
            return
        if not self.runtime.enabled("reply"):
            self.note_context(trace, "skipped", "被动回复上下文模块已关闭")
            self.record(
                trace,
                "chat.context",
                {"reason": "回复上下文接管已关闭，沿用宿主"},
                status="skipped",
            )
            return
        layout = resolve_layout(
            self.runtime.settings, "chat.group" if event.get_group_id() else "chat.private"
        )
        character = copy.deepcopy(self.runtime.settings["character"])
        usage = usage_for(self.runtime.settings)
        recipient = self.runtime.social.recipient_context(event.unified_msg_origin)
        group_prompt = self.runtime.settings["reply"]["group_prompt"].strip()
        original = {
            key: copy.deepcopy(getattr(req, key, None))
            for key in ("contexts", "system_prompt", "prompt", "extra_user_content_parts")
        }
        try:
            history = await self.history(event.unified_msg_origin, initialize=True)
            context = await self.runtime.context_bundle(
                event.unified_msg_origin,
                "qq:" + event.get_sender_id(),
                event.message_str,
                usage=usage,
            )
            speaker = event.get_sender_name() or "当前聊天对象"
            blocks = [
                block("profile", "角色补充资料", character["profile"], "Living World 角色设置"),
                block("world", "世界设定", character["world"], "Living World 世界设置"),
                block(
                    "speaker",
                    "当前交谈对象",
                    recipient + "\n本轮消息发送者：" + speaker,
                    "本轮 QQ 会话与发送者称呼",
                ),
                *context["sources"],
            ]
            if event.get_group_id():
                blocks.append(
                    block(
                        "group_history",
                        "近期群消息",
                        group_messages_text(history["messages"]),
                        history["source"],
                    )
                )
                trace["saved_contexts"] = original["contexts"] or []
                trace["conversation_id"] = str(
                    getattr(getattr(req, "conversation", None), "cid", "")
                )
                req.extra_user_content_parts = [
                    part
                    for part in (req.extra_user_content_parts or [])
                    if not self._native_group_part(part)
                ]
                if group_prompt:
                    blocks.append(
                        block(
                            "group_reply",
                            "本轮群聊回复要求",
                            GROUP_REPLY_HEADING + "\n" + group_prompt,
                            "05 聊天与对象 → 回复与插话（本轮开始时的已保存文案）",
                            instruction=True,
                        )
                    )
            assembled = assemble(
                layout, blocks, original["system_prompt"] or "", original["prompt"] or ""
            )
            # System additions are attached as no-save parts at agent start, never to the request.
            parts = {}
            for role, segments in assembled["segments"].items():
                parts[role] = {}
                for side, text in segments.items():
                    if text:
                        part = TextPart(text=text)
                        part._no_save = True
                        parts[role][side] = part
            req.extra_user_content_parts = [
                *(req.extra_user_content_parts or []),
                *parts["user"].values(),
            ]
            trace.update(
                layout_parts=parts,
                layout_segments=assembled["segments"],
                layout_system_initial=assembled["system_prompt"],
                layout_snapshot=layout,
                system_restored=False,
            )
            sources = assembled["sources"]
            if not event.get_group_id():
                sources.append(
                    source_item(
                        "聊天历史",
                        "AstrBot 当前对话的本轮请求历史；不另外拼入其他对话",
                        json_value(original["contexts"] or []),
                        "宿主原有历史消息，不重复加入动态资料",
                    )
                )
            trace.update(managed=True, request=req, original=original)
            self.note_context(trace, "prepared", "上下文已组装，尚未进入宿主 Agent")
            event.set_extra("living_world_reply", True)
            self.record(
                trace,
                "chat.context",
                {
                    "history": history,
                    "dynamic_context": context["text"],
                    "sources": sources,
                    "injected_text": assembled["injected_text"],
                    "context_layout": layout,
                    "context_layout_version": 2,
                    "context_usage": usage,
                    "injection_segments": assembled["segments"],
                    "group_history_replaced": bool(event.get_group_id()),
                    "placement": "按本轮布局快照注入 system／user（不写入聊天历史）",
                },
                status="success",
            )
            captured = {
                key: json_value(getattr(req, key, None))
                for key in (
                    "prompt",
                    "system_prompt",
                    "contexts",
                    "image_urls",
                    "audio_urls",
                    "extra_user_content_parts",
                    "session_id",
                    "model",
                    "tool_calls_result",
                )
            }
            captured["tools"] = req.func_tool.openai_schema() if req.func_tool else []
            captured.update(persona_id=self.runtime.settings["persona_id"], scope=trace["scope"])
            self.record(
                trace,
                "reply.request",
                captured,
                kind="snapshot",
                status="captured",
                boundary="Living World 组织后的宿主请求快照；实际逐次参数请查看 reply.model",
            )
            self.runtime.debug.patch(
                trace["root"],
                status="running",
                response={"reason": "已接管上下文，等待宿主模型与发送"},
            )
        except Exception as exc:
            for key, value in original.items():
                setattr(req, key, value)
            trace.pop("saved_contexts", None)
            trace["managed"] = False
            self.note_context(trace, "failed", "上下文组装失败，已沿用宿主：" + str(exc)[:200])
            self.record(
                trace,
                "chat.context",
                {"error": str(exc), "reason": "上下文接管失败，已恢复宿主请求"},
                status="failed",
            )
            logger.warning("Chat context assembly failed", exc_info=True)

    @staticmethod
    def _historical_messages(raw):
        from astrbot.core.agent.message import Message

        try:
            from astrbot.core.agent.message import bind_checkpoint_messages
        except ImportError:
            return [Message.model_validate(row) for row in raw if row.get("role") != "_checkpoint"]
        return bind_checkpoint_messages(raw)

    def agent_begin(self, event, run_context):
        trace = event.get_extra("living_world_trace")
        if (
            not trace
            or trace.get("owner") is not self
            or not trace.get("managed")
            or self.runtime.stopped
        ):
            return
        trace["run_context"] = run_context
        try:
            self._place_layout(trace, run_context)
            if "saved_contexts" not in trace or trace.get("history_hidden"):
                self.note_context(trace, "injected", "Living World 上下文已交给宿主 Agent")
                return
            history = self._historical_messages(copy.deepcopy(trace["saved_contexts"]))
            messages = run_context.messages
            at = 1 if messages and getattr(messages[0], "role", "") == "system" else 0
            if json_value(messages[at : at + len(history)]) != json_value(history):
                raise ValueError("其他链路已改变请求历史，保留宿主历史")
            del messages[at : at + len(history)]
            trace["history_hidden"] = True
            self.note_context(trace, "injected", "Living World 群上下文已交给宿主 Agent")
        except Exception as exc:  # noqa: BLE001 - History must stay intact if the host shape changed.
            # Discard our dynamic group block if the host's history cannot be replaced safely.
            req = trace["request"]
            original = trace["original"]
            self._remove_layout(trace, run_context)
            req.extra_user_content_parts = original["extra_user_content_parts"]
            trace["managed"] = False
            event.set_extra("living_world_reply", False)
            self.note_context(trace, "failed", "群历史替换失败，已沿用宿主：" + str(exc)[:200])
            self.record(trace, "chat.history_replace", {"error": str(exc)}, status="failed")

    @staticmethod
    def _owned_layout_part(trace, part):
        return getattr(part, "_no_save", False) and any(
            getattr(part, "text", None) == own.text
            for parts in trace.get("layout_parts", {}).values()
            for own in parts.values()
        )

    @staticmethod
    def _system_base(trace, text):
        segments = trace["layout_segments"]["system"].values()
        if not any(segment and segment in text for segment in segments):
            return text
        if text == trace["layout_system_initial"]:
            return trace["original"]["system_prompt"] or ""
        for segment in trace["layout_segments"]["system"].values():
            if segment:
                if text.count(segment) != 1:
                    raise ValueError("本轮系统资料被其他链路改变，无法确认注入位置")
                text = text.replace(segment, "", 1)
        return text

    def _place_layout(self, trace, run_context):
        """Place temporary parts around the actual anchors after late host additions."""
        from astrbot.core.agent.message import Message, TextPart

        def owned(part):
            return self._owned_layout_part(trace, part)

        for role in ("system", "user"):
            messages = run_context.messages if role == "system" else reversed(run_context.messages)
            message = next((item for item in messages if item.role == role), None)
            parts = trace["layout_parts"][role]
            if message is None:
                if role == "system" and parts:
                    message = Message(role="system", content=[])
                    run_context.messages.insert(0, message)
                elif parts:
                    raise ValueError("本轮消息定位行不存在")
                else:
                    continue
            content = message.content
            if isinstance(content, str):
                base = self._system_base(trace, content) if role == "system" else content
                content = [TextPart(text=base)] if base else []
            else:
                content = [part for part in content if not owned(part)]
                if role == "system":
                    for part in content:
                        text = getattr(part, "text", "")
                        if any(
                            segment and segment in text
                            for segment in trace["layout_segments"]["system"].values()
                        ):
                            previous_no_save = getattr(part, "_no_save", False)
                            part._no_save = True
                            part.text = self._system_base(trace, text)
                            part._no_save = previous_no_save
            message.content = [
                *([parts["before"]] if "before" in parts else []),
                *content,
                *([parts["after"]] if "after" in parts else []),
            ]
        extras = [
            part for part in (trace["request"].extra_user_content_parts or []) if not owned(part)
        ]
        user = trace["layout_parts"]["user"]
        trace["request"].extra_user_content_parts = [
            *([user["before"]] if "before" in user else []),
            *extras,
            *([user["after"]] if "after" in user else []),
        ]

    def _remove_layout(self, trace, run_context):
        from astrbot.core.agent.message import TextPart

        for message in run_context.messages:
            if isinstance(message.content, list):
                message.content = [
                    p for p in message.content if not self._owned_layout_part(trace, p)
                ]
            elif message.role == "system":
                try:
                    message.content = self._system_base(trace, message.content)
                except ValueError:
                    # Preserve changed host content for this call but never archive uncertain material.
                    temporary = TextPart(text=message.content)
                    temporary._no_save = True
                    message.content = [temporary]
        if "saved_contexts" in trace:
            for message in reversed(run_context.messages):
                if message.role == "user" and isinstance(message.content, list):
                    message.content.extend(
                        copy.deepcopy(p)
                        for p in trace["original"]["extra_user_content_parts"] or []
                        if self._native_group_part(p)
                    )
                    break
        self._restore_request_system(trace)

    @diagnostic_write
    def _restore_request_system(self, trace):
        if (
            trace.get("request")
            and trace.get("layout_segments")
            and not trace.get("system_restored")
        ):
            try:
                trace["request"].system_prompt = self._system_base(
                    trace, trace["request"].system_prompt or ""
                )
            except ValueError:
                trace["request"].system_prompt = trace["original"]["system_prompt"]
                logger.warning("Changed temporary system context was excluded from request history")
            trace["system_restored"] = True

    def restore_history(self, event, run_context):
        trace = event.get_extra("living_world_trace")
        if trace:
            self._restore_request_system(trace)
        if not trace or trace.get("restored") or not trace.get("history_hidden"):
            return
        req = trace["request"]
        try:
            if (
                str(getattr(getattr(req, "conversation", None), "cid", ""))
                != trace["conversation_id"]
            ):
                raise ValueError("生成期间会话发生变化")
            raw = copy.deepcopy(trace["saved_contexts"])
            restored = self._historical_messages(raw)
            messages = run_context.messages
            at = 1 if messages and getattr(messages[0], "role", "") == "system" else 0
            messages[at:at] = restored
            req.contexts = raw
            trace["restored"] = True
        except Exception as exc:  # noqa: BLE001 - Never overwrite saved history after failed restoration.
            # An incomplete provider-visible list must never overwrite host history.
            req.conversation = None
            self.record(
                trace,
                "chat.history_restore",
                {"error": str(exc), "reason": "已阻止不完整历史覆盖宿主存档"},
                status="failed",
            )

    def runner_failed(self, event, runner, exc):
        self.restore_history(event, runner.run_context)
        trace = event.get_extra("living_world_trace")
        self.record(trace, "chat.error", {"error": str(exc) or type(exc).__name__}, status="failed")
        self.runtime.debug.patch(
            trace["root"],
            status="cancelled"
            if isinstance(exc, (asyncio.CancelledError, GeneratorExit))
            else "failed",
        )

    def response(self, event, resp):
        trace = event.get_extra("living_world_trace")
        if trace and trace.get("managed"):
            self._restore_request_system(trace)
            self.record(
                trace,
                "reply.result",
                {},
                response=response_value(resp),
                kind="snapshot",
                status="failed" if getattr(resp, "role", "") == "err" else "success",
                boundary="宿主 Agent 的最终回复；发送状态单独记录",
            )
            self.runtime.debug.patch(
                trace["root"],
                status="failed" if getattr(resp, "role", "") == "err" else "generated",
                response={"reason": "模型阶段已结束；请查看发送步骤"},
            )
            for stack in trace["tools"].values():
                for entry, _ in stack:
                    self.runtime.debug.finish(entry, status="unknown", error="工具未返回完成回调")

    def tool_start(self, event, tool, args):
        trace = event.get_extra("living_world_trace")
        if not trace or not trace.get("managed"):
            return
        key = (asyncio.current_task(), id(tool))
        entry = self.record(
            trace,
            "reply.tool",
            {"name": tool.name, "arguments": json_value(args)},
            kind="tool",
            boundary="当前聊天工具的入参与公开返回；不捕获第三方工具内部调用",
        )
        trace["tools"].setdefault(key, []).append((entry, pause_tools()))

    def tool_end(self, event, tool, args, result):
        trace = event.get_extra("living_world_trace")
        key = (asyncio.current_task(), id(tool))
        stack = trace.get("tools", {}).get(key, []) if trace else []
        if stack:
            entry, token = stack.pop()
            resume_tools(token)
            self.runtime.debug.finish(
                entry,
                json_value(result),
                status="failed" if getattr(result, "isError", False) else "success",
            )

    def _wrap_send(self, event, trace):
        original = getattr(event, "send", None)
        if not callable(original):
            return
        previous = vars(event).get("send")

        async def send(message, *args, **kwargs):
            self._start_trace(trace)
            try:
                payload = json_value(message)
            except Exception:
                logger.warning("Message serialization failed", exc_info=True)
                payload = {"captured": False, "type": type(message).__name__}
            entry = self.record(
                trace,
                "reply.send",
                {"message": payload},
                kind="message",
                boundary="AstrBot 当前消息 event.send 的真实内容与传输返回；不是已读回执",
            )
            try:
                result = await original(message, *args, **kwargs)
            except BaseException as exc:
                trace["failed_sends"] += 1
                self.runtime.debug.finish(
                    entry, status="unknown", error=str(exc) or type(exc).__name__
                )
                self.runtime.debug.patch(
                    trace["root"],
                    status="partial" if trace["sent"] else "unknown",
                    response={"sent": trace["sent"], "failed_or_unknown": trace["failed_sends"]},
                )
                raise
            trace["sent"] += 1
            try:
                self.runtime.debug.finish(
                    entry, {"accepted": True, "transport_result": json_value(result)}, status="sent"
                )
                if event.get_group_id() and trace.get("managed") and not self.runtime.stopped:
                    self.add_group_message(
                        trace["scope"],
                        {
                            "id": uuid.uuid4().hex,
                            "sender_id": event.get_self_id(),
                            "sender_name": "Bot",
                            "text": _text(payload),
                            "scope": trace["scope"],
                            "time": time.time(),
                        },
                    )
                self.runtime.debug.patch(
                    trace["root"],
                    status="partial" if trace["failed_sends"] else "sent",
                    response={"sent": trace["sent"], "failed_or_unknown": trace["failed_sends"]},
                )
            except Exception:
                logger.warning(
                    "Post-send observation failed; transport already succeeded", exc_info=True
                )
            return result

        event.send = send
        trace["send_patch"] = (send, previous)

    def close(self):
        self.audit.close()
        for ref in self.events:
            event = ref()
            if event is None:
                continue
            trace = event.get_extra("living_world_trace")
            if trace and trace.get("run_context"):
                self.restore_history(event, trace["run_context"])
            if trace:
                self._restore_request_system(trace)
            if trace and "send_patch" in trace:
                wrapper, previous = trace["send_patch"]
                if getattr(event, "send", None) is wrapper:
                    if previous is None:
                        delattr(event, "send")
                    else:
                        event.send = previous
        self.events.clear()
