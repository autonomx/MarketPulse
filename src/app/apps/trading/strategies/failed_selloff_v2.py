from __future__ import annotations

"""Causal gap-as-impulse failed-selloff evaluator for strategy version 2.0.0.

The 1.x evaluator deliberately remains unchanged in ``gap_pullback.py``.  This
module contains only the separately versioned V2 market-structure semantics
selected by the V11 research program:

    premarket gap as impulse -> confirmed L1 -> confirmed B1 -> confirmed
    higher L2 -> direct B1/VWAP break, with causal timing geometry.

All pivots require a finalized right-side 1-minute bar.  The evaluator never
uses future bars and never re-emits a breakout that occurred on an older prefix.
"""

from decimal import Decimal

from app.apps.trading.gapper_dataset import GapperCandidate
from app.apps.trading.models import MarketBar

from .gap_pullback import _ET, _regular_bars, session_vwap
from .models import GapPullbackConfig, GapPullbackFeatures, GapPullbackResult, StrategySignal
from typing import Any, cast


def _quality(features: GapPullbackFeatures) -> int:
    return (
        features.catalyst_score
        + features.supply_score
        + features.opening_structure_score
        + features.pullback_quality_score
        + features.reclaim_break_score
    )


def _result(
    candidate: GapperCandidate,
    state: str,
    reason: str,
    transitions: list[str],
    features: GapPullbackFeatures,
    regular: list[MarketBar],
    signal: StrategySignal | None = None,
) -> GapPullbackResult:
    scored = features.model_copy(update={"quality_score": _quality(features)})
    return GapPullbackResult(
        instrument_id=candidate.instrument_id,
        state=cast(Any, state),
        reason_code=reason,
        features=scored,
        transitions=cast(Any, tuple(transitions)),
        signal=signal,
        evaluated_bar_count=len(regular),
    )


def _local_low(regular: list[MarketBar], index: int) -> bool:
    if index < 0 or index + 1 >= len(regular):
        return False
    left = regular[index - 1].low if index > 0 else regular[index].low
    return regular[index].low <= left and regular[index].low < regular[index + 1].low


def _local_high(regular: list[MarketBar], index: int) -> bool:
    if index <= 0 or index + 1 >= len(regular):
        return False
    return regular[index].high >= regular[index - 1].high and regular[index].high > regular[index + 1].high


def _volume_ratio(regular: list[MarketBar], index: int, lookback: int) -> Decimal | None:
    prior = regular[max(0, index - lookback) : index]
    if not prior:
        return None
    average = sum((bar.volume for bar in prior), Decimal("0")) / Decimal(len(prior))
    if average <= 0:
        return None
    return regular[index].volume / average


