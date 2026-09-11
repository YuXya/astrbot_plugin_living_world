"""Civil-day schedule bounds, including midnight as an exclusive end."""

import re
from datetime import datetime, time, timedelta


SCHEDULE_DEFAULTS = {"schedule_start": "08:00", "schedule_end": "24:00"}
SLEEP_NOTICE = "日程范围外，睡梦中"


class ScheduleSleepError(RuntimeError):
    """An autonomous model call or transport was stopped before it started."""


def schedule_range(parameters):
    """Validate clocks without treating 00:00 as the end of the previous day."""
    start = parameters.get("schedule_start", SCHEDULE_DEFAULTS["schedule_start"])
    end = parameters.get("schedule_end", SCHEDULE_DEFAULTS["schedule_end"])
    clock = r"(?:[01]\d|2[0-3]):[0-5]\d"
    if not isinstance(start, str) or not re.fullmatch(clock, start):
        raise ValueError("日程开始时间必须为 HH:MM，范围 00:00—23:59")
    if not isinstance(end, str) or not re.fullmatch(clock + r"|24:00", end):
        raise ValueError("日程结束时间必须为 HH:MM，最晚为当天 24:00")
    if start >= end:
        raise ValueError("日程开始时间必须早于结束时间，不支持跨到次日凌晨")
    return start, end


def schedule_bounds(parameters, day, tz):
    """Resolve both ends in the character's time zone on the requested date."""
    start, end = schedule_range(parameters)
    first = datetime.combine(day, time.fromisoformat(start), tzinfo=tz)
    last = (
        datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz)
        if end == "24:00"
        else datetime.combine(day, time.fromisoformat(end), tzinfo=tz)
    )
    return first, last
