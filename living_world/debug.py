"""Bounded diagnostics and templates, separate from durable business records."""

import dataclasses
import enum
import functools
import logging
import math
import time
import uuid

from .debug_views import build_views, record_groups
from .debug_payload import compact_record, snapshot_value
from .layout import task_label
from .prompts import PROMPTS

BOUNDARY = "任务输入快照；API 原文以实际捕获的 HTTP 正文为准"
DEFAULT_TEMPLATES = PROMPTS
SECRET_FIELDS = {
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "password",
    "cookie",
    "cookies",
    "access_token",
    "refresh_token",
    "client_secret",
}

logger = logging.getLogger(__name__)


def diagnostic_write(method):
    """A debug-only storage failure must not interrupt a business operation."""

    @functools.wraps(method)
    def guarded(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except Exception:
            logger.warning("Debug record write failed: %s", method.__name__, exc_info=True)
            return None

    return guarded


def json_value(value):
    """Keep complete text while omitting authentication fields and opaque objects."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, enum.Enum):
        return json_value(value.value)
    if isinstance(value, dict):
        return {
            str(k): "[认证信息已隐藏]" if str(k).lower() in SECRET_FIELDS else json_value(v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [json_value(v) for v in value]
    if hasattr(value, "model_dump"):
        return json_value(value.model_dump())
    # AstrBot message components still use Pydantic v1 on Python 3.12.
    if hasattr(value, "dict") and callable(value.dict):
        return json_value(value.dict())
    if hasattr(value, "openai_schema"):
        return json_value(value.openai_schema())
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return json_value({f.name: getattr(value, f.name) for f in dataclasses.fields(value)})
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return {"type": type(value).__name__, "captured": False}


def response_value(response):
    """Expose computed completion text as well as the provider's original response."""
    value = json_value(response)
    if isinstance(value, dict) and hasattr(response, "completion_text"):
        value["completion_text"] = response.completion_text
        value.pop("_completion_text", None)
    return value


def _observation_only(rows):
    """Recognize legacy observation turns without discarding attempted work."""
    roots = [row for row in rows if row.get("task") == "chat.turn"]
    if len(roots) != 1:
        return False
    root = roots[0]
    if (
        root.get("capture_version") != 2
        or root.get("status") != "observed"
        or root.get("response") != {"reason": "尚未进入模型阶段；其他链路是否处理暂不确定"}
    ):
        return False
    return all(
        row.get("task") in {"chat.turn", "chat.route", "chat.history"}
        and row.get("kind") in {"turn", "event"}
        and not row.get("http_calls")
        and "http_capture" not in row
        and row.get("reply") is None
        and (
            row.get("response") is None
            or (isinstance(row["response"], dict) and set(row["response"]) <= {"reason"})
        )
        for row in rows
    )


class DebugService:
    def __init__(self, runtime):
        self.runtime = runtime
        self.defaults = dict(DEFAULT_TEMPLATES)
        for summary in self.metadata():
            if summary.get("status") != "running":
                continue
            row = runtime.store.get("debug_records", summary["id"])
            if row.get("status") == "running":
                row.update(status="interrupted", error="插件重启，调用结果未确认")
                runtime.store.put("debug_records", row["id"], row)
        self.trim()

    def metadata(self):
        fields = (
            "id",
            "task",
            "category",
            "module",
            "scope",
            "kind",
            "turn_id",
            "parent_id",
            "created_at",
            "status",
            "capture_version",
            "error",
        )
        project = getattr(self.runtime.store, "project", None)
        if project is not None:
            return project("debug_records", fields)
        return [
            {key: row[key] for key in fields if key in row}
            for row in self.runtime.store.list("debug_records")
        ]

    def page_index(self):
        records = self.metadata()
        return {"debug_records": records, "debug_views": build_views(records), "debug_lazy": True}

    def page_record(self, record_id):
        groups = record_groups(self.metadata())
        selected = groups.get(record_id) or next(
            (rows for rows in groups.values() if any(row["id"] == record_id for row in rows)), []
        )
        records = [self.runtime.store.get("debug_records", row["id"]) for row in selected]
        records = [row for row in records if row]
        if not records:
            raise ValueError("这次调用记录已清理或不存在，请刷新调用列表")
        views = build_views(
            records,
            self.runtime.store.list("life_days") + self.runtime.store.list("life_day_history"),
        )
        return {"view": views[0]}

    @diagnostic_write
    def begin(
        self,
        task,
        request,
        *,
        module="",
        scope="global",
        kind="model",
        boundary=BOUNDARY,
        turn_id="",
        parent_id="",
    ):
        if not self.runtime.enabled("debug"):
            return None
        record = {
            "id": uuid.uuid4().hex,
            "capture_version": 2,
            "task": task,
            "category": task,
            "module": module,
            "scope": scope,
            "kind": kind,
            "boundary": boundary,
            "created_at": time.time(),
            "request": snapshot_value(json_value(request)),
            "status": "running",
            "turn_id": turn_id,
            "parent_id": parent_id,
        }
        record = compact_record(record)
        self.runtime.store.put("debug_records", record["id"], record)
        self.trim()
        return record

    @diagnostic_write
    def finish(self, record, response=None, *, status="success", error=""):
        if (
            not self.runtime.enabled("debug")
            or not record
            or not self.runtime.store.get("debug_records", record["id"])
        ):
            return
        record = {
            **self.runtime.store.get("debug_records", record["id"], record),
            "response": snapshot_value(json_value(response)),
            "status": status,
            "error": str(error),
            "finished_at": time.time(),
        }
        self.runtime.store.put("debug_records", record["id"], record)
        self.trim()

    def trim(self):
        limit = int(self.runtime.settings["debug"]["retain_per_category"])
        counts = {}
        deletes = []
        groups = sorted(
            record_groups(self.metadata()).values(),
            key=lambda rows: rows[0].get("created_at", 0),
            reverse=True,
        )
        for rows in groups:
            possible_observation = any(
                row.get("task") == "chat.turn" and row.get("status") == "observed" for row in rows
            )
            if possible_observation and _observation_only(
                [self.runtime.store.get("debug_records", row["id"], {}) for row in rows]
            ):
                deletes.extend(("debug_records", row["id"]) for row in rows)
                continue
            root = rows[0]
            category = (
                "__chat_turn__"
                if any(r.get("turn_id") for r in rows)
                else root.get("category", "unknown")
            )
            counts[category] = counts.get(category, 0) + 1
            if counts[category] > limit:
                deletes.extend(("debug_records", row["id"]) for row in rows)
        if deletes:
            self.runtime.store.apply_batch([], deletes)

    def clear(self, category=None):
        count = 0
        records = self.metadata()
        for rows in record_groups(records).values():
            if category is None or any(row.get("category") == category for row in rows):
                for row in rows:
                    self.runtime.store.delete("debug_records", row["id"])
                    count += 1
        return {
            "status": "success",
            "deleted": count,
            "text": "仅清理调试记录；日程、记忆与执行防重记录保留",
        }

    @diagnostic_write
    def patch(self, record, **changes):
        if not self.runtime.enabled("debug") or not record:
            return
        current = self.runtime.store.get("debug_records", record["id"])
        if current:
            current.update(json_value(changes))
            self.runtime.store.put("debug_records", current["id"], current)

    def template(self, task, default):
        self.defaults[task] = default
        # Only the static instruction is kept here; never store the dynamic context.
        self.runtime.store.put(
            "prompt_defaults", task, {"id": task, "template": default, "schema_version": 4}
        )
        return self.runtime.store.get("prompt_templates", task, {}).get("template", default)

    def get_default(self, task):
        return self.defaults.get(task) or self.runtime.store.get("prompt_defaults", task, {}).get(
            "template", ""
        )

    def save_template(self, task, template):
        if task not in self.defaults and not self.runtime.store.get("prompt_defaults", task):
            raise ValueError("未知提示词任务")
        if not isinstance(template, str) or not template.strip() or len(template) > 100000:
            raise ValueError("模板需要填写非空文本，最多 100000 字符")
        self.runtime.store.put(
            "prompt_templates", task, {"id": task, "template": template, "schema_version": 4}
        )
        return {
            "status": "success",
            "task": task,
            "template": template,
            "text": "只保存此模板文本；本轮临时资料不会自动写入模板",
        }

    def reset_template(self, task):
        self.runtime.store.put(
            "prompt_templates",
            task,
            {"id": task, "template": self.get_default(task), "schema_version": 4},
        )
        return {"status": "success", "task": task, "template": self.get_default(task)}

    def snapshot(self):
        tasks = set(self.defaults) | {r["id"] for r in self.runtime.store.list("prompt_defaults")}
        return {
            "templates": [
                {
                    "task": task,
                    "label": task_label(task),
                    "default_template": self.get_default(task),
                    "template": self.runtime.store.get("prompt_templates", task, {}).get(
                        "template", self.get_default(task)
                    ),
                }
                for task in sorted(tasks)
            ],
            "boundary": BOUNDARY,
        }

    def views(self):
        return build_views(
            self.runtime.store.list("debug_records"),
            self.runtime.store.list("life_days") + self.runtime.store.list("life_day_history"),
        )

    def export_body(self, call_id, side):
        if side not in {"request", "response"}:
            raise ValueError("请选择请求或返回")
        for view in self.views():
            for call in view["calls"]:
                if call["id"] == call_id:
                    body = call.get(side + "_body")
                    if body is None:
                        raise ValueError("未捕获原始正文，不能以快照代替")
                    kind = "json" if side == "request" else call.get("response_type", "text")
                    return {
                        "body": body,
                        "filename": f"living-world-{call_id}-{side}.{kind}",
                        "format": kind,
                    }
        raise ValueError("调用记录不存在或已被清理")
