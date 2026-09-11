"""Context settings save independently of business maintenance and full-page reads."""

import copy

import pytest

from living_world.runtime import Runtime
from test_runtime import FakeHost


@pytest.mark.parametrize(
    "patch",
    [
        {"context_usage": {"limits": {"memory.related": 7}}},
        {"context_layout": {"tasks": {"chat.private": ["memory.recent"]}}},
        {"reply": {"private_prompt": "本轮简短回复。"}},
        {
            "reply": {"private_prompt": "修改文案，保留插话间隔。"},
            "social": {"interjection_interval_minutes": 30},
        },
    ],
)
async def test_context_save_only_validates_and_persists(tmp_path, monkeypatch, patch):
    path = tmp_path / "save.sqlite"
    runtime = Runtime(path, FakeHost())
    before = copy.deepcopy(runtime.settings)
    version = runtime.config_version
    runtime.store.put("memory_jobs", "pending", {"id": "pending", "text": "待提炼资料"})
    runtime.store.put("debug_records", "old", {"id": "old", "text": "保留记录"})

    def unexpected(*args, **kwargs):
        raise AssertionError("Context save must not read records or start maintenance")

    try:
        with monkeypatch.context() as guard:
            for target, name in (
                (runtime.store, "list"),
                (runtime.drives, "settle"),
                (runtime.memory, "maintain"),
                (runtime.life, "reconcile_actions"),
                (runtime.debug, "trim"),
                (runtime, "kick_memory"),
                (runtime, "snapshot"),
            ):
                guard.setattr(target, name, unexpected)
            result = await runtime.update_settings(patch)
        assert result["settings"] == runtime.settings == runtime.store.get("settings", "current")
        assert runtime.config_version == version
        for key in set(before) - set(patch):
            assert runtime.settings[key] == before[key]
        assert runtime.store.get("memory_jobs", "pending")["text"] == "待提炼资料"
        assert runtime.store.get("debug_records", "old")["text"] == "保留记录"
        saved = copy.deepcopy(runtime.settings)
    finally:
        await runtime.stop()
    reloaded = Runtime(path, FakeHost())
    try:
        assert reloaded.settings == saved
    finally:
        await reloaded.stop()


async def test_invalid_or_failed_context_save_preserves_memory_and_disk(tmp_path, monkeypatch):
    runtime = Runtime(tmp_path / "save.sqlite", FakeHost())
    try:
        await runtime.update_settings({"context_usage": {"limits": {"memory.related": 7}}})
        before = copy.deepcopy(runtime.settings)
        with pytest.raises(ValueError):
            await runtime.update_settings({"context_usage": {"limits": {"memory.related": -1}}})
        assert runtime.settings == before

        def disk_failure(*args, **kwargs):
            raise OSError("Disk unavailable")

        with monkeypatch.context() as guard:
            guard.setattr(runtime.store, "put", disk_failure)
            with pytest.raises(OSError, match="Disk unavailable"):
                await runtime.update_settings({"context_usage": {"limits": {"memory.related": 8}}})
        assert runtime.settings == before == runtime.store.get("settings", "current")
    finally:
        await runtime.stop()
