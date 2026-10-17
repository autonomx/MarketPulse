from __future__ import annotations

import logging

import hashlib
import json
import math
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Iterable

from pydantic import BaseModel, ConfigDict, Field

from .market_evidence import MARKET_EVIDENCE_POLICY_VERSION
from .strategies.models import GapPullbackConfig
from .strategy_repository import StrategyEvent, TradingStrategyConfigDocument

logger = logging.getLogger(__name__)


V2_PROSPECTIVE_START = date(2026, 8, 24)
V2_MIN_MATCHED_TRADES = 20
V2_MIN_DISTINCT_SESSIONS = 15
V2_MIN_DISTINCT_SYMBOLS = 10
V2_MIN_EXECUTION_MATCH_RATE = Decimal("0.90")
V2_MIN_EXPECTANCY_R = Decimal("0.20")
V2_MAX_DRAWDOWN_R = Decimal("5")
V2_ONE_SIDED_90_Z = Decimal("1.2815515655446004")
V2_LIVE_MATCH_WINDOW_MINUTES = 10
V2_QUALIFICATION_VERSION = "v2-prospective-qualification-3"
V2_REPLAY_VERSION = "v2-shadow-replay-3"
PROSPECTIVE_ECONOMIC_POLICY_VERSION = "prospective-economic-shadow-v1"

V2_QUALIFICATION_EVENT_TYPES = (
    "shadow_execution",
    "v2_shadow_replay_trade",
    "v2_shadow_replay_session",
    "v2_promotion_review",
    "prospective_economic_auto_paper_review",
)


class V2QualificationThresholds(BaseModel):
    model_config = ConfigDict(frozen=True)

    prospective_start: date = V2_PROSPECTIVE_START
    minimum_matched_trades: int = V2_MIN_MATCHED_TRADES
    minimum_distinct_sessions: int = V2_MIN_DISTINCT_SESSIONS
    minimum_distinct_symbols: int = V2_MIN_DISTINCT_SYMBOLS
    minimum_execution_match_rate: Decimal = V2_MIN_EXECUTION_MATCH_RATE
    minimum_expectancy_r: Decimal = V2_MIN_EXPECTANCY_R
    one_sided_confidence_level: Decimal = Decimal("0.90")
    maximum_drawdown_r: Decimal = V2_MAX_DRAWDOWN_R
    live_match_window_minutes: int = V2_LIVE_MATCH_WINDOW_MINUTES


class V2ProspectiveQualification(BaseModel):
    model_config = ConfigDict(frozen=True)

    strategy_id: str
    qualification_version: str = V2_QUALIFICATION_VERSION
    prospective_start: date = V2_PROSPECTIVE_START
    market_evidence_policy_version: str = MARKET_EVIDENCE_POLICY_VERSION
    expected_profile_fingerprint: str
    current_profile_fingerprint: str
    profile_match: bool
    replay_trade_count: int = Field(ge=0)
    matched_eligible_trade_count: int = Field(ge=0)
    distinct_sessions: int = Field(ge=0)
    distinct_symbols: int = Field(ge=0)
    execution_match_rate: Decimal | None = None
    expectancy_r: Decimal | None = None
    one_sided_90_lcb_r: Decimal | None = None
    max_drawdown_r: Decimal | None = None
    thresholds: V2QualificationThresholds = Field(default_factory=V2QualificationThresholds)
    evidence_fingerprint: str
    prospective_economic_reviewed: bool = False
    qualified: bool = False
    reviewed: bool = False
    auto_paper_authorized: bool = False
    reason_codes: tuple[str, ...] = ()


def frozen_v2_config() -> GapPullbackConfig:
    """Canonical V11/2.0 prospective profile frozen before 2026-08-24 evidence."""

    return GapPullbackConfig(
        strategy_id="gap_pullback_v1",
        strategy_version="2.0.0",
        structure_interval="1m",
        execution_interval="1m",
        universe_scan_time_et=time(9, 20),
        auto_archive_daily_universe=True,
        universe_archive_grace_minutes=10,
        universe_discovery_count=50,
        minimum_gap_pct=Decimal("20"),
        minimum_price=Decimal("0.50"),
        maximum_price=Decimal("20"),
        minimum_premarket_dollar_volume=Decimal("100000"),
        minimum_tod_rvol=Decimal("3"),
        maximum_spread_bps=Decimal("150"),
        preferred_float_min_shares=Decimal("2000000"),
        preferred_float_max_shares=Decimal("30000000"),
        float_preference_mode="ignore",
        require_catalyst_evidence=False,
        reject_dilution_flags=(),
        opening_impulse_min_pct=Decimal("0"),
        pullback_min_pct=Decimal("8"),
        pullback_max_pct=Decimal("25"),
        pullback_volume_max_ratio=Decimal("5"),
        higher_low_buffer_bps=Decimal("50"),
        breakout_volume_ratio=Decimal("1.25"),
        pivot_left_bars=1,
        pivot_right_bars=1,
        volume_lookback_bars=5,
        require_breakout_hold=False,
        breakout_hold_bars=1,
        breakout_hold_tolerance_bps=Decimal("25"),
        minimum_quality_score=0,
        v2_recovery_min_pct=Decimal("5"),
        v2_second_pullback_min_pct=Decimal("2"),
        v2_minimum_l1_to_b1_minutes=4,
        v2_maximum_l2_to_signal_minutes=8,
        v2_minimum_breakout_volume_ratio=Decimal("0"),
        v2_profit_protection_trigger_r=Decimal("0.75"),
        v2_protected_stop_r=Decimal("0.25"),
        v2_max_hold_minutes=60,
        stop_buffer_bps=Decimal("15"),
        reward_multiple=Decimal("1.5"),
        entry_start_et=time(9, 35),
        last_entry_et=time(11, 30),
    )


