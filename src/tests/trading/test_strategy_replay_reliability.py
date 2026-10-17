from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.apps.trading.providers.errors import (
    ProviderDataUnavailableError,
    ProviderRateLimitedError,
    ProviderUnavailableError,
)
from app.apps.trading.strategy_replay_reliability import (
    ReplayExpectedObservation,
    ReplayIncompleteError,
    ReplayObservationAvailability,
    assess_replay_completeness,
    classify_provider_failure,
    historical_replay_http_runtime,
    replay_observation_from_result,
    require_replay_valid_for_inference,
)


def _expected(*symbols: str, session: date = date(2026, 9, 11)):
    return tuple(
        ReplayExpectedObservation(session_date=session, instrument_id=symbol)
        for symbol in symbols
    )


def _row(
    symbol: str,
    *,
    arm: str = "leader-momentum-continuation",
    availability: str = "evaluated",
    session: date = date(2026, 9, 11),
) -> ReplayObservationAvailability:
    return ReplayObservationAvailability(
        arm=arm,
        session_date=session,
        instrument_id=symbol,
        availability=availability,
    )


def test_provider_failures_keep_rate_limit_distinct_from_missing_bars() -> None:
    assert classify_provider_failure(ProviderRateLimitedError("HTTP 429")) == "provider_rate_limited"
    assert classify_provider_failure(ProviderDataUnavailableError("no bars")) == "bars_unavailable"
    assert classify_provider_failure(ProviderUnavailableError("transport")) == "provider_unavailable"


def test_legacy_replay_reasons_are_normalized_without_erasing_provider_failure() -> None:
    throttled = replay_observation_from_result(
        arm="leader-momentum-continuation",
        session_date=date(2026, 8, 13),
        instrument_id="XHG",
        status="data_unavailable",
        reason="RuntimeError: XHG SIP 5m: Alpaca SIP rate limited the request",
    )
    missing = replay_observation_from_result(
        arm="leader-momentum-continuation",
        session_date=date(2026, 8, 24),
        instrument_id="XPON",
        status="data_unavailable",
        reason="LEADER_MOMENTUM_1M_REGULAR_BARS_UNAVAILABLE",
    )

    assert throttled.availability == "provider_rate_limited"
    assert missing.availability == "bars_unavailable"


def test_session_presence_cannot_hide_observation_level_incompleteness() -> None:
    first = date(2026, 9, 10)
    second = date(2026, 9, 11)
    expected = (
        *_expected("A", "B", session=first),
        *_expected("C", "D", session=second),
    )
    rows = (
        _row("A", session=first),
        _row("B", session=first, availability="provider_rate_limited"),
        _row("C", session=second),
        _row("D", session=second, availability="bars_unavailable"),
    )

    report = assess_replay_completeness(
        rows,
        expected,
        arms=("leader-momentum-continuation",),
    )
    arm = report.arms[0]

    assert arm.sessions_with_any_record_count == 2
    assert arm.fully_evaluable_session_count == 0
    assert arm.evaluated_observation_count == 2
    assert arm.evaluated_fraction == Decimal("0.5")
    assert arm.provider_rate_limited_count == 1
    assert arm.bars_unavailable_count == 1
    assert arm.valid_for_strategy_inference is False
    assert report.valid_for_strategy_inference is False
    assert "REPLAY_PROVIDER_RATE_LIMITED" in arm.reason_codes
    assert "REPLAY_BARS_UNAVAILABLE" in arm.reason_codes


def test_complete_replay_is_valid_for_strategy_inference() -> None:
    expected = _expected("A", "B", "C")
    rows = tuple(_row(item.instrument_id) for item in expected)

    report = assess_replay_completeness(
        rows,
        expected,
        arms=("leader-momentum-continuation",),
    )

    assert report.valid_for_strategy_inference is True
    assert report.reason_codes == ()
    assert report.arms[0].evaluated_observation_count == 3
    assert report.arms[0].fully_evaluable_session_count == 1


def test_missing_and_duplicate_observations_fail_closed() -> None:
    expected = _expected("A", "B")
    rows = (_row("A"), _row("A"))

    report = assess_replay_completeness(
        rows,
        expected,
        arms=("leader-momentum-continuation",),
    )
    arm = report.arms[0]

    assert arm.missing_observation_count == 1
    assert arm.duplicate_observation_count == 1
    assert "REPLAY_OBSERVATIONS_MISSING" in arm.reason_codes
    assert "REPLAY_OBSERVATIONS_DUPLICATED" in arm.reason_codes
    with pytest.raises(ReplayIncompleteError, match="replay_not_valid_for_strategy_inference"):
        require_replay_valid_for_inference(report)


def test_historical_replay_runtime_uses_conservative_retry_profile() -> None:
    runtime = historical_replay_http_runtime("test-history")

    assert runtime.max_concurrency == 2
    assert runtime.max_attempts == 6
    assert runtime.initial_backoff_seconds == 1.0
