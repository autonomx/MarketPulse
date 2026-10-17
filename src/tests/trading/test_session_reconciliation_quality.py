from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.apps.trading.models import AdjustmentMode, MarketBar
from app.apps.trading.prospective_prediction_evidence import SIPTradeEvent
from app.apps.trading.session_reconciliation_monitor import _complete_sip_5m_certificate


ET = ZoneInfo("America/New_York")
INSTRUMENT = "equity:NASDAQ:TEST"


def _bars(*, missing_index=None):
    start = datetime(2026, 9, 17, 9, 30, tzinfo=ET)
    rows = []
    for index in range(78):
        if index == missing_index:
            continue
        bar_start = (start + timedelta(minutes=5 * index)).astimezone(timezone.utc)
        rows.append(
            MarketBar(
                instrument_id=INSTRUMENT,
                interval="5m",
                start_time=bar_start,
                end_time=bar_start + timedelta(minutes=5),
                open=Decimal("10"),
                high=Decimal("10.1"),
                low=Decimal("9.9"),
                close=Decimal("10"),
                volume=Decimal("100"),
                is_final=True,
                adjustment_mode=AdjustmentMode.RAW,
                session="regular",
                provider="alpaca_sip",
            )
        )
    return rows


def _trade(at_et):
    return SIPTradeEvent(
        instrument_id=INSTRUMENT,
        price=Decimal("10"),
        event_timestamp=at_et.astimezone(timezone.utc),
        received_timestamp=datetime(2026, 9, 18, tzinfo=timezone.utc),
        sequence=1,
    )


def test_missing_sip_bar_with_trade_remains_unresolved():
    start = datetime(2026, 9, 17, 9, 30, tzinfo=ET)
    missing_index = 10
    missing_start = start + timedelta(minutes=5 * missing_index)
    cert = _complete_sip_5m_certificate(
        _bars(missing_index=missing_index),
        [_trade(missing_start + timedelta(minutes=1))],
        instrument_id=INSTRUMENT,
        session_date=date(2026, 9, 17),
    )
    assert cert.status == "INVALID"
    assert missing_start.astimezone(timezone.utc) in cert.unresolved_gaps


def test_missing_sip_bar_with_no_trades_is_confirmed_nontrading():
    cert = _complete_sip_5m_certificate(
        _bars(missing_index=10),
        [],
        instrument_id=INSTRUMENT,
        session_date=date(2026, 9, 17),
    )
    assert cert.status == "VALID"
    assert cert.unresolved_gaps == ()
    assert len(cert.confirmed_nontrading_ranges) == 1
