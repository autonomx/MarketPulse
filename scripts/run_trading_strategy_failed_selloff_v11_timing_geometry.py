from __future__ import annotations

"""V11 research: causal timing geometry on the best V10 direct structure.

V10 showed that the raw L1 -> B1 -> higher-L2 -> B1/VWAP direct breakout can
trade profitably in the latest revealed regime, while the one-bar hold and V8's
post-signal AND gates are harmful.  A row-level diagnostic across all three
revealed regimes then showed the same causal separation repeatedly: winners
formed L1->B1 more slowly and resolved L2->breakout more quickly than losers.

V11 tests only those two timing dimensions on the exact V10 direct structure.
No gap, breakout-volume, breakout-body, or post-signal hold filters are added.
The frozen 2026-04-29..2026-05-22 external holdout is never loaded.
Production strategy behavior remains unchanged.
"""

import argparse
import csv
import itertools
import json
from dataclasses import dataclass, replace
from datetime import date, time
from decimal import Decimal
from pathlib import Path

import app.apps.trading.strategy_backtest as _bt
import scripts.run_trading_strategy_failed_selloff_v4_management as _v4
import scripts.run_trading_strategy_failed_selloff_v7_higher_low as _v7
import scripts.run_trading_strategy_failed_selloff_v8_orderly_base as _v8
from scripts.run_trading_strategy_backtest import strict_v11_strategy
from scripts.run_trading_strategy_liquidity_sweep import (
    _cache_namespace,
    _dataset_cache_path,
    _load_cached_dataset,
    _trading_dates,
)


_ORIGINAL_EVALUATE = _v7._higher_low_evaluate
_ACTIVE_GATES = None


@dataclass(frozen=True)
class TimingGates:
    minimum_l1_to_b1_minutes: int
    maximum_l2_to_signal_minutes: int

    @property
    def gate_id(self) -> str:
        return f"base{self.minimum_l1_to_b1_minutes}-resolve{self.maximum_l2_to_signal_minutes}"


def _base_variant():
    template = next(
        v for v in _v7._grid()
        if v.minimum_premarket_dollar_volume == Decimal("250000")
        and v.higher_low_buffer_pct == Decimal("0.5")
        and v.minimum_breakout_volume_ratio == Decimal("0.8")
        and v.require_breakout_hold
    )
    return replace(
        template,
        minimum_premarket_dollar_volume=Decimal("100000"),
        higher_low_buffer_pct=Decimal("0.5"),
        minimum_breakout_volume_ratio=Decimal("0"),
        require_breakout_hold=False,
        last_entry_et=time(11, 30),
    )


def _find_index(regular, value, *, low=False, start=0):
    for idx in range(start, len(regular)):
        if (regular[idx].low if low else regular[idx].high) == value:
            return idx
    return None


def _reject(candidate, result, regular, reason):
    transitions = list(result.transitions)
    if transitions and transitions[-1] == "entry_ready":
        transitions.pop()
    transitions.append("rejected")
    return _v7._result(candidate, "rejected", reason, transitions, result.features, regular)


def _timing_evaluate(candidate, bars, config=None):
    result = _ORIGINAL_EVALUATE(candidate, bars, config)
    gates = _ACTIVE_GATES
    if gates is None or result.signal is None or result.state != "entry_ready":
        return result

    regular = _v7._v2._regular_bars(list(bars))
    f = result.features
    l1_idx = _find_index(regular, f.l1, low=True)
    b1_idx = _find_index(regular, f.b1, low=False, start=(l1_idx + 1 if l1_idx is not None else 0))
    l2_idx = _find_index(regular, f.l2, low=True, start=(b1_idx + 1 if b1_idx is not None else 0))
    if l1_idx is None or b1_idx is None or l2_idx is None:
        return _reject(candidate, result, regular, "V11_STRUCTURE_INDEX_MISSING")

    l1_to_b1 = b1_idx - l1_idx
    l2_to_signal = len(regular) - 1 - l2_idx
    if l1_to_b1 < gates.minimum_l1_to_b1_minutes:
        return _reject(candidate, result, regular, "V11_BASE_TOO_FAST")
    if l2_to_signal > gates.maximum_l2_to_signal_minutes:
        return _reject(candidate, result, regular, "V11_RESOLUTION_TOO_SLOW")
    return result


def _variant_id(gates):
    return f"v11-liq100000-hl0.5-bvol0-direct-last1130-{gates.gate_id}"


