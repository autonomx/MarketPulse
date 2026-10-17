from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

"""Frozen prospective economic-SHADOW qualification policy.

The historical V3-V8 recovery studies showed that a descriptive recovery label can
be accurate while being economically exhausted by entry time. This module makes
the prospective research target explicit: +1R before -1R within 60 full finalized
one-minute bars using captured execution evidence and a structural stop known at
signal time.

Everything here is evidence/promotion policy only. It has no paper-order or broker
dependency and cannot authorize execution by itself.
"""

import hashlib
import json
import math
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field

from .strategy_deep_recovery import DEEP_RECOVERY_RULE_VERSION, DEEP_RECOVERY_SETUP_ID
from .strategy_repository import StrategyEvent, TradingStrategyConfigDocument
from .strategy_v2_qualification import v2_profile_fingerprint
from app.apps.trading.us_equity_calendar import EASTERN as _ET


PROSPECTIVE_ECONOMIC_VERSION = "prospective-economic-shadow-v1"
PROSPECTIVE_ECONOMIC_START = date(2026, 8, 24)
PROSPECTIVE_ECONOMIC_HORIZON_MINUTES = 60
PROSPECTIVE_ECONOMIC_TARGET_R = Decimal("1")
PROSPECTIVE_ECONOMIC_STOP_R = Decimal("-1")

PROSPECTIVE_ECONOMIC_MIN_MATCHED_OUTCOMES = 30
PROSPECTIVE_ECONOMIC_MIN_DISTINCT_SESSIONS = 20
PROSPECTIVE_ECONOMIC_MIN_DISTINCT_SYMBOLS = 15
PROSPECTIVE_ECONOMIC_MIN_EXECUTION_MATCH_RATE = Decimal("0.90")
PROSPECTIVE_ECONOMIC_MIN_WIN_RATE = Decimal("0.65")
PROSPECTIVE_ECONOMIC_MIN_EXPECTANCY_R = Decimal("0.20")
PROSPECTIVE_ECONOMIC_MAX_DRAWDOWN_R = Decimal("5")
PROSPECTIVE_ECONOMIC_ONE_SIDED_90_Z = Decimal("1.2815515655446004")

PROSPECTIVE_ECONOMIC_HOLDOUT_START = date(2026, 3, 31)
PROSPECTIVE_ECONOMIC_HOLDOUT_END = date(2026, 4, 28)
PROSPECTIVE_ECONOMIC_HOLDOUT_MIN_TRADES = 5
PROSPECTIVE_ECONOMIC_HOLDOUT_ROBUST_MIN_WIN_RATE = Decimal("0.60")
PROSPECTIVE_ECONOMIC_HOLDOUT_ROBUST_MIN_EXPECTANCY_R = Decimal("0")
PROSPECTIVE_ECONOMIC_HOLDOUT_ROBUST_MAX_DRAWDOWN_R = Decimal("5")
PROSPECTIVE_ECONOMIC_HOLDOUT_GOLD_MIN_WIN_RATE = Decimal("0.75")
PROSPECTIVE_ECONOMIC_HOLDOUT_GOLD_MIN_EXPECTANCY_R = Decimal("0.20")
PROSPECTIVE_ECONOMIC_HOLDOUT_GOLD_MAX_DRAWDOWN_R = Decimal("3")

PROSPECTIVE_ECONOMIC_SOAK_MIN_MATCHED_OUTCOMES = 10
PROSPECTIVE_ECONOMIC_SOAK_MIN_DISTINCT_SESSIONS = 8
PROSPECTIVE_ECONOMIC_SOAK_MIN_DISTINCT_SYMBOLS = 8
PROSPECTIVE_ECONOMIC_SOAK_MIN_EXECUTION_MATCH_RATE = Decimal("0.90")
PROSPECTIVE_ECONOMIC_SOAK_MIN_WIN_RATE = Decimal("0.55")
PROSPECTIVE_ECONOMIC_SOAK_MIN_EXPECTANCY_R = Decimal("0")
PROSPECTIVE_ECONOMIC_SOAK_MAX_DRAWDOWN_R = Decimal("5")

PROSPECTIVE_ECONOMIC_EVENT_TYPES = (
    "prospective_economic_candidate",
    "prospective_economic_signal",
    "prospective_economic_outcome",
    "prospective_economic_evaluation",
    "prospective_economic_holdout_review",
    "prospective_economic_auto_paper_review",
)


