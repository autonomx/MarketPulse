from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

# The one Eastern-time definition for trading (WP-8.3): import EASTERN from here
# instead of building another ZoneInfo.
EASTERN_TIMEZONE = "America/New_York"
EASTERN = ZoneInfo(EASTERN_TIMEZONE)
_ET = EASTERN


def _observed(day: date) -> date:
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def _nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + (occurrence - 1) * 7)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        first_next = date(year + 1, 1, 1)
    else:
        first_next = date(year, month + 1, 1)
    value = first_next - timedelta(days=1)
    return value - timedelta(days=(value.weekday() - weekday) % 7)


def _easter_sunday(year: int) -> date:
    """Gregorian Easter via the Meeus/Jones/Butcher algorithm."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def regular_holidays(year: int) -> set[date]:
    holidays = {
        _observed(date(year, 1, 1)),
        _nth_weekday(year, 1, 0, 3),  # MLK Day
        _nth_weekday(year, 2, 0, 3),  # Washington's Birthday
        _easter_sunday(year) - timedelta(days=2),  # Good Friday
        _last_weekday(year, 5, 0),  # Memorial Day
        _observed(date(year, 7, 4)),
        _nth_weekday(year, 9, 0, 1),  # Labor Day
        _nth_weekday(year, 11, 3, 4),  # Thanksgiving
        _observed(date(year, 12, 25)),
    }
    if year >= 2022:
        holidays.add(_observed(date(year, 6, 19)))  # Juneteenth
    # The observed New Year's holiday can fall in the prior calendar year.
    holidays.add(_observed(date(year + 1, 1, 1)))
    return holidays


def early_close_time(session_date: date) -> time | None:
    """Return the standard 13:00 ET early close when rule-based and scheduled."""
    month, day = session_date.month, session_date.day
    # Early closes fall only on July 3, the Friday after Thanksgiving (23-29
    # November) and December 24. Bar loops ask about every bar, so other dates
    # answer before the year's holidays are computed.
    if not ((month == 7 and day <= 3) or (month == 11 and day >= 23) or (month == 12 and day == 24)):
        return None
    if session_date.weekday() >= 5 or session_date in regular_holidays(session_date.year):
        return None
    thanksgiving = _nth_weekday(session_date.year, 11, 3, 4)
    if session_date == thanksgiving + timedelta(days=1):
        return time(13, 0)

    independence = date(session_date.year, 7, 4)
    if independence.weekday() in {1, 2, 3, 4}:  # Tue-Fri: prior trading day closes early.
        candidate = independence - timedelta(days=1)
        while candidate.weekday() >= 5 or candidate in regular_holidays(candidate.year):
            candidate -= timedelta(days=1)
        if session_date == candidate:
            return time(13, 0)

    christmas_eve = date(session_date.year, 12, 24)
    if christmas_eve.weekday() < 5 and christmas_eve not in regular_holidays(session_date.year):
        if session_date == christmas_eve:
            return time(13, 0)
    return None


REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)


def regular_close_time(session_date: date) -> time:
    """The regular session's close: 13:00 ET on a scheduled early close, else 16:00."""
    return early_close_time(session_date) or REGULAR_CLOSE


def after_regular_close(moment: datetime) -> bool:
    """Whether an aware timestamp is at or after its day's regular close (ET)."""
    local = moment.astimezone(_ET)
    return local.time() >= regular_close_time(local.date())


# By then the session's final regular bars have arrived, including feeds that
# lag up to 15 minutes; a live evaluation of the session cannot change after it.
SESSION_SETTLE = timedelta(minutes=30)


def regular_session_settled(moment: datetime) -> bool:
    """Whether an aware timestamp is at least SESSION_SETTLE past its day's regular close (ET)."""
    local = moment.astimezone(_ET)
    close = datetime.combine(local.date(), regular_close_time(local.date()), tzinfo=_ET)
    return local >= close + SESSION_SETTLE


def us_equity_session(source_time: datetime) -> str:
    """Classify the standard U.S. listed-equity session from an aware timestamp.

    This covers recurring Nasdaq/NYSE holiday and early-close rules. Exceptional
    unscheduled closures and symbol-specific halts remain provider-status events,
    which are separately consumed from Alpaca's IEX status stream.
    """
    if source_time.tzinfo is None:
        raise ValueError("US equity session timestamps must be timezone-aware")
    local = source_time.astimezone(_ET)
    session_date = local.date()
    if local.weekday() >= 5 or session_date in regular_holidays(session_date.year):
        return "closed"
    clock = local.timetz().replace(tzinfo=None)
    early_close = early_close_time(session_date)
    regular_close = early_close or REGULAR_CLOSE
    # Nasdaq extended trading ends at 17:00 ET on standard 13:00 early-close
    # sessions rather than the normal 20:00 ET. Keep this explicit so a fresh
    # quote after that cutoff cannot be mislabeled as executable extended-post.
    extended_close = time(17, 0) if early_close is not None else time(20, 0)
    if time(4, 0) <= clock < time(9, 30):
        return "extended_pre"
    if time(9, 30) <= clock < regular_close:
        return "regular"
    if regular_close <= clock < extended_close:
        return "extended_post"
    return "closed"
