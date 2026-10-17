"""A strategy written by the recipe in docs/trading/STRATEGY_RECIPE.md (test fixture)."""
from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.apps.trading.strategies.contract import DataRequirements, Proposal, StrategyContext


class FakeBreakoutConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_version: str = "1.0.0"
    breakout_price: Decimal = Field(default=Decimal("10"), gt=0)


class FakeBreakoutStrategy:
    """Propose an entry when the last closed bar finishes above a fixed level."""

    kind = "fake_breakout_v1"
    version = "1.0.0"
    config_model = FakeBreakoutConfig
    data_requirements = DataRequirements(interval="1m", lookback_bars=30, instruments=("equity:NASDAQ:FAKE",))
    allowed_modes = ("off", "shadow")

    def evaluate(self, ctx: StrategyContext) -> list[Proposal]:
        proposals = []
        for instrument_id, bars in sorted(ctx.bars.items()):
            if bars and bars[-1].close > ctx.config.breakout_price:
                proposals.append(
                    Proposal(
                        instrument_id=instrument_id,
                        reason_code="FAKE_BREAKOUT",
                        entry_price=bars[-1].close,
                        stop_price=bars[-1].low,
                    )
                )
        return proposals
