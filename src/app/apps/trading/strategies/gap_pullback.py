from __future__ import annotations

from datetime import time
from decimal import Decimal

from app.apps.trading.gapper_dataset import GapperCandidate
from app.apps.trading.models import MarketBar

from .models import (
    GapPullbackConfig,
    GapPullbackFeatures,
    GapPullbackResult,
    GapPullbackState,
    StrategySignal,
)
from app.apps.trading.us_equity_calendar import EASTERN as _ET
from app.apps.trading.us_equity_calendar import regular_close_time


_REGULAR_OPEN = time(9, 30)


def _regular_bars(bars: list[MarketBar] | tuple[MarketBar, ...]) -> list[MarketBar]:
    if not bars:
        return []
    latest_date = bars[-1].start_time.astimezone(_ET).date()
    result: list[MarketBar] = []
    for bar in bars:
        local = bar.start_time.astimezone(_ET)
        if local.date() != latest_date:
            continue
        if _REGULAR_OPEN <= local.time() < regular_close_time(local.date()):
            result.append(bar)
    return result


def session_vwap(bars: list[MarketBar] | tuple[MarketBar, ...]) -> Decimal | None:
    """Regular-session VWAP reset at 09:30 America/New_York."""
    regular = _regular_bars(bars)
    total_volume = sum((bar.volume for bar in regular), Decimal("0"))
    if total_volume <= 0:
        return None
    numerator = sum(
        (((bar.high + bar.low + bar.close) / Decimal("3")) * bar.volume for bar in regular),
        Decimal("0"),
    )
    return numerator / total_volume


def _confirmed_pivots(
    bars: list[MarketBar],
    *,
    left: int,
    right: int,
    low: bool,
) -> list[int]:
    """Return only pivots knowable from the supplied prefix."""
    output: list[int] = []
    for j in range(left, len(bars) - right):
        value = bars[j].low if low else bars[j].high
        neighborhood = bars[j - left : j + right + 1]
        values = [item.low if low else item.high for item in neighborhood]
        if low and value == min(values):
            output.append(j)
        elif not low and value == max(values):
            output.append(j)
    return output


def _average_volume(bars: list[MarketBar]) -> Decimal:
    if not bars:
        return Decimal("0")
    return sum((bar.volume for bar in bars), Decimal("0")) / Decimal(len(bars))


def _breakout_volume_ratio(
    regular: list[MarketBar],
    index: int,
    lookback_bars: int,
) -> Decimal:
    prior = regular[max(0, index - lookback_bars) : index]
    average = _average_volume(prior)
    return regular[index].volume / average if average > 0 else Decimal("0")


def _quality_total(features: GapPullbackFeatures) -> int:
    return (
        features.catalyst_score
        + features.supply_score
        + features.opening_structure_score
        + features.pullback_quality_score
        + features.reclaim_break_score
    )


def _result(
    candidate: GapperCandidate,
    state: GapPullbackState,
    reason: str,
    transitions: list[GapPullbackState],
    features: GapPullbackFeatures,
    bars: list[MarketBar],
    signal: StrategySignal | None = None,
) -> GapPullbackResult:
    return GapPullbackResult(
        instrument_id=candidate.instrument_id,
        state=state,
        reason_code=reason,
        features=features.model_copy(update={"quality_score": _quality_total(features)}),
        transitions=tuple(transitions),
        signal=signal,
        evaluated_bar_count=len(bars),
    )


