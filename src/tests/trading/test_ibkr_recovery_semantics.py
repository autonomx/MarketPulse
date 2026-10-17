from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.apps.trading.market_data_recovery import (
    StrategyDataRequirement,
    assess_data_requirement,
    reconcile_recovery,
)
from app.apps.trading.models import MarketBar
from app.apps.trading.service import _coalesced_gap_ranges


ET = ZoneInfo("America/New_York")
INSTRUMENT = "equity:NASDAQ:AAPL"


def _bar(start_et: datetime, *, provider: str) -> MarketBar:
    start = start_et.astimezone(timezone.utc)
    return MarketBar(
        instrument_id=INSTRUMENT,
        interval="1m",
        start_time=start,
        end_time=start + timedelta(minutes=1),
        open=Decimal("10"),
        high=Decimal("10.1"),
        low=Decimal("9.9"),
        close=Decimal("10"),
        volume=Decimal("1000"),
        is_final=True,
        session="regular",
        provider=provider,
        provider_event_id=f"{provider}:{start.isoformat()}",
        received_at=start + timedelta(minutes=1),
    )


def _recovered():
    opening = datetime(2026, 9, 17, 9, 30, tzinfo=ET)
    primary = [
        _bar(opening, provider="yahoo"),
        _bar(opening + timedelta(minutes=1), provider="yahoo"),
        _bar(opening + timedelta(minutes=3), provider="yahoo"),
    ]
    fallback = [_bar(opening + timedelta(minutes=2), provider="ibkr")]
    recovered = reconcile_recovery(
        instrument_id=INSTRUMENT,
        interval="1m",
        session_date=opening.date(),
        as_of=opening + timedelta(minutes=4),
        primary_bars=primary,
        fallback_bars=fallback,
        primary_provider="yahoo",
        fallback_provider="ibkr",
        requested_binding="yahoo:historical_polling:" + INSTRUMENT,
        resolved_binding="ibkr:socket:" + INSTRUMENT,
        fallback_attempted=True,
    )
    return opening, recovered


def test_mixed_yahoo_ibkr_price_requires_explicit_feature_permission():
    opening, recovered = _recovered()
    requirement = StrategyDataRequirement(
        interval="1m",
        continuity="session",
        required_fields=("ohlc",),
        allow_mixed_provider_price=False,
    )

    assessment = assess_data_requirement(
        recovered.bars,
        session_date=opening.date(),
        as_of=opening + timedelta(minutes=4),
        requirement=requirement,
        recovery_report=recovered.report,
        bucket_evidence=recovered.bucket_evidence,
    )

    assert assessment.evaluable is False
    assert assessment.reason_codes == ("MIXED_PROVIDER_PRICE_NOT_AUTHORIZED",)


def test_price_only_feature_can_explicitly_accept_mixed_yahoo_ibkr_evidence():
    opening, recovered = _recovered()
    requirement = StrategyDataRequirement(
        interval="1m",
        continuity="session",
        required_fields=("ohlc",),
        allow_mixed_provider_price=True,
    )

    assessment = assess_data_requirement(
        recovered.bars,
        session_date=opening.date(),
        as_of=opening + timedelta(minutes=4),
        requirement=requirement,
        recovery_report=recovered.report,
        bucket_evidence=recovered.bucket_evidence,
    )

    assert assessment.evaluable is True
    assert assessment.status == "recovered_complete"


def test_volume_feature_rejects_mixed_provider_volume_by_default():
    opening, recovered = _recovered()
    requirement = StrategyDataRequirement(
        interval="1m",
        continuity="session",
        required_fields=("ohlc", "volume"),
        allow_mixed_provider_price=True,
    )

    assessment = assess_data_requirement(
        recovered.bars,
        session_date=opening.date(),
        as_of=opening + timedelta(minutes=4),
        requirement=requirement,
        recovery_report=recovered.report,
        bucket_evidence=recovered.bucket_evidence,
    )

    assert assessment.evaluable is False
    assert assessment.reason_codes == ("MIXED_PROVIDER_VOLUME_NOT_AUTHORIZED",)


def test_ibkr_volume_unknown_scope_remains_blocked_even_when_mixing_is_allowed():
    opening, recovered = _recovered()
    requirement = StrategyDataRequirement(
        interval="1m",
        continuity="session",
        required_fields=("ohlc", "volume"),
        allow_mixed_provider_price=True,
        allow_mixed_provider_volume=True,
    )

    assessment = assess_data_requirement(
        recovered.bars,
        session_date=opening.date(),
        as_of=opening + timedelta(minutes=4),
        requirement=requirement,
        recovery_report=recovered.report,
        bucket_evidence=recovered.bucket_evidence,
    )

    assert assessment.evaluable is False
    assert assessment.reason_codes == ("UNKNOWN_VOLUME_SCOPE_NOT_AUTHORIZED",)


def test_ibkr_gap_repair_coalesces_only_adjacent_ranges():
    start = datetime(2026, 9, 17, 9, 30, tzinfo=ET).astimezone(timezone.utc)
    ranges = _coalesced_gap_ranges(
        {
            start,
            start + timedelta(minutes=1),
            start + timedelta(minutes=4),
            start + timedelta(minutes=5),
        },
        timedelta(minutes=1),
    )

    assert ranges == [
        (start, start + timedelta(minutes=2)),
        (start + timedelta(minutes=4), start + timedelta(minutes=6)),
    ]
