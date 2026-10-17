from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.apps.trading.api import create_trading_router
from app.apps.trading.catalog import INSTRUMENTS, default_binding, instrument_by_id
from app.apps.trading.models import BarsResponse, DatasetProvenance, MarketBar
from app.apps.trading.providers.aggregation import aggregated_dataset_fingerprint
from app.apps.trading.providers.clock_aggregation import aggregate_market_bars_clock
from app.apps.trading.providers.registry import ProviderRegistry
from app.apps.trading.us_equity_calendar import us_equity_session

UTC = timezone.utc
ET = ZoneInfo("America/New_York")


def bar(start: datetime, minutes: int = 1, *, session: str | None = None, index: int = 0, now: datetime | None = None) -> MarketBar:
    start = start.astimezone(UTC)
    end = start + timedelta(minutes=minutes)
    price = Decimal(100 + index % 17)
    return MarketBar(
        instrument_id="clock:test",
        interval=f"{minutes}m" if minutes < 1440 else "1d",
        start_time=start,
        end_time=end,
        open=price,
        high=price + 1,
        low=price - 1,
        close=price + Decimal("0.5"),
        volume=Decimal(1),
        is_final=True,
        session=session or "24x7",
        provider="test",
        provider_event_id=f"e{index}",
        received_at=now or end,
    )


def minute_bars(first: datetime, count: int, *, equity: bool = False, skip: set[int] = frozenset(), now: datetime | None = None) -> list[MarketBar]:
    bars = []
    for index in range(count):
        if index in skip:
            continue
        start = first.astimezone(UTC) + timedelta(minutes=index)
        session = us_equity_session(start) if equity else None
        bars.append(bar(start, session=session, index=index, now=now))
    return bars


def clock(bars: list[MarketBar], target: str, base: str = "1m", *, equity: bool = False, extended: bool = True, complete: bool = False) -> list[MarketBar]:
    return aggregate_market_bars_clock(
        bars,
        target_interval=target,
        base_interval=base,
        calendar="XNYS" if equity else "24x7",
        exchange_tz="America/New_York" if equity else "UTC",
        include_extended=extended,
        history_complete=complete,
    )


def edges(bars: list[MarketBar], zone=UTC) -> list[tuple[str, str]]:
    return [(item.start_time.astimezone(zone).strftime("%m-%d %H:%M"), item.end_time.astimezone(zone).strftime("%H:%M")) for item in bars]


def test_crypto_7m_buckets_run_from_utc_midnight_with_a_short_last_bucket_and_missing_minutes() -> None:
    first = datetime(2026, 10, 7, 23, 50, tzinfo=UTC)
    now = datetime(2026, 10, 8, 0, 18, 30, tzinfo=UTC)
    bars = minute_bars(first, 28, skip={18, 19}, now=now)  # 00:08 and 00:09 are missing
    result = clock(bars, "7m")
    # 23:48 bucket is partial (data starts 23:50) and dropped; 1,440 / 7 leaves 23:55-00:00.
    assert edges(result) == [
        ("10-07 23:55", "00:00"),
        ("10-08 00:00", "00:07"),
        ("10-08 00:07", "00:14"),
        ("10-08 00:14", "00:21"),
    ]
    assert result[2].volume == Decimal(5)  # two missing minutes
    assert [item.is_final for item in result] == [True, True, True, False]
    assert all(item.provider_event_id.startswith("aggregate-clock:1m:") for item in result)
    assert clock(bars, "7m", complete=True)[0].start_time == datetime(2026, 10, 7, 23, 48, tzinfo=UTC)


@pytest.mark.parametrize(("equity", "first"), [
    (False, datetime(2026, 10, 1, 0, 0, tzinfo=UTC)),
    (True, datetime(2026, 9, 28, 4, 0, tzinfo=ET)),
])
def test_bucket_edges_do_not_depend_on_how_much_history_is_fetched(equity: bool, first: datetime) -> None:
    full = minute_bars(first, 5_000 if not equity else 5 * 24 * 60, equity=equity)
    full = [item for item in full if not equity or item.session != "closed"][-5_000:]
    window = full[-500:]
    for target in ("7m", "13m", "1h", "3h"):
        long_result = {item.start_time: item for item in clock(full, target, equity=equity)}
        short_result = clock(window, target, equity=equity)
        assert short_result
        for item in short_result:
            assert long_result[item.start_time] == item


