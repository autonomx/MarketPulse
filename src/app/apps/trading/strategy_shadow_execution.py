from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from .indicator_signals import (
    indicator_entry_confirmation,
    multi_timeframe_indicator_context,
)
from .providers.alpaca_iex import ALPACA_IEX_PARTIAL_MARKET
from .strategy_prospective_signal_features import build_prospective_signal_features


ShadowExecutionReason = Literal[
    "SHADOW_EXECUTION_OBSERVED",
    "SHADOW_EXECUTION_INELIGIBLE",
]


_EXECUTION_FIELDS = (
    "instrument_id",
    "binding_id",
    "provider",
    "last",
    "bid",
    "ask",
    "bid_size",
    "ask_size",
    "high",
    "low",
    "bar_volume",
    "bar_start_time",
    "source_time",
    "spread_bps",
    "execution_eligible",
    "freshness_mode",
    "rejection_reasons",
    "halted",
)


@dataclass(frozen=True)
class ShadowExecutionEvidence:
    reason_code: ShadowExecutionReason
    execution: dict[str, Any]


def _full_indicator_warmup(context) -> bool:
    one = context.one_minute
    five = context.five_minute
    return all(
        value is not None
        for value in (
            one.ema9,
            one.ema20,
            one.macd,
            one.macd_signal,
            one.stochastic_rsi_k,
            one.stochastic_rsi_d,
            five.ema9,
            five.ema20,
            five.macd,
            five.macd_signal,
            five.stochastic_rsi_k,
            five.stochastic_rsi_d,
        )
    )


def observe_shadow_execution(
    market_service,
    *,
    instrument_id: str,
    binding_id: str | None,
) -> ShadowExecutionEvidence:
    """Capture causal execution/research evidence for a SHADOW signal without trading.

    This module intentionally has no paper repository or order dependency. SHADOW
    may observe quote/book/freshness/halt state plus finalized 1m/5m momentum,
    premarket structure and point-in-time research context, but only AUTO PAPER is
    allowed to create orders or protections.

    Supplemental telemetry can fail independently. Neither indicator/research
    availability nor the prospective feature record may upgrade or downgrade the
    execution observation returned by the authoritative execution provider.
    """

    execution = market_service.execution_observation(instrument_id, binding_id)
    payload = {field: getattr(execution, field, None) for field in _EXECUTION_FIELDS}
    payload["indicator_context_source"] = "alpaca_iex_same_day_1m"
    payload["indicator_context_partial_market"] = ALPACA_IEX_PARTIAL_MARKET
    payload["indicator_context_cutoff"] = getattr(execution, "source_time", None)

    finalized = []
    context = None
    full_warmup = False
    try:
        source_time = execution.source_time
        bars = market_service.execution_indicator_bars(
            instrument_id,
            binding_id,
            as_of=source_time,
        )
        finalized = [
            bar
            for bar in bars
            if bar.is_final and bar.end_time <= source_time
        ]
        context = multi_timeframe_indicator_context(finalized)
        entry_allowed, entry_reasons = indicator_entry_confirmation(context)
        full_warmup = _full_indicator_warmup(context)
        payload["indicator_context"] = context.model_dump(mode="json")
        payload["indicator_context_bar_count"] = len(finalized)
        payload["indicator_context_full_warmup"] = full_warmup
        payload["indicator_entry_confirmed"] = entry_allowed
        payload["indicator_entry_reason_codes"] = entry_reasons
        payload["indicator_context_error"] = None
    except Exception as exc:  # telemetry must never alter the execution decision
        payload["indicator_context"] = None
        payload["indicator_context_bar_count"] = 0
        payload["indicator_context_full_warmup"] = False
        payload["indicator_entry_confirmed"] = None
        payload["indicator_entry_reason_codes"] = ()
        payload["indicator_context_error"] = f"{type(exc).__name__}: {exc}"

    try:
        payload["prospective_signal_features"] = build_prospective_signal_features(
            instrument_id=instrument_id,
            decision_at=execution.source_time,
            bars=finalized,
            indicator_context=context,
            indicator_full_warmup=full_warmup,
        )
        payload["prospective_signal_features_error"] = None
    except Exception as exc:  # feature capture is evidence-only and fail-open
        payload["prospective_signal_features"] = None
        payload["prospective_signal_features_error"] = f"{type(exc).__name__}: {exc}"

    reason: ShadowExecutionReason = (
        "SHADOW_EXECUTION_OBSERVED"
        if execution.execution_eligible
        else "SHADOW_EXECUTION_INELIGIBLE"
    )
    return ShadowExecutionEvidence(reason_code=reason, execution=payload)
