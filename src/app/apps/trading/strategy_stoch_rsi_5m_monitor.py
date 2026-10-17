from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from .feature_qualification import FeatureRequirement, qualify_bar_feature
from .market_data_recovery import latest_clean_bars
from .service import TradingMarketDataService
from .strategy_repository import (
    TradingStrategyConfigDocument,
    TradingStrategyRepository,
)
from .strategy_stoch_rsi_5m import evaluate_stoch_rsi_5m
from .strategy_stoch_rsi_5m_early_single import evaluate_stoch_rsi_5m_early_single
from .strategies.models import StochRsi5mConfig
from .trade_logging import trade_log


async def record_stoch_rsi_5m_candidates(
    record_event: Callable[..., Awaitable[bool]],
    current_run_id: str | None,
    config: TradingStrategyConfigDocument,
    strategy_repository: TradingStrategyRepository,
    market_service: TradingMarketDataService,
    universe,
    *,
    observed_at: datetime | None = None,
) -> None:
    """Record deterministic 5m Stoch RSI evidence without creating orders."""

    stoch_config = config.config
    if not isinstance(stoch_config, StochRsi5mConfig):
        raise TypeError("stoch-rsi-5min strategy requires StochRsi5mConfig")
    evaluate = (
        evaluate_stoch_rsi_5m_early_single
        if stoch_config.trade_selection == "early_single"
        else evaluate_stoch_rsi_5m
    )
    evaluation_clock = (
        observed_at.astimezone(timezone.utc)
        if observed_at is not None
        else datetime.now(timezone.utc)
    )
    for candidate in universe.candidates:
        candidate_observed_at = evaluation_clock
        # Premarket/universe enrichment gaps are not a Stoch-RSI dependency.
        # This arm is qualified from the finalized regular-session 5m event
        # sequence it actually consumes.
        coverage_certificate = None

        try:
            recovery_method = getattr(market_service, "recovered_bars", None)
            if callable(recovery_method):
                recovered = await asyncio.to_thread(
                    recovery_method,
                    candidate.instrument_id,
                    "5m",
                    500,
                    candidate.binding_id,
                    session_date=universe.session_date,
                    as_of=candidate_observed_at,
                )
                response_bars = list(recovered.bars)
                bar_provenance = {
                    "resolved_binding": recovered.report.resolved_binding,
                    "dataset_fingerprint": recovered.report.dataset_fingerprint,
                    "as_of": (
                        response_bars[-1].end_time
                        if response_bars
                        else recovered.report.as_of
                    ),
                    "bar_count": len(response_bars),
                    "source_providers": list(recovered.report.source_providers),
                    "recovered_bar_count": recovered.report.recovered_bar_count,
                }
            else:
                response = await asyncio.to_thread(
                    market_service.bars,
                    candidate.instrument_id,
                    "5m",
                    500,
                    candidate.binding_id,
                )
                response_bars = list(response.bars)
                bar_provenance = {
                    "resolved_binding": response.provenance.resolved_binding,
                    "dataset_fingerprint": response.provenance.dataset_fingerprint,
                    "as_of": response.provenance.as_of,
                    "bar_count": len(response_bars),
                }
            coverage_certificate = qualify_bar_feature(
                response_bars,
                FeatureRequirement(
                    requirement_id="stoch-rsi-5m-recursive-v3",
                    feature_name="stoch_rsi_5m_recursive_state",
                    interval="5m",
                    dependency_class="RECURSIVE",
                    allow_approximate_reseed=True,
                    reseed_after_clean_bars=30,
                    # One missing bucket is stepped over, as the evaluator does.
                    bridge_isolated_gaps=True,
                ),
                instrument_id=candidate.instrument_id,
                session_date=universe.session_date,
                observed_at=candidate_observed_at,
            )
            if coverage_certificate.status == "INVALID":
                await record_event(
                    strategy_repository,
                    config,
                    instrument_id=candidate.instrument_id,
                    event_type="stoch_rsi_5m",
                    state="data_gap",
                    reason_code="STOCH_RSI_5M_FEATURE_DATA_INCOMPLETE",
                    observed_at=candidate_observed_at,
                    payload={
                        "universe_id": universe.universe_id,
                        "coverage_certificate": coverage_certificate.model_dump(
                            mode="json"
                        ),
                        "candidate_market_data_complete": getattr(
                            candidate, "market_data_complete", None
                        ),
                        "candidate_data_quality_flags": list(
                            getattr(candidate, "data_quality_flags", ())
                        ),
                        "research_only": True,
                        "execution_authority": False,
                    },
                )
                continue
            evaluation_bars = response_bars
            if coverage_certificate.status == "DEGRADED":
                evaluation_bars = latest_clean_bars(
                    response_bars,
                    session_date=universe.session_date,
                    interval="5m",
                    as_of=candidate_observed_at,
                    bridge_single_missing=True,
                )
            snapshot = evaluate(evaluation_bars, stoch_config)
            event_observed_at = snapshot.as_of or candidate_observed_at
            payload = {
                "universe_id": universe.universe_id,
                "universe_source": getattr(universe, "discovery_source", None),
                "strategy_version": config.strategy_version,
                "mode": "shadow",
                "trade_selection": stoch_config.trade_selection,
                "snapshot": snapshot.model_dump(mode="json"),
                "coverage_certificate": (
                    coverage_certificate.model_dump(mode="json")
                    if coverage_certificate is not None
                    else None
                ),
                "five_minute_ema_period": 5,
                "entry_policy": {
                    "oversold_arm_threshold": str(stoch_config.oversold_threshold),
                    "recovery_confirmation_threshold": str(
                        stoch_config.recovery_threshold
                    ),
                    "entry_above_ema_period": 5,
                },
                "exit_policy": {
                    "close_below_ema_period": 5,
                    "stoch_rsi_cross_down_below": "80",
                    "stoch_rsi_overbought_cross_down_above": str(
                        stoch_config.overbought_threshold
                    ),
                    "allow_sequential_trades_per_symbol": (
                        stoch_config.trade_selection == "sequential"
                    ),
                },
                "bar_provenance": bar_provenance,
                "research_only": True,
                "execution_authority": False,
            }
            await record_event(
                strategy_repository,
                config,
                instrument_id=candidate.instrument_id,
                event_type="stoch_rsi_5m",
                state=snapshot.state,
                reason_code=snapshot.reason_code,
                observed_at=event_observed_at,
                payload=payload,
            )
        except Exception as exc:
            await record_event(
                strategy_repository,
                config,
                instrument_id=candidate.instrument_id,
                event_type="stoch_rsi_5m",
                state="waiting_data",
                reason_code="STOCH_RSI_5M_MARKET_DATA_UNAVAILABLE",
                observed_at=candidate_observed_at,
                payload={
                    "universe_id": universe.universe_id,
                    "error_type": type(exc).__name__,
                    "detail": str(exc),
                    "research_only": True,
                    "execution_authority": False,
                },
            )
            trade_log(
                "auto_trading",
                "stoch_rsi_5m_evaluation_error",
                run_id=current_run_id,
                strategy_id=config.strategy_id,
                instrument_id=candidate.instrument_id,
                error_type=type(exc).__name__,
                detail=str(exc),
                research_only=True,
                execution_authority=False,
            )
