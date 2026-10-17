from __future__ import annotations

"""Shared causal-data readiness and final trade-authorization assessment."""

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, cast

from .gapper_dataset import GapperCandidate, GapperUniverseSnapshot
from .market_evidence import (
    BarCoverageAssessment,
    DEFAULT_MARKET_EVIDENCE_POLICY,
    MARKET_EVIDENCE_POLICY_VERSION,
    SessionEvaluabilityAssessment,
    TradeAuthorizationAssessment,
    premarket_evidence_feature_compatible,
)
from app.apps.trading.us_equity_calendar import EASTERN as _ET
from app.apps.trading.us_equity_calendar import regular_close_time


_REGULAR_OPEN = time(9, 30)
_MAX_LATEST_BAR_LATENCY_SECONDS = Decimal("90")


def finviz_membership_only_mode(config) -> bool:
    """True when Finviz rank membership, not enrichment, owns premarket authority."""

    return (
        getattr(config, "strategy_version", None) == "2.0.0"
        and getattr(config, "universe_discovery_source", None) == "finviz"
    )


def _minute_floor(value: datetime) -> datetime:
    return value.replace(second=0, microsecond=0)


def _expected_latest_start(observed_at: datetime, session_date: date) -> datetime | None:
    observed_et = observed_at.astimezone(_ET)
    opening = datetime.combine(session_date, _REGULAR_OPEN, tzinfo=_ET)
    if observed_et.date() != session_date or observed_et < opening + timedelta(minutes=1):
        return None
    floor = _minute_floor(observed_et)
    expected = floor - timedelta(minutes=1)
    close = datetime.combine(session_date, regular_close_time(session_date), tzinfo=_ET)
    return min(max(expected, opening), close - timedelta(minutes=1)).astimezone(timezone.utc)


def assess_bar_coverage(
    bars: list[Any] | tuple[Any, ...],
    *,
    session_date: date,
    observed_at: datetime,
    provider: str | None = None,
    fallback_provider: str | None = None,
) -> BarCoverageAssessment:
    """Prove opening coverage, contiguous finalized minutes and currentness."""

    finalized = sorted(
        [
            bar
            for bar in bars
            if getattr(bar, "is_final", False)
            and bar.start_time.astimezone(_ET).date() == session_date
            and _REGULAR_OPEN
            <= bar.start_time.astimezone(_ET).timetz().replace(tzinfo=None)
            < regular_close_time(session_date)
            and bar.end_time <= observed_at.astimezone(timezone.utc)
        ],
        key=lambda bar: bar.start_time,
    )
    expected_latest = _expected_latest_start(observed_at, session_date)
    if not finalized:
        reason = (
            "CURRENT_SESSION_NOT_STARTED"
            if expected_latest is None
            else "CURRENT_SESSION_1M_UNAVAILABLE"
        )
        return BarCoverageAssessment(
            provider=provider,
            fallback_provider=fallback_provider,
            expected_latest_bar=expected_latest,
            ready=False,
            reason_codes=(reason,),
        )

    instrument_id = str(getattr(finalized[0], "instrument_id", "") or "") or None
    starts = {bar.start_time.astimezone(timezone.utc) for bar in finalized}
    opening = datetime.combine(session_date, _REGULAR_OPEN, tzinfo=_ET).astimezone(timezone.utc)
    opening_present = opening in starts
    actual_latest = finalized[-1].start_time.astimezone(timezone.utc)
    latest_present = expected_latest is None or actual_latest >= expected_latest

    missing: list[datetime] = []
    if expected_latest is not None:
        cursor = opening
        while cursor <= expected_latest:
            if cursor not in starts:
                missing.append(cursor)
            cursor += timedelta(minutes=1)

    latency = max(
        Decimal("0"),
        Decimal(str((observed_at.astimezone(timezone.utc) - finalized[-1].end_time).total_seconds())),
    )
    reasons: list[str] = []
    if not opening_present:
        reasons.append("OPENING_1M_HISTORY_INCOMPLETE")
    if missing:
        reasons.append("CURRENT_SESSION_1M_GAPS")
    if not latest_present or latency > _MAX_LATEST_BAR_LATENCY_SECONDS:
        reasons.append("CURRENT_SESSION_1M_STALE")

    return BarCoverageAssessment(
        instrument_id=instrument_id,
        provider=provider or str(getattr(finalized[-1], "provider", "") or "") or None,
        fallback_provider=fallback_provider,
        coverage_start=finalized[0].start_time,
        coverage_end=finalized[-1].end_time,
        expected_latest_bar=expected_latest,
        actual_latest_bar=actual_latest,
        missing_minutes=tuple(missing),
        latency_seconds=latency,
        opening_bar_present=opening_present,
        latest_bar_present=latest_present,
        contiguous=not missing,
        ready=not reasons,
        reason_codes=tuple(reasons),
    )


