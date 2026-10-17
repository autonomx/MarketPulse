from __future__ import annotations
from app.config.env import env_str as _env_str

"""Causal session-market evidence and trend-continuation research."""

import asyncio
import copy
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict

from .models import MarketBar
from app.apps.trading.us_equity_calendar import EASTERN as _ET


FULL_SESSION_1M_LIMIT = 500

TREND_CONTINUATION_SETUP_ID: Final = "trend_continuation_v1"
TREND_CONTINUATION_RULE_VERSION: Final = "1.0.0-shadow"
TREND_MIN_1M_BARS = 20
TREND_MIN_SESSION_RETURN_PCT = Decimal("5")
TREND_MIN_VOLUME_RATIO = Decimal("1.25")
TREND_MIN_CLOSE_LOCATION = Decimal("0.60")
TREND_MAX_EMA9_EXTENSION_PCT = Decimal("6")
TREND_MAX_RISK_PCT = Decimal("8")


TrendState = Literal[
    "waiting_session",
    "waiting_history",
    "waiting_trend",
    "waiting_breakout",
    "waiting_volume",
    "too_extended",
    "risk_too_wide",
    "signal_ready",
    "expired",
]


class TrendContinuationShadowEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    setup_id: Literal["trend_continuation_v1"] = TREND_CONTINUATION_SETUP_ID
    rule_version: Literal["1.0.0-shadow"] = TREND_CONTINUATION_RULE_VERSION
    state: TrendState
    reason_code: str
    observed_at: datetime | None = None
    current_price: Decimal | None = None
    session_vwap: Decimal | None = None
    session_return_pct: Decimal | None = None
    ema9: Decimal | None = None
    ema20: Decimal | None = None
    ema9_extension_pct: Decimal | None = None
    prior_breakout_high: Decimal | None = None
    breakout_confirmed: bool | None = None
    volume_ratio: Decimal | None = None
    close_location: Decimal | None = None
    research_stop_price: Decimal | None = None
    research_risk_pct: Decimal | None = None
    execution_authority: Literal[False] = False

    @property
    def signal_ready(self) -> bool:
        return self.state == "signal_ready"


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _ema(values: list[Decimal], period: int) -> list[Decimal | None]:
    output: list[Decimal | None] = [None] * len(values)
    if len(values) < period:
        return output
    value = sum(values[:period], Decimal("0")) / Decimal(period)
    output[period - 1] = value
    alpha = Decimal("2") / Decimal(period + 1)
    for index in range(period, len(values)):
        value = (values[index] - value) * alpha + value
        output[index] = value
    return output


def _close_location(bar: MarketBar) -> Decimal:
    width = bar.high - bar.low
    if width <= 0:
        return Decimal("1") if bar.close >= bar.open else Decimal("0")
    return (bar.close - bar.low) / width


