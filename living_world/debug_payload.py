"""Keep diagnostic text without duplicating host history or inline media binaries."""


def snapshot_value(value):
    """This applies only to snapshots; captured HTTP bodies must remain byte-exact."""
    if isinstance(value, str):
        if value.startswith("data:") and ";base64," in value[:160]:
            mime, _, payload = value.partition(",")
            return f"[媒体数据未重复保存：{mime[5:].split(';')[0]}，{len(payload)} 字符]"
        return value
    if isinstance(value, list):
        return [snapshot_value(item) for item in value]
    if isinstance(value, dict):
        kind = value.get("type")
        media = (
            (isinstance(kind, str) and kind in {"image", "audio", "input_audio"})
            or value.get("mimeType")
            or value.get("mime_type")
        )
        return {
            key: f"[媒体数据未重复保存：{len(item)} 字符]"
            if media and key == "data" and isinstance(item, str)
            else snapshot_value(item)
            for key, item in value.items()
        }
    return value


def compact_record(record):
    """Remove redundant snapshot data while leaving exact HTTP evidence untouched."""
    result = dict(record)
    for key in ("request", "response", "reply"):
        if key in record:
            value = record[key]
            if (
                key == "request"
                and record.get("task") == "reply.request"
                and isinstance(value, dict)
            ):
                value = dict(value)
                contexts = value.pop("contexts", None)
                if contexts is not None:
                    value["host_history_messages"] = (
                        len(contexts) if isinstance(contexts, list) else None
                    )
                    value["snapshot_notice"] = (
                        "宿主历史不在插件快照中重复保存；此项不是 API 请求原文。"
                    )
            result[key] = snapshot_value(value)
    return result