def resolve_causal_equity_bars(
    market_service,
    candidate: GapperCandidate,
    *,
    session_date: date,
    observed_at: datetime,
    allow_shadow_fallback: bool,
    limit: int = 500,
) -> tuple[list[Any], BarCoverageAssessment, str | None]:
    """Resolve current-session causal bars through the shared recovery boundary."""

    recovery = getattr(market_service, "recovered_bars", None)
    if callable(recovery):
        try:
            recovered = recovery(
                candidate.instrument_id,
                "1m",
                limit,
                candidate.binding_id,
                session_date=session_date,
                as_of=observed_at,
            )
        except Exception as exc:
            primary = []
            primary_error: str | None = f"{type(exc).__name__}: {exc}"
        else:
            primary = list(recovered.bars)
            primary_error = recovered.report.primary_error
            assessment = assess_bar_coverage(
                primary,
                session_date=session_date,
                observed_at=observed_at,
                provider="shared_recovery",
                fallback_provider=(
                    recovered.report.fallback_provider
                    if recovered.report.fallback_attempted
                    else None
                ),
            )
            # Shared recovery has already exhausted the permitted Yahoo/local/
            # IEX ladder. Never issue a second hidden fallback request here.
            return primary, assessment, primary_error

    primary_error = None
    try:
        response = market_service.bars(
            candidate.instrument_id,
            "1m",
            limit,
            candidate.binding_id,
        )
        primary = list(response.bars)
    except Exception as exc:
        primary = []
        primary_error = f"{type(exc).__name__}: {exc}"

    primary_assessment = assess_bar_coverage(
        primary,
        session_date=session_date,
        observed_at=observed_at,
        provider="configured_history",
    )
    if primary_assessment.ready or not allow_shadow_fallback:
        return primary, primary_assessment, primary_error

    try:
        fallback = market_service.execution_indicator_bars(
            candidate.instrument_id,
            candidate.binding_id,
            as_of=observed_at,
        )
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        return primary, primary_assessment, primary_error or detail

    fallback_assessment = assess_bar_coverage(
        list(fallback),
        session_date=session_date,
        observed_at=observed_at,
        provider="alpaca_iex",
        fallback_provider="alpaca_iex",
    )
    if fallback_assessment.ready:
        return list(fallback), fallback_assessment, primary_error
    return primary, primary_assessment, primary_error


def source_member_valid(
    universe: GapperUniverseSnapshot,
    candidate: GapperCandidate,
) -> bool:
    dispositions = getattr(universe, "source_member_dispositions", ())
    if not dispositions:
        return candidate in universe.candidates
    return any(
        item.status == "materialized"
        and item.instrument_id == candidate.instrument_id
        for item in dispositions
    )