def evaluate_trend_continuation_shadow(
    bars: list[MarketBar] | tuple[MarketBar, ...],
    *,
    entry_start_et: time,
    last_entry_et: time,
    stop_buffer_bps: Decimal = Decimal("15"),
) -> TrendContinuationShadowEvaluation:
    """Detect a TNON-like no-L1 trend continuation without touching V2."""

    from .strategies.gap_pullback import session_vwap
    from .strategy_timeframes import resample_final_bars

    regular = sorted(
        (bar for bar in bars if bar.is_final and bar.session == "regular"),
        key=lambda bar: bar.start_time,
    )
    if not regular:
        return TrendContinuationShadowEvaluation(
            state="waiting_session",
            reason_code="TREND_CONTINUATION_WAITING_SESSION",
        )

    current = regular[-1]
    observed_at = current.end_time
    observed_et = observed_at.astimezone(_ET).time()
    base: dict[str, Any] = {"observed_at": observed_at, "current_price": current.close}
    if observed_et < entry_start_et:
        return TrendContinuationShadowEvaluation(
            state="waiting_session",
            reason_code="TREND_CONTINUATION_ENTRY_WINDOW_NOT_OPEN",
            **base,
        )
    if observed_et > last_entry_et:
        return TrendContinuationShadowEvaluation(
            state="expired",
            reason_code="TREND_CONTINUATION_ENTRY_WINDOW_CLOSED",
            **base,
        )
    if len(regular) < TREND_MIN_1M_BARS:
        return TrendContinuationShadowEvaluation(
            state="waiting_history",
            reason_code="TREND_CONTINUATION_INSUFFICIENT_HISTORY",
            **base,
        )

    closes = [bar.close for bar in regular]
    ema9_values = _ema(closes, 9)
    ema20_values = _ema(closes, 20)
    ema9 = ema9_values[-1]
    ema20 = ema20_values[-1]
    ema9_prior = next((v for v in reversed(ema9_values[:-3]) if v is not None), None)
    ema20_prior = next((v for v in reversed(ema20_values[:-3]) if v is not None), None)
    vwap = session_vwap(regular)
    session_return = (
        (current.close / regular[0].open - Decimal("1")) * Decimal("100")
        if regular[0].open > 0
        else Decimal("0")
    )
    extension = (
        (current.close / ema9 - Decimal("1")) * Decimal("100")
        if ema9 is not None and ema9 > 0
        else None
    )
    common: dict[str, Any] = {
        **base,
        "session_vwap": vwap,
        "session_return_pct": session_return,
        "ema9": ema9,
        "ema20": ema20,
        "ema9_extension_pct": extension,
    }
    trend_ok = (
        ema9 is not None
        and ema20 is not None
        and ema9_prior is not None
        and ema20_prior is not None
        and current.close > ema9 > ema20
        and ema9 > ema9_prior
        and ema20 > ema20_prior
        and vwap is not None
        and current.close > vwap
        and session_return >= TREND_MIN_SESSION_RETURN_PCT
    )
    if not trend_ok:
        return TrendContinuationShadowEvaluation(
            state="waiting_trend",
            reason_code="TREND_CONTINUATION_TREND_NOT_CONFIRMED",
            **common,
        )
    if extension is None or extension > TREND_MAX_EMA9_EXTENSION_PCT:
        return TrendContinuationShadowEvaluation(
            state="too_extended",
            reason_code="TREND_CONTINUATION_EMA_EXTENSION_TOO_HIGH",
            **common,
        )

    sampled = [
        bar for bar in resample_final_bars(regular, "3m") if bar.session == "regular"
    ]
    if len(sampled) < 6:
        return TrendContinuationShadowEvaluation(
            state="waiting_history",
            reason_code="TREND_CONTINUATION_3M_HISTORY_INSUFFICIENT",
            **common,
        )
    current_3m = sampled[-1]
    prior_high = max(bar.high for bar in sampled[-4:-1])
    breakout = current_3m.close > prior_high
    location = _close_location(current_3m)
    common.update(
        {
            "prior_breakout_high": prior_high,
            "breakout_confirmed": breakout,
            "close_location": location,
        }
    )
    if not breakout or location < TREND_MIN_CLOSE_LOCATION:
        return TrendContinuationShadowEvaluation(
            state="waiting_breakout",
            reason_code="TREND_CONTINUATION_WAITING_3M_BREAKOUT",
            **common,
        )

    prior_volume = sampled[-6:-1]
    average_volume = sum((bar.volume for bar in prior_volume), Decimal("0")) / Decimal(5)
    volume_ratio = current_3m.volume / average_volume if average_volume > 0 else None
    common["volume_ratio"] = volume_ratio
    if volume_ratio is None or volume_ratio < TREND_MIN_VOLUME_RATIO:
        return TrendContinuationShadowEvaluation(
            state="waiting_volume",
            reason_code="TREND_CONTINUATION_VOLUME_NOT_EXPANDING",
            **common,
        )

    stop_reference = min(bar.low for bar in sampled[-3:])
    stop_price = stop_reference * (
        Decimal("1") - Decimal(stop_buffer_bps) / Decimal("10000")
    )
    risk_pct = (
        (current.close - stop_price) / current.close * Decimal("100")
        if current.close > stop_price > 0
        else None
    )
    common.update(
        {"research_stop_price": stop_price, "research_risk_pct": risk_pct}
    )
    if risk_pct is None or risk_pct <= 0 or risk_pct > TREND_MAX_RISK_PCT:
        return TrendContinuationShadowEvaluation(
            state="risk_too_wide",
            reason_code="TREND_CONTINUATION_RISK_TOO_WIDE",
            **common,
        )
    return TrendContinuationShadowEvaluation(
        state="signal_ready",
        reason_code="TREND_CONTINUATION_BREAKOUT_SHADOW",
        **common,
    )


def _merge_causal_bars(
    primary: list[MarketBar] | tuple[MarketBar, ...],
    fallback: list[MarketBar] | tuple[MarketBar, ...],
    *,
    session_date: date,
    observed_at: datetime,
) -> list[MarketBar]:
    observed_utc = observed_at.astimezone(timezone.utc)
    merged: dict[datetime, MarketBar] = {}
    for bars in (fallback, primary):  # primary wins duplicate minutes
        for bar in bars:
            if (
                bar.is_final
                and bar.end_time <= observed_utc
                and bar.start_time.astimezone(_ET).date() == session_date
            ):
                merged[bar.start_time.astimezone(timezone.utc)] = bar
    return [merged[key] for key in sorted(merged)]