def managed_finviz_v2_config() -> GapPullbackConfig:
    """Exact managed Finviz V2 execution profile eligible for its own evidence."""

    return frozen_v2_config().model_copy(
        update={
            "universe_scan_time_et": time(9, 15),
            "universe_discovery_source": "finviz",
            "universe_discovery_count": 5,
            "intraday_learning_enabled": True,
            "stoch_trend_capture_enabled": True,
            "intraday_llm_enabled": True,
            "intraday_llm_top_n": 5,
            "intraday_llm_interval_minutes": 10,
        }
    )


def v2_profile_fingerprint(config: GapPullbackConfig) -> str:
    """Bind strategy semantics and market-evidence semantics to one identity."""

    payload = config.model_dump(
        mode="json",
        exclude={
            "intraday_learning_enabled",
            "stoch_trend_capture_enabled",
            "intraday_llm_enabled",
            "intraday_llm_top_n",
            "intraday_llm_interval_minutes",
            "universe_discovery_source",
            "research_score_adjustment_enabled",
            # Which task runs the pass is operational, not strategy semantics.
            "execution_owner",
        },
    )
    # Added after profiles were qualified (WP-8.3): only an enabled opt-in is
    # part of the identity, so existing qualification evidence still matches.
    if config.research_score_adjustment_enabled:
        payload["research_score_adjustment_enabled"] = True
    if config.universe_discovery_source != "yahoo":
        payload["universe_discovery_source"] = config.universe_discovery_source
    payload["market_evidence_policy_version"] = MARKET_EVIDENCE_POLICY_VERSION
    payload["frozen_spread_authority"] = "research_only"
    payload["live_entry_spread_authority"] = "execution_policy"
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


FROZEN_V2_PROFILE_FINGERPRINT = v2_profile_fingerprint(frozen_v2_config())
MANAGED_FINVIZ_V2_PROFILE_FINGERPRINT = v2_profile_fingerprint(
    managed_finviz_v2_config()
)
V2_QUALIFIABLE_PROFILE_FINGERPRINTS = frozenset(
    {
        FROZEN_V2_PROFILE_FINGERPRINT,
        MANAGED_FINVIZ_V2_PROFILE_FINGERPRINT,
    }
)


def v2_qualification_profile_fingerprint(
    config: GapPullbackConfig,
) -> str | None:
    """Return the exact frozen profile identity allowed to accrue AUTO PAPER evidence."""

    if config.strategy_version != "2.0.0":
        return None
    current = v2_profile_fingerprint(config)
    return current if current in V2_QUALIFIABLE_PROFILE_FINGERPRINTS else None


def _decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        logger.debug("suppressed error in %s", "_decimal", exc_info=True)
        return None


def _session_date(event: StrategyEvent) -> date | None:
    raw = event.payload.get("session_date")
    if raw:
        try:
            return date.fromisoformat(str(raw))
        except ValueError:
            return None
    return event.observed_at.astimezone(timezone.utc).date()


def _entry_time(event: StrategyEvent) -> datetime | None:
    raw = event.payload.get("entry_time")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _is_clean_replay_session(event: StrategyEvent, expected_profile: str) -> bool:
    return (
        event.event_type == "v2_shadow_replay_session"
        and event.payload.get("qualification_version") == V2_QUALIFICATION_VERSION
        and event.payload.get("replay_version") == V2_REPLAY_VERSION
        and event.payload.get("profile_fingerprint") == expected_profile
        and event.payload.get("market_evidence_policy_version") == MARKET_EVIDENCE_POLICY_VERSION
        and event.payload.get("status") in {"completed", "completed_no_trigger"}
        and event.payload.get("qualification_eligible") is True
    )