def _session_day(day: date, *, extended: bool) -> list[MarketBar]:
    bars = minute_bars(datetime(day.year, day.month, day.day, 4, 0, tzinfo=ET), 16 * 60, equity=True)
    return clock(bars, "1h", equity=True, extended=extended, complete=True)


@pytest.mark.parametrize(("before", "after", "utc_before", "utc_after"), [
    (date(2026, 3, 6), date(2026, 3, 9), "14:30", "13:30"),  # DST starts 2026-03-08
    (date(2026, 10, 30), date(2026, 11, 2), "13:30", "14:30"),  # DST ends 2026-11-01
])
def test_equity_buckets_follow_the_local_session_across_dst(before: date, after: date, utc_before: str, utc_after: str) -> None:
    for day, utc_open in ((before, utc_before), (after, utc_after)):
        regular = _session_day(day, extended=False)
        assert edges(regular, ET) == [
            (f"{day:%m-%d} 09:30", "10:30"), (f"{day:%m-%d} 10:30", "11:30"), (f"{day:%m-%d} 11:30", "12:30"),
            (f"{day:%m-%d} 12:30", "13:30"), (f"{day:%m-%d} 13:30", "14:30"), (f"{day:%m-%d} 14:30", "15:30"),
            (f"{day:%m-%d} 15:30", "16:00"),
        ]
        assert regular[0].start_time.astimezone(UTC).strftime("%H:%M") == utc_open
        assert {item.session for item in regular} == {"regular"}


def test_extended_hours_anchor_at_four_and_cut_buckets_at_session_boundaries() -> None:
    result = _session_day(date(2026, 10, 7), extended=True)
    by_session = {(item.start_time.astimezone(ET).strftime("%H:%M"), item.end_time.astimezone(ET).strftime("%H:%M")): item.session for item in result}
    assert by_session[("09:00", "09:30")] == "extended_pre"
    assert by_session[("09:30", "10:00")] == "regular"
    assert by_session[("15:00", "16:00")] == "regular"
    assert by_session[("16:00", "17:00")] == "extended_post"
    assert by_session[("19:00", "20:00")] == "extended_post"
    assert len(result) == 17  # 04:00-09:00 (5), 09:00-09:30, 09:30-16:00 (7), 16:00-20:00 (4)
    # Hiding extended hours keeps every regular minute.
    regular = [item for item in result if item.session == "regular"]
    assert sum(item.volume for item in regular) == sum(item.volume for item in _session_day(date(2026, 10, 7), extended=False))


def test_early_close_cuts_the_regular_session_and_ends_extended_trading_at_17() -> None:
    day = date(2026, 11, 27)
    regular = _session_day(day, extended=False)
    assert edges(regular, ET)[-1] == ("11-27 12:30", "13:00")
    extended = _session_day(day, extended=True)
    post = [item for item in extended if item.session == "extended_post"]
    assert edges(post, ET) == [("11-27 13:00", "14:00"), ("11-27 14:00", "15:00"), ("11-27 15:00", "16:00"), ("11-27 16:00", "17:00")]


def test_winter_post_market_crossing_utc_midnight_stays_on_its_session_date() -> None:
    first = datetime(2026, 12, 3, 18, 30, tzinfo=ET)  # 23:30 UTC
    bars = minute_bars(first, 12 * 60, equity=True)  # through 06:30 ET on 12-04
    result = clock(bars, "1h", equity=True, extended=True)
    late_post = next(item for item in result if item.start_time == datetime(2026, 12, 4, 0, 0, tzinfo=UTC))
    assert late_post.session == "extended_post"
    assert late_post.end_time == datetime(2026, 12, 4, 1, 0, tzinfo=UTC)
    assert late_post.volume == Decimal(60)
    next_pre = [item for item in result if item.session == "extended_pre"]
    assert next_pre[0].start_time.astimezone(ET) == datetime(2026, 12, 4, 4, 0, tzinfo=ET)


