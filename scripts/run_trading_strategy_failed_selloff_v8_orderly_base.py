from __future__ import annotations

"""V8 research: orderly-base filters on the V7 higher-low structure.

V7 restored the intended L1 -> B1 -> L2 higher-low sequence, but the revealed
58-session diagnostic showed that its losing signals tended to form too fast,
resolve too late after L2, or pull back on volume that did not contract relative
to the L1->B1 bounce.  V8 keeps V7's causal structure and exact paper engine and
adds only those pre-entry quality gates.

The frozen 2026-04-29..2026-05-22 external block is deliberately not loaded.
Production strategy defaults are unchanged.
"""

import argparse
import csv
import itertools
import json
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import app.apps.trading.strategy_backtest as _bt
import scripts.run_trading_strategy_failed_selloff_v4_management as _v4
import scripts.run_trading_strategy_failed_selloff_v7_higher_low as _v7
from scripts.run_trading_strategy_backtest import strict_v11_strategy
from scripts.run_trading_strategy_liquidity_sweep import (
    _cache_namespace,
    _dataset_cache_path,
    _load_cached_dataset,
    _trading_dates,
)


_ORIGINAL_RESULT = _v7._result
_ORIGINAL_EVALUATE = _v7._higher_low_evaluate
_ACTIVE_GATES = None


@dataclass(frozen=True)
class OrderlyBaseGates:
    minimum_l1_to_b1_minutes: int
    maximum_l2_to_signal_minutes: int
    maximum_pullback_to_bounce_volume_ratio: Decimal
    minimum_breakout_body_pct_range: Decimal = Decimal("70")

    @property
    def gate_id(self) -> str:
        return (
            f"base{self.minimum_l1_to_b1_minutes}"
            f"-resolve{self.maximum_l2_to_signal_minutes}"
            f"-pvol{self.maximum_pullback_to_bounce_volume_ratio}"
        )


def _normalized_result(candidate, state, reason, transitions, features, regular, signal=None):
    canonical = ["breakout_hold" if item == "breakout_hold_confirmed" else item for item in transitions]
    return _ORIGINAL_RESULT(candidate, state, reason, canonical, features, regular, signal)


def _mean(values):
    values = list(values)
    return sum(values, Decimal("0")) / Decimal(len(values)) if values else None


def _find_index(regular, *, start=0, low=None, high=None):
    for index in range(start, len(regular)):
        if low is not None and regular[index].low == low:
            return index
        if high is not None and regular[index].high == high:
            return index
    return None


def _reject_signal(candidate, result, regular, reason):
    transitions = list(result.transitions)
    if transitions and transitions[-1] == "entry_ready":
        transitions.pop()
    transitions.append("rejected")
    return _v7._result(candidate, "rejected", reason, transitions, result.features, regular)