def _copy_response_with_bars(response: Any, bars: list[MarketBar]) -> Any:
    provenance = getattr(response, "provenance", None)
    updated_provenance = provenance
    if provenance is not None and hasattr(provenance, "model_copy"):
        updated_provenance = provenance.model_copy(
            update={
                "freshness_mode": "fallback",
                "fallback_reason": "provider_specific_1m_gap_filled_by_alpaca_iex",
                "history_complete": True,
            }
        )
    if hasattr(response, "model_copy"):
        updates: dict[str, Any] = {"bars": bars}
        if updated_provenance is not None:
            updates["provenance"] = updated_provenance
        return response.model_copy(update=updates)
    cloned = copy.copy(response)
    setattr(cloned, "bars", bars)
    return cloned


class _FullSessionMarketServiceProxy:
    def __init__(
        self,
        delegate: Any,
        *,
        session_date: date,
        observed_at: datetime,
        allow_shadow_fallback: bool,
    ) -> None:
        self._delegate = delegate
        self._session_date = session_date
        self._observed_at = observed_at.astimezone(timezone.utc)
        self._allow_shadow_fallback = allow_shadow_fallback

    def __getattr__(self, name: str):
        return getattr(self._delegate, name)

    def bars(
        self,
        instrument_id,
        interval,
        limit=500,
        binding_id=None,
        cancellation=None,
    ):
        requested_limit = int(limit or FULL_SESSION_1M_LIMIT)
        if interval == "1m":
            requested_limit = max(requested_limit, FULL_SESSION_1M_LIMIT)
        response = self._delegate.bars(
            instrument_id,
            interval,
            requested_limit,
            binding_id,
        )
        if interval != "1m" or not self._allow_shadow_fallback:
            return response

        from .strategy_evaluability import assess_bar_coverage

        primary = list(getattr(response, "bars", ()) or ())
        coverage = assess_bar_coverage(
            primary,
            session_date=self._session_date,
            observed_at=self._observed_at,
            provider="configured_history",
        )
        if coverage.ready:
            return response
        try:
            fallback = list(
                self._delegate.execution_indicator_bars(
                    instrument_id,
                    binding_id,
                    as_of=self._observed_at,
                )
            )
        except Exception:
            return response
        merged = _merge_causal_bars(
            primary,
            fallback,
            session_date=self._session_date,
            observed_at=self._observed_at,
        )
        merged_coverage = assess_bar_coverage(
            merged,
            session_date=self._session_date,
            observed_at=self._observed_at,
            provider="configured_history+alpaca_iex",
            fallback_provider="alpaca_iex",
        )
        return (
            _copy_response_with_bars(response, merged)
            if merged_coverage.ready
            else response
        )


def _current_session_bars(
    values: list[Any],
    *,
    session_date: date,
    observed_at: datetime,
) -> list[Any]:
    cutoff = observed_at.astimezone(timezone.utc)
    result = []
    for bar in values:
        start = getattr(bar, "start_time", None)
        end = getattr(bar, "end_time", None)
        if not isinstance(start, datetime) or not isinstance(end, datetime):
            continue
        if not bool(getattr(bar, "is_final", False)):
            continue
        if end.astimezone(timezone.utc) > cutoff:
            continue
        if start.astimezone(_ET).date() != session_date:
            continue
        result.append(bar)
    return sorted(result, key=lambda item: item.start_time)


def _merge_current_session_bars(primary: list[Any], fallback: list[Any]) -> list[Any]:
    merged: dict[datetime, Any] = {}
    for values in (fallback, primary):  # configured history wins exact duplicates
        for bar in values:
            merged[bar.start_time.astimezone(timezone.utc)] = bar
    return [merged[key] for key in sorted(merged)]


def _copy_response_with_current_session_bars(response: Any, bars: list[Any]) -> Any:
    if response is None:
        return SimpleNamespace(bars=bars, provenance=None)
    if hasattr(response, "model_copy"):
        return response.model_copy(update={"bars": bars})
    cloned = copy.copy(response)
    setattr(cloned, "bars", bars)
    return cloned


