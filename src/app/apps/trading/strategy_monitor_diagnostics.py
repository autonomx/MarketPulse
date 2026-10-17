"""Diagnostic v2 candidate events of the strategy monitor (moved out of the class)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

from .service import TradingMarketDataService
from .strategies.failed_selloff_v2 import evaluate_gap_pullback_v2
from .strategy_evaluability import (
    candidate_morning_evidence_eligible,
    resolve_causal_equity_bars,
)

# The monitor imports this module lazily, so importing it here makes no cycle.
from .strategy_monitor import (
    StrategyRunHost,
)
from .strategy_repository import (
    TradingStrategyConfigDocument,
    TradingStrategyRepository,
)
from .strategy_timeframes import resample_final_bars


async def record_diagnostic_v2_candidates(
    monitor: StrategyRunHost,
    config: TradingStrategyConfigDocument,
    strategy_repository: TradingStrategyRepository,
    market_service: TradingMarketDataService,
    universe,
) -> None:
    if config.config.strategy_version != "2.0.0" or config.mode != "shadow":
        return

    for candidate in universe.candidates:
        morning_ok, morning_reasons = candidate_morning_evidence_eligible(
            candidate,
            config.config,
        )
        if morning_ok:
            continue
        now = datetime.now(timezone.utc)
        bars, coverage, primary_error = await asyncio.to_thread(
            resolve_causal_equity_bars,
            market_service,
            candidate,
            session_date=universe.session_date,
            observed_at=now,
            allow_shadow_fallback=True,
        )
        if not coverage.ready:
            continue
        diagnostic_candidate = candidate.model_copy(
            update={
                "market_data_complete": True,
                "data_quality_flags": (),
                "premarket_dollar_volume": max(
                    candidate.premarket_dollar_volume,
                    config.config.minimum_premarket_dollar_volume,
                ),
                "tod_rvol": max(
                    candidate.tod_rvol or Decimal("0"),
                    config.config.minimum_tod_rvol,
                ),
                "spread_bps": Decimal("0"),
            }
        )
        diagnostic_config = config.config.model_copy(
            update={
                "minimum_gap_pct": min(
                    config.config.minimum_gap_pct,
                    candidate.gap_pct,
                ),
                "minimum_price": min(
                    config.config.minimum_price,
                    candidate.premarket_price,
                ),
                "maximum_price": max(
                    config.config.maximum_price,
                    candidate.premarket_price,
                ),
                "minimum_premarket_dollar_volume": Decimal("0"),
                "minimum_tod_rvol": Decimal("0"),
                "maximum_spread_bps": max(
                    config.config.maximum_spread_bps,
                    Decimal("100000"),
                ),
                "require_catalyst_evidence": False,
                "reject_dilution_flags": (),
                "float_preference_mode": "ignore",
            }
        )
        structure = resample_final_bars(
            bars,
            diagnostic_config.structure_interval,
        )
        if not structure:
            continue
        result = evaluate_gap_pullback_v2(
            diagnostic_candidate,
            structure,
            diagnostic_config,
        )
        result = result.model_copy(
            update={
                "features": result.features.model_copy(
                    update={
                        "spread_bps": candidate.spread_bps,
                        "tod_rvol": candidate.tod_rvol,
                    }
                )
            }
        )
        observed_at = structure[-1].end_time
        persisted = await monitor._event(
            strategy_repository,
            config,
            instrument_id=candidate.instrument_id,
            event_type="diagnostic_state",
            state=result.state,
            reason_code=result.reason_code,
            observed_at=observed_at,
            payload={
                "universe_id": universe.universe_id,
                "qualification_eligible": False,
                "morning_evidence_reason_codes": list(morning_reasons),
                "bar_coverage": coverage.model_dump(mode="json"),
                "primary_bar_error": primary_error,
                "features": result.features.model_dump(mode="json"),
                "transitions": list(result.transitions),
                "signal": result.signal.model_dump(mode="json")
                if result.signal
                else None,
                "research_only": True,
                "execution_authority": False,
            },
        )
        if persisted:
            monitor.diagnostic_evaluation_count = (
                getattr(monitor, "diagnostic_evaluation_count", 0) + 1
            )
