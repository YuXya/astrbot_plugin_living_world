"""Program-owned motivation values with deterministic online time and real SQLite."""

import asyncio
import copy
import sqlite3
from types import SimpleNamespace

import pytest

from living_world.config import settings_from
from living_world.drives import DriveService
from living_world.store import Store


class Clock:
    def __init__(self, value=0.0):
        self.value = value

    def __call__(self):
        return self.value


@pytest.fixture
def world(tmp_path):
    clock = Clock()
    runtime = SimpleNamespace(store=Store(tmp_path / "drives.sqlite"), settings=settings_from())
    runtime.enabled = lambda name: runtime.settings["modules"].get(name, False)
    drives = DriveService(runtime, clock=clock)
    runtime.drives = drives
    yield runtime, drives, clock
    runtime.store.close()


def meter(drives, kind="loneliness"):
    return drives.snapshot()["meters"][kind]


def test_new_values_and_default_rates_and_stage_text(world):
    runtime, drives, _ = world
    assert runtime.enabled("drives")
    assert meter(drives)["value"] == 0
    assert meter(drives, "energy")["value"] == 70
    assert meter(drives)["config"]["growth_per_hour"] == 10
    assert meter(drives)["config"]["costs"] == {"social": 10}
    assert meter(drives, "energy")["config"]["costs"] == {"news": 10, "search": 10}
    assert meter(drives)["thought"] == "不是很想聊天。"
    assert meter(drives, "energy")["thought"] == "想了解新鲜事，或查查感兴趣的问题。"


@pytest.mark.parametrize(
    "value,display,lower,upper",
    [
        (0, 0, 0, 40),
        (40, 40, 0, 40),
        (40.999, 40, 0, 40),
        (41, 41, 41, 80),
        (80, 80, 41, 80),
        (80.999, 80, 41, 80),
        (81, 81, 81, 100),
        (100, 100, 81, 100),
    ],
)
def test_value_display_and_stage_boundaries_use_integer_floor(world, value, display, lower, upper):
    _, drives, _ = world
    drives.set_value("loneliness", value)
    current = meter(drives)
    assert current["value"] == value and current["display_value"] == display
    assert current["stage"]["min"] == lower and current["stage"]["max"] == upper
    assert current["thought"] == current["stage"]["text"]


def test_continuous_growth_is_projected_read_only_then_persisted(world):
    runtime, drives, clock = world
    before = runtime.store.export()
    clock.value = 1800
    assert meter(drives)["value"] == 5
    assert meter(drives, "energy")["value"] == 75
    assert runtime.store.export() == before
    drives.settle()
    persisted = runtime.store.get("drive_state", "current")
    assert persisted["values"] == {"loneliness": 5, "energy": 75}
    clock.value += 18
    assert meter(drives)["value"] == pytest.approx(5.05)
    drives.settle()
    assert meter(drives)["value"] == pytest.approx(5.05)


def test_ceiling_does_not_bank_overflow_or_reset_at_day_boundary(world):
    _, drives, clock = world
    drives.set_value("loneliness", 99)
    clock.value += 3600 * 30
    drives.settle()
    assert meter(drives)["value"] == 100
    drives.debit("one-round", "social")
    assert meter(drives)["value"] == 90
    drives.settle()
    assert meter(drives)["value"] == 90


def test_reopen_resumes_value_without_offline_accumulation(world):
    runtime, drives, clock = world
    clock.value = 1800
    drives.settle()
    path = runtime.store.db.execute("PRAGMA database_list").fetchone()[2]
    runtime.store.close()
    runtime.store = Store(path)
    restarted_clock = Clock(9_000_000)
    restarted = DriveService(runtime, clock=restarted_clock)
    assert meter(restarted)["value"] == 5
    restarted_clock.value += 1800
    assert meter(restarted)["value"] == 10


def test_rate_changes_settle_old_time_and_do_not_overwrite_current_value(world):
    _, drives, clock = world
    config = copy.deepcopy(meter(drives)["config"])
    clock.value = 1800
    config["growth_per_hour"] = 20
    drives.save_settings("loneliness", config)
    assert meter(drives)["value"] == 5
    clock.value += 1800
    assert meter(drives)["value"] == 15
    drives.set_value("loneliness", 28)
    assert meter(drives)["value"] == 28
    clock.value += 900
    assert meter(drives)["value"] == 33


def test_zero_growth_and_distinct_costs_are_valid(world):
    _, drives, clock = world
    config = copy.deepcopy(meter(drives, "energy")["config"])
    config.update(growth_per_hour=0, costs={"news": 0, "search": 3.5})
    drives.save_settings("energy", config)
    clock.value += 3600
    drives.debit("free-news", "news")
    assert meter(drives, "energy")["value"] == 70
    drives.debit("search", "search")
    assert meter(drives, "energy")["value"] == 66.5


