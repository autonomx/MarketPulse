"""Daily budget for trading research model calls (shared across processes).

Trading research analyzers call a model on every few strategy cycles; on busy
days they used the whole provider quota (on a subscription CLI: the user's own
session limit). The budget counts calls per Eastern-time day in the durable
evidence session counters, so every process and restart share one count, and
lets each priority tier spend only up to its share of the day:

* ``high`` (intraday LLM, read by live strategy passes): the whole budget;
* ``normal`` (AI shadow research arms, and any analyzer not listed): until 60%
  of the day's calls are used.

A call over its tier's share raises ``TradingAIBudgetExhausted`` before any
provider request. ``OMNIX_TRADING_AI_DAILY_CALL_BUDGET=0`` turns the budget off.
Research only: nothing here grants execution authority.
"""
from __future__ import annotations

import math
from collections.abc import Callable
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Literal

from app.config.env import environment

from .evidence_storage import default_evidence_backend
from .us_equity_calendar import EASTERN

Tier = Literal["high", "normal"]

AI_BUDGET_COUNTER_PROVIDER = "trading_ai_budget"
DEFAULT_DAILY_CALL_BUDGET = 400
TIER_SHARE: dict[Tier, float] = {"high": 1.0, "normal": 0.6}
CONTRACT_TIERS: dict[str, Tier] = {
    "trading.intraday_llm.batch": "high",
    "trading.ai_shadow_v2.batch": "normal",
    "trading.ai_shadow_v2.catalyst_semantics": "normal",
    "trading.ai_shadow_v3.batch": "normal",
}


class TradingAIBudgetExhausted(RuntimeError):
    """The day's share for this call's priority tier is used up; no provider call was made."""

    def __init__(self, contract_id: str, tier: Tier, used: int, limit: int) -> None:
        super().__init__(
            f"trading_ai_budget_exhausted:{contract_id}:tier={tier};used={used};limit={limit}"
        )
        self.contract_id = contract_id
        self.tier = tier
        self.used = used
        self.limit = limit


def daily_call_budget() -> int:
    # The literal default keeps the generated configuration registry accurate.
    raw = environment().get("OMNIX_TRADING_AI_DAILY_CALL_BUDGET", "400").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_DAILY_CALL_BUDGET


def contract_tier(contract_id: str) -> Tier:
    return CONTRACT_TIERS.get(contract_id, "normal")


class TradingAIBudget:
    def __init__(
        self,
        *,
        backend: Any | None = None,
        daily_calls: int | Callable[[], int] = daily_call_budget,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._backend = backend
        self._daily_calls = daily_calls
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        # Serializes check-and-count within a process; across processes the
        # overshoot is at most one call per concurrent caller.
        self._lock = Lock()

    @property
    def backend(self) -> Any:
        if self._backend is None:
            self._backend = default_evidence_backend()
        return self._backend

    def _limit(self) -> int:
        return self._daily_calls() if callable(self._daily_calls) else int(self._daily_calls)

    def _day(self):
        return self._clock().astimezone(EASTERN).date()

    def acquire(self, contract_id: str) -> None:
        """Count one call for ``contract_id`` or raise if its tier's share is used up."""

        daily = self._limit()
        if daily <= 0:
            return
        tier = contract_tier(contract_id)
        limit = math.floor(daily * TIER_SHARE[tier])
        day = self._day()
        with self._lock:
            counters, _ = self.backend.session_counters(AI_BUDGET_COUNTER_PROVIDER, day)
            used = int(counters.get("calls", 0))
            if used >= limit:
                self.backend.add_session_counters(AI_BUDGET_COUNTER_PROVIDER, day, {f"denied:{tier}": 1})
                raise TradingAIBudgetExhausted(contract_id, tier, used, limit)
            self.backend.add_session_counters(
                AI_BUDGET_COUNTER_PROVIDER, day, {"calls": 1, f"calls:{tier}": 1, f"calls:{contract_id}": 1}
            )

    def status(self) -> dict[str, Any]:
        daily = self._limit()
        counters, updated = self.backend.session_counters(AI_BUDGET_COUNTER_PROVIDER, self._day())
        return {
            "daily_call_budget": daily,
            "enabled": daily > 0,
            "tier_limits": {tier: math.floor(daily * share) for tier, share in TIER_SHARE.items()},
            "counters": counters,
            "updated_at": updated.isoformat() if updated is not None else None,
        }


_default_budget: TradingAIBudget | None = None
_default_lock = Lock()


def default_trading_ai_budget() -> TradingAIBudget:
    global _default_budget
    if _default_budget is None:
        with _default_lock:
            if _default_budget is None:
                _default_budget = TradingAIBudget()
    return _default_budget


__all__ = [
    "AI_BUDGET_COUNTER_PROVIDER",
    "CONTRACT_TIERS",
    "DEFAULT_DAILY_CALL_BUDGET",
    "TIER_SHARE",
    "TradingAIBudget",
    "TradingAIBudgetExhausted",
    "contract_tier",
    "daily_call_budget",
    "default_trading_ai_budget",
]