def _daily(day: date) -> MarketBar:
    open_at = datetime(day.year, day.month, day.day, 9, 30, tzinfo=ET)
    item = bar(open_at, 390, session="regular", index=day.toordinal())
    return item.model_copy(update={"interval": "1d"})


def test_weekly_and_monthly_from_daily_bars_are_calendar_periods_between_session_open_and_close() -> None:
    days = [date(2026, 8, 31) + timedelta(days=offset) for offset in range(70)]
    trading = [day for day in days if day.weekday() < 5 and day != date(2026, 9, 7)]  # Labor Day
    daily = [_daily(day) for day in trading]
    weekly = clock(daily, "1w", "1d", equity=True, complete=True)
    labor_week = next(item for item in weekly if item.start_time.astimezone(ET).date() == date(2026, 9, 8))
    assert labor_week.start_time.astimezone(ET).strftime("%a %H:%M") == "Tue 09:30"
    assert labor_week.end_time.astimezone(ET) == datetime(2026, 9, 11, 16, 0, tzinfo=ET)
    assert labor_week.volume == Decimal(4)
    monthly = clock(daily, "1mo", "1d", equity=True)
    # August is partial (only the 31st) and is dropped; November (from the 2nd) is the forming month.
    assert [item.start_time.astimezone(ET).date() for item in monthly] == [date(2026, 9, 1), date(2026, 10, 1), date(2026, 11, 2)]
    assert monthly[-1].end_time.astimezone(ET) == datetime(2026, 11, 30, 16, 0, tzinfo=ET)
    assert monthly[0].end_time.astimezone(ET) == datetime(2026, 9, 30, 16, 0, tzinfo=ET)
    november = clock([_daily(date(2026, 11, 2)), _daily(date(2026, 11, 30))], "1mo", "1d", equity=True)
    assert november[0].start_time.astimezone(ET) == datetime(2026, 11, 2, 9, 30, tzinfo=ET)


def test_equity_multi_day_buckets_count_trading_days_and_skip_holidays() -> None:
    days = [date(2026, 11, 16) + timedelta(days=offset) for offset in range(21)]
    holiday = date(2026, 11, 26)  # Thanksgiving
    trading = [day for day in days if day.weekday() < 5 and day != holiday]
    daily = [_daily(day) for day in trading]
    two_day = clock(daily, "2d", "1d", equity=True, complete=True)

    def trading_days(first: date, last: date) -> int:
        span = [first + timedelta(days=offset) for offset in range((last - first).days + 1)]
        return sum(1 for day in span if day.weekday() < 5 and day != holiday)

    spans = [(item.start_time.astimezone(ET).date(), item.end_time.astimezone(ET).date()) for item in two_day]
    # Every bucket spans exactly two trading days, across weekends and the holiday.
    assert all(trading_days(first, last) == 2 for first, last in spans)
    assert spans[0] == (date(2026, 11, 13), date(2026, 11, 16))  # Fri + Mon, though only Mon is loaded
    assert (date(2026, 11, 25), date(2026, 11, 27)) in spans  # Wed + Fri around Thanksgiving
    # Edges don't depend on which day the loaded window starts on.
    later = clock(daily[3:], "2d", "1d", equity=True, complete=True)
    assert {(item.start_time, item.end_time) for item in later} <= {(item.start_time, item.end_time) for item in two_day}


def test_a_bucket_is_not_final_while_its_last_base_bar_is_still_forming() -> None:
    first = datetime(2026, 10, 7, 0, 0, tzinfo=UTC)
    bars = minute_bars(first, 7, now=first + timedelta(hours=1))
    bars[-1] = bars[-1].model_copy(update={"is_final": False})
    assert clock(bars, "7m", complete=True)[0].is_final is False
    bars[-1] = bars[-1].model_copy(update={"is_final": True})
    assert clock(bars, "7m", complete=True)[0].is_final is True