def evaluate_gap_pullback_v2(
    candidate: GapperCandidate,
    bars: list[MarketBar] | tuple[MarketBar, ...],
    config: GapPullbackConfig,
) -> GapPullbackResult:
    """Evaluate version 2.0.0 using only information finalized at this prefix."""

    regular = _regular_bars(list(bars))
    transitions: list[str] = ["discovered"]

    severe = tuple(
        flag for flag in candidate.dilution_flags if flag in set(config.reject_dilution_flags)
    )
    float_ok = (
        candidate.float_shares is not None
        and config.preferred_float_min_shares <= candidate.float_shares <= config.preferred_float_max_shares
    )
    features = GapPullbackFeatures(
        gap_pct=candidate.gap_pct,
        spread_bps=candidate.spread_bps,
        tod_rvol=candidate.tod_rvol,
        float_shares=candidate.float_shares,
        catalyst_evidence_count=len(candidate.catalyst_evidence_ids),
        dilution_flags=tuple(candidate.dilution_flags),
        catalyst_score=2 if candidate.catalyst_evidence_ids else 0,
        supply_score=0 if severe else (2 if float_ok else 1),
        opening_structure_score=2 if candidate.gap_pct >= Decimal("40") else 1,
    )

    membership_only = (
        config.strategy_version == "2.0.0"
        and config.universe_discovery_source == "finviz"
    )
    rejection: str | None = None
    if not membership_only:
        # Feature-local inputs below own authorization. A missing unrelated
        # enrichment flag must not become a trade-wide DATA_INCOMPLETE veto.
        if candidate.gap_pct < config.minimum_gap_pct:
            rejection = "GAP_BELOW_MINIMUM"
        elif not config.minimum_price <= candidate.premarket_price <= config.maximum_price:
            rejection = "PRICE_OUT_OF_RANGE"
        elif candidate.premarket_dollar_volume < config.minimum_premarket_dollar_volume:
            rejection = "PREMARKET_DOLLAR_VOLUME_LOW"
        elif candidate.tod_rvol is None:
            rejection = "TOD_RVOL_MISSING"
        elif candidate.tod_rvol < config.minimum_tod_rvol:
            rejection = "TOD_RVOL_LOW"
        elif candidate.spread_bps is None:
            rejection = "SPREAD_MISSING"
        elif candidate.spread_bps > config.maximum_spread_bps:
            rejection = "SPREAD_TOO_WIDE"
    if rejection is None and config.require_catalyst_evidence and not candidate.catalyst_evidence_ids:
        rejection = "CATALYST_EVIDENCE_REQUIRED"
    elif rejection is None and severe:
        rejection = "DILUTION_SUPPLY_RISK"
    elif rejection is None and config.float_preference_mode == "require" and not float_ok:
        rejection = "FLOAT_OUTSIDE_REQUIRED_RANGE"

    if rejection is not None:
        transitions.append("rejected")
        return _result(candidate, "rejected", rejection, transitions, features, regular)

    transitions.append("qualified_gap")
    if not regular:
        return _result(
            candidate,
            "qualified_gap",
            "WAITING_FOR_REGULAR_SESSION",
            transitions,
            features,
            regular,
        )

    current_et = regular[-1].end_time.astimezone(_ET)
    minutes_since_open = max(0, current_et.hour * 60 + current_et.minute - (9 * 60 + 30))
    features = features.model_copy(update={"minutes_since_open": minutes_since_open})
    if current_et.time() < config.entry_start_et:
        return _result(
            candidate,
            "qualified_gap",
            "ENTRY_WINDOW_NOT_OPEN",
            transitions,
            features,
            regular,
        )

    reference = regular[0].open if membership_only else max(candidate.premarket_price, regular[0].open)

    # Earliest confirmed first selloff low with depth in the configured range.
    l1_idx: int | None = None
    for index in range(0, len(regular) - 1):
        if not _local_low(regular, index):
            continue
        depth = (reference - regular[index].low) / reference * Decimal("100")
        if config.pullback_min_pct <= depth <= config.pullback_max_pct:
            l1_idx = index
            break
    if l1_idx is None:
        transitions.append("first_pullback")
        if current_et.time() > config.last_entry_et:
            transitions.append("expired")
            return _result(candidate, "expired", "ENTRY_WINDOW_CLOSED", transitions, features, regular)
        return _result(
            candidate,
            "first_pullback",
            "WAITING_FOR_CONFIRMED_L1",
            transitions,
            features,
            regular,
        )

    l1 = regular[l1_idx].low
    selloff_depth = (reference - l1) / reference * Decimal("100")
    features = features.model_copy(update={"l1": l1, "pullback_depth_pct": selloff_depth})
    transitions.extend(["first_pullback", "first_low_confirmed"])

    # Earliest confirmed recovery high after L1.
    b1_idx: int | None = None
    for index in range(l1_idx + 1, len(regular) - 1):
        if not _local_high(regular, index):
            continue
        recovery = (regular[index].high / l1 - Decimal("1")) * Decimal("100")
        if recovery >= config.v2_recovery_min_pct:
            b1_idx = index
            break
    if b1_idx is None:
        if current_et.time() > config.last_entry_et:
            transitions.append("expired")
            return _result(candidate, "expired", "ENTRY_WINDOW_CLOSED", transitions, features, regular)
        return _result(
            candidate,
            "first_low_confirmed",
            "WAITING_FOR_CONFIRMED_B1",
            transitions,
            features,
            regular,
        )

    b1 = regular[b1_idx].high
    l1_to_b1 = b1_idx - l1_idx
    features = features.model_copy(update={"b1": b1, "l1_to_b1_minutes": l1_to_b1})
    transitions.append("bounce_high_confirmed")
    if l1_to_b1 < config.v2_minimum_l1_to_b1_minutes:
        transitions.append("rejected")
        return _result(candidate, "rejected", "V2_BASE_TOO_FAST", transitions, features, regular)

    # Earliest confirmed second pullback that stays above L1. If a confirmed
    # second low reaches/breaks L1 first, seller failure has not been proven.
    l2_idx: int | None = None
    required_l2 = l1 * (Decimal("1") + config.higher_low_buffer_bps / Decimal("10000"))
    for index in range(b1_idx + 1, len(regular) - 1):
        if not _local_low(regular, index):
            continue
        second_pullback = (b1 - regular[index].low) / b1 * Decimal("100")
        if regular[index].low >= required_l2 and second_pullback >= config.v2_second_pullback_min_pct:
            l2_idx = index
            break
        if regular[index].low <= l1:
            transitions.append("rejected")
            return _result(candidate, "rejected", "SECOND_LOW_NOT_HIGHER", transitions, features, regular)

    if l2_idx is None:
        transitions.append("second_pullback")
        if current_et.time() > config.last_entry_et:
            transitions.append("expired")
            return _result(candidate, "expired", "ENTRY_WINDOW_CLOSED", transitions, features, regular)
        return _result(
            candidate,
            "second_pullback",
            "WAITING_FOR_CONFIRMED_L2",
            transitions,
            features,
            regular,
        )

    l2 = regular[l2_idx].low
    second_pullback = (b1 - l2) / b1 * Decimal("100")
    features = features.model_copy(
        update={
            "l2": l2,
            "second_pullback_depth_pct": second_pullback,
            "pullback_quality_score": 2,
        }
    )
    transitions.append("higher_low_confirmed")

    # L2 itself needs one finalized right-side bar. A breakout cannot be acted
    # on until the bar after that confirmation, matching the research harness.
    breakout_idx: int | None = None
    breakout_vwap: Decimal | None = None
    breakout_ratio: Decimal | None = None
    for index in range(l2_idx + 2, len(regular)):
        prefix_vwap = session_vwap(regular[: index + 1])
        if prefix_vwap is None:
            continue
        bar = regular[index]
        if bar.close <= b1 or bar.close <= prefix_vwap or bar.close <= bar.open:
            continue
        ratio = _volume_ratio(regular, index, config.volume_lookback_bars)
        if ratio is None:
            continue
        if ratio < config.v2_minimum_breakout_volume_ratio:
            continue
        breakout_idx = index
        breakout_vwap = prefix_vwap
        breakout_ratio = ratio
        break

    if breakout_idx is None:
        if current_et.time() > config.last_entry_et:
            transitions.append("expired")
            return _result(candidate, "expired", "ENTRY_WINDOW_CLOSED", transitions, features, regular)
        return _result(
            candidate,
            "higher_low_confirmed",
            "WAITING_FOR_B1_VWAP_BREAK",
            transitions,
            features,
            regular,
        )

    l2_to_signal = breakout_idx - l2_idx
    breakout = regular[breakout_idx]
    features = features.model_copy(
        update={
            "l2_to_signal_minutes": l2_to_signal,
            "session_vwap": breakout_vwap,
            "vwap_distance_pct": (
                None
                if breakout_vwap in (None, Decimal("0"))
                else (breakout.close / breakout_vwap - Decimal("1")) * Decimal("100")
            ),
            "breakout_volume_ratio": breakout_ratio,
            "reclaim_break_score": 2,
        }
    )
    transitions.extend(["vwap_reclaim", "lower_high_break"])

    if l2_to_signal > config.v2_maximum_l2_to_signal_minutes:
        transitions.append("rejected")
        return _result(candidate, "rejected", "V2_RESOLUTION_TOO_SLOW", transitions, features, regular)

    # Do not re-emit a historical signal on every later monitor cycle.
    if breakout_idx != len(regular) - 1:
        return _result(
            candidate,
            "lower_high_break",
            "BREAKOUT_ALREADY_PASSED",
            transitions,
            features,
            regular,
        )

    entry = breakout.close
    stop = l2 * (Decimal("1") - config.stop_buffer_bps / Decimal("10000"))
    risk = entry - stop
    if risk <= 0:
        transitions.append("rejected")
        return _result(candidate, "rejected", "NON_POSITIVE_RISK_DISTANCE", transitions, features, regular)

    target = entry + risk * config.reward_multiple
    signal = StrategySignal(
        instrument_id=candidate.instrument_id,
        state="entry_ready",
        entry_price=entry,
        stop_price=stop,
        target_price=target,
        risk_per_share=risk,
        reason_code="FAILED_SELLOFF_V2_TIMING_BREAK",
        quality_score=_quality(features),
    )
    if signal.quality_score < config.minimum_quality_score:
        transitions.append("rejected")
        return _result(
            candidate,
            "rejected",
            "QUALITY_SCORE_BELOW_MINIMUM",
            transitions,
            features,
            regular,
        )

    transitions.append("entry_ready")
    return _result(
        candidate,
        "entry_ready",
        "FAILED_SELLOFF_V2_TIMING_BREAK",
        transitions,
        features,
        regular,
        signal,
    )
