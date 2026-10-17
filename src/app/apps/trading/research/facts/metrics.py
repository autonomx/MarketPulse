from __future__ import annotations

from decimal import Decimal

from ..contracts import SupplyFact, SupplyMetrics
from typing import Any, cast

_ACTIVE = {"active", "exercisable"}


def _pct(value: Decimal | None, base: Decimal | None) -> Decimal | None:
    if value is None or base is None or base <= 0:
        return None
    return value / base * Decimal("100")


def derive_supply_metrics(
    facts: list[SupplyFact] | tuple[SupplyFact, ...],
    *,
    float_shares: Decimal | None = None,
    market_cap: Decimal | None = None,
    market_price: Decimal | None = None,
) -> SupplyMetrics:
    resolved = [fact for fact in facts if fact.resolution_status == "resolved"]
    unresolved = any(fact.resolution_status != "resolved" for fact in facts)
    active = [fact for fact in resolved if fact.status in _ACTIVE]
    share_total = sum((fact.shares or Decimal("0") for fact in active), Decimal("0"))
    atm_capacity = sum((fact.remaining_capacity_usd or Decimal("0") for fact in active if fact.supply_type == "atm"), Decimal("0"))
    resale_shares = sum((fact.shares or Decimal("0") for fact in active if fact.supply_type == "resale_registration" and fact.registration_status == "effective"), Decimal("0"))
    itm_warrant_shares = sum(
        (fact.shares or Decimal("0") for fact in active if fact.supply_type == "warrant" and fact.strike_price is not None and market_price is not None and fact.strike_price <= market_price),
        Decimal("0"),
    )
    immediate = bool(
        any(fact.supply_type == "atm" for fact in active)
        or resale_shares > 0
        or itm_warrant_shares > 0
        or any(fact.supply_type in {"registered_offering", "equity_line"} for fact in active)
    ) if resolved else None
    status = "risk_found" if immediate else "unresolved" if unresolved or not facts else "clear"
    return SupplyMetrics(
        potential_dilution_pct_float=_pct(share_total, float_shares),
        remaining_atm_pct_market_cap=_pct(atm_capacity, market_cap),
        in_the_money_warrant_pct_float=_pct(itm_warrant_shares, float_shares),
        registered_resale_pct_float=_pct(resale_shares, float_shares),
        immediate_supply_risk=immediate,
        supply_resolution_status=cast(Any, status),
    )