def _grid():
    # Include nearby values around the row-level cross-regime separation rather
    # than selecting a single median-derived threshold.
    for base_minutes, resolve_minutes in itertools.product(
        (3, 4, 5, 6, 7),
        (3, 4, 6, 8, 10, 12),
    ):
        yield TimingGates(base_minutes, resolve_minutes)


def _load_block(cache, start, end):
    rows = []
    for session_date in _trading_dates(start, end):
        path = _dataset_cache_path(cache, session_date)
        if not path.exists():
            raise FileNotFoundError(f"missing revealed dataset: {path}")
        rows.append(_load_cached_dataset(path, session_date))
    return rows


def _decimal(value, fallback="-999"):
    if value is None:
        return Decimal(fallback)
    return Decimal(str(value))


def _worst_r(row):
    trades = row.get("trades") or []
    if not trades:
        return Decimal("-999")
    return min(Decimal(str(trade["r_multiple"])) for trade in trades)


def _positive(row, minimum_trades):
    return (
        int(row["trade_count"]) >= minimum_trades
        and _decimal(row.get("expectancy_r")) > 0
        and _decimal(row.get("pnl"), "0") > 0
    )


def _run(base, gates, datasets, *, initial_cash, spread):
    global _ACTIVE_GATES
    _ACTIVE_GATES = gates
    _v7._ACTIVE_VARIANT = base
    _v4._ACTIVE_MANAGEMENT = base
    row = _v4._BASE_RUN_VARIANT(
        base,
        datasets,
        initial_cash=initial_cash,
        spread=spread,
        max_hold_minutes=base.max_hold_minutes,
    )
    row["variant_id"] = _variant_id(gates)
    return row