class _CurrentSessionMarketDataProxy:
    """Bound shadow history to today's causal prefix and recover missing bars."""

    def __init__(self, delegate: Any, *, session_date: date, observed_at: datetime) -> None:
        self._delegate = delegate
        self._session_date = session_date
        self._observed_at = observed_at.astimezone(timezone.utc)

    def __getattr__(self, name: str):
        return getattr(self._delegate, name)

    def _base_bars(self, instrument_id, interval, limit=500, binding_id=None, cancellation=None):
        if interval != "1m":
            return self._delegate.bars(
                instrument_id,
                interval,
                limit,
                binding_id,
                cancellation,
            )

        requested_limit = max(500, int(limit or 500))
        recovery = getattr(self._delegate, "recovered_bars", None)
        if callable(recovery) and self._observed_at.astimezone(_ET).time() >= time(9, 30):
            recovered = recovery(
                instrument_id,
                interval,
                requested_limit,
                binding_id,
                session_date=self._session_date,
                as_of=self._observed_at,
                cancellation=cancellation,
                knowledge_mode="live",
            )
            return _copy_response_with_current_session_bars(
                recovered.primary_response,
                list(recovered.bars),
            )

        response = None
        primary_error: Exception | None = None
        try:
            response = self._delegate.bars(
                instrument_id,
                interval,
                requested_limit,
                binding_id,
            )
            primary = _current_session_bars(
                list(getattr(response, "bars", ()) or ()),
                session_date=self._session_date,
                observed_at=self._observed_at,
            )
        except Exception as exc:
            primary_error = exc
            primary = []

        if self._observed_at.astimezone(_ET).time() < time(9, 30):
            if response is not None:
                return _copy_response_with_current_session_bars(response, primary)
            if primary_error is not None:
                raise primary_error
            return SimpleNamespace(bars=[])

        needs_fallback = primary_error is not None or not primary
        if not needs_fallback:
            try:
                from .strategy_evaluability import assess_bar_coverage

                needs_fallback = not assess_bar_coverage(
                    primary,
                    session_date=self._session_date,
                    observed_at=self._observed_at,
                    provider="shadow_primary_history",
                ).ready
            except Exception:
                needs_fallback = False

        fallback: list[Any] = []
        if needs_fallback:
            try:
                fallback = _current_session_bars(
                    list(
                        self._delegate.execution_indicator_bars(
                            instrument_id,
                            binding_id,
                            as_of=self._observed_at,
                        )
                    ),
                    session_date=self._session_date,
                    observed_at=self._observed_at,
                )
            except Exception:
                fallback = []

        merged = _merge_current_session_bars(primary, fallback)
        if merged:
            return _copy_response_with_current_session_bars(response, merged)
        if primary_error is not None:
            raise primary_error
        return _copy_response_with_current_session_bars(response, primary)

    def bars_for_requirement(
        self,
        instrument_id,
        interval,
        limit=500,
        binding_id=None,
        cancellation=None,
        *,
        requirement,
    ):
        from .strategy_shadow_recovery import recover_bars_for_requirement

        return recover_bars_for_requirement(
            self,
            instrument_id,
            interval,
            limit,
            binding_id,
            cancellation,
            requirement=requirement,
            base_bars=self._base_bars,
        )

    def bars(self, instrument_id, interval, limit=500, binding_id=None, cancellation=None):
        from .strategy_shadow_recovery import recover_shadow_bars

        return recover_shadow_bars(
            self,
            instrument_id,
            interval,
            limit,
            binding_id,
            cancellation,
            base_bars=self._base_bars,
        )


class _PartialCurrentSessionMarketDataProxy:
    """Causal current-session prefix adapter for deep-recovery research."""

    allow_partial_current_session = True

    def __init__(self, delegate: Any, *, session_date: date, observed_at: datetime) -> None:
        self._delegate = delegate
        self._inner = _CurrentSessionMarketDataProxy(
            delegate,
            session_date=session_date,
            observed_at=observed_at,
        )

    def __getattr__(self, name: str):
        return getattr(self._delegate, name)

    def bars(self, instrument_id, interval, limit=500, binding_id=None, cancellation=None):
        try:
            return self._inner._base_bars(
                instrument_id,
                interval,
                limit,
                binding_id,
                cancellation,
            )
        except Exception:
            return SimpleNamespace(bars=[], provenance=None)


def _trend_events_for_session(repository, strategy_id: str, session_date: date):
    if hasattr(repository, "recent_events"):
        return repository.recent_events(strategy_id, 10_000)
    if not hasattr(repository, "events_by_types_between"):
        return []
    start_et = datetime.combine(session_date, time(0, 0), tzinfo=_ET)
    end_et = start_et + timedelta(days=1)
    return repository.events_by_types_between(
        strategy_id,
        event_types=("trend_continuation_shadow",),
        start_time=start_et.astimezone(timezone.utc),
        end_time=end_et.astimezone(timezone.utc),
        limit=10_000,
    )


