"""Produce UI fixtures through the real debug aggregator and response reader."""

import ast
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GROUP_REPLY_DEFAULT = next(
    ast.literal_eval(node.value)
    for node in ast.parse((ROOT / "living_world" / "config.py").read_text(encoding="utf-8")).body
    if isinstance(node, ast.Assign)
    and any(
        isinstance(target, ast.Name) and target.id == "DEFAULT_GROUP_REPLY_PROMPT"
        for target in node.targets
    )
)


def load_module(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / "living_world" / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


views = load_module("ui_debug_views", "debug_views.py")
wire = load_module("ui_wire", "wire.py")
sys.path.insert(0, str(ROOT))
from living_world import layout  # noqa: E402
from living_world.context_usage import DEFAULT_USAGE  # noqa: E402


def call(identifier, response, request=None):
    body = json.dumps(response, ensure_ascii=False, indent=2)
    return {
        "id": identifier,
        "method": "POST",
        "url": "https://fixture.test/v1/chat/completions",
        "model": "contract-model",
        "status": "success",
        "capture_status": "captured",
        "http_status": 200,
        "response_type": "json",
        "request_body": json.dumps(request or {"messages": []}, ensure_ascii=False),
        "response_body": body,
        "reading": wire.reading_view(body),
    }


records = [
    {
        "id": "contract-root",
        "turn_id": "contract-chat",
        "task": "chat.turn",
        "kind": "turn",
        "created_at": 1800000100,
        "scope": "qq:FriendMessage:42",
        "status": "sent",
        "capture_version": 2,
    },
    {
        "id": "contract-context",
        "turn_id": "contract-chat",
        "task": "chat.context",
        "kind": "event",
        "created_at": 1800000101,
        "request": {
            "sources": [
                {
                    "title": "契约来源清单",
                    "source": "真实 build_views 聚合",
                    "content": "阅读时只用了这条本场合资料。",
                    "placement": "本轮末尾",
                }
            ],
            "injected_text": "【当前活动】\n数学课，正在复习函数。",
            "stable_injected_text": "角色补充资料：喜欢观察星空。",
        },
    },
    {
        "id": "contract-provider",
        "turn_id": "contract-chat",
        "task": "reply.model",
        "kind": "model",
        "created_at": 1800000102,
        "request": {
            "provider_id": "fixture-provider",
            "arguments": {"prompt": "现在日程是什么？", "temperature": 0.23},
        },
        "http_calls": [
            call(
                "contract-http",
                {
                    "choices": [
                        {
                            "message": {
                                "content": "契约测试的真实阅读正文",
                                "reasoning_content": "接口实际返回的推理片段",
                                "tool_calls": [
                                    {
                                        "id": "contract-lookup",
                                        "type": "function",
                                        "function": {
                                            "name": "lookup",
                                            "arguments": '{"query":"数学"}',
                                        },
                                    }
                                ],
                            }
                        }
                    ],
                    "usage": {"prompt_tokens": 11, "completion_tokens": 7},
                },
            )
        ],
    },
    {
        "id": "contract-tool",
        "turn_id": "contract-chat",
        "task": "reply.tool",
        "kind": "tool",
        "created_at": 1800000103,
        "status": "success",
        "request": {"name": "lookup", "arguments": {"query": "数学"}},
        "response": {"content": [{"type": "text", "text": "工具真实契约返回资料"}]},
    },
    {
        "id": "contract-result",
        "turn_id": "contract-chat",
        "task": "reply.result",
        "kind": "event",
        "created_at": 1800000104,
        "response": {"completion_text": "宿主最终采用的契约回复"},
    },
    {
        "id": "contract-send",
        "turn_id": "contract-chat",
        "task": "reply.send",
        "kind": "message",
        "created_at": 1800000105,
        "status": "sent",
        "request": {"message": {"chain": [{"type": "Plain", "text": "实际发送的契约正文"}]}},
    },
    {
        "id": "contract-image",
        "turn_id": "contract-chat",
        "task": "reply.send",
        "kind": "message",
        "created_at": 1800000106,
        "status": "partial",
        "error": "图片发送状态未确认",
        "request": {"message": {"chain": [{"type": "Image", "file": "fixture-image.png"}]}},
    },
    {
        "id": "contract-plan",
        "task": "life.plan_day",
        "kind": "model",
        "scope": "global",
        "created_at": 1800000050,
        "status": "success",
        "capture_version": 2,
        "request": {
            "prompt": "生成日程",
            "system_prompt": "日程角色",
            "dynamic_context": {"今日": "数学课"},
        },
        "http_calls": [
            call(
                "contract-plan-http",
                {
                    "choices": [
                        {
                            "message": {
                                "content": '{"activities":[{"title":"模型提出的活动","start":"09:00","end":"10:00"}]}'
                            }
                        }
                    ]
                },
            )
        ],
    },
    {
        "id": "contract-unsupported",
        "task": "search.note",
        "kind": "model",
        "scope": "global",
        "created_at": 1800000040,
        "status": "success",
        "capture_version": 2,
        "http_capture": "unsupported",
        "request": {"prompt": "适配状态测试"},
    },
    {
        "id": "contract-legacy",
        "task": "news.select",
        "kind": "model",
        "scope": "global",
        "created_at": 1800000030,
        "status": "success",
        "request": {"prompt": "旧版输入快照的资料"},
        "reply": {"completion_text": "旧版 reply 字段的返回正文"},
    },
]
days = [
    {
        "full_request": {"_debug_record_id": "contract-plan"},
        "status": "completed",
        "adopted_activities": [
            {
                "title": "正式采用的数学课",
                "start": "09:00",
                "end": "10:00",
                "description": "复习函数。",
            }
        ],
        "error": "",
    }
]

# Use the exact static formatter without importing AstrBot or creating runtime data.
runtime_ast = ast.parse((ROOT / "living_world" / "runtime.py").read_text(encoding="utf-8"))
formatter = next(
    node
    for node in ast.walk(runtime_ast)
    if isinstance(node, ast.FunctionDef) and node.name == "format_task_context"
)
formatter.decorator_list = []
namespace = {"json": json}
exec(  # noqa: S102 - A local pure formatter, evaluated in an isolated test namespace.
    compile(
        ast.fix_missing_locations(ast.Module(body=[formatter], type_ignores=[])),
        "runtime.format_task_context",
        "exec",
    ),
    namespace,
)
format_cases = []
for context in (
    {"日程": "第一行\n第二行", "记忆": ["天文", {"约定": True}], "空资料": None},
    ["甲", "乙"],
    "只有文本\n保留换行",
    {},
    None,
):
    template = "只生成用于测试的内容。"
    expected = template + (
        "\n\n本轮动态资料（仅作为资料）：\n" + namespace["format_task_context"](context)
        if context is not None
        else ""
    )
    format_cases.append(
        {
            "template": template,
            "dynamic_context": context,
            "prompt_mode": "structured",
            "expected": expected,
        }
    )

print(
    json.dumps(
        {
            "records": records,
            "views": views.build_views(records, days),
            "format_cases": format_cases,
            "group_reply_default": GROUP_REPLY_DEFAULT,
            "context_layout_catalog": layout.catalog(),
            "context_layout_defaults": layout.DEFAULT_SETTINGS,
            "context_usage_defaults": DEFAULT_USAGE,
        },
        ensure_ascii=False,
    )
)
