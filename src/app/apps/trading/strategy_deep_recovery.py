from __future__ import annotations

"""Research-only causal deep-recovery continuation state for prospective SHADOW.

This module has no paper repository, order, protection or broker dependency. It
mirrors frozen V2's candidate hard gates by asking the frozen V2 evaluator to
qualify the candidate with an empty regular-session prefix, then evaluates a
separately versioned deep-recovery shape from finalized 1-minute bars.
"""

from datetime import datetime, time
from decimal import Decimal
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict

from .gapper_dataset import GapperCandidate
from .models import MarketBar
from .strategies.failed_selloff_v2 import evaluate_gap_pullback_v2
from .strategies.gap_pullback import _ET, _regular_bars, session_vwap
from .strategies.models import GapPullbackConfig


DEEP_RECOVERY_SETUP_ID: Final = "deep_recovery_continuation_v1"
DEEP_RECOVERY_RULE_VERSION: Final = "1.0.0-shadow"
DEEP_RECOVERY_OPENING_END_ET = time(9, 45)
DEEP_RECOVERY_MINIMUM_SELLOFF_PCT = Decimal("5")
DEEP_RECOVERY_TRIGGER_PCT = Decimal("30")
DEEP_RECOVERY_BREAKOUT_LOOKBACK = 3
DEEP_RECOVERY_STOP_LOOKBACK = 5
DEEP_RECOVERY_MAX_RISK_PCT = Decimal("8")
DEEP_RECOVERY_MAX_VWAP_EXTENSION_PCT = Decimal("12")
DEEP_RECOVERY_RISK_OVERLAY_VERSION = "deep-recovery-risk-overlay-v1"

DeepRecoveryState = Literal[
    "hard_gate_rejected",
    "waiting_opening_range",
    "waiting_post_opening_low",
    "waiting_selloff",
    "waiting_recovery",
    "waiting_vwap",
    "waiting_breakout",
    "signal_ready",
    "expired",
]


class DeepRecoveryShadowEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    setup_id: Literal["deep_recovery_continuation_v1"] = DEEP_RECOVERY_SETUP_ID
    rule_version: Literal["1.0.0-shadow"] = DEEP_RECOVERY_RULE_VERSION
    state: DeepRecoveryState
    reason_code: str
    observed_at: datetime | None = None
    opening_high: Decimal | None = None
    opening_high_at: datetime | None = None
    running_low: Decimal | None = None
    running_low_at: datetime | None = None
    selloff_pct: Decimal | None = None
    recovery_pct: Decimal | None = None
    session_vwap: Decimal | None = None
    vwap_distance_pct: Decimal | None = None
    prior_breakout_high: Decimal | None = None
    breakout_confirmed: bool | None = None
    research_stop_reference: Decimal | None = None
    research_stop_price: Decimal | None = None
    research_risk_pct: Decimal | None = None
    hard_gate_features: dict[str, Any]
    execution_authority: Literal[False] = False

    @property
    def signal_ready(self) -> bool:
        return self.state == "signal_ready"


def _evaluation(
    *,
    state: DeepRecoveryState,
    reason_code: str,
    hard_gate_features: dict[str, Any],
    **values: Any,
) -> DeepRecoveryShadowEvaluation:
    return DeepRecoveryShadowEvaluation(
        state=state,
        reason_code=reason_code,
        hard_gate_features=hard_gate_features,
        **values,
    )


def apply_deep_recovery_risk_policy(
    evaluation: DeepRecoveryShadowEvaluation,
) -> DeepRecoveryShadowEvaluation:
    if not evaluation.signal_ready:
        return evaluation
    risk_pct = evaluation.research_risk_pct
    vwap_extension = evaluation.vwap_distance_pct
    features = dict(evaluation.hard_gate_features)
    features["deep_recovery_risk_overlay"] = {
        "version": DEEP_RECOVERY_RISK_OVERLAY_VERSION,
        "max_research_risk_pct": str(DEEP_RECOVERY_MAX_RISK_PCT),
        "max_vwap_extension_pct": str(DEEP_RECOVERY_MAX_VWAP_EXTENSION_PCT),
    }
    if risk_pct is None or risk_pct > DEEP_RECOVERY_MAX_RISK_PCT:
        return evaluation.model_copy(
            update={
                "state": "waiting_breakout",
                "reason_code": "DEEP_RECOVERY_RISK_TOO_WIDE",
                "hard_gate_features": features,
            }
        )
    if (
        vwap_extension is None
        or vwap_extension > DEEP_RECOVERY_MAX_VWAP_EXTENSION_PCT
    ):
        return evaluation.model_copy(
            update={
                "state": "waiting_breakout",
                "reason_code": "DEEP_RECOVERY_EXTENSION_TOO_HIGH",
                "hard_gate_features": features,
            }
        )
    return evaluation.model_copy(update={"hard_gate_features": features})