class ProspectiveEconomicThresholds(BaseModel):
    model_config = ConfigDict(frozen=True)

    prospective_start: date = PROSPECTIVE_ECONOMIC_START
    horizon_minutes: int = PROSPECTIVE_ECONOMIC_HORIZON_MINUTES
    minimum_matched_outcomes: int = PROSPECTIVE_ECONOMIC_MIN_MATCHED_OUTCOMES
    minimum_distinct_sessions: int = PROSPECTIVE_ECONOMIC_MIN_DISTINCT_SESSIONS
    minimum_distinct_symbols: int = PROSPECTIVE_ECONOMIC_MIN_DISTINCT_SYMBOLS
    minimum_execution_match_rate: Decimal = PROSPECTIVE_ECONOMIC_MIN_EXECUTION_MATCH_RATE
    minimum_win_rate: Decimal = PROSPECTIVE_ECONOMIC_MIN_WIN_RATE
    minimum_expectancy_r: Decimal = PROSPECTIVE_ECONOMIC_MIN_EXPECTANCY_R
    one_sided_confidence_level: Decimal = Decimal("0.90")
    maximum_drawdown_r: Decimal = PROSPECTIVE_ECONOMIC_MAX_DRAWDOWN_R
    holdout_start: date = PROSPECTIVE_ECONOMIC_HOLDOUT_START
    holdout_end: date = PROSPECTIVE_ECONOMIC_HOLDOUT_END
    soak_minimum_matched_outcomes: int = PROSPECTIVE_ECONOMIC_SOAK_MIN_MATCHED_OUTCOMES
    soak_minimum_distinct_sessions: int = PROSPECTIVE_ECONOMIC_SOAK_MIN_DISTINCT_SESSIONS
    soak_minimum_distinct_symbols: int = PROSPECTIVE_ECONOMIC_SOAK_MIN_DISTINCT_SYMBOLS


class ProspectiveEconomicMetrics(BaseModel):
    model_config = ConfigDict(frozen=True)

    signal_count: int = 0
    matched_signal_count: int = 0
    matched_outcome_count: int = 0
    distinct_sessions: int = 0
    distinct_symbols: int = 0
    execution_match_rate: Decimal | None = None
    win_count: int = 0
    win_rate: Decimal | None = None
    expectancy_r: Decimal | None = None
    one_sided_90_lcb_r: Decimal | None = None
    max_drawdown_r: Decimal | None = None


class ProspectiveEconomicStatus(BaseModel):
    model_config = ConfigDict(frozen=True)

    strategy_id: str
    policy_version: str = PROSPECTIVE_ECONOMIC_VERSION
    profile_fingerprint: str
    thresholds: ProspectiveEconomicThresholds = Field(default_factory=ProspectiveEconomicThresholds)
    metrics: ProspectiveEconomicMetrics
    evidence_fingerprint: str
    sample_ready: bool = False
    quantitative_pass: bool = False
    evaluation_recorded: bool = False
    evaluation_passed: bool = False
    evaluation_event_id: str | None = None
    sealed_holdout_unlocked: bool = False
    holdout_reviewed: bool = False
    holdout_verdict: Literal["UNOPENED", "UNDERPOWERED", "FAIL", "ROBUST", "GOLD"] = "UNOPENED"
    holdout_event_id: str | None = None
    soak_metrics: ProspectiveEconomicMetrics = Field(default_factory=ProspectiveEconomicMetrics)
    soak_passed: bool = False
    auto_paper_reviewed: bool = False
    auto_paper_research_authorized: bool = False
    pipeline_evidence_fingerprint: str
    reason_codes: tuple[str, ...] = ()


def _decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        logger.debug("suppressed error in %s", "_decimal", exc_info=True)
        return None


def _coalesce_decimal(value: Decimal | None, default: Decimal) -> Decimal:
    return default if value is None else value


def _sample_stdev(values: list[Decimal]) -> Decimal | None:
    if len(values) < 2:
        return None
    mean = sum(values, Decimal("0")) / Decimal(len(values))
    variance = sum(((value - mean) ** 2 for value in values), Decimal("0")) / Decimal(len(values) - 1)
    return Decimal(str(math.sqrt(float(variance))))