def test_crypto_days_weeks_and_months_use_fixed_epochs() -> None:
    daily = [bar(datetime(2026, 10, 1, tzinfo=UTC) + timedelta(days=offset), 1440, index=offset) for offset in range(30)]
    two_day = clock(daily, "2d", "1d", complete=True)
    assert all((item.start_time.date() - date(1970, 1, 1)).days % 2 == 0 for item in two_day)
    weeks = clock(daily, "2w", "1d", complete=True)
    assert all(item.start_time.weekday() == 0 and (item.start_time.date() - date(1970, 1, 5)).days % 14 == 0 for item in weeks)
    quarters = clock(daily, "3mo", "1d", complete=True)
    assert [item.start_time for item in quarters] == [datetime(2026, 10, 1, tzinfo=UTC)]
    assert quarters[0].end_time == datetime(2027, 1, 1, tzinfo=UTC)
    assert quarters[0].is_final is False


def test_equity_intervals_longer_than_a_day_must_be_whole_days() -> None:
    with pytest.raises(ValueError):
        clock(minute_bars(datetime(2026, 10, 7, 9, 30, tzinfo=ET), 10, equity=True), "36h", equity=True)


def _fixture_registry(calls: list[tuple[str, int]]):
    instrument_id = INSTRUMENTS[0].instrument_id
    binding = default_binding(instrument_id)
    instrument = instrument_by_id(instrument_id)
    assert binding is not None and instrument is not None

    class FixtureProvider:
        provider_id = "binance"

        def get_bars(self, requested_id: str, interval: str, limit: int = 500) -> BarsResponse:
            calls.append((interval, limit))
            base = minute_bars(datetime(2026, 10, 8, 0, 0, tzinfo=UTC), 30)
            now = datetime(2026, 10, 8, 0, 30, tzinfo=UTC)
            return BarsResponse(
                instrument=instrument,
                binding=binding,
                provenance=DatasetProvenance(
                    instrument_id=requested_id,
                    requested_binding=binding.binding_id,
                    resolved_binding=binding.binding_id,
                    dataset_fingerprint="fixture-base",
                    freshness_mode="polled",
                    as_of=now,
                    received_at=now,
                ),
                interval=interval,
                bars=[item.model_copy(update={"instrument_id": requested_id}) for item in base],
            )

    return instrument_id, binding, ProviderRegistry(factories={"binance": lambda: FixtureProvider()})


def test_registry_clock_mode_is_opt_in_and_changes_only_clock_fingerprints() -> None:
    calls: list[tuple[str, int]] = []
    instrument_id, binding, registry = _fixture_registry(calls)
    count = registry.bars(instrument_id, "7m", 4, binding.binding_id)
    assert count.provenance.dataset_fingerprint == aggregated_dataset_fingerprint(
        "fixture-base", target_interval="7m", base_interval="1m", factor=7,
    )
    assert count.bars[0].provider_event_id.startswith("aggregate:1m:")
    clocked = registry.bars(instrument_id, "7m", 4, binding.binding_id, alignment="clock")
    assert clocked.provenance.dataset_fingerprint != count.provenance.dataset_fingerprint
    assert [item.start_time.strftime("%H:%M") for item in clocked.bars] == ["00:00", "00:07", "00:14", "00:21", "00:28"]
    assert clocked.bars[-1].is_final is False
    assert calls == [("1m", 34), ("1m", 34)]


def test_bars_api_requests_clock_alignment_only_when_asked() -> None:
    seen: list[dict] = []
    class Service:
        def bars(self, *args, **kwargs):
            seen.append({"args": args, "kwargs": kwargs})
            raise ValueError("stop")

    app = FastAPI()
    app.include_router(create_trading_router(market_service_factory=lambda: Service()))
    client = TestClient(app)
    client.get("/api/trading/bars", params={"instrument_id": "crypto:BINANCE:spot:BTC-USDT", "interval": "7m"})
    client.get("/api/trading/bars", params={"instrument_id": "crypto:BINANCE:spot:BTC-USDT", "interval": "7m", "alignment": "clock", "extended_hours": "false"})
    assert seen[0]["kwargs"] == {}
    assert seen[1]["kwargs"] == {"alignment": "clock", "include_extended_hours": False}
