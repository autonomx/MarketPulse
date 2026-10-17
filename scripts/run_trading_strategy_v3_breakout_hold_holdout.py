from __future__ import annotations

"""Dependency-free compatibility helpers for the frozen post-V2 holdout protocol.

The historical one-shot runner was removed with its research-only dependency chain,
but the protocol selector tests intentionally remain as a frozen contract.  Keep the
pure selection/status functions available without resurrecting obsolete runners or
provider/backtest dependencies.
"""

from decimal import Decimal


def _decimal_metric(row: dict[str, object], key: str) -> Decimal | None:
    raw = row.get(key)
    return Decimal(str(raw)) if raw is not None else None


def development_eligible(row: dict[str, object], minimum_trades: int) -> bool:
    expectancy = _decimal_metric(row, "expectancy_r")
    lcb = _decimal_metric(row, "one_sided_90_lcb_r")
    return (
        int(row.get("trade_count") or 0) >= minimum_trades
        and expectancy is not None
        and expectancy > 0
        and lcb is not None
        and lcb > 0
    )


def select_development_champion(
    rows: list[dict[str, object]],
    minimum_trades: int,
) -> dict[str, object] | None:
    eligible = [row for row in rows if development_eligible(row, minimum_trades)]
    if not eligible:
        return None

    def rank(row: dict[str, object]):
        lcb = _decimal_metric(row, "one_sided_90_lcb_r") or Decimal("-Infinity")
        expectancy = _decimal_metric(row, "expectancy_r") or Decimal("-Infinity")
        threshold = Decimal(str(row["minimum_breakout_volume_ratio"]))
        return (lcb, int(row["trade_count"]), expectancy, -threshold)

    return max(eligible, key=rank)


def _status(row: dict[str, object], minimum_trades: int) -> str:
    trades = int(row.get("trade_count") or 0)
    expectancy = _decimal_metric(row, "expectancy_r")
    lcb = _decimal_metric(row, "one_sided_90_lcb_r")
    if trades < minimum_trades:
        return "inconclusive_low_trade_count"
    if expectancy is not None and expectancy > 0 and lcb is not None and lcb > 0:
        return "pass"
    return "fail"


def main() -> int:
    raise SystemExit(
        "The historical one-shot V3 holdout runner is archived; only its frozen "
        "dependency-free selection/status contract remains in the active tree."
    )


if __name__ == "__main__":
    raise SystemExit(main())