async def _collect_trend_signal(
    monitor,
    config,
    repository,
    market_service,
    universe,
    *,
    now: datetime,
) -> None:
    if not (
        _env_str("OMNIX_TRADING_TREND_CONTINUATION_SHADOW", "1")
        .strip()
        .lower()
        in {"1", "true", "yes", "on"}
        and config.enabled
        and config.mode == "shadow"
        and config.config.strategy_version == "2.0.0"
        and getattr(universe, "discovery_source", None) == "finviz"
    ):
        return

    from .strategy_evaluability import assess_bar_coverage
    from .strategy_shadow_execution import observe_shadow_execution
    from .trade_logging import trade_log

    recent = await asyncio.to_thread(
        _trend_events_for_session,
        repository,
        config.strategy_id,
        universe.session_date,
    )
    signaled = {
        event.instrument_id
        for event in recent
        if event.event_type == "trend_continuation_shadow"
        and event.observed_at.astimezone(_ET).date() == universe.session_date
    }
    for candidate in universe.candidates:
        if candidate.instrument_id in signaled:
            continue
        try:
            response = await asyncio.to_thread(
                market_service.bars,
                candidate.instrument_id,
                "1m",
                FULL_SESSION_1M_LIMIT,
                candidate.binding_id,
            )
            bars = list(response.bars)
            coverage = assess_bar_coverage(
                bars,
                session_date=universe.session_date,
                observed_at=now,
                provider="trend_continuation_shadow",
            )
            if not coverage.ready:
                continue
            evaluation = evaluate_trend_continuation_shadow(
                bars,
                entry_start_et=config.risk.entry_start_et,
                last_entry_et=config.risk.last_entry_et,
                stop_buffer_bps=config.config.stop_buffer_bps,
            )
        except Exception as exc:
            trade_log(
                "auto_trading",
                "trend_continuation_shadow_error",
                strategy_id=config.strategy_id,
                instrument_id=candidate.instrument_id,
                error_type=type(exc).__name__,
                detail=str(exc),
                execution_authority=False,
            )
            continue
        if not evaluation.signal_ready:
            continue

        execution = None
        execution_error = None
        risk_decision = {
            "allowed": False,
            "reason_codes": ["TREND_CONTINUATION_EXECUTION_EVIDENCE_ERROR"],
        }
        try:
            evidence = await asyncio.to_thread(
                observe_shadow_execution,
                market_service,
                instrument_id=candidate.instrument_id,
                binding_id=candidate.binding_id,
            )
            execution = evidence.execution
            reasons = []
            if execution.get("halted") is True:
                reasons.append("TREND_CONTINUATION_HALTED")
            if execution.get("execution_eligible") is not True:
                reasons.append("TREND_CONTINUATION_EXECUTION_INELIGIBLE")
            spread = _decimal(execution.get("spread_bps"))
            if spread is None:
                reasons.append("TREND_CONTINUATION_SPREAD_MISSING")
            elif spread > config.risk.max_spread_bps:
                reasons.append("TREND_CONTINUATION_SPREAD_TOO_WIDE")
            risk_decision = {
                "allowed": not reasons,
                "reason_codes": list(dict.fromkeys(reasons)),
            }
        except Exception as exc:
            execution_error = f"{type(exc).__name__}: {exc}"

        observed_at = evaluation.observed_at or now
        persisted = await monitor._event(
            repository,
            config,
            instrument_id=candidate.instrument_id,
            event_type="trend_continuation_shadow",
            state="signal_ready",
            reason_code="TREND_CONTINUATION_SHADOW_SIGNAL",
            observed_at=observed_at,
            payload={
                "setup_id": evaluation.setup_id,
                "rule_version": evaluation.rule_version,
                "evaluation": evaluation.model_dump(mode="json"),
                "universe_id": universe.universe_id,
                "bar_coverage": coverage.model_dump(mode="json"),
                "execution": execution,
                "execution_error": execution_error,
                "risk_decision": risk_decision,
                "research_only": True,
                "execution_authority": False,
            },
        )
        if persisted:
            trade_log(
                "auto_trading",
                "trend_continuation_shadow_signal",
                strategy_id=config.strategy_id,
                instrument_id=candidate.instrument_id,
                observed_at=observed_at,
                session_return_pct=evaluation.session_return_pct,
                volume_ratio=evaluation.volume_ratio,
                ema9_extension_pct=evaluation.ema9_extension_pct,
                research_risk_pct=evaluation.research_risk_pct,
                execution_eligible=risk_decision["allowed"],
                execution_authority=False,
            )