def candidate_morning_evidence_eligible(candidate: GapperCandidate, config) -> tuple[bool, tuple[str, ...]]:
    """Return premarket authority eligibility for the active strategy contract.

    Managed Finviz V2 intentionally treats the frozen ranked cohort as the only
    premarket authority. Liquidity, TOD-RVOL, gap/price enrichment and frozen
    spread remain research features. Optional catalyst/supply constraints are
    preserved when a profile explicitly enables them.
    """

    reasons: list[str] = []
    membership_only = finviz_membership_only_mode(config)

    if not membership_only:
        liquidity = getattr(candidate, "premarket_liquidity", None)
        if config.strategy_version == "2.0.0":
            if liquidity is None:
                reasons.append("PREMARKET_LIQUIDITY_EVIDENCE_MISSING")
            else:
                if not premarket_evidence_feature_compatible(liquidity):
                    reasons.append("PREMARKET_LIQUIDITY_POLICY_MISMATCH")
                if not liquidity.ready:
                    reasons.extend(liquidity.reason_codes)
                if (
                    liquidity.baseline_session_count
                    < DEFAULT_MARKET_EVIDENCE_POLICY.minimum_tod_rvol_baseline_sessions
                ):
                    reasons.append("TOD_RVOL_BASELINE_INSUFFICIENT")

        if candidate.gap_pct < config.minimum_gap_pct:
            reasons.append("GAP_BELOW_MINIMUM")
        if not config.minimum_price <= candidate.premarket_price <= config.maximum_price:
            reasons.append("PRICE_OUT_OF_RANGE")
        if candidate.premarket_dollar_volume < config.minimum_premarket_dollar_volume:
            reasons.append("PREMARKET_DOLLAR_VOLUME_LOW")
        if candidate.tod_rvol is None:
            reasons.append("TOD_RVOL_MISSING")
        elif candidate.tod_rvol < config.minimum_tod_rvol:
            reasons.append("TOD_RVOL_LOW")

    # Frozen spread is never entry authority. The live execution observation owns
    # the spread gate at the actual trade attempt.
    severe = tuple(
        flag for flag in candidate.dilution_flags if flag in set(config.reject_dilution_flags)
    )
    if config.require_catalyst_evidence and not candidate.catalyst_evidence_ids:
        reasons.append("CATALYST_EVIDENCE_REQUIRED")
    if severe:
        reasons.append("DILUTION_SUPPLY_RISK")
    if config.float_preference_mode == "require":
        if (
            candidate.float_shares is None
            or not config.preferred_float_min_shares
            <= candidate.float_shares
            <= config.preferred_float_max_shares
        ):
            reasons.append("FLOAT_OUTSIDE_REQUIRED_RANGE")

    return not reasons, tuple(dict.fromkeys(reasons))

def assess_session_evaluability(
    universe: GapperUniverseSnapshot,
    config,
) -> SessionEvaluabilityAssessment:
    dispositions = tuple(getattr(universe, "source_member_dispositions", ()))
    source_members = tuple(getattr(universe, "source_candidate_symbols", ()))
    accounted = len(dispositions)
    failures = [
        item for item in dispositions
        if item.status in {"enrichment_failed", "provider_unavailable"}
    ]
    evaluable = 0
    unevaluable = 0
    reasons: list[str] = []

    if source_members and (
        not dispositions
        or accounted != len(source_members)
        or [item.symbol for item in dispositions] != list(source_members)
    ):
        reasons.append("SOURCE_MEMBER_DISPOSITIONS_INCOMPLETE")
    if failures:
        reasons.append("SOURCE_MEMBER_DATA_FAILURE")

    for candidate in universe.candidates:
        eligible, candidate_reasons = candidate_morning_evidence_eligible(candidate, config)
        if eligible:
            evaluable += 1
        else:
            unevaluable += 1
            reasons.extend(candidate_reasons)

    if not source_members and not universe.candidates:
        status = "zero_candidate_scan"
    elif not universe.candidates and source_members:
        if dispositions and not failures and accounted == len(source_members):
            status = "zero_candidate_scan"
        else:
            status = "not_evaluable_data"
    elif reasons:
        status = "partial_data" if evaluable > 0 else "not_evaluable_data"
    else:
        status = "complete"

    qualification_eligible = status in {"complete", "completed_no_trigger"}
    if finviz_membership_only_mode(config):
        # Trading may proceed from a fully-accounted ranked cohort even when
        # premarket enrichment is incomplete. Promotion evidence remains stricter:
        # only sessions with every member materialized and clean research evidence count.
        blocking_source_failure = bool(failures) or "SOURCE_MEMBER_DISPOSITIONS_INCOMPLETE" in reasons
        if universe.candidates and not blocking_source_failure:
            status = "complete"
        qualification_eligible = bool(
            status in {"complete", "completed_no_trigger"}
            and not failures
            and len(universe.candidates) == len(source_members)
            and all(candidate.market_data_complete for candidate in universe.candidates)
        )
    return SessionEvaluabilityAssessment(
        status=cast(Any, status),
        source_member_count=len(source_members),
        accounted_source_member_count=accounted,
        materialized_candidate_count=len(universe.candidates),
        evaluable_candidate_count=evaluable,
        unevaluable_candidate_count=unevaluable,
        source_failure_count=len(failures),
        reason_codes=tuple(dict.fromkeys(reasons)),
        qualification_eligible=qualification_eligible,
    )


