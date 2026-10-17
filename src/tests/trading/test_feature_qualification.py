from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.apps.trading.feature_qualification import (
    FeatureRequirement,
    qualify_bar_feature,
)
from app.apps.trading.models import MarketBar


ET = ZoneInfo("America/New_York")
INSTRUMENT = "equity:NASDAQ:TEST"


def _bar(start_et: datetime, *, interval: str = "1m") -> MarketBar:
    minutes = int(interval[:-1])
    start = start_et.astimezone(timezone.utc)
    end = start + timedelta(minutes=minutes)
    return MarketBar(
        instrument_id=INSTRUMENT,
        interval=interval,
        start_time=start,
        end_time=end,
        open=Decimal("10"),
        high=Decimal("10.1"),
        low=Decimal("9.9"),
        close=Decimal("10"),
        volume=Decimal("1000"),
        provider="test",
        session="regular",
    )


def _series(start_et: datetime, count: int, *, interval: str = "1m"):
    step = timedelta(minutes=int(interval[:-1]))
    return [_bar(start_et + step * i, interval=interval) for i in range(count)]


def test_rolling_feature_heals_when_old_gap_leaves_declared_window():
    start = datetime(2026, 9, 17, 9, 30, tzinfo=ET)
    bars = _series(start, 150)
    # Remove 09:40. At noon it is outside a 20-minute rolling dependency.
    bars = [bar for bar in bars if bar.start_time != (start + timedelta(minutes=10)).astimezone(timezone.utc)]
    observed = datetime(2026, 9, 17, 12, 0, tzinfo=ET)
    requirement = FeatureRequirement(
        requirement_id="recent-breakout-v1",
        feature_name="recent_breakout",
        interval="1m",
        dependency_class="ROLLING_WINDOW",
        lookback_bars=20,
    )
    certificate = qualify_bar_feature(
        bars,
        requirement,
        instrument_id=INSTRUMENT,
        session_date=start.date(),
        observed_at=observed,
    )
    assert certificate.status == "VALID"
    assert certificate.unresolved_gaps == ()


def test_session_cumulative_feature_does_not_heal_after_old_gap():
    start = datetime(2026, 9, 17, 9, 30, tzinfo=ET)
    bars = _series(start, 150)
    missing = (start + timedelta(minutes=10)).astimezone(timezone.utc)
    bars = [bar for bar in bars if bar.start_time != missing]
    requirement = FeatureRequirement(
        requirement_id="session-vwap-v1",
        feature_name="session_vwap",
        interval="1m",
        dependency_class="SESSION_CUMULATIVE",
    )
    certificate = qualify_bar_feature(
        bars,
        requirement,
        instrument_id=INSTRUMENT,
        session_date=start.date(),
        observed_at=datetime(2026, 9, 17, 12, 0, tzinfo=ET),
    )
    assert certificate.status == "INVALID"
    assert missing in certificate.unresolved_gaps


def test_recursive_feature_requires_explicit_degraded_reseed_policy():
    start = datetime(2026, 9, 17, 9, 30, tzinfo=ET)
    bars = _series(start, 150)
    missing = (start + timedelta(minutes=5)).astimezone(timezone.utc)
    bars = [bar for bar in bars if bar.start_time != missing]
    requirement = FeatureRequirement(
        requirement_id="ema20-v1",
        feature_name="ema20",
        interval="1m",
        dependency_class="RECURSIVE",
        allow_approximate_reseed=True,
        reseed_after_clean_bars=30,
    )
    certificate = qualify_bar_feature(
        bars,
        requirement,
        instrument_id=INSTRUMENT,
        session_date=start.date(),
        observed_at=datetime(2026, 9, 17, 12, 0, tzinfo=ET),
    )
    assert certificate.status == "DEGRADED"
    assert certificate.exact is False
    assert "RECURSIVE_RESEEDED_APPROXIMATE" in certificate.reason_codes


def test_point_in_time_only_requires_a_fresh_observation():
    start = datetime(2026, 9, 17, 11, 58, tzinfo=ET)
    bars = _series(start, 2)
    requirement = FeatureRequirement(
        requirement_id="last-price-v1",
        feature_name="last_price",
        interval="1m",
        dependency_class="POINT_IN_TIME",
        max_staleness_seconds=90,
    )
    certificate = qualify_bar_feature(
        bars,
        requirement,
        instrument_id=INSTRUMENT,
        session_date=start.date(),
        observed_at=datetime(2026, 9, 17, 12, 0, 20, tzinfo=ET),
    )
    assert certificate.status == "VALID"


def _stoch_requirement(*, bridge: bool) -> FeatureRequirement:
    return FeatureRequirement(
        requirement_id="stoch-test",
        feature_name="stoch",
        interval="5m",
        dependency_class="RECURSIVE",
        allow_approximate_reseed=True,
        reseed_after_clean_bars=30,
        bridge_isolated_gaps=bridge,
    )


def _qualify_5m(missing_buckets, *, bridge: bool, count: int = 20):
    start = datetime(2026, 9, 17, 9, 30, tzinfo=ET)
    holes = {(start + timedelta(minutes=5 * index)).astimezone(timezone.utc) for index in missing_buckets}
    bars = [bar for bar in _series(start, count, interval="5m") if bar.start_time not in holes]
    return qualify_bar_feature(
        bars,
        _stoch_requirement(bridge=bridge),
        instrument_id=INSTRUMENT,
        session_date=start.date(),
        observed_at=start + timedelta(minutes=5 * count, seconds=5),
    )


def test_an_isolated_missing_bucket_is_bridged_only_when_the_requirement_opts_in():
    bridged = _qualify_5m([6, 12], bridge=True)
    assert bridged.status == "DEGRADED"
    assert bridged.exact is False
    assert bridged.reason_codes == ("RECURSIVE_ISOLATED_GAP_BRIDGED",)
    assert bridged.unresolved_gaps == ()

    # Without the opt-in, the same tape breaks the recursion as before.
    assert _qualify_5m([6, 12], bridge=False).status == "INVALID"


def test_two_consecutive_missing_buckets_still_break_the_recursion():
    certificate = _qualify_5m([6, 7], bridge=True)
    assert certificate.status == "INVALID"
    assert "RECURSIVE_SEED_CONTINUITY_BROKEN" in certificate.reason_codes


def test_a_missing_latest_bucket_is_not_bridged_before_the_next_bar_exists():
    # Nothing after it yet: it may be a gap that keeps going.
    certificate = _qualify_5m([19], bridge=True)
    assert "RECURSIVE_ISOLATED_GAP_BRIDGED" not in certificate.reason_codes
