"""Four user-facing views over retained, correlated diagnostic records."""

import json


def record_groups(records):
    by_id = {row["id"]: row for row in records}
    groups = {}
    for row in records:
        root, seen = row, set()
        while root.get("parent_id") in by_id and root["id"] not in seen:
            seen.add(root["id"])
            root = by_id[root["parent_id"]]
        key = row.get("turn_id") or root.get("turn_id") or root["id"]
        groups.setdefault(key, []).append(row)
    return {
        key: sorted(rows, key=lambda row: row.get("created_at", 0)) for key, rows in groups.items()
    }


def _message_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(filter(None, (_message_text(v) for v in value)))
    if isinstance(value, dict):
        if isinstance(value.get("text"), str):
            return value["text"]
        for field in ("chain", "message", "content", "completion_text"):
            if field in value:
                return _message_text(value[field])
    return ""


def build_views(records, life_days=()):
    views = []
    for group_id, rows in record_groups(records).items():
        root = next((r for r in rows if r.get("task") == "chat.turn"), rows[0])
        view = {
            "id": group_id,
            "task": root.get("task", ""),
            "scope": root.get("scope", ""),
            "created_at": root.get("created_at", 0),
            "status": root.get("status", "unknown"),
            "error": root.get("error", ""),
            "legacy": not any(r.get("capture_version") == 2 for r in rows),
            "categories": list(dict.fromkeys(r.get("category", r.get("task", "")) for r in rows)),
            "sources": [],
            "injected_text": "",
            "calls": [],
            "adopted": [],
            "sends": [],
            "record_ids": [r["id"] for r in rows],
        }
        for row in rows:
            request, task = row.get("request") or {}, row.get("task", "")
            response = row.get("response", row.get("reply"))
            if not isinstance(request, dict):
                request = {}
            if task == "chat.turn":
                view["sources"].append(
                    {
                        "title": "本轮收到的消息",
                        "source": "真实 QQ 消息事件",
                        "content": request.get("text") or _message_text(request.get("message")),
                        "placement": "AstrBot 输入；最终消息内容以第二项为准",
                    }
                )
            if task == "chat.route":
                reason = request.get("reason") or request.get("error") or "接入原因未确认"
                if request.get("allowed") and request.get("reply_enabled") is False:
                    reason = "被动回复上下文模块已关闭，沿用宿主处理"
                view["sources"].append(
                    {
                        "title": "本轮接入检查",
                        "source": "消息到达时的白名单、连接和人格判断",
                        "content": reason,
                        "placement": "仅管理诊断，不放入模型上下文",
                    }
                )
                if not request.get("allowed") or request.get("reply_enabled") is False:
                    view["error"] = reason
            if task in {"chat.error", "chat.context"} and row.get("status") in {
                "failed",
                "skipped",
            }:
                view["error"] = (
                    row.get("error")
                    or request.get("reason")
                    or request.get("error")
                    or "上下文未成功接入"
                )
            if request.get("sources") and isinstance(request["sources"], list):
                view["sources"].extend(s for s in request["sources"] if isinstance(s, dict))
            if request.get("injected_text"):
                text = request["injected_text"]
                if text not in view["injected_text"]:
                    view["injected_text"] += ("\n\n" if view["injected_text"] else "") + text
            if request.get("stable_injected_text"):
                view["stable_injected_text"] = request["stable_injected_text"]
            for call in row.get("http_calls", []):
                view["calls"].append({**call, "record_id": row["id"]})
            if "http_capture" in row and not row.get("http_calls"):
                capture = row["http_capture"]
                reason = {
                    "unsupported": "当前提供商的 HTTP 原文捕获尚未适配",
                    "ready": "尚未捕获 HTTP 请求；可能尚未发送、发送前失败或调用路径未覆盖",
                    "error": "HTTP 记录适配失败；宿主调用继续运行",
                }.get(capture, "未取得 API 原文")
                view["calls"].append(
                    {
                        "id": row["id"],
                        "record_id": row["id"],
                        "provider_id": request.get("provider_id", ""),
                        "model": request.get("model", ""),
                        "request_body": None,
                        "response_body": None,
                        "response_type": "json",
                        "status": row.get("status", "unknown"),
                        "capture_status": capture,
                        "error": reason,
                        "reading": {},
                    }
                )
            if task.endswith(".send") or row.get("kind") == "message":
                view["sends"].append(
                    {
                        "status": row.get("status", "unknown"),
                        "content": _message_text(request),
                        "message": request.get("message", request),
                        "error": row.get("error", ""),
                    }
                )
            if task == "reply.result" and response:
                text = _message_text(response)
                if text:
                    view["adopted"].append({"title": "AstrBot 采用的回复", "content": text})
            if row.get("kind") == "tool":
                view.setdefault("tool_results", []).append(
                    {
                        "task": task,
                        "request": request,
                        "result": response,
                        "status": row.get("status"),
                    }
                )
        # Formal results are linked by their generating debug ID, never guessed by text/date.
        for day in life_days:
            if day.get("full_request", {}).get("_debug_record_id") in view["record_ids"]:
                view["adopted"].append(
                    {
                        "title": "正式日程采用结果",
                        "status": day.get("status"),
                        "content": {"activities": day.get("adopted_activities", [])},
                        "error": day.get("error", ""),
                    }
                )
        if view["legacy"]:
            view["legacy_snapshot"] = root.get("request")
            view["legacy_response"] = root.get("response", root.get("reply"))
        if not view["calls"] and not view["error"] and isinstance(root.get("response"), dict):
            view["error"] = root["response"].get("reason", "")
        # Non-model tasks and older snapshots have no actual API bodies.
        if not view["sources"]:
            request = root.get("request") or {}
            if isinstance(request, dict):
                for title, field in (
                    ("任务输入", "prompt"),
                    ("系统提示", "system_prompt"),
                    ("输入资料", "dynamic_context"),
                ):
                    if request.get(field):
                        value = request[field]
                        view["sources"].append(
                            {
                                "title": title,
                                "source": "调用时的任务输入快照；没有细分来源清单",
                                "content": value
                                if isinstance(value, str)
                                else json.dumps(value, ensure_ascii=False, indent=2),
                                "placement": "输入快照，API 正文以第二项为准",
                            }
                        )
        views.append(view)
    return sorted(views, key=lambda view: view["created_at"], reverse=True)