def _is_canonical_replay(
    event: StrategyEvent,
    expected_profile: str,
    clean_sessions: set[date],
) -> bool:
    if event.event_type != "v2_shadow_replay_trade":
        return False
    if event.payload.get("qualification_version") != V2_QUALIFICATION_VERSION:
        return False
    if event.payload.get("replay_version") != V2_REPLAY_VERSION:
        return False
    if event.payload.get("profile_fingerprint") != expected_profile:
        return False
    if event.payload.get("market_evidence_policy_version") != MARKET_EVIDENCE_POLICY_VERSION:
        return False
    if event.payload.get("universe_source") != "auto_archive_shadow":
        return False
    session = _session_date(event)
    return (
        session is not None
        and session in clean_sessions
        and session >= V2_PROSPECTIVE_START
        and _decimal(event.payload.get("r_result")) is not None
    )


def _is_eligible_live_shadow(event: StrategyEvent, expected_profile: str) -> bool:
    return (
        event.event_type == "shadow_execution"
        and event.reason_code == "SHADOW_EXECUTION_OBSERVED"
        and event.payload.get("execution_authority") is False
        and event.payload.get("universe_source") == "auto_archive_shadow"
        and event.payload.get("profile_fingerprint") == expected_profile
        and bool((event.payload.get("execution") or {}).get("execution_eligible"))
        and event.observed_at.astimezone(timezone.utc).date() >= V2_PROSPECTIVE_START
    )


def _matches_live(replay: StrategyEvent, live: StrategyEvent) -> bool:
    if replay.instrument_id != live.instrument_id:
        return False
    if _session_date(replay) != _session_date(live):
        return False
    entry = _entry_time(replay)
    if entry is None:
        return False
    observed = live.observed_at.astimezone(timezone.utc)
    delta_minutes = abs(Decimal(str((entry - observed).total_seconds())) / Decimal("60"))
    return delta_minutes <= Decimal(V2_LIVE_MATCH_WINDOW_MINUTES)


def _sample_stdev(values: list[Decimal]) -> Decimal | None:
    if len(values) < 2:
        return None
    mean = sum(values, Decimal("0")) / Decimal(len(values))
    variance = sum(((value - mean) ** 2 for value in values), Decimal("0")) / Decimal(len(values) - 1)
    return Decimal(str(math.sqrt(float(variance))))


def _one_sided_90_lcb(values: list[Decimal]) -> Decimal | None:
    if len(values) < 2:
        return None
    mean = sum(values, Decimal("0")) / Decimal(len(values))
    stdev = _sample_stdev(values)
    if stdev is None:
        return None
    standard_error = stdev / Decimal(str(math.sqrt(len(values))))
    return mean - (V2_ONE_SIDED_90_Z * standard_error)


