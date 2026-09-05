"""Bounded diagnostics and templates, separate from durable business records."""

import dataclasses
import enum
import math
import time
import uuid

from .prompts import PROMPTS

BOUNDARY = "Living World → AstrBot 模型调用参数；不包含提供商 SDK 最终 HTTP 请求"
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
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return json_value({f.name: getattr(value, f.name) for f in dataclasses.fields(value)})
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return {"type": type(value).__name__, "captured": False}


class DebugService:
    def __init__(self, runtime):
        self.runtime = runtime
        self.defaults = dict(DEFAULT_TEMPLATES)
        for row in runtime.store.list("debug_records"):
            if row.get("status") == "running":
                row.update(status="interrupted", error="插件重启，调用结果未确认")
                runtime.store.put("debug_records", row["id"], row)
        self.trim()

    def begin(self, task, request, *, module="", scope="global", kind="model", boundary=BOUNDARY):
        if not self.runtime.enabled("debug"):
            return None
        record = {
            "id": uuid.uuid4().hex,
            "task": task,
            "category": task,
            "module": module,
            "scope": scope,
            "kind": kind,
            "boundary": boundary,
            "created_at": time.time(),
            "request": json_value(request),
            "status": "running",
        }
        self.runtime.store.put("debug_records", record["id"], record)
        self.trim()
        return record

    def finish(self, record, response=None, *, status="success", error=""):
        if not record or not self.runtime.store.get("debug_records", record["id"]):
            return
        record = {
            **record,
            "response": json_value(response),
            "reply": json_value(response),
            "status": status,
            "error": str(error),
            "finished_at": time.time(),
        }
        self.runtime.store.put("debug_records", record["id"], record)
        self.trim()

    def trim(self):
        limit = int(self.runtime.settings["debug"]["retain_per_category"])
        counts = {}
        records = sorted(
            self.runtime.store.list("debug_records"),
            key=lambda r: r.get("created_at", 0),
            reverse=True,
        )
        for row in records:
            category = row.get("category", "unknown")
            counts[category] = counts.get(category, 0) + 1
            if counts[category] > limit:
                self.runtime.store.delete("debug_records", row["id"])

    def clear(self, category=None):
        count = 0
        for row in self.runtime.store.list("debug_records"):
            if category is None or row.get("category") == category:
                self.runtime.store.delete("debug_records", row["id"])
                count += 1
        return {
            "status": "success",
            "deleted": count,
            "text": "仅清理调试记录；日程、记忆与执行防重记录保留",
        }

    def template(self, task, default):
        self.defaults[task] = default
        # Only the static instruction is kept here; never store the dynamic context.
        self.runtime.store.put("prompt_defaults", task, {"id": task, "template": default})
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
        self.runtime.store.put("prompt_templates", task, {"id": task, "template": template})
        return {
            "status": "success",
            "task": task,
            "text": "只保存此模板文本；测试上下文不会自动写入模板",
        }

    def reset_template(self, task):
        self.runtime.store.delete("prompt_templates", task)
        return {"status": "success", "task": task, "template": self.get_default(task)}

    def snapshot(self):
        tasks = set(self.defaults) | {r["id"] for r in self.runtime.store.list("prompt_defaults")}
        return {
            "templates": [
                {
                    "task": task,
                    "default_template": self.get_default(task),
                    "template": self.runtime.store.get("prompt_templates", task, {}).get(
                        "template", self.get_default(task)
                    ),
                }
                for task in sorted(tasks)
            ],
            "boundary": BOUNDARY,
        }