def _orderly_base_evaluate(candidate, bars, config=None):
    result = _ORIGINAL_EVALUATE(candidate, bars, config)
    gates = _ACTIVE_GATES
    if gates is None or result.signal is None or result.state != "entry_ready":
        return result

    regular = _v7._v2._regular_bars(list(bars))
    if len(regular) < 2:
        return _reject_signal(candidate, result, regular, "V8_INSUFFICIENT_FINALIZED_BARS")

    f = result.features
    l1_idx = _find_index(regular, low=f.l1)
    b1_idx = _find_index(regular, start=(l1_idx or 0) + 1, high=f.b1)
    l2_idx = _find_index(regular, start=(b1_idx or 0) + 1, low=f.l2)
    if l1_idx is None or b1_idx is None or l2_idx is None:
        return _reject_signal(candidate, result, regular, "V8_STRUCTURE_INDEX_MISSING")

    l1_to_b1 = b1_idx - l1_idx
    l2_to_signal = len(regular) - 1 - l2_idx
    if l1_to_b1 < gates.minimum_l1_to_b1_minutes:
        return _reject_signal(candidate, result, regular, "V8_BASE_FORMED_TOO_FAST")
    if l2_to_signal > gates.maximum_l2_to_signal_minutes:
        return _reject_signal(candidate, result, regular, "V8_BREAKOUT_RESOLVED_TOO_LATE")

    bounce_bars = regular[l1_idx + 1 : b1_idx + 1]
    pullback_bars = regular[b1_idx + 1 : l2_idx + 1]
    bounce_volume = _mean(bar.volume for bar in bounce_bars)
    pullback_volume = _mean(bar.volume for bar in pullback_bars)
    if bounce_volume in (None, Decimal("0")) or pullback_volume is None:
        return _reject_signal(candidate, result, regular, "V8_VOLUME_CONTRACTION_MISSING")
    pullback_ratio = pullback_volume / bounce_volume
    if pullback_ratio > gates.maximum_pullback_to_bounce_volume_ratio:
        return _reject_signal(candidate, result, regular, "V8_PULLBACK_VOLUME_NOT_CONTRACTING")

    # V7's held signal uses the previous finalized bar as the actual breakout.
    breakout = regular[-2]
    breakout_range = breakout.high - breakout.low
    if breakout_range <= 0:
        return _reject_signal(candidate, result, regular, "V8_BREAKOUT_RANGE_INVALID")
    body_pct = abs(breakout.close - breakout.open) / breakout_range * Decimal("100")
    if body_pct < gates.minimum_breakout_body_pct_range:
        return _reject_signal(candidate, result, regular, "V8_BREAKOUT_BODY_WEAK")

    return result


def _variant_id(base, gates):
    return f"v8-liq{int(base.minimum_premarket_dollar_volume)}-{gates.gate_id}"


def _grid():
    template = next(
        v for v in _v7._grid()
        if v.minimum_premarket_dollar_volume == Decimal("250000")
        and v.higher_low_buffer_pct == Decimal("0.5")
        and v.minimum_breakout_volume_ratio == Decimal("0.8")
        and v.require_breakout_hold
    )
    for liquidity, base_minutes, resolve_minutes, pullback_ratio in itertools.product(
        (Decimal("100000"), Decimal("250000"), Decimal("500000")),
        (3, 4, 5),
        (8, 12, 16),
        (Decimal("0.6"), Decimal("0.8"), Decimal("1.0")),
    ):
        base = replace(template, minimum_premarket_dollar_volume=liquidity)
        gates = OrderlyBaseGates(
            minimum_l1_to_b1_minutes=base_minutes,
            maximum_l2_to_signal_minutes=resolve_minutes,
            maximum_pullback_to_bounce_volume_ratio=pullback_ratio,
        )
        yield base, gates


def _run_variant(base, gates, datasets, *, initial_cash, spread):
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
    row["variant_id"] = _variant_id(base, gates)
    row["parameters"] = {
        **dict(row.get("parameters") or {}),
        "minimum_l1_to_b1_minutes": gates.minimum_l1_to_b1_minutes,
        "maximum_l2_to_signal_minutes": gates.maximum_l2_to_signal_minutes,
        "maximum_pullback_to_bounce_volume_ratio": str(gates.maximum_pullback_to_bounce_volume_ratio),
        "minimum_breakout_body_pct_range": str(gates.minimum_breakout_body_pct_range),
    }
    return row


def _decimal(value, fallback="-999"):
    if value is None:
        return Decimal(fallback)
    return Decimal(str(value))


def _worst_r(row):
    trades = row.get("trades") or []
    if not trades:
        return Decimal("-999")
    return min(Decimal(str(trade["r_multiple"])) for trade in trades)


def _passes_block(row):
    return (
        int(row["trade_count"]) >= 2
        and _decimal(row.get("expectancy_r")) > 0
        and _decimal(row.get("pnl"), "0") > 0
    )


def _load_block(cache, start, end):
    result = []
    for session_date in _trading_dates(start, end):
        path = _dataset_cache_path(cache, session_date)
        if not path.exists():
            raise FileNotFoundError(f"missing revealed dataset: {path}")
        result.append(_load_cached_dataset(path, session_date))
    return result