def _max_drawdown_r(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    equity = Decimal("0")
    peak = Decimal("0")
    max_drawdown = Decimal("0")
    for value in values:
        equity += value
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    return max_drawdown


def _evidence_fingerprint(
    *,
    strategy_id: str,
    profile_fingerprint: str,
    replay_events: list[StrategyEvent],
    matched_events: list[StrategyEvent],
    expectancy_r: Decimal | None,
    lcb_r: Decimal | None,
    max_drawdown_r: Decimal | None,
) -> str:
    payload = {
        "qualification_version": V2_QUALIFICATION_VERSION,
        "market_evidence_policy_version": MARKET_EVIDENCE_POLICY_VERSION,
        "strategy_id": strategy_id,
        "profile_fingerprint": profile_fingerprint,
        "replay_event_ids": [event.event_id for event in replay_events],
        "matched_replay_event_ids": [event.event_id for event in matched_events],
        "expectancy_r": str(expectancy_r) if expectancy_r is not None else None,
        "one_sided_90_lcb_r": str(lcb_r) if lcb_r is not None else None,
        "max_drawdown_r": str(max_drawdown_r) if max_drawdown_r is not None else None,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def evaluate_v2_prospective_qualification(
    strategy: TradingStrategyConfigDocument,
    events: Iterable[StrategyEvent],
) -> V2ProspectiveQualification:
    current_profile = v2_profile_fingerprint(strategy.config)
    recognized_profile = v2_qualification_profile_fingerprint(strategy.config)
    expected_profile = recognized_profile or FROZEN_V2_PROFILE_FINGERPRINT
    ordered = sorted(events, key=lambda item: (item.observed_at, item.event_id))
    clean_sessions = {
        session
        for event in ordered
        if _is_clean_replay_session(event, expected_profile)
        and (session := _session_date(event)) is not None
    }
    replay_events = [
        event
        for event in ordered
        if _is_canonical_replay(event, expected_profile, clean_sessions)
    ]
    live_events = [event for event in ordered if _is_eligible_live_shadow(event, expected_profile)]

    matched: list[StrategyEvent] = []
    used_live: set[str] = set()
    for replay in replay_events:
        candidate = next(
            (
                live
                for live in live_events
                if live.event_id not in used_live and _matches_live(replay, live)
            ),
            None,
        )
        if candidate is not None:
            used_live.add(candidate.event_id)
            matched.append(replay)

    values = [_decimal(event.payload.get("r_result")) for event in matched]
    r_values = [value for value in values if value is not None]
    expectancy = sum(r_values, Decimal("0")) / Decimal(len(r_values)) if r_values else None
    lcb = _one_sided_90_lcb(r_values)
    drawdown = _max_drawdown_r(r_values)
    replay_count = len(replay_events)
    match_rate = Decimal(len(matched)) / Decimal(replay_count) if replay_count else None
    sessions = {_session_date(event) for event in matched}
    sessions.discard(None)
    symbols = {event.instrument_id for event in matched}

    evidence_fingerprint = _evidence_fingerprint(
        strategy_id=strategy.strategy_id,
        profile_fingerprint=current_profile,
        replay_events=replay_events,
        matched_events=matched,
        expectancy_r=expectancy,
        lcb_r=lcb,
        max_drawdown_r=drawdown,
    )

    prospective_economic_reviewed = any(
        event.event_type == "prospective_economic_auto_paper_review"
        and event.payload.get("policy_version") == PROSPECTIVE_ECONOMIC_POLICY_VERSION
        and event.payload.get("v2_profile_fingerprint") == expected_profile
        and event.payload.get("approved") is True
        and event.payload.get("execution_authority") is False
        for event in ordered
    )

    reasons: list[str] = []
    profile_match = recognized_profile is not None and current_profile == expected_profile
    if not profile_match:
        reasons.append("V2_PROFILE_MISMATCH")
    if len(matched) < V2_MIN_MATCHED_TRADES:
        reasons.append("V2_MATCHED_TRADES_LOW")
    if len(sessions) < V2_MIN_DISTINCT_SESSIONS:
        reasons.append("V2_DISTINCT_SESSIONS_LOW")
    if len(symbols) < V2_MIN_DISTINCT_SYMBOLS:
        reasons.append("V2_DISTINCT_SYMBOLS_LOW")
    if match_rate is None or match_rate < V2_MIN_EXECUTION_MATCH_RATE:
        reasons.append("V2_EXECUTION_MATCH_RATE_LOW")
    if expectancy is None or expectancy < V2_MIN_EXPECTANCY_R:
        reasons.append("V2_EXPECTANCY_LOW")
    if lcb is None or lcb <= 0:
        reasons.append("V2_EXPECTANCY_LCB_NOT_POSITIVE")
    if drawdown is None or drawdown > V2_MAX_DRAWDOWN_R:
        reasons.append("V2_DRAWDOWN_TOO_HIGH")
    if not prospective_economic_reviewed:
        reasons.append("V2_PROSPECTIVE_ECONOMIC_PIPELINE_REVIEW_REQUIRED")

    qualified = not reasons
    reviewed = any(
        event.event_type == "v2_promotion_review"
        and event.payload.get("qualification_version") == V2_QUALIFICATION_VERSION
        and event.payload.get("profile_fingerprint") == current_profile
        and event.payload.get("evidence_fingerprint") == evidence_fingerprint
        and event.payload.get("approved") is True
        for event in ordered
    )
    if qualified and not reviewed:
        reasons.append("V2_OPERATOR_REVIEW_REQUIRED")

    return V2ProspectiveQualification(
        strategy_id=strategy.strategy_id,
        expected_profile_fingerprint=expected_profile,
        current_profile_fingerprint=current_profile,
        profile_match=profile_match,
        replay_trade_count=replay_count,
        matched_eligible_trade_count=len(matched),
        distinct_sessions=len(sessions),
        distinct_symbols=len(symbols),
        execution_match_rate=match_rate,
        expectancy_r=expectancy,
        one_sided_90_lcb_r=lcb,
        max_drawdown_r=drawdown,
        evidence_fingerprint=evidence_fingerprint,
        prospective_economic_reviewed=prospective_economic_reviewed,
        qualified=qualified,
        reviewed=reviewed,
        auto_paper_authorized=qualified and reviewed,
        reason_codes=tuple(reasons),
    )


__all__ = [
    "FROZEN_V2_PROFILE_FINGERPRINT",
    "MANAGED_FINVIZ_V2_PROFILE_FINGERPRINT",
    "V2_PROSPECTIVE_START",
    "V2_QUALIFICATION_EVENT_TYPES",
    "V2_QUALIFICATION_VERSION",
    "V2_REPLAY_VERSION",
    "V2ProspectiveQualification",
    "evaluate_v2_prospective_qualification",
    "frozen_v2_config",
    "managed_finviz_v2_config",
    "v2_profile_fingerprint",
    "v2_qualification_profile_fingerprint",
]
