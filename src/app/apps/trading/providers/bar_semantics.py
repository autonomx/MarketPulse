from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from app.apps.trading.us_equity_calendar import regular_close_time


_INTERVAL = re.compile(r"^(?P<count>[1-9][0-9]*)(?P<unit>mo|m|h|d|w)$", re.IGNORECASE)


def interval_duration(interval: str) -> timedelta:
    match = _INTERVAL.fullmatch(interval.strip())
    if match is None:
        raise ValueError(f"unsupported Trading interval: {interval}")
    count = int(match.group("count"))
    unit = match.group("unit").lower()
    if unit == "mo":
        return timedelta(days=30 * count)
    if unit == "m":
        return timedelta(minutes=count)
    if unit == "h":
        return timedelta(hours=count)
    if unit == "d":
        return timedelta(days=count)
    return timedelta(weeks=count)


def normalized_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("Trading provider timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def continuous_bar_end(start_time: datetime, interval: str) -> datetime:
    return normalized_utc(start_time) + interval_duration(interval)


def equity_session_bounds(
    session_date: date,
    timezone_name: str,
) -> tuple[datetime, datetime]:
    zone = ZoneInfo(timezone_name)
    session_open = datetime.combine(session_date, time(9, 30), tzinfo=zone)
    session_close = datetime.combine(session_date, regular_close_time(session_date), tzinfo=zone)
    return session_open.astimezone(timezone.utc), session_close.astimezone(timezone.utc)


def equity_bar_times(
    provider_timestamp: datetime,
    interval: str,
    timezone_name: str,
) -> tuple[datetime, datetime, str]:
    timestamp = normalized_utc(provider_timestamp)
    zone = ZoneInfo(timezone_name)
    local = timestamp.astimezone(zone)
    unit = interval[-1].lower()
    if unit in {"d", "w"}:
        start, end = equity_session_bounds(local.date(), timezone_name)
        if unit == "w":
            end = start + interval_duration(interval)
        return start, end, "regular"

    end = timestamp + interval_duration(interval)
    local_time = local.timetz().replace(tzinfo=None)
    if local_time < time(9, 30):
        session = "extended_pre"
    elif local_time >= regular_close_time(local.date()):
        session = "extended_post"
    else:
        session = "regular"
    return timestamp, end, session


def is_final_bar(end_time: datetime, received_at: datetime) -> bool:
    return normalized_utc(end_time) <= normalized_utc(received_at)
