"""The regular-session close comes from the US equity calendar (WP-8.3)."""
from __future__ import annotations

import ast
from datetime import date, datetime, time
from pathlib import Path

from app.apps.trading.providers.bar_semantics import equity_bar_times, equity_session_bounds
from app.apps.trading.strategy_outcome_quality import regular_session_reference
from app.apps.trading.us_equity_calendar import EASTERN, after_regular_close, regular_close_time

TRADING = Path(__file__).resolve().parents[2] / "app" / "apps" / "trading"
DAY_AFTER_THANKSGIVING = date(2026, 11, 27)
ORDINARY_DAY = date(2026, 11, 25)


def test_an_early_close_day_ends_at_13_00() -> None:
    assert regular_close_time(DAY_AFTER_THANKSGIVING) == time(13, 0)
    assert regular_close_time(ORDINARY_DAY) == time(16, 0)
    assert after_regular_close(datetime(2026, 11, 27, 13, 30, tzinfo=EASTERN))
    assert not after_regular_close(datetime(2026, 11, 25, 13, 30, tzinfo=EASTERN))


def test_an_ordinary_date_does_not_compute_the_holidays(monkeypatch) -> None:
    # Bar loops ask for the close of every bar; only the three possible
    # early-close dates may pay for the holiday calendar.
    def refuse(_year: int) -> set[date]:
        raise AssertionError("regular_holidays computed for an ordinary date")

    monkeypatch.setattr("app.apps.trading.us_equity_calendar.regular_holidays", refuse)
    assert regular_close_time(date(2026, 11, 10)) == time(16, 0)
    assert regular_close_time(date(2026, 6, 15)) == time(16, 0)


def test_bars_after_an_early_close_are_extended_hours() -> None:
    _start, end = equity_session_bounds(DAY_AFTER_THANKSGIVING, "America/New_York")
    assert end.astimezone(EASTERN).time() == time(13, 0)

    after = datetime(2026, 11, 27, 14, 0, tzinfo=EASTERN)
    assert equity_bar_times(after, "1m", "America/New_York")[2] == "extended_post"
    assert not regular_session_reference(after)
    ordinary = datetime(2026, 11, 25, 14, 0, tzinfo=EASTERN)
    assert equity_bar_times(ordinary, "1m", "America/New_York")[2] == "regular"


def test_no_trading_module_hard_codes_the_close() -> None:
    offenders = []
    for path in sorted(TRADING.rglob("*.py")):
        if path.name == "us_equity_calendar.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and getattr(node.func, "id", getattr(node.func, "attr", None)) == "time"
                and [getattr(arg, "value", None) for arg in node.args] in ([16, 0], [16])
            ):
                offenders.append(f"{path.relative_to(TRADING)}:{node.lineno}")
    assert offenders == [], "use us_equity_calendar.regular_close_time"
