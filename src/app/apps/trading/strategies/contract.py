"""The trading strategy contract (WP-8.3).

A strategy is a pure, deterministic function from market data to proposals.
It declares what it is (``kind``, ``version``), how it is configured
(``config_model``), which modes it may run in, and which data it needs; the
``StrategyRunner`` fetches that data, calls ``evaluate`` and records the
proposals. A strategy never places orders, calls providers or writes state.

Adding a strategy: implement this protocol in one module and add one entry to
``app/apps/trading/strategies/registrations.py`` (see docs/trading/STRATEGY_RECIPE.md).
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from ..models import MarketBar
from .models import StrategyBarInterval, StrategyMode, StrategyRiskProfile


class DataRequirements(BaseModel):
    """The bars a strategy needs for each instrument it evaluates."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    interval: StrategyBarInterval = "1m"
    lookback_bars: int = Field(default=390, ge=1, le=5_000)
    # Instruments evaluated when the configuration has no active universe.
    instruments: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class StrategyContext:
    """Everything one evaluation may read; nothing else is available to it."""

    strategy_id: str
    config: BaseModel
    risk: StrategyRiskProfile
    observed_at: datetime
    bars: Mapping[str, tuple[MarketBar, ...]] = field(default_factory=dict)


class Proposal(BaseModel):
    """A proposed entry. Proposals are evidence; the order gateway owns authority."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str = Field(min_length=1)
    side: Literal["buy"] = "buy"
    reason_code: str = Field(min_length=1, max_length=120)
    entry_price: Decimal | None = Field(default=None, gt=0)
    stop_price: Decimal | None = Field(default=None, gt=0)
    target_price: Decimal | None = Field(default=None, gt=0)


@runtime_checkable
class Strategy(Protocol):
    kind: str
    version: str
    config_model: type[BaseModel]
    data_requirements: DataRequirements
    allowed_modes: tuple[StrategyMode, ...]

    def evaluate(self, ctx: StrategyContext) -> list[Proposal]: ...