def test_debits_are_once_per_action_and_only_change_the_corresponding_value(world):
    _, drives, _ = world
    drives.set_value("loneliness", 20)
    drives.debit("social-one", "social")
    drives.debit("social-one", "social")
    assert meter(drives)["value"] == 10
    assert meter(drives, "energy")["value"] == 70
    drives.debit("news-one", "news")
    drives.debit("search-one", "search")
    assert meter(drives)["value"] == 10
    assert meter(drives, "energy")["value"] == 50
    drives.set_value("energy", 5)
    drives.debit("low-energy", "search")
    drives.debit("zero-energy", "search")
    assert meter(drives, "energy")["value"] == 0


def test_outer_transaction_rollback_restores_value_claim_and_online_anchor(world):
    runtime, drives, clock = world
    clock.value = 1800
    before = runtime.store.export()
    with pytest.raises(sqlite3.IntegrityError):
        with runtime.store.transaction():
            runtime.store.put("life_action_usage", "source", {"id": "source"})
            drives.debit("source", "news")
            raise sqlite3.IntegrityError("simulated final transaction failure")
    assert runtime.store.export() == before
    assert meter(drives, "energy")["value"] == 75
    with runtime.store.transaction():
        drives.debit("source", "news")
    assert meter(drives, "energy")["value"] == 65
    assert meter(drives)["value"] == 5


async def test_concurrent_debits_and_manual_set_are_serialized_without_duplicate_charge(world):
    _, drives, _ = world
    drives.set_value("energy", 100)
    await asyncio.gather(*(asyncio.to_thread(drives.debit, "same", "search") for _ in range(8)))
    assert meter(drives, "energy")["value"] == 90
    await asyncio.gather(
        *(asyncio.to_thread(drives.debit, f"distinct-{i}", "search") for i in range(9))
    )
    assert meter(drives, "energy")["value"] == 0
    drives.set_value("energy", 100)
    drives.debit("same", "search")
    assert meter(drives, "energy")["value"] == 100


@pytest.mark.parametrize("value", [-1, 101, float("nan"), float("inf"), True, "text"])
def test_invalid_manual_value_is_rejected_atomically(world, value):
    runtime, drives, _ = world
    before = runtime.store.export()
    with pytest.raises((ValueError, TypeError)):
        drives.set_value("loneliness", value)
    assert runtime.store.export() == before


@pytest.mark.parametrize(
    "stages",
    [
        [],
        [{"max": 99, "text": "missing last value"}],
        [{"max": 50, "text": "one"}, {"max": 49, "text": "overlap"}, {"max": 100, "text": "last"}],
        [{"max": -1, "text": "negative"}, {"max": 100, "text": "last"}],
        [{"max": 40.5, "text": "fraction"}, {"max": 100, "text": "last"}],
    ],
)
def test_invalid_stage_ranges_leave_configuration_unchanged(world, stages):
    runtime, drives, _ = world
    before = copy.deepcopy(runtime.settings)
    config = copy.deepcopy(meter(drives)["config"])
    config["stages"] = stages
    with pytest.raises((ValueError, TypeError)):
        drives.save_settings("loneliness", config)
    assert runtime.settings == before


def test_configuration_write_failure_rolls_back_growth_and_runtime_settings(world):
    runtime, drives, clock = world
    config = copy.deepcopy(meter(drives)["config"])
    config["growth_per_hour"] = 20
    before_settings, before_records = copy.deepcopy(runtime.settings), runtime.store.export()
    clock.value = 1800
    runtime.store.db.execute(
        "CREATE TEMP TRIGGER reject_drive_settings BEFORE INSERT ON objects WHEN NEW.namespace='settings' BEGIN SELECT RAISE(ABORT, 'settings disk failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="settings disk failure"):
        drives.save_settings("loneliness", config)
    assert runtime.settings == before_settings
    assert runtime.store.export() == before_records
    assert meter(drives)["value"] == 5
    runtime.store.db.execute("DROP TRIGGER reject_drive_settings")
    drives.save_settings("loneliness", config)
    clock.value += 1800
    assert meter(drives)["value"] == 15


def test_custom_stages_cover_every_integer_and_are_exactly_the_injected_thought(world):
    _, drives, _ = world
    config = copy.deepcopy(meter(drives)["config"])
    config["stages"] = [
        {"max": 0, "text": "只想安静"},
        {"max": 99, "text": "想看看朋友"},
        {"max": 100, "text": "想立刻聊聊"},
    ]
    drives.save_settings("loneliness", config)
    for value in range(101):
        drives.set_value("loneliness", value)
        current = meter(drives)
        expected = "只想安静" if value == 0 else "想立刻聊聊" if value == 100 else "想看看朋友"
        assert current["stage"]["min"] <= value <= current["stage"]["max"]
        assert current["thought"] == expected