def evaluate_gap_pullback(
    candidate: GapperCandidate,
    bars: list[MarketBar] | tuple[MarketBar, ...],
    config: GapPullbackConfig | None = None,
) -> GapPullbackResult:
    """Evaluate the first failed sell-off as a causal, inspectable state machine.

    Research/LLM annotations may help operators inspect candidates, but AUTO PAPER
    is authorized only by the deterministic candidate evidence and structure
    gates evaluated here.
    """
    active = config or GapPullbackConfig()
    if active.strategy_version == "2.0.0":
        from .failed_selloff_v2 import evaluate_gap_pullback_v2

        adjusted = candidate
        if candidate.spread_bps is None or candidate.spread_bps > active.maximum_spread_bps:
            adjusted = candidate.model_copy(update={"spread_bps": Decimal("0")})
        result = evaluate_gap_pullback_v2(adjusted, bars, active)
        if adjusted is not candidate:
            result = result.model_copy(
                update={
                    "features": result.features.model_copy(
                        update={"spread_bps": candidate.spread_bps}
                    )
                }
            )
        return result

    regular = _regular_bars(bars)
    transitions: list[GapPullbackState] = ["discovered"]

    severe_dilution = tuple(
        flag for flag in candidate.dilution_flags if flag in set(active.reject_dilution_flags)
    )
    float_in_preferred = (
        candidate.float_shares is not None
        and active.preferred_float_min_shares <= candidate.float_shares <= active.preferred_float_max_shares
    )
    catalyst_score = 2 if candidate.catalyst_evidence_ids else 0
    supply_score = 0 if severe_dilution else (2 if float_in_preferred else 1)

    base_features = GapPullbackFeatures(
        gap_pct=candidate.gap_pct,
        spread_bps=candidate.spread_bps,
        tod_rvol=candidate.tod_rvol,
        float_shares=candidate.float_shares,
        catalyst_evidence_count=len(candidate.catalyst_evidence_ids),
        dilution_flags=tuple(candidate.dilution_flags),
        catalyst_score=catalyst_score,
        supply_score=supply_score,
    )

    rejection: str | None = None
    if candidate.gap_pct < active.minimum_gap_pct:
        rejection = "GAP_BELOW_MINIMUM"
    elif not active.minimum_price <= candidate.premarket_price <= active.maximum_price:
        rejection = "PRICE_OUT_OF_RANGE"
    elif candidate.premarket_dollar_volume < active.minimum_premarket_dollar_volume:
        rejection = "PREMARKET_DOLLAR_VOLUME_LOW"
    elif candidate.tod_rvol is None and not active.allow_missing_tod_rvol:
        rejection = "TOD_RVOL_MISSING"
    elif candidate.tod_rvol is not None and candidate.tod_rvol < active.minimum_tod_rvol:
        rejection = "TOD_RVOL_LOW"
    elif candidate.spread_bps is None:
        rejection = "SPREAD_MISSING"
    elif candidate.spread_bps > active.maximum_spread_bps:
        rejection = "SPREAD_TOO_WIDE"
    elif active.require_catalyst_evidence and not candidate.catalyst_evidence_ids:
        rejection = "CATALYST_EVIDENCE_REQUIRED"
    elif severe_dilution:
        rejection = "DILUTION_SUPPLY_RISK"
    elif active.float_preference_mode == "require" and not float_in_preferred:
        rejection = "FLOAT_OUTSIDE_REQUIRED_RANGE"

    if rejection:
        transitions.append("rejected")
        return _result(candidate, "rejected", rejection, transitions, base_features, regular)

    transitions.append("qualified_gap")
    if not regular:
        return _result(
            candidate,
            "qualified_gap",
            "WAITING_FOR_REGULAR_SESSION",
            transitions,
            base_features,
            regular,
        )

    current_et = regular[-1].end_time.astimezone(_ET)
    minutes_since_open = max(0, current_et.hour * 60 + current_et.minute - (9 * 60 + 30))
    base_features = base_features.model_copy(update={"minutes_since_open": minutes_since_open})
    if current_et.time() < active.entry_start_et:
        return _result(
            candidate,
            "qualified_gap",
            "ENTRY_WINDOW_NOT_OPEN",
            transitions,
            base_features,
            regular,
        )

    opening_price = regular[0].open
    required_high = opening_price * (Decimal("1") + active.opening_impulse_min_pct / Decimal("100"))
    impulse_idx: int | None = None
    for index, bar in enumerate(regular):
        if bar.high >= required_high:
            impulse_idx = index
            break
    if impulse_idx is None:
        if current_et.time() > active.last_entry_et:
            transitions.append("expired")
            return _result(candidate, "expired", "NO_OPENING_IMPULSE", transitions, base_features, regular)
        return _result(
            candidate,
            "qualified_gap",
            "WAITING_FOR_OPENING_IMPULSE",
            transitions,
            base_features,
            regular,
        )

    transitions.append("opening_impulse")
    impulse_high = max(bar.high for bar in regular[: impulse_idx + 1])
    impulse_pct = (impulse_high / opening_price - Decimal("1")) * Decimal("100")
    impulse_average_volume = _average_volume(regular[: impulse_idx + 1])
    opening_score = 2 if impulse_pct >= active.opening_impulse_min_pct * Decimal("1.5") else 1
    features = base_features.model_copy(
        update={
            "opening_impulse_pct": impulse_pct,
            "impulse_average_volume": impulse_average_volume,
            "opening_structure_score": opening_score,
        }
    )

    lows = _confirmed_pivots(
        regular,
        left=active.pivot_left_bars,
        right=active.pivot_right_bars,
        low=True,
    )
    l1_idx = next((idx for idx in lows if idx > impulse_idx), None)
    if l1_idx is None:
        transitions.append("first_pullback")
        state: GapPullbackState = "first_pullback"
        if current_et.time() > active.last_entry_et:
            transitions.append("expired")
            state = "expired"
        return _result(candidate, state, "WAITING_FOR_FIRST_LOW_CONFIRMATION", transitions, features, regular)

    l1 = regular[l1_idx].low
    pre_l1_high = max(bar.high for bar in regular[: l1_idx + 1])
    pullback_depth = (pre_l1_high - l1) / pre_l1_high * Decimal("100")
    features = features.model_copy(update={"l1": l1, "pullback_depth_pct": pullback_depth})
    if not active.pullback_min_pct <= pullback_depth <= active.pullback_max_pct:
        transitions.append("rejected")
        return _result(candidate, "rejected", "PULLBACK_DEPTH_OUT_OF_RANGE", transitions, features, regular)
    transitions.extend(["first_pullback", "first_low_confirmed"])

    highs = _confirmed_pivots(
        regular,
        left=active.pivot_left_bars,
        right=active.pivot_right_bars,
        low=False,
    )
    b1_idx = next((idx for idx in highs if idx > l1_idx), None)
    if b1_idx is None:
        return _result(
            candidate,
            "first_low_confirmed",
            "WAITING_FOR_BOUNCE_HIGH",
            transitions,
            features,
            regular,
        )
    b1 = regular[b1_idx].high
    features = features.model_copy(update={"b1": b1})
    transitions.append("bounce_high_confirmed")

    l2_idx = next((idx for idx in lows if idx > b1_idx), None)
    if l2_idx is None:
        transitions.append("second_pullback")
        state = "second_pullback"
        if current_et.time() > active.last_entry_et:
            transitions.append("expired")
            state = "expired"
        return _result(
            candidate,
            state,
            "WAITING_FOR_SECOND_LOW_CONFIRMATION",
            transitions,
            features,
            regular,
        )

    l2 = regular[l2_idx].low
    transitions.append("second_pullback")
    minimum_higher_low = l1 * (
        Decimal("1") + active.higher_low_buffer_bps / Decimal("10000")
    )
    features = features.model_copy(update={"l2": l2})
    if l2 <= minimum_higher_low:
        transitions.append("rejected")
        return _result(candidate, "rejected", "SECOND_LOW_NOT_HIGHER", transitions, features, regular)

    selling_bars = [
        bar for bar in regular[impulse_idx + 1 : l2_idx + 1] if bar.close < bar.open
    ]
    pullback_selling_average = _average_volume(selling_bars)
    pullback_volume_ratio = (
        pullback_selling_average / impulse_average_volume
        if impulse_average_volume > 0
        else Decimal("0")
    )
    pullback_score = (
        2
        if pullback_volume_ratio <= active.pullback_volume_max_ratio * Decimal("0.70")
        else 1
        if pullback_volume_ratio <= active.pullback_volume_max_ratio
        else 0
    )
    features = features.model_copy(
        update={
            "pullback_selling_average_volume": pullback_selling_average,
            "pullback_volume_ratio": pullback_volume_ratio,
            "pullback_quality_score": pullback_score,
        }
    )
    if pullback_volume_ratio > active.pullback_volume_max_ratio:
        transitions.append("rejected")
        return _result(
            candidate,
            "rejected",
            "PULLBACK_SELLING_VOLUME_TOO_HIGH",
            transitions,
            features,
            regular,
        )
    transitions.append("higher_low_confirmed")

    current_vwap = session_vwap(regular)
    if current_vwap is None:
        return _result(
            candidate,
            "higher_low_confirmed",
            "VWAP_UNAVAILABLE",
            transitions,
            features,
            regular,
        )
    current = regular[-1]
    features = features.model_copy(
        update={
            "session_vwap": current_vwap,
            "vwap_distance_pct": (current.close / current_vwap - Decimal("1")) * Decimal("100"),
        }
    )

    breakout_idx: int | None = None
    breakout_ratio = Decimal("0")
    for index in range(l2_idx + 1, len(regular)):
        prefix_vwap = session_vwap(regular[: index + 1])
        if prefix_vwap is None:
            continue
        ratio = _breakout_volume_ratio(regular, index, active.volume_lookback_bars)
        bar = regular[index]
        if (
            bar.close > prefix_vwap
            and bar.close > b1
            and ratio >= active.breakout_volume_ratio
        ):
            breakout_idx = index
            breakout_ratio = ratio
            break

    if breakout_idx is None:
        if current.close <= current_vwap:
            return _result(
                candidate,
                "higher_low_confirmed",
                "WAITING_FOR_VWAP_RECLAIM",
                transitions,
                features,
                regular,
            )
        transitions.append("vwap_reclaim")
        if current.close <= b1:
            return _result(
                candidate,
                "vwap_reclaim",
                "WAITING_FOR_B1_BREAK",
                transitions,
                features,
                regular,
            )
        transitions.append("lower_high_break")
        ratio = _breakout_volume_ratio(regular, len(regular) - 1, active.volume_lookback_bars)
        features = features.model_copy(update={"breakout_volume_ratio": ratio})
        if ratio < active.breakout_volume_ratio:
            return _result(
                candidate,
                "lower_high_break",
                "BREAKOUT_VOLUME_TOO_LOW",
                transitions,
                features,
                regular,
            )
        return _result(
            candidate,
            "lower_high_break",
            "WAITING_FOR_CAUSAL_BREAKOUT_CONFIRMATION",
            transitions,
            features,
            regular,
        )

    transitions.extend(["vwap_reclaim", "lower_high_break"])
    features = features.model_copy(update={"breakout_volume_ratio": breakout_ratio})

    hold_count = len(regular) - breakout_idx - 1
    if active.require_breakout_hold:
        if hold_count < active.breakout_hold_bars:
            features = features.model_copy(update={"breakout_hold_bars": hold_count})
            return _result(
                candidate,
                "lower_high_break",
                "WAITING_FOR_BREAKOUT_HOLD",
                transitions,
                features,
                regular,
            )
        hold_floor = b1 * (
            Decimal("1") - active.breakout_hold_tolerance_bps / Decimal("10000")
        )
        hold_bars = regular[
            breakout_idx + 1 : breakout_idx + 1 + active.breakout_hold_bars
        ]
        if any(bar.low < hold_floor or bar.close < b1 for bar in hold_bars):
            transitions.append("rejected")
            return _result(
                candidate,
                "rejected",
                "BREAKOUT_HOLD_FAILED",
                transitions,
                features.model_copy(update={"breakout_hold_bars": len(hold_bars)}),
                regular,
            )
        transitions.append("breakout_hold")
        features = features.model_copy(
            update={
                "breakout_hold_bars": len(hold_bars),
                "reclaim_break_score": 2,
            }
        )
    else:
        features = features.model_copy(
            update={
                "breakout_hold_bars": 0,
                "reclaim_break_score": 2,
            }
        )

    signal_index = breakout_idx + (
        active.breakout_hold_bars if active.require_breakout_hold else 0
    )
    if signal_index != len(regular) - 1:
        return _result(
            candidate,
            "breakout_hold" if active.require_breakout_hold else "lower_high_break",
            "BREAKOUT_ALREADY_PASSED",
            transitions,
            features,
            regular,
        )

    quality_score = _quality_total(features)
    if quality_score < active.minimum_quality_score:
        transitions.append("rejected")
        return _result(
            candidate,
            "rejected",
            "QUALITY_SCORE_BELOW_MINIMUM",
            transitions,
            features,
            regular,
        )

    if current_et.time() > active.last_entry_et:
        transitions.append("expired")
        return _result(candidate, "expired", "ENTRY_WINDOW_CLOSED", transitions, features, regular)

    stop = l2 * (Decimal("1") - active.stop_buffer_bps / Decimal("10000"))
    entry = current.close
    risk_per_share = entry - stop
    if risk_per_share <= 0:
        transitions.append("rejected")
        return _result(
            candidate,
            "rejected",
            "NON_POSITIVE_RISK_DISTANCE",
            transitions,
            features,
            regular,
        )
    target = entry + risk_per_share * active.reward_multiple
    transitions.append("entry_ready")
    signal = StrategySignal(
        instrument_id=candidate.instrument_id,
        state="entry_ready",
        entry_price=entry,
        stop_price=stop,
        target_price=target,
        risk_per_share=risk_per_share,
        reason_code="FAILED_SELL_OFF_CONFIRMED",
        quality_score=quality_score,
    )
    return _result(
        candidate,
        "entry_ready",
        "FAILED_SELL_OFF_CONFIRMED",
        transitions,
        features,
        regular,
        signal,
    )