def parse_args():
    parser = argparse.ArgumentParser(description="Run V8 orderly-base failed-selloff research.")
    parser.add_argument("--dataset-cache-dir", default=".cache/trading-liquidity-datasets")
    parser.add_argument("--output-dir", default="artifacts/failed-selloff-v8-orderly-base")
    parser.add_argument("--initial-cash", default="100000")
    parser.add_argument("--assumed-spread-bps", default="40")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    _v7._result = _normalized_result
    _bt.evaluate_gap_pullback = _orderly_base_evaluate
    _bt._find_trade = _v4._managed_find_trade

    spread = Decimal(args.assumed_spread_bps)
    initial_cash = Decimal(args.initial_cash)
    namespace, _ = _cache_namespace(strict_v11_strategy(minimum_premarket_dollar_volume=Decimal("100000")), spread)
    cache = Path(args.dataset_cache_dir) / namespace
    specs = (
        (date(2026, 5, 26), date(2026, 6, 18)),
        (date(2026, 6, 26), date(2026, 7, 23)),
        (date(2026, 7, 24), date(2026, 8, 21)),
    )
    blocks = [_load_block(cache, start, end) for start, end in specs]
    all_datasets = [dataset for block in blocks for dataset in block]
    if len(all_datasets) != 58:
        raise ValueError(f"V8 expects 58 revealed sessions, got {len(all_datasets)}")

    variants = list(_grid())
    state = {
        _variant_id(base, gates): {
            "base": base,
            "gates": gates,
            "blocks": [],
            "full": None,
            "eliminated_after": None,
        }
        for base, gates in variants
    }
    survivors = variants
    for block_index, block in enumerate(blocks, 1):
        next_survivors = []
        print(f"V8 block {block_index}: evaluating {len(survivors)} variant(s)")
        for index, (base, gates) in enumerate(survivors, 1):
            key = _variant_id(base, gates)
            row = _run_variant(base, gates, block, initial_cash=initial_cash, spread=spread)
            state[key]["blocks"].append(row)
            if _passes_block(row):
                next_survivors.append((base, gates))
            else:
                state[key]["eliminated_after"] = block_index
            if index % 9 == 0 or index == len(survivors):
                print(f"  progress {index}/{len(survivors)}")
        survivors = next_survivors
        print(f"V8 block {block_index}: {len(survivors)} survivor(s)")
        if not survivors:
            break

    final = []
    if survivors and all(len(state[_variant_id(base, gates)]["blocks"]) == 3 for base, gates in survivors):
        for base, gates in survivors:
            key = _variant_id(base, gates)
            full = _run_variant(base, gates, all_datasets, initial_cash=initial_cash, spread=spread)
            state[key]["full"] = full
            if (
                int(full["trade_count"]) >= 8
                and _decimal(full.get("expectancy_r")) > 0
                and _decimal(full.get("pnl"), "0") > 0
                and _worst_r(full) > Decimal("-1.75")
            ):
                final.append((base, gates))
            else:
                state[key]["eliminated_after"] = "full"

    final.sort(
        key=lambda item: (
            min(_decimal(row.get("expectancy_r")) for row in state[_variant_id(*item)]["blocks"]),
            _decimal(state[_variant_id(*item)]["full"].get("expectancy_r")),
            _decimal(state[_variant_id(*item)]["full"].get("pnl"), "0"),
            _worst_r(state[_variant_id(*item)]["full"]),
        ),
        reverse=True,
    )

    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    serial = []
    for base, gates in variants:
        key = _variant_id(base, gates)
        bundle = state[key]
        serial.append(
            {
                "variant_id": key,
                "parameters": {
                    "minimum_premarket_dollar_volume": str(base.minimum_premarket_dollar_volume),
                    "minimum_l1_to_b1_minutes": gates.minimum_l1_to_b1_minutes,
                    "maximum_l2_to_signal_minutes": gates.maximum_l2_to_signal_minutes,
                    "maximum_pullback_to_bounce_volume_ratio": str(gates.maximum_pullback_to_bounce_volume_ratio),
                    "minimum_breakout_body_pct_range": str(gates.minimum_breakout_body_pct_range),
                    "higher_low_buffer_pct": str(base.higher_low_buffer_pct),
                    "minimum_breakout_volume_ratio": str(base.minimum_breakout_volume_ratio),
                    "reward_multiple": str(base.reward_multiple),
                },
                "blocks": bundle["blocks"],
                "full": bundle["full"],
                "eliminated_after": bundle["eliminated_after"],
            }
        )
    (output / "results.json").write_text(json.dumps(serial, indent=2, default=str) + "\n", encoding="utf-8")

    with (output / "comparison.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = [
            "variant_id", "eliminated_after",
            "b1_trades", "b1_exp", "b1_pnl",
            "b2_trades", "b2_exp", "b2_pnl",
            "b3_trades", "b3_exp", "b3_pnl",
            "full_trades", "full_exp", "full_pnl", "worst_r",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in serial:
            row = {"variant_id": item["variant_id"], "eliminated_after": item["eliminated_after"]}
            for i in range(3):
                block = item["blocks"][i] if i < len(item["blocks"]) else None
                row[f"b{i+1}_trades"] = None if block is None else block["trade_count"]
                row[f"b{i+1}_exp"] = None if block is None else block["expectancy_r"]
                row[f"b{i+1}_pnl"] = None if block is None else block["pnl"]
            full = item["full"]
            row["full_trades"] = None if full is None else full["trade_count"]
            row["full_exp"] = None if full is None else full["expectancy_r"]
            row["full_pnl"] = None if full is None else full["pnl"]
            row["worst_r"] = None if full is None else str(_worst_r(full))
            writer.writerow(row)

    survivor_counts = [
        sum(len(bundle["blocks"]) > i and _passes_block(bundle["blocks"][i]) for bundle in state.values())
        for i in range(3)
    ]
    lines = [
        "# Failed-selloff V8 orderly-base research",
        "",
        "Revealed-data research only. The frozen April/May external block is not loaded.",
        "",
        f"- Starting variants: {len(variants)}",
        f"- Block 1 survivors: {survivor_counts[0]}",
        f"- Block 2 survivors: {survivor_counts[1]}",
        f"- Block 3 survivors: {survivor_counts[2]}",
        f"- Full-rule survivors: {len(final)}",
        "- Per-regime promotion requires >=2 trades, positive expectancy R, and positive dollar P&L.",
        "- Full promotion additionally requires >=8 trades and worst realized trade better than -1.75R.",
        "- V8 gates are causal: L1->B1 base time, L2->signal resolution time, pullback/bounce volume contraction, and breakout body strength.",
        "",
    ]
    if final:
        lines.extend(
            [
                "| Rank | Variant | B1 expR/P&L | B2 expR/P&L | B3 expR/P&L | Full trades | Full expR | Full P&L | Worst R |",
                "|---:|---|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for rank, (base, gates) in enumerate(final, 1):
            bundle = state[_variant_id(base, gates)]
            b1, b2, b3 = bundle["blocks"]
            full = bundle["full"]
            lines.append(
                f"| {rank} | `{_variant_id(base, gates)}` | {b1['expectancy_r']} / {b1['pnl']} | "
                f"{b2['expectancy_r']} / {b2['pnl']} | {b3['expectancy_r']} / {b3['pnl']} | "
                f"{full['trade_count']} | {full['expectancy_r']} | {full['pnl']} | {_worst_r(full)} |"
            )
        chosen_base, chosen_gates = final[0]
        chosen_key = _variant_id(chosen_base, chosen_gates)
        chosen_full = state[chosen_key]["full"]
        lines.extend(
            [
                "",
                "## V8 conclusion",
                "",
                f"Preliminary V8 candidate: `{chosen_key}`.",
                f"Full revealed development: {chosen_full['trade_count']} trades, {chosen_full['expectancy_r']}R expectancy, P&L {chosen_full['pnl']}.",
                "Freeze this exact rule before using the April/May holdout.",
            ]
        )
    else:
        lines.extend(
            [
                "## V8 conclusion",
                "",
                "No V8 variant survives all three revealed regimes. Keep the April/May holdout untouched.",
            ]
        )
    (output / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print((output / "summary.md").read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