def _one_sided_90_lcb(values: list[Decimal]) -> Decimal | None:
    stdev = _sample_stdev(values)
    if stdev is None:
        return None
    mean = sum(values, Decimal("0")) / Decimal(len(values))
    standard_error = stdev / Decimal(str(math.sqrt(len(values))))
    return mean - PROSPECTIVE_ECONOMIC_ONE_SIDED_90_Z * standard_error


def _max_drawdown_r(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    equity = Decimal("0")
    peak = Decimal("0")
    maximum = Decimal("0")
    for value in values:
        equity += value
        peak = max(peak, equity)
        maximum = max(maximum, peak - equity)
    return maximum


def _session_date(event: StrategyEvent) -> date:
    raw = event.payload.get("session_date")
    if raw:
        try:
            return date.fromisoformat(str(raw))
        except ValueError:
            pass
    return event.observed_at.astimezone(_ET).date()


def prospective_economic_profile_fingerprint(config: TradingStrategyConfigDocument) -> str:
    payload = {
        "policy_version": PROSPECTIVE_ECONOMIC_VERSION,
        "prospective_start": PROSPECTIVE_ECONOMIC_START.isoformat(),
        "source_setup_id": DEEP_RECOVERY_SETUP_ID,
        "source_rule_version": DEEP_RECOVERY_RULE_VERSION,
        "v2_profile_fingerprint": v2_profile_fingerprint(config.config),
        "economic_label": "+1R before -1R within 60 full finalized 1m bars beginning at/after signal; stop wins intrabar ties",
        "mark_to_market": "if neither boundary is hit, close of bar 60 R clipped to [-1,+1]",
        "evidence_completion_semantics": (
            "execution_match_rate = completed economic outcomes / all frozen signals; "
            "execution-ineligible, incomplete and still-pending signals remain in the denominator"
        ),
        "thresholds": ProspectiveEconomicThresholds().model_dump(mode="json"),
        "holdout_policy": {
            "minimum_trades": PROSPECTIVE_ECONOMIC_HOLDOUT_MIN_TRADES,
            "robust_minimum_win_rate": str(PROSPECTIVE_ECONOMIC_HOLDOUT_ROBUST_MIN_WIN_RATE),
            "robust_minimum_expectancy_r": str(PROSPECTIVE_ECONOMIC_HOLDOUT_ROBUST_MIN_EXPECTANCY_R),
            "robust_maximum_drawdown_r": str(PROSPECTIVE_ECONOMIC_HOLDOUT_ROBUST_MAX_DRAWDOWN_R),
            "gold_minimum_win_rate": str(PROSPECTIVE_ECONOMIC_HOLDOUT_GOLD_MIN_WIN_RATE),
            "gold_minimum_expectancy_r": str(PROSPECTIVE_ECONOMIC_HOLDOUT_GOLD_MIN_EXPECTANCY_R),
            "gold_maximum_drawdown_r": str(PROSPECTIVE_ECONOMIC_HOLDOUT_GOLD_MAX_DRAWDOWN_R),
        },
        "soak_policy": {
            "minimum_matched_outcomes": PROSPECTIVE_ECONOMIC_SOAK_MIN_MATCHED_OUTCOMES,
            "minimum_distinct_sessions": PROSPECTIVE_ECONOMIC_SOAK_MIN_DISTINCT_SESSIONS,
            "minimum_distinct_symbols": PROSPECTIVE_ECONOMIC_SOAK_MIN_DISTINCT_SYMBOLS,
            "minimum_execution_match_rate": str(PROSPECTIVE_ECONOMIC_SOAK_MIN_EXECUTION_MATCH_RATE),
            "minimum_win_rate": str(PROSPECTIVE_ECONOMIC_SOAK_MIN_WIN_RATE),
            "minimum_expectancy_r": str(PROSPECTIVE_ECONOMIC_SOAK_MIN_EXPECTANCY_R),
            "maximum_drawdown_r": str(PROSPECTIVE_ECONOMIC_SOAK_MAX_DRAWDOWN_R),
        },
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _matching_events(events: Iterable[StrategyEvent], profile_fingerprint: str) -> list[StrategyEvent]:
    return sorted(
        [
            event for event in events
            if event.event_type in PROSPECTIVE_ECONOMIC_EVENT_TYPES
            and event.observed_at.astimezone(timezone.utc).date() >= PROSPECTIVE_ECONOMIC_START
            and event.payload.get("policy_version") == PROSPECTIVE_ECONOMIC_VERSION
            and event.payload.get("profile_fingerprint") == profile_fingerprint
        ],
        key=lambda event: (event.observed_at, event.event_id),
    )


def _metrics(events: list[StrategyEvent], *, after: datetime | None = None) -> ProspectiveEconomicMetrics:
    signals = [
        event for event in events
        if event.event_type == "prospective_economic_signal"
        and (after is None or event.observed_at > after)
    ]
    matched_signals = [
        event for event in signals
        if event.payload.get("execution_eligible") is True
        and _decimal(event.payload.get("entry_price")) is not None
        and (_decimal(event.payload.get("risk_per_share")) or Decimal("0")) > 0
    ]
    matched_ids = {event.event_id for event in matched_signals}
    outcomes = [
        event for event in events
        if event.event_type == "prospective_economic_outcome"
        and event.payload.get("signal_event_id") in matched_ids
        and event.payload.get("data_complete") is True
        and _decimal(event.payload.get("r_result_60m")) is not None
        and (after is None or event.observed_at > after)
    ]
    values = [_decimal(event.payload.get("r_result_60m")) for event in outcomes]
    r_values = [value for value in values if value is not None]
    wins = [event for event in outcomes if event.payload.get("one_r_before_minus_one_r") is True]
    sessions = {_session_date(event) for event in outcomes}
    symbols = {event.instrument_id for event in outcomes}
    return ProspectiveEconomicMetrics(
        signal_count=len(signals),
        matched_signal_count=len(matched_signals),
        matched_outcome_count=len(outcomes),
        distinct_sessions=len(sessions),
        distinct_symbols=len(symbols),
        # This is intentionally end-to-end evidence completion, not merely quote
        # eligibility. Missing/incomplete/pending outcomes therefore cannot be
        # censored out of the denominator and make the prospective sample look
        # cleaner than the actual frozen signal stream.
        execution_match_rate=(
            Decimal(len(outcomes)) / Decimal(len(signals)) if signals else None
        ),
        win_count=len(wins),
        win_rate=Decimal(len(wins)) / Decimal(len(outcomes)) if outcomes else None,
        expectancy_r=(sum(r_values, Decimal("0")) / Decimal(len(r_values))) if r_values else None,
        one_sided_90_lcb_r=_one_sided_90_lcb(r_values),
        max_drawdown_r=_max_drawdown_r(r_values),
    )


def _sample_ready(metrics: ProspectiveEconomicMetrics) -> bool:
    return (
        metrics.matched_outcome_count >= PROSPECTIVE_ECONOMIC_MIN_MATCHED_OUTCOMES
        and metrics.distinct_sessions >= PROSPECTIVE_ECONOMIC_MIN_DISTINCT_SESSIONS
        and metrics.distinct_symbols >= PROSPECTIVE_ECONOMIC_MIN_DISTINCT_SYMBOLS
    )


def _quantitative_pass(metrics: ProspectiveEconomicMetrics) -> bool:
    return (
        _sample_ready(metrics)
        and _coalesce_decimal(metrics.execution_match_rate, Decimal("0")) >= PROSPECTIVE_ECONOMIC_MIN_EXECUTION_MATCH_RATE
        and _coalesce_decimal(metrics.win_rate, Decimal("0")) >= PROSPECTIVE_ECONOMIC_MIN_WIN_RATE
        and _coalesce_decimal(metrics.expectancy_r, Decimal("-999")) >= PROSPECTIVE_ECONOMIC_MIN_EXPECTANCY_R
        and _coalesce_decimal(metrics.one_sided_90_lcb_r, Decimal("-999")) > 0
        and _coalesce_decimal(metrics.max_drawdown_r, Decimal("999")) <= PROSPECTIVE_ECONOMIC_MAX_DRAWDOWN_R
    )


def prospective_economic_evidence_fingerprint(
    *,
    strategy_id: str,
    profile_fingerprint: str,
    events: Iterable[StrategyEvent],
    metrics: ProspectiveEconomicMetrics,
) -> str:
    relevant = [
        event for event in events
        if event.event_type in {"prospective_economic_signal", "prospective_economic_outcome"}
    ]
    payload = {
        "policy_version": PROSPECTIVE_ECONOMIC_VERSION,
        "strategy_id": strategy_id,
        "profile_fingerprint": profile_fingerprint,
        "event_ids": [event.event_id for event in relevant],
        "metrics": metrics.model_dump(mode="json"),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def holdout_verdict(
    *,
    trade_count: int,
    win_rate: Decimal,
    expectancy_r: Decimal,
    max_drawdown_r: Decimal,
) -> Literal["UNDERPOWERED", "FAIL", "ROBUST", "GOLD"]:
    if trade_count < PROSPECTIVE_ECONOMIC_HOLDOUT_MIN_TRADES:
        return "UNDERPOWERED"
    if (
        win_rate >= PROSPECTIVE_ECONOMIC_HOLDOUT_GOLD_MIN_WIN_RATE
        and expectancy_r >= PROSPECTIVE_ECONOMIC_HOLDOUT_GOLD_MIN_EXPECTANCY_R
        and max_drawdown_r <= PROSPECTIVE_ECONOMIC_HOLDOUT_GOLD_MAX_DRAWDOWN_R
    ):
        return "GOLD"
    if (
        win_rate >= PROSPECTIVE_ECONOMIC_HOLDOUT_ROBUST_MIN_WIN_RATE
        and expectancy_r > PROSPECTIVE_ECONOMIC_HOLDOUT_ROBUST_MIN_EXPECTANCY_R
        and max_drawdown_r <= PROSPECTIVE_ECONOMIC_HOLDOUT_ROBUST_MAX_DRAWDOWN_R
    ):
        return "ROBUST"
    return "FAIL"


def _soak_pass(metrics: ProspectiveEconomicMetrics) -> bool:
    return (
        metrics.matched_outcome_count >= PROSPECTIVE_ECONOMIC_SOAK_MIN_MATCHED_OUTCOMES
        and metrics.distinct_sessions >= PROSPECTIVE_ECONOMIC_SOAK_MIN_DISTINCT_SESSIONS
        and metrics.distinct_symbols >= PROSPECTIVE_ECONOMIC_SOAK_MIN_DISTINCT_SYMBOLS
        and _coalesce_decimal(metrics.execution_match_rate, Decimal("0")) >= PROSPECTIVE_ECONOMIC_SOAK_MIN_EXECUTION_MATCH_RATE
        and _coalesce_decimal(metrics.win_rate, Decimal("0")) >= PROSPECTIVE_ECONOMIC_SOAK_MIN_WIN_RATE
        and _coalesce_decimal(metrics.expectancy_r, Decimal("-999")) > PROSPECTIVE_ECONOMIC_SOAK_MIN_EXPECTANCY_R
        and _coalesce_decimal(metrics.max_drawdown_r, Decimal("999")) <= PROSPECTIVE_ECONOMIC_SOAK_MAX_DRAWDOWN_R
    )


def evaluate_prospective_economic_status(
    strategy: TradingStrategyConfigDocument,
    events: Iterable[StrategyEvent],
) -> ProspectiveEconomicStatus:
    profile = prospective_economic_profile_fingerprint(strategy)
    ordered = _matching_events(events, profile)
    metrics = _metrics(ordered)
    sample_ready = _sample_ready(metrics)
    quantitative_pass = _quantitative_pass(metrics)
    evidence = prospective_economic_evidence_fingerprint(
        strategy_id=strategy.strategy_id,
        profile_fingerprint=profile,
        events=ordered,
        metrics=metrics,
    )

    evaluation = next(
        (event for event in ordered if event.event_type == "prospective_economic_evaluation"),
        None,
    )
    evaluation_recorded = evaluation is not None
    evaluation_passed = bool(
        evaluation is not None
        and evaluation.payload.get("evidence_fingerprint") == evidence
        and evaluation.payload.get("passed") is True
    )
    # The one-shot evaluation intentionally binds to the snapshot that existed at
    # evaluation time. Later prospective data is soak evidence, not a re-score.
    if evaluation is not None:
        evaluation_passed = bool(evaluation.payload.get("passed") is True)

    # A holdout review is one-shot regardless of verdict. Do not filter to
    # approved events here: a recorded FAIL/UNDERPOWERED result must retire this
    # promotion path rather than disappearing and allowing a second attempt.
    holdout = next(
        (
            event for event in ordered
            if event.event_type == "prospective_economic_holdout_review"
            and evaluation is not None
            and event.payload.get("evaluation_event_id") == evaluation.event_id
        ),
        None,
    )
    holdout_verdict_value: Literal["UNOPENED", "UNDERPOWERED", "FAIL", "ROBUST", "GOLD"] = "UNOPENED"
    if holdout is not None:
        raw = str(holdout.payload.get("holdout_verdict") or "FAIL")
        holdout_verdict_value = raw if raw in {"UNDERPOWERED", "FAIL", "ROBUST", "GOLD"} else "FAIL"  # type: ignore[assignment]
    holdout_passed = holdout_verdict_value in {"ROBUST", "GOLD"}

    soak_metrics = _metrics(ordered, after=holdout.observed_at if holdout is not None else None)
    soak_passed = holdout_passed and _soak_pass(soak_metrics)

    pipeline_payload = {
        "policy_version": PROSPECTIVE_ECONOMIC_VERSION,
        "profile_fingerprint": profile,
        "evaluation_event_id": evaluation.event_id if evaluation is not None else None,
        "holdout_event_id": holdout.event_id if holdout is not None else None,
        "holdout_verdict": holdout_verdict_value,
        "soak_metrics": soak_metrics.model_dump(mode="json"),
    }
    pipeline_fingerprint = hashlib.sha256(
        json.dumps(pipeline_payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    auto_review = next(
        (
            event for event in ordered
            if event.event_type == "prospective_economic_auto_paper_review"
            and event.payload.get("pipeline_evidence_fingerprint") == pipeline_fingerprint
            and event.payload.get("approved") is True
        ),
        None,
    )

    reasons: list[str] = []
    if not sample_ready:
        reasons.append("PROSPECTIVE_ECONOMIC_SAMPLE_INCOMPLETE")
    if sample_ready and not quantitative_pass:
        reasons.append("PROSPECTIVE_ECONOMIC_QUANTITATIVE_GATE_FAILED")
    if not evaluation_recorded:
        reasons.append("PROSPECTIVE_ECONOMIC_ONE_SHOT_EVALUATION_REQUIRED")
    elif not evaluation_passed:
        reasons.append("PROSPECTIVE_ECONOMIC_ONE_SHOT_EVALUATION_FAILED")
    if evaluation_passed and holdout is None:
        reasons.append("PROSPECTIVE_ECONOMIC_SEALED_HOLDOUT_REQUIRED")
    elif holdout is not None and not holdout_passed:
        reasons.append("PROSPECTIVE_ECONOMIC_SEALED_HOLDOUT_FAILED")
    if holdout_passed and not soak_passed:
        reasons.append("PROSPECTIVE_ECONOMIC_SHADOW_SOAK_INCOMPLETE")
    if soak_passed and auto_review is None:
        reasons.append("PROSPECTIVE_ECONOMIC_AUTO_PAPER_REVIEW_REQUIRED")

    authorized = bool(evaluation_passed and holdout_passed and soak_passed and auto_review is not None)
    return ProspectiveEconomicStatus(
        strategy_id=strategy.strategy_id,
        profile_fingerprint=profile,
        metrics=metrics,
        evidence_fingerprint=evidence,
        sample_ready=sample_ready,
        quantitative_pass=quantitative_pass,
        evaluation_recorded=evaluation_recorded,
        evaluation_passed=evaluation_passed,
        evaluation_event_id=evaluation.event_id if evaluation is not None else None,
        sealed_holdout_unlocked=evaluation_passed,
        holdout_reviewed=holdout is not None,
        holdout_verdict=holdout_verdict_value,
        holdout_event_id=holdout.event_id if holdout is not None else None,
        soak_metrics=soak_metrics,
        soak_passed=soak_passed,
        auto_paper_reviewed=auto_review is not None,
        auto_paper_research_authorized=authorized,
        pipeline_evidence_fingerprint=pipeline_fingerprint,
        reason_codes=tuple(reasons),
    )


__all__ = [
    "PROSPECTIVE_ECONOMIC_EVENT_TYPES",
    "PROSPECTIVE_ECONOMIC_HOLDOUT_END",
    "PROSPECTIVE_ECONOMIC_HOLDOUT_START",
    "PROSPECTIVE_ECONOMIC_START",
    "PROSPECTIVE_ECONOMIC_VERSION",
    "ProspectiveEconomicMetrics",
    "ProspectiveEconomicStatus",
    "ProspectiveEconomicThresholds",
    "evaluate_prospective_economic_status",
    "holdout_verdict",
    "prospective_economic_evidence_fingerprint",
    "prospective_economic_profile_fingerprint",
]