def parse_args():
    parser = argparse.ArgumentParser(description="Run V11 causal timing-geometry research.")
    parser.add_argument("--dataset-cache-dir", default=".cache/trading-liquidity-datasets")
    parser.add_argument("--output-dir", default="artifacts/failed-selloff-v11-timing-geometry")
    parser.add_argument("--initial-cash", default="100000")
    parser.add_argument("--assumed-spread-bps", default="40")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    spread = Decimal(args.assumed_spread_bps)
    initial_cash = Decimal(args.initial_cash)
    namespace, _ = _cache_namespace(
        strict_v11_strategy(minimum_premarket_dollar_volume=Decimal("100000")), spread
    )
    cache = Path(args.dataset_cache_dir) / namespace

    blocks = [
        _load_block(cache, date(2026, 5, 26), date(2026, 6, 18)),
        _load_block(cache, date(2026, 6, 26), date(2026, 7, 23)),
        _load_block(cache, date(2026, 7, 24), date(2026, 8, 21)),
    ]
    all_datasets = [dataset for block in blocks for dataset in block]
    if len(all_datasets) != 58:
        raise ValueError(f"expected 58 revealed sessions, got {len(all_datasets)}")

    base = _base_variant()
    _v7._result = _v8._normalized_result
    _bt.evaluate_gap_pullback = _timing_evaluate
    _bt._find_trade = _v4._managed_find_trade

    variants = list(_grid())
    bundles = []
    print(f"V11 timing grid: {len(variants)} variants")
    for index, gates in enumerate(variants, 1):
        rows = [
            _run(base, gates, block, initial_cash=initial_cash, spread=spread)
            for block in blocks
        ]
        bundle = {"gates": gates, "blocks": rows, "full": None}
        bundles.append(bundle)
        if index % 5 == 0 or index == len(variants):
            print(f"  progress {index}/{len(variants)}")

    cross_regime = []
    for bundle in bundles:
        b1, b2, b3 = bundle["blocks"]
        if _positive(b1, 2) and _positive(b2, 1) and _positive(b3, 2):
            cross_regime.append(bundle)
    print(f"V11 cross-regime survivors: {len(cross_regime)}")

    final = []
    for bundle in cross_regime:
        full = _run(base, bundle["gates"], all_datasets, initial_cash=initial_cash, spread=spread)
        bundle["full"] = full
        if (
            _positive(full, 8)
            and _worst_r(full) > Decimal("-1.75")
            and _decimal(full.get("max_drawdown_pct"), "999") < Decimal("2")
        ):
            final.append(bundle)

    final.sort(
        key=lambda bundle: (
            min(_decimal(row["expectancy_r"]) for row in bundle["blocks"]),
            _decimal(bundle["full"]["expectancy_r"]),
            _decimal(bundle["full"]["pnl"], "0"),
            _worst_r(bundle["full"]),
        ),
        reverse=True,
    )

    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    serial = []
    for bundle in bundles:
        gates = bundle["gates"]
        serial.append({
            "variant_id": _variant_id(gates),
            "parameters": {
                "minimum_l1_to_b1_minutes": gates.minimum_l1_to_b1_minutes,
                "maximum_l2_to_signal_minutes": gates.maximum_l2_to_signal_minutes,
                "minimum_premarket_dollar_volume": "100000",
                "higher_low_buffer_pct": "0.5",
                "minimum_breakout_volume_ratio": "0",
                "require_breakout_hold": False,
                "last_entry_et": "11:30:00",
            },
            "blocks": bundle["blocks"],
            "full": bundle["full"],
        })
    (output / "results.json").write_text(json.dumps(serial, indent=2, default=str) + "\n", encoding="utf-8")

    with (output / "comparison.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = [
            "variant_id", "base_min", "resolve_max",
            "r1_trades", "r1_exp", "r1_pnl",
            "r2_trades", "r2_exp", "r2_pnl",
            "r3_trades", "r3_exp", "r3_pnl",
            "full_trades", "full_exp", "full_pnl", "full_dd", "worst_r",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in serial:
            row = {
                "variant_id": item["variant_id"],
                "base_min": item["parameters"]["minimum_l1_to_b1_minutes"],
                "resolve_max": item["parameters"]["maximum_l2_to_signal_minutes"],
            }
            for idx, prefix in enumerate(("r1", "r2", "r3")):
                result = item["blocks"][idx]
                row[f"{prefix}_trades"] = result["trade_count"]
                row[f"{prefix}_exp"] = result["expectancy_r"]
                row[f"{prefix}_pnl"] = result["pnl"]
            full = item["full"]
            row["full_trades"] = None if full is None else full["trade_count"]
            row["full_exp"] = None if full is None else full["expectancy_r"]
            row["full_pnl"] = None if full is None else full["pnl"]
            row["full_dd"] = None if full is None else full["max_drawdown_pct"]
            row["worst_r"] = None if full is None else str(_worst_r(full))
            writer.writerow(row)

    lines = [
        "# Failed-selloff V11 timing-geometry research",
        "",
        "Revealed data only; April/May external holdout excluded.",
        "",
        f"- Starting timing variants: {len(variants)}",
        f"- Positive in all three revealed regimes: {len(cross_regime)}",
        f"- Full 58-session promotion survivors: {len(final)}",
        "- Only varied causal L1->B1 minimum duration and L2->signal maximum duration.",
        "- Exact V10 direct structure retained: $100k liquidity, 0.5% higher-L2 buffer, no breakout-volume minimum, no hold, 11:30 ET cutoff, causal V4 management.",
        "",
        "## Cross-regime candidates",
        "",
        "| Variant | R1 n / exp / P&L | R2 n / exp / P&L | R3 n / exp / P&L | Full n / exp / P&L / DD / worst R |",
        "|---|---|---|---|---|",
    ]
    for bundle in cross_regime:
        b1, b2, b3 = bundle["blocks"]
        full = bundle["full"]
        full_text = "not promoted"
        if full is not None:
            full_text = f"{full['trade_count']} / {full['expectancy_r']} / {full['pnl']} / {full['max_drawdown_pct']} / {_worst_r(full)}"
        lines.append(
            f"| {_variant_id(bundle['gates'])} | {b1['trade_count']} / {b1['expectancy_r']} / {b1['pnl']} | "
            f"{b2['trade_count']} / {b2['expectancy_r']} / {b2['pnl']} | "
            f"{b3['trade_count']} / {b3['expectancy_r']} / {b3['pnl']} | {full_text} |"
        )

    if final:
        lines.extend(["", "## Promotable revealed-data candidates", ""])
        for rank, bundle in enumerate(final, 1):
            full = bundle["full"]
            lines.append(
                f"{rank}. `{_variant_id(bundle['gates'])}` — {full['trade_count']} trades, "
                f"{full['expectancy_r']}R expectancy, P&L {full['pnl']}, max DD {full['max_drawdown_pct']}%, worst {_worst_r(full)}R."
            )
    else:
        lines.extend(["", "## Conclusion", "", "No V11 timing-geometry variant qualifies for external validation."])

    (output / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print((output / "summary.md").read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
