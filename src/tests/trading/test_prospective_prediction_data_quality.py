from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.apps.trading.models import AdjustmentMode, MarketBar
from app.apps.trading.prospective_prediction_data_quality import cross_check_analysis_prices
from app.apps.trading.prospective_prediction_evidence import SIPTradeEvent, select_analysis_session_prices

UTC = timezone.utc
SESSION = date(2026, 9, 16)
OPEN = datetime(2026, 9, 16, 13, 30, tzinfo=UTC)
CLOSE = datetime(2026, 9, 16, 20, 0, tzinfo=UTC)
INSTRUMENT = "equity:NASDAQ:MEDS"


def _prices():
    return select_analysis_session_prices(
        [
            SIPTradeEvent(
                instrument_id=INSTRUMENT,
                price=Decimal("4.035"),
                event_timestamp=OPEN,
                received_timestamp=OPEN + timedelta(milliseconds=10),
                provider_event_id="open",
                sequence=1,
            ),
            SIPTradeEvent(
                instrument_id=INSTRUMENT,
                price=Decimal("6.06"),
                event_timestamp=CLOSE - timedelta(milliseconds=1),
                received_timestamp=CLOSE + timedelta(milliseconds=10),
                provider_event_id="close",
                sequence=2,
            ),
        ],
        session_date=SESSION,
        outcome_state="FINAL",
    )


def _bar(start, end, open_price, high, low, close, *, interval="1m", adjustment=AdjustmentMode.RAW):
    return MarketBar(
        instrument_id=INSTRUMENT,
        interval=interval,
        start_time=start,
        end_time=end,
        open=Decimal(open_price),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal("1000"),
        adjustment_mode=adjustment,
        session="regular",
        provider="alpaca_sip",
        received_at=end + timedelta(seconds=1),
    )


def test_adjusted_daily_ohlc_cannot_override_raw_sip_outcome() -> None:
    prices = _prices()
    raw = [
        _bar(OPEN, OPEN + timedelta(minutes=1), "4.035", "4.47", "4.00", "4.31"),
        _bar(CLOSE - timedelta(minutes=1), CLOSE, "6.36", "6.40", "5.94", "6.06"),
    ]
    # Mirrors the type of bad aggregate that previously mis-scored MEDS.
    daily = _bar(
        OPEN,
        CLOSE,
        "11.60",
        "12.50",
        "10.80",
        "11.23",
        interval="1d",
        adjustment=AdjustmentMode.SPLIT,
    )

    result = cross_check_analysis_prices(prices, intraday_bars=raw, daily_bar=daily)

    assert result.authoritative_source == "consolidated_sip_trade_events"
    assert result.first_raw_bar_open == Decimal("4.035")
    assert result.last_raw_bar_close == Decimal("6.06")
    assert result.session_boundary_complete is True
    assert result.data_conflict is True
    assert "DAILY_BAR_ADJUSTED_NON_AUTHORITATIVE" in result.flags
    assert "DAILY_OPEN_MISMATCH" in result.flags
    assert "DAILY_CLOSE_MISMATCH" in result.flags


def test_intraday_boundary_mismatch_is_data_conflict() -> None:
    prices = _prices()
    raw = [
        _bar(OPEN, OPEN + timedelta(minutes=1), "11.60", "11.70", "11.50", "11.55"),
        _bar(CLOSE - timedelta(minutes=1), CLOSE, "11.30", "11.35", "11.20", "11.23"),
    ]

    result = cross_check_analysis_prices(prices, intraday_bars=raw)

    assert result.data_conflict is True
    assert "INTRADAY_OPEN_MISMATCH" in result.flags
    assert "INTRADAY_CLOSE_MISMATCH" in result.flags