def evaluate_deep_recovery_shadow(
    candidate: GapperCandidate,
    bars: list[MarketBar] | tuple[MarketBar, ...],
    config: GapPullbackConfig,
) -> DeepRecoveryShadowEvaluation:
    """Evaluate the predeclared deep-recovery SHADOW shape on a finalized prefix."""

    if config.strategy_version != "2.0.0":
        raise ValueError("deep-recovery SHADOW is defined only beside frozen strategy version 2.0.0")

    # This preserves the exact frozen V2 hard candidate gates without modifying
    # or re-implementing them. With no regular bars, a qualified V2 candidate
    # stops at WAITING_FOR_REGULAR_SESSION; a hard-gate failure is rejected.
    hard_gate = evaluate_gap_pullback_v2(candidate, (), config)
    hard_features = hard_gate.features.model_dump(mode="json")
    if hard_gate.state == "rejected":
        return _evaluation(
            state="hard_gate_rejected",
            reason_code=hard_gate.reason_code,
            hard_gate_features=hard_features,
        )

    regular = _regular_bars([bar for bar in bars if bar.is_final])
    if not regular:
        return _evaluation(
            state="waiting_opening_range",
            reason_code="WAITING_FOR_REGULAR_SESSION",
            hard_gate_features=hard_features,
        )

    current = regular[-1]
    observed_at = current.end_time
    current_et = observed_at.astimezone(_ET)
    if current_et.time() < DEEP_RECOVERY_OPENING_END_ET:
        return _evaluation(
            state="waiting_opening_range",
            reason_code="WAITING_FOR_OPENING_RANGE",
            hard_gate_features=hard_features,
            observed_at=observed_at,
        )

    opening = [
        bar
        for bar in regular
        if bar.start_time.astimezone(_ET).time() < DEEP_RECOVERY_OPENING_END_ET
    ]
    if not opening:
        return _evaluation(
            state="waiting_opening_range",
            reason_code="OPENING_RANGE_UNAVAILABLE",
            hard_gate_features=hard_features,
            observed_at=observed_at,
        )
    opening_high = max(bar.high for bar in opening)
    opening_index = next(index for index, bar in enumerate(regular) if bar.high == opening_high)
    opening_bar = regular[opening_index]
    post_opening = regular[opening_index + 1 :]
    if not post_opening:
        return _evaluation(
            state="waiting_post_opening_low",
            reason_code="WAITING_FOR_POST_OPENING_LOW",
            hard_gate_features=hard_features,
            observed_at=observed_at,
            opening_high=opening_high,
            opening_high_at=opening_bar.end_time,
        )

    running_low = min(bar.low for bar in post_opening)
    running_low_bar = next(bar for bar in post_opening if bar.low == running_low)
    selloff_pct = (opening_high - running_low) / opening_high * Decimal("100")
    recovery_pct = (
        (current.close / running_low - Decimal("1")) * Decimal("100")
        if running_low > 0
        else Decimal("0")
    )
    vwap = session_vwap(regular)
    vwap_distance = (
        (current.close / vwap - Decimal("1")) * Decimal("100")
        if vwap is not None and vwap > 0
        else None
    )
    prior = regular[max(0, len(regular) - 1 - DEEP_RECOVERY_BREAKOUT_LOOKBACK) : -1]
    prior_breakout_high = max((bar.high for bar in prior), default=None)
    breakout_confirmed = (
        current.close > prior_breakout_high
        if prior_breakout_high is not None
        else None
    )
    stop_window = regular[max(0, len(regular) - DEEP_RECOVERY_STOP_LOOKBACK) :]
    stop_reference = min((bar.low for bar in stop_window), default=None)
    stop_price = (
        stop_reference * (Decimal("1") - config.stop_buffer_bps / Decimal("10000"))
        if stop_reference is not None and stop_reference > 0
        else None
    )
    risk_pct = (
        (current.close - stop_price) / current.close * Decimal("100")
        if stop_price is not None and current.close > stop_price
        else None
    )
    common = {
        "observed_at": observed_at,
        "opening_high": opening_high,
        "opening_high_at": opening_bar.end_time,
        "running_low": running_low,
        "running_low_at": running_low_bar.end_time,
        "selloff_pct": selloff_pct,
        "recovery_pct": recovery_pct,
        "session_vwap": vwap,
        "vwap_distance_pct": vwap_distance,
        "prior_breakout_high": prior_breakout_high,
        "breakout_confirmed": breakout_confirmed,
        "research_stop_reference": stop_reference,
        "research_stop_price": stop_price,
        "research_risk_pct": risk_pct,
    }

    if current_et.time() > config.last_entry_et:
        return _evaluation(
            state="expired",
            reason_code="ENTRY_WINDOW_CLOSED",
            hard_gate_features=hard_features,
            **common,
        )
    if selloff_pct < DEEP_RECOVERY_MINIMUM_SELLOFF_PCT:
        return _evaluation(
            state="waiting_selloff",
            reason_code="DEEP_RECOVERY_SELLOFF_TOO_SHALLOW",
            hard_gate_features=hard_features,
            **common,
        )
    if recovery_pct < DEEP_RECOVERY_TRIGGER_PCT:
        return _evaluation(
            state="waiting_recovery",
            reason_code="WAITING_FOR_30PCT_RECOVERY",
            hard_gate_features=hard_features,
            **common,
        )
    if vwap is None or current.close <= vwap:
        return _evaluation(
            state="waiting_vwap",
            reason_code="RECOVERY_BELOW_VWAP",
            hard_gate_features=hard_features,
            **common,
        )
    if prior_breakout_high is None or current.close <= prior_breakout_high:
        return _evaluation(
            state="waiting_breakout",
            reason_code="WAITING_FOR_3BAR_BREAKOUT",
            hard_gate_features=hard_features,
            **common,
        )
    if stop_price is None or risk_pct is None or risk_pct <= 0:
        return _evaluation(
            state="waiting_breakout",
            reason_code="RESEARCH_STOP_UNAVAILABLE",
            hard_gate_features=hard_features,
            **common,
        )

    return apply_deep_recovery_risk_policy(
        _evaluation(
            state="signal_ready",
            reason_code="DEEP_RECOVERY_30PCT_CONTINUATION_SHADOW",
            hard_gate_features=hard_features,
            **common,
        )
    )