def build_trade_authorization(
    *,
    strategy_id: str,
    instrument_id: str,
    trade_attempt_id: str,
    universe_id: str,
    strategy_profile_fingerprint: str,
    source_member_valid_value: bool,
    morning_evidence_eligible: bool,
    session_evaluability_complete: bool,
    bar_coverage_ready: bool,
    strategy_entry_ready: bool,
    execution_observation_present: bool,
    execution_eligible: bool,
    risk_sizing_valid: bool,
    session_open: bool,
    provider_ready: bool,
    provider_circuit_clear: bool,
    strategy_kill_switch_clear: bool,
    qualification_authorized: bool,
    profile_matches: bool,
    evidence_policy_matches: bool,
    market_evidence_policy_version: str = MARKET_EVIDENCE_POLICY_VERSION,
) -> TradeAuthorizationAssessment:
    predicates = {
        "SOURCE_MEMBER_INVALID": source_member_valid_value,
        "MORNING_EVIDENCE_INELIGIBLE": morning_evidence_eligible,
        "SESSION_NOT_EVALUABLE": session_evaluability_complete,
        "BAR_COVERAGE_NOT_READY": bar_coverage_ready,
        "STRATEGY_NOT_ENTRY_READY": strategy_entry_ready,
        "EXECUTION_OBSERVATION_MISSING": execution_observation_present,
        "EXECUTION_INELIGIBLE": execution_eligible,
        "RISK_SIZING_INVALID": risk_sizing_valid,
        "SESSION_CLOSED": session_open,
        "PROVIDER_NOT_READY": provider_ready,
        "PROVIDER_CIRCUIT_OPEN": provider_circuit_clear,
        "STRATEGY_KILL_SWITCH": strategy_kill_switch_clear,
        "QUALIFICATION_NOT_AUTHORIZED": qualification_authorized,
        "STRATEGY_PROFILE_MISMATCH": profile_matches,
        "MARKET_EVIDENCE_POLICY_MISMATCH": evidence_policy_matches,
    }
    reasons = tuple(code for code, passed in predicates.items() if not passed)
    return TradeAuthorizationAssessment(
        strategy_id=strategy_id,
        instrument_id=instrument_id,
        trade_attempt_id=trade_attempt_id,
        universe_id=universe_id,
        strategy_profile_fingerprint=strategy_profile_fingerprint,
        market_evidence_policy_version=market_evidence_policy_version,
        source_member_valid=source_member_valid_value,
        morning_evidence_eligible=morning_evidence_eligible,
        session_evaluability_complete=session_evaluability_complete,
        bar_coverage_ready=bar_coverage_ready,
        strategy_entry_ready=strategy_entry_ready,
        execution_observation_present=execution_observation_present,
        execution_eligible=execution_eligible,
        risk_sizing_valid=risk_sizing_valid,
        session_open=session_open,
        provider_ready=provider_ready,
        provider_circuit_clear=provider_circuit_clear,
        strategy_kill_switch_clear=strategy_kill_switch_clear,
        qualification_authorized=qualification_authorized,
        profile_matches=profile_matches,
        evidence_policy_matches=evidence_policy_matches,
        reason_codes=reasons,
        authorized=not reasons,
    )


__all__ = [
    "assess_bar_coverage",
    "assess_session_evaluability",
    "build_trade_authorization",
    "candidate_morning_evidence_eligible",
    "finviz_membership_only_mode",
    "resolve_causal_equity_bars",
    "source_member_valid",
]
