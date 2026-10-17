from __future__ import annotations

"""Reliability contracts for historical strategy research replays.

Session-level provider coverage is not sufficient evidence for a benchmark: a
run can touch every session while silently losing most symbol observations to
rate limits or provider failures.  This module normalizes those failures,
reports observation-level completeness per arm, and provides a fail-closed gate
for strategy inference.

The module is research/replay infrastructure only.  It does not alter strategy
signals, qualification thresholds, risk, or execution authority.
"""

from datetime import date
from decimal import Decimal
from typing import Any, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field

from .providers.errors import (
    ProviderCancelledError,
    ProviderContractError,
    ProviderDataUnavailableError,
    ProviderRateLimitedError,
    ProviderUnavailableError,
)
from .providers.http_runtime import ProviderHttpRuntime


ReplayAvailability = Literal[
    "evaluated",
    "provider_rate_limited",
    "provider_unavailable",
    "bars_unavailable",
    "data_gap",
    "metadata_unavailable",
    "provider_contract_error",
    "provider_cancelled",
    "unknown_failure",
]


class ReplayExpectedObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_date: date
    instrument_id: str = Field(min_length=1)


class ReplayObservationAvailability(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    arm: str = Field(min_length=1)
    session_date: date
    instrument_id: str = Field(min_length=1)
    availability: ReplayAvailability
    source_status: str | None = None
    reason: str | None = None


class ReplayCompletenessPolicy(BaseModel):
    """Evidence policy for deciding whether a replay supports inference.

    Provider failures are always blocking.  The opt-in allowances exist for
    explicitly scoped research where genuine exchange/data absences are
    acceptable, but the default benchmark contract is intentionally 100%.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    minimum_evaluated_fraction: Decimal = Field(
        default=Decimal("1"), ge=0, le=1
    )
    allow_data_gaps: bool = False
    allow_bars_unavailable: bool = False
    allow_metadata_unavailable: bool = False


STRICT_REPLAY_COMPLETENESS_POLICY = ReplayCompletenessPolicy()


class ReplayArmCompleteness(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    arm: str
    expected_observation_count: int = Field(ge=0)
    recorded_observation_count: int = Field(ge=0)
    evaluated_observation_count: int = Field(ge=0)
    missing_observation_count: int = Field(ge=0)
    duplicate_observation_count: int = Field(ge=0)
    unexpected_observation_count: int = Field(ge=0)

    provider_rate_limited_count: int = Field(ge=0)
    provider_unavailable_count: int = Field(ge=0)
    bars_unavailable_count: int = Field(ge=0)
    data_gap_count: int = Field(ge=0)
    metadata_unavailable_count: int = Field(ge=0)
    provider_contract_error_count: int = Field(ge=0)
    provider_cancelled_count: int = Field(ge=0)
    unknown_failure_count: int = Field(ge=0)

    expected_session_count: int = Field(ge=0)
    sessions_with_any_record_count: int = Field(ge=0)
    fully_evaluable_session_count: int = Field(ge=0)
    evaluated_fraction: Decimal = Field(ge=0, le=1)
    valid_for_strategy_inference: bool
    reason_codes: tuple[str, ...] = ()


class ReplayCompletenessReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    arms: tuple[ReplayArmCompleteness, ...]
    valid_for_strategy_inference: bool
    reason_codes: tuple[str, ...] = ()


class ReplayIncompleteError(RuntimeError):
    """Raised when a benchmark is accidentally published from incomplete data."""


_FAILURE_FIELDS: dict[ReplayAvailability, str] = {
    "provider_rate_limited": "provider_rate_limited_count",
    "provider_unavailable": "provider_unavailable_count",
    "bars_unavailable": "bars_unavailable_count",
    "data_gap": "data_gap_count",
    "metadata_unavailable": "metadata_unavailable_count",
    "provider_contract_error": "provider_contract_error_count",
    "provider_cancelled": "provider_cancelled_count",
    "unknown_failure": "unknown_failure_count",
}


def historical_replay_http_runtime(
    provider_id: str,
    *,
    session: Any | None = None,
) -> ProviderHttpRuntime:
    """Return a conservative provider runtime for bulk historical research.

    The shared runtime already honors numeric Retry-After and retries 429/5xx.
    Historical jobs prefer lower concurrency and a longer bounded retry envelope
    than latency-sensitive live paths.  Callers may still supply cached bars and
    avoid provider traffic entirely.
    """

    return ProviderHttpRuntime(
        provider_id,
        session=session,
        max_concurrency=2,
        max_attempts=6,
        initial_backoff_seconds=1.0,
    )


def classify_provider_failure(error: BaseException) -> ReplayAvailability:
    """Normalize provider exceptions without confusing throttling with no data."""

    if isinstance(error, ProviderRateLimitedError):
        return "provider_rate_limited"
    if isinstance(error, ProviderDataUnavailableError):
        return "bars_unavailable"
    if isinstance(error, ProviderUnavailableError):
        return "provider_unavailable"
    if isinstance(error, ProviderContractError):
        return "provider_contract_error"
    if isinstance(error, ProviderCancelledError):
        return "provider_cancelled"
    return _classify_reason(f"{type(error).__name__}: {error}")


def _classify_reason(reason: str | None) -> ReplayAvailability:
    folded = str(reason or "").strip().lower()
    if not folded:
        return "unknown_failure"
    if "rate limit" in folded or "rate_limit" in folded or "http 429" in folded:
        return "provider_rate_limited"
    if "contract" in folded and ("provider" in folded or "payload" in folded):
        return "provider_contract_error"
    if "cancelled" in folded or "canceled" in folded:
        return "provider_cancelled"
    if (
        "transport failure" in folded
        or "provider unavailable" in folded
        or "connectionerror" in folded
        or "timeout" in folded
        or "http 500" in folded
        or "http 502" in folded
        or "http 503" in folded
        or "http 504" in folded
    ):
        return "provider_unavailable"
    if "metadata unavailable" in folded or "candidate metadata unavailable" in folded:
        return "metadata_unavailable"
    if (
        "bars_unavailable" in folded
        or "bars unavailable" in folded
        or "bars unavailable" in folded
        or "returned no regular-session" in folded
        or "returned no regular session" in folded
        or "1m_regular_bars_unavailable" in folded
        or "1m_bars_unavailable" in folded
    ):
        return "bars_unavailable"
    if "data_gap" in folded or "data gap" in folded:
        return "data_gap"
    return "unknown_failure"


def replay_observation_from_result(
    *,
    arm: str,
    session_date: date,
    instrument_id: str,
    status: str,
    reason: str | None = None,
) -> ReplayObservationAvailability:
    """Convert a legacy replay row into explicit availability evidence."""

    normalized_status = str(status or "").strip().lower()
    if normalized_status == "data_gap":
        availability: ReplayAvailability = "data_gap"
    elif normalized_status in {
        "data_unavailable",
        "unavailable",
        "provider_error",
        "error",
    }:
        availability = _classify_reason(reason)
    else:
        availability = "evaluated"
    return ReplayObservationAvailability(
        arm=arm,
        session_date=session_date,
        instrument_id=instrument_id,
        availability=availability,
        source_status=status or None,
        reason=reason,
    )


def _arm_completeness(
    arm: str,
    observations: Sequence[ReplayObservationAvailability],
    expected: Sequence[ReplayExpectedObservation],
    *,
    policy: ReplayCompletenessPolicy,
) -> ReplayArmCompleteness:
    expected_keys = {(item.session_date, item.instrument_id) for item in expected}
    expected_sessions = {item.session_date for item in expected}
    rows = [item for item in observations if item.arm == arm]

    by_key: dict[tuple[date, str], list[ReplayObservationAvailability]] = {}
    for row in rows:
        by_key.setdefault((row.session_date, row.instrument_id), []).append(row)

    missing = expected_keys - set(by_key)
    unexpected = set(by_key) - expected_keys
    duplicate_count = sum(max(0, len(values) - 1) for values in by_key.values())

    evaluated_keys = {
        key
        for key, values in by_key.items()
        if key in expected_keys and any(item.availability == "evaluated" for item in values)
    }
    counts = {field: 0 for field in _FAILURE_FIELDS.values()}
    for key, values in by_key.items():
        if key not in expected_keys:
            continue
        # One benchmark key is expected to produce one authoritative row.  If a
        # duplicate exists we still count its evidence but invalidate the run.
        for row in values:
            field = _FAILURE_FIELDS.get(row.availability)
            if field is not None:
                counts[field] += 1

    sessions_with_any = {
        session_date
        for session_date, _ in set(by_key) & expected_keys
    }
    fully_evaluable_sessions = 0
    for session_date in expected_sessions:
        session_keys = {key for key in expected_keys if key[0] == session_date}
        if session_keys and session_keys <= evaluated_keys:
            fully_evaluable_sessions += 1

    expected_count = len(expected_keys)
    evaluated_count = len(evaluated_keys)
    fraction = (
        Decimal(evaluated_count) / Decimal(expected_count)
        if expected_count
        else Decimal("1")
    )

    reasons: list[str] = []
    if missing:
        reasons.append("REPLAY_OBSERVATIONS_MISSING")
    if unexpected:
        reasons.append("REPLAY_OBSERVATIONS_UNEXPECTED")
    if duplicate_count:
        reasons.append("REPLAY_OBSERVATIONS_DUPLICATED")
    if counts["provider_rate_limited_count"]:
        reasons.append("REPLAY_PROVIDER_RATE_LIMITED")
    if counts["provider_unavailable_count"]:
        reasons.append("REPLAY_PROVIDER_UNAVAILABLE")
    if counts["provider_contract_error_count"]:
        reasons.append("REPLAY_PROVIDER_CONTRACT_ERROR")
    if counts["provider_cancelled_count"]:
        reasons.append("REPLAY_PROVIDER_CANCELLED")
    if counts["unknown_failure_count"]:
        reasons.append("REPLAY_UNKNOWN_DATA_FAILURE")
    if counts["data_gap_count"] and not policy.allow_data_gaps:
        reasons.append("REPLAY_DATA_GAPS")
    if counts["bars_unavailable_count"] and not policy.allow_bars_unavailable:
        reasons.append("REPLAY_BARS_UNAVAILABLE")
    if (
        counts["metadata_unavailable_count"]
        and not policy.allow_metadata_unavailable
    ):
        reasons.append("REPLAY_METADATA_UNAVAILABLE")
    if fraction < policy.minimum_evaluated_fraction:
        reasons.append("REPLAY_EVALUATED_FRACTION_BELOW_MINIMUM")

    return ReplayArmCompleteness(
        arm=arm,
        expected_observation_count=expected_count,
        recorded_observation_count=sum(
            len(values) for key, values in by_key.items() if key in expected_keys
        ),
        evaluated_observation_count=evaluated_count,
        missing_observation_count=len(missing),
        duplicate_observation_count=duplicate_count,
        unexpected_observation_count=len(unexpected),
        expected_session_count=len(expected_sessions),
        sessions_with_any_record_count=len(sessions_with_any),
        fully_evaluable_session_count=fully_evaluable_sessions,
        evaluated_fraction=fraction,
        valid_for_strategy_inference=not reasons,
        reason_codes=tuple(reasons),
        **counts,
    )


def assess_replay_completeness(
    observations: Sequence[ReplayObservationAvailability],
    expected: Sequence[ReplayExpectedObservation],
    *,
    arms: Sequence[str],
    policy: ReplayCompletenessPolicy = STRICT_REPLAY_COMPLETENESS_POLICY,
) -> ReplayCompletenessReport:
    """Assess a benchmark at observation granularity for every requested arm."""

    ordered_arms = tuple(dict.fromkeys(str(value) for value in arms if str(value)))
    reports = tuple(
        _arm_completeness(arm, observations, expected, policy=policy)
        for arm in ordered_arms
    )
    reasons = tuple(
        dict.fromkeys(
            reason
            for report in reports
            for reason in report.reason_codes
        )
    )
    valid = bool(reports) and all(
        report.valid_for_strategy_inference for report in reports
    )
    if not reports:
        reasons = ("REPLAY_ARMS_MISSING",)
        valid = False
    return ReplayCompletenessReport(
        arms=reports,
        valid_for_strategy_inference=valid,
        reason_codes=reasons,
    )


def require_replay_valid_for_inference(report: ReplayCompletenessReport) -> None:
    """Fail closed before publishing P/L or strategy conclusions."""

    if report.valid_for_strategy_inference:
        return
    details = "; ".join(
        f"{arm.arm}: {arm.evaluated_observation_count}/"
        f"{arm.expected_observation_count} evaluated"
        + (f" ({','.join(arm.reason_codes)})" if arm.reason_codes else "")
        for arm in report.arms
    )
    raise ReplayIncompleteError(
        "replay_not_valid_for_strategy_inference"
        + (f": {details}" if details else "")
    )


__all__ = [
    "ReplayArmCompleteness",
    "ReplayAvailability",
    "ReplayCompletenessPolicy",
    "ReplayCompletenessReport",
    "ReplayExpectedObservation",
    "ReplayIncompleteError",
    "ReplayObservationAvailability",
    "STRICT_REPLAY_COMPLETENESS_POLICY",
    "assess_replay_completeness",
    "classify_provider_failure",
    "historical_replay_http_runtime",
    "replay_observation_from_result",
    "require_replay_valid_for_inference",
]
