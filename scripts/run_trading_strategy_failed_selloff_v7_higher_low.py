from __future__ import annotations

"""V7 research: restore the actual failed-selloff L1/B1/L2 structure.

V2/V5 treated a recovery from the first selloff low followed by a local-high
break as sufficient.  Across revealed regimes that remained unstable even after
liquidity, breakout-volume, risk-distance and time filters.  V7 changes the
structure itself:

    gap as impulse -> confirmed selloff low L1 -> confirmed bounce high B1 ->
    confirmed second low L2 above L1 -> VWAP + B1 breakout -> optional hold

The stop is under L2, not under L1.  All pivots use only finalized 1-minute bars
and one right-side finalized bar for confirmation.  The April/May external
holdout is not loaded by this script.  Production defaults remain unchanged.
"""

import argparse
import csv
import itertools
import json
from dataclasses import dataclass
from datetime import date, time
from decimal import Decimal
from pathlib import Path

import app.apps.trading.strategy_backtest as _bt
from app.apps.trading.strategies.gap_pullback import session_vwap
from app.apps.trading.strategies.models import GapPullbackFeatures, StrategySignal
import scripts.run_trading_strategy_failed_selloff_v2_sweep as _v2
import scripts.run_trading_strategy_failed_selloff_v4_management as _v4
from scripts.run_trading_strategy_backtest import strict_v11_strategy
from scripts.run_trading_strategy_liquidity_sweep import (
    _cache_namespace,
    _dataset_cache_path,
    _load_cached_dataset,
    _trading_dates,
)


_ACTIVE_VARIANT = None


@dataclass(frozen=True)
class HigherLowVariant:
    minimum_premarket_dollar_volume: Decimal
    minimum_tod_rvol: Decimal
    selloff_min_pct: Decimal
    selloff_max_pct: Decimal
    recovery_min_pct: Decimal
    breakout_lookback_bars: int
    bars_after_low: int
    breakout_volume_ratio: Decimal
    last_entry_et: time
    reward_multiple: Decimal
    breakeven_trigger_r: Decimal | None
    protected_stop_r: Decimal
    max_hold_minutes: int
    higher_low_buffer_pct: Decimal
    minimum_breakout_volume_ratio: Decimal
    require_breakout_hold: bool

    @property
    def variant_id(self) -> str:
        hold = "hold" if self.require_breakout_hold else "direct"
        return (
            f"v7-liq{int(self.minimum_premarket_dollar_volume)}"
            f"-hl{self.higher_low_buffer_pct}"
            f"-bvol{self.minimum_breakout_volume_ratio}"
            f"-{hold}"
        )


def _grid():
    # 3 * 3 * 2 * 2 = 36 structural variants.  Entry cutoff and management are
    # fixed from previously revealed development evidence; the external holdout
    # remains untouched.
    for liquidity, hl_buffer, volume_ratio, hold in itertools.product(
        (Decimal("250000"), Decimal("500000"), Decimal("1000000")),
        (Decimal("0.5"), Decimal("1.0"), Decimal("2.0")),
        (Decimal("0.8"), Decimal("1.25")),
        (False, True),
    ):
        yield HigherLowVariant(
            minimum_premarket_dollar_volume=liquidity,
            minimum_tod_rvol=Decimal("3"),
            selloff_min_pct=Decimal("8"),
            selloff_max_pct=Decimal("25"),
            recovery_min_pct=Decimal("5"),
            breakout_lookback_bars=1,
            bars_after_low=1,
            breakout_volume_ratio=Decimal("0"),
            last_entry_et=time(10, 30),
            reward_multiple=Decimal("1.5"),
            breakeven_trigger_r=Decimal("0.75"),
            protected_stop_r=Decimal("0.25"),
            max_hold_minutes=60,
            higher_low_buffer_pct=hl_buffer,
            minimum_breakout_volume_ratio=volume_ratio,
            require_breakout_hold=hold,
        )


def _quality(features: GapPullbackFeatures) -> int:
    return (
        features.catalyst_score
        + features.supply_score
        + features.opening_structure_score
        + features.pullback_quality_score
        + features.reclaim_break_score
    )


def _base_features(candidate, config):
    severe = tuple(flag for flag in candidate.dilution_flags if flag in set(config.reject_dilution_flags))
    float_ok = (
        candidate.float_shares is not None
        and config.preferred_float_min_shares <= candidate.float_shares <= config.preferred_float_max_shares
    )
    features = GapPullbackFeatures(
        gap_pct=candidate.gap_pct,
        spread_bps=candidate.spread_bps,
        tod_rvol=candidate.tod_rvol,
        float_shares=candidate.float_shares,
        catalyst_evidence_count=len(candidate.catalyst_evidence_ids),
        dilution_flags=tuple(candidate.dilution_flags),
        catalyst_score=2 if candidate.catalyst_evidence_ids else 0,
        supply_score=0 if severe else (2 if float_ok else 1),
        opening_structure_score=2 if candidate.gap_pct >= Decimal("40") else 1,
    )
    return features, severe, float_ok


def _result(candidate, state, reason, transitions, features, regular, signal=None):
    scored = features.model_copy(update={"quality_score": _quality(features)})
    return _v2._result(candidate, state, reason, transitions, scored, regular, signal)


def _local_low(regular, index):
    if index < 0 or index + 1 >= len(regular):
        return False
    left = regular[index - 1].low if index > 0 else regular[index].low
    return regular[index].low <= left and regular[index].low < regular[index + 1].low


def _local_high(regular, index):
    if index <= 0 or index + 1 >= len(regular):
        return False
    return regular[index].high >= regular[index - 1].high and regular[index].high > regular[index + 1].high


def _volume_ratio(regular, index):
    prior = regular[max(0, index - 5):index]
    if not prior:
        return None
    average = sum((bar.volume for bar in prior), Decimal("0")) / Decimal(len(prior))
    if average <= 0:
        return None
    return regular[index].volume / average


def _higher_low_evaluate(candidate, bars, config=None):
    variant = _ACTIVE_VARIANT
    active = config
    if variant is None or active is None:
        return _v2._failed_selloff_v2_evaluate(candidate, bars, config)

    regular = _v2._regular_bars(list(bars))
    transitions = ["discovered"]
    features, severe, float_ok = _base_features(candidate, active)

    rejection = None
    if candidate.gap_pct < active.minimum_gap_pct:
        rejection = "GAP_BELOW_MINIMUM"
    elif not active.minimum_price <= candidate.premarket_price <= active.maximum_price:
        rejection = "PRICE_OUT_OF_RANGE"
    elif candidate.premarket_dollar_volume < active.minimum_premarket_dollar_volume:
        rejection = "PREMARKET_DOLLAR_VOLUME_LOW"
    elif candidate.tod_rvol is not None and candidate.tod_rvol < active.minimum_tod_rvol:
        rejection = "TOD_RVOL_LOW"
    elif candidate.spread_bps is None:
        rejection = "SPREAD_MISSING"
    elif candidate.spread_bps > active.maximum_spread_bps:
        rejection = "SPREAD_TOO_WIDE"
    elif active.require_catalyst_evidence and not candidate.catalyst_evidence_ids:
        rejection = "CATALYST_EVIDENCE_REQUIRED"
    elif severe:
        rejection = "DILUTION_SUPPLY_RISK"
    elif active.float_preference_mode == "require" and not float_ok:
        rejection = "FLOAT_OUTSIDE_REQUIRED_RANGE"
    if rejection:
        transitions.append("rejected")
        return _result(candidate, "rejected", rejection, transitions, features, regular)

    transitions.append("qualified_gap")
    if not regular:
        return _result(candidate, "qualified_gap", "WAITING_FOR_REGULAR_SESSION", transitions, features, regular)

    current = regular[-1]
    current_et = current.end_time.astimezone(_v2._ET)
    if current_et.time() < active.entry_start_et:
        return _result(candidate, "qualified_gap", "ENTRY_WINDOW_NOT_OPEN", transitions, features, regular)
    if current_et.time() > variant.last_entry_et:
        transitions.append("expired")
        return _result(candidate, "expired", "ENTRY_WINDOW_CLOSED", transitions, features, regular)

    reference = max(candidate.premarket_price, regular[0].open)

    # Earliest confirmed first selloff low whose depth matches the setup.
    l1_idx = None
    for i in range(0, len(regular) - 1):
        if not _local_low(regular, i):
            continue
        depth = (reference - regular[i].low) / reference * Decimal("100")
        if variant.selloff_min_pct <= depth <= variant.selloff_max_pct:
            l1_idx = i
            break
    if l1_idx is None:
        return _result(candidate, "first_pullback", "WAITING_FOR_CONFIRMED_L1", transitions + ["first_pullback"], features, regular)

    l1 = regular[l1_idx].low
    selloff_depth = (reference - l1) / reference * Decimal("100")
    features = features.model_copy(update={"l1": l1, "pullback_depth_pct": selloff_depth})
    transitions.extend(["first_pullback", "first_low_confirmed"])

    # Earliest confirmed bounce high after L1 with a meaningful recovery.
    b1_idx = None
    for j in range(l1_idx + 1, len(regular) - 1):
        if not _local_high(regular, j):
            continue
        recovery = (regular[j].high / l1 - Decimal("1")) * Decimal("100")
        if recovery >= variant.recovery_min_pct:
            b1_idx = j
            break
    if b1_idx is None:
        return _result(candidate, "first_low_confirmed", "WAITING_FOR_CONFIRMED_B1", transitions, features, regular)

    b1 = regular[b1_idx].high
    features = features.model_copy(update={"b1": b1})
    transitions.append("bounce_high_confirmed")

    # Require a real second pullback after B1, confirmed by a later finalized
    # bar, that remains meaningfully above L1.
    l2_idx = None
    required_l2 = l1 * (Decimal("1") + variant.higher_low_buffer_pct / Decimal("100"))
    for k in range(b1_idx + 1, len(regular) - 1):
        if not _local_low(regular, k):
            continue
        second_pullback = (b1 - regular[k].low) / b1 * Decimal("100")
        if regular[k].low >= required_l2 and second_pullback >= Decimal("2"):
            l2_idx = k
            break
        if regular[k].low <= l1:
            return _result(candidate, "rejected", "SECOND_LOW_NOT_HIGHER", transitions + ["rejected"], features, regular)
    if l2_idx is None:
        return _result(candidate, "bounce_high_confirmed", "WAITING_FOR_CONFIRMED_L2", transitions, features, regular)

    l2 = regular[l2_idx].low
    features = features.model_copy(update={"l2": l2, "pullback_quality_score": 2})
    transitions.append("higher_low_confirmed")

    # Find the first qualifying breakout after L2 has itself been confirmed.
    breakout_idx = None
    breakout_ratio = None
    for m in range(l2_idx + 2, len(regular)):
        partial = regular[: m + 1]
        vwap = session_vwap(partial)
        if vwap is None:
            continue
        bar = regular[m]
        if bar.close <= b1 or bar.close <= vwap or bar.close <= bar.open:
            continue
        ratio = _volume_ratio(regular, m)
        if ratio is None or ratio < variant.minimum_breakout_volume_ratio:
            continue
        breakout_idx = m
        breakout_ratio = ratio
        break
    if breakout_idx is None:
        return _result(candidate, "higher_low_confirmed", "WAITING_FOR_B1_VWAP_BREAK", transitions, features, regular)

    vwap_now = session_vwap(regular[: breakout_idx + 1])
    features = features.model_copy(
        update={
            "session_vwap": vwap_now,
            "vwap_distance_pct": None if vwap_now is None else (regular[breakout_idx].close / vwap_now - Decimal("1")) * Decimal("100"),
            "breakout_volume_ratio": breakout_ratio,
            "reclaim_break_score": 2,
        }
    )
    transitions.extend(["vwap_reclaim", "lower_high_break"])

    signal_idx = breakout_idx
    if variant.require_breakout_hold:
        if len(regular) <= breakout_idx + 1:
            return _result(candidate, "lower_high_break", "WAITING_FOR_BREAKOUT_HOLD", transitions, features, regular)
        hold_idx = breakout_idx + 1
        hold_bar = regular[hold_idx]
        hold_vwap = session_vwap(regular[: hold_idx + 1])
        stop_reference = l2 * (Decimal("1") - active.stop_buffer_bps / Decimal("10000"))
        if hold_bar.low <= stop_reference:
            return _result(candidate, "rejected", "BREAKOUT_HOLD_INVALIDATED", transitions + ["rejected"], features, regular)
        if hold_vwap is None or hold_bar.close <= b1 or hold_bar.close <= hold_vwap:
            return _result(candidate, "lower_high_break", "WAITING_FOR_BREAKOUT_HOLD", transitions, features, regular)
        signal_idx = hold_idx
        transitions.append("breakout_hold_confirmed")

    # Do not keep re-emitting the same historical breakout on later bars.  The
    # backtester sees the signal only when the corresponding finalized signal
    # bar is the current bar.
    if signal_idx != len(regular) - 1:
        return _result(candidate, "lower_high_break", "BREAKOUT_ALREADY_PASSED", transitions, features, regular)

    entry = regular[signal_idx].close
    stop = l2 * (Decimal("1") - active.stop_buffer_bps / Decimal("10000"))
    risk = entry - stop
    if risk <= 0:
        return _result(candidate, "rejected", "NON_POSITIVE_RISK_DISTANCE", transitions + ["rejected"], features, regular)
    target = entry + risk * active.reward_multiple
    signal = StrategySignal(
        instrument_id=candidate.instrument_id,
        state="entry_ready",
        entry_price=entry,
        stop_price=stop,
        target_price=target,
        risk_per_share=risk,
        reason_code="FAILED_SELLOFF_HIGHER_LOW_BREAK",
        quality_score=_quality(features),
    )
    transitions.append("entry_ready")
    return _result(candidate, "entry_ready", "FAILED_SELLOFF_HIGHER_LOW_BREAK", transitions, features, regular, signal)


def _run_variant(variant, datasets, *, initial_cash, spread):
    global _ACTIVE_VARIANT
    _ACTIVE_VARIANT = variant
    _v4._ACTIVE_MANAGEMENT = variant
    return _v4._BASE_RUN_VARIANT(
        variant,
        datasets,
        initial_cash=initial_cash,
        spread=spread,
        max_hold_minutes=variant.max_hold_minutes,
    )


def _expectancy(row):
    value = row.get("expectancy_r")
    return Decimal(str(value)) if value is not None else Decimal("-999")


def _worst(row):
    return min((Decimal(str(t["r_multiple"])) for t in row.get("trades") or []), default=Decimal("-999"))


def _load_block(cache, start, end):
    result = []
    for d in _trading_dates(start, end):
        path = _dataset_cache_path(cache, d)
        if not path.exists():
            raise FileNotFoundError(f"missing revealed dataset: {path}")
        result.append(_load_cached_dataset(path, d))
    return result


def _passes(row):
    return int(row["trade_count"]) >= 2 and _expectancy(row) > 0


def parse_args():
    p = argparse.ArgumentParser(description="Run V7 higher-low failed-selloff research.")
    p.add_argument("--dataset-cache-dir", default=".cache/trading-liquidity-datasets")
    p.add_argument("--output-dir", default="artifacts/failed-selloff-v7-higher-low")
    p.add_argument("--initial-cash", default="100000")
    p.add_argument("--assumed-spread-bps", default="40")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    _bt.evaluate_gap_pullback = _higher_low_evaluate
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
    blocks = [_load_block(cache, a, b) for a, b in specs]
    all_datasets = [d for block in blocks for d in block]
    if len(all_datasets) != 58:
        raise ValueError(f"V7 expects 58 revealed sessions, got {len(all_datasets)}")

    variants = list(_grid())
    state = {v.variant_id: {"variant": v, "blocks": [], "full": None, "eliminated_after": None} for v in variants}
    survivors = variants
    for bi, block in enumerate(blocks, 1):
        next_survivors = []
        print(f"V7 block {bi}: evaluating {len(survivors)} variant(s)")
        for idx, variant in enumerate(survivors, 1):
            row = _run_variant(variant, block, initial_cash=initial_cash, spread=spread)
            state[variant.variant_id]["blocks"].append(row)
            if _passes(row):
                next_survivors.append(variant)
            else:
                state[variant.variant_id]["eliminated_after"] = bi
            if idx % 6 == 0 or idx == len(survivors):
                print(f"  progress {idx}/{len(survivors)}")
        survivors = next_survivors
        print(f"V7 block {bi}: {len(survivors)} survivor(s)")
        if not survivors:
            break

    final = []
    if survivors and all(len(state[v.variant_id]["blocks"]) == 3 for v in survivors):
        for variant in survivors:
            full = _run_variant(variant, all_datasets, initial_cash=initial_cash, spread=spread)
            state[variant.variant_id]["full"] = full
            if int(full["trade_count"]) >= 12 and _expectancy(full) > 0:
                final.append(variant)
            else:
                state[variant.variant_id]["eliminated_after"] = "full"

    final = sorted(
        final,
        key=lambda v: (
            min(_expectancy(r) for r in state[v.variant_id]["blocks"]),
            _expectancy(state[v.variant_id]["full"]),
            _worst(state[v.variant_id]["full"]),
        ),
        reverse=True,
    )

    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    serial = []
    for v in variants:
        bundle = state[v.variant_id]
        serial.append({
            "variant_id": v.variant_id,
            "parameters": {
                "minimum_premarket_dollar_volume": str(v.minimum_premarket_dollar_volume),
                "higher_low_buffer_pct": str(v.higher_low_buffer_pct),
                "minimum_breakout_volume_ratio": str(v.minimum_breakout_volume_ratio),
                "require_breakout_hold": v.require_breakout_hold,
                "last_entry_et": v.last_entry_et.isoformat(),
                "recovery_min_pct": str(v.recovery_min_pct),
                "reward_multiple": str(v.reward_multiple),
            },
            "blocks": bundle["blocks"],
            "full": bundle["full"],
            "eliminated_after": bundle["eliminated_after"],
        })
    (output / "results.json").write_text(json.dumps(serial, indent=2, default=str) + "\n", encoding="utf-8")

    with (output / "comparison.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = ["variant_id", "eliminated_after", "b1_trades", "b1_exp", "b2_trades", "b2_exp", "b3_trades", "b3_exp", "full_trades", "full_exp", "full_pnl", "worst_r"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in serial:
            bs = item["blocks"]
            full = item["full"]
            row = {"variant_id": item["variant_id"], "eliminated_after": item["eliminated_after"]}
            for i in range(3):
                r = bs[i] if i < len(bs) else None
                row[f"b{i+1}_trades"] = None if r is None else r["trade_count"]
                row[f"b{i+1}_exp"] = None if r is None else r["expectancy_r"]
            row["full_trades"] = None if full is None else full["trade_count"]
            row["full_exp"] = None if full is None else full["expectancy_r"]
            row["full_pnl"] = None if full is None else full["pnl"]
            row["worst_r"] = None if full is None else str(_worst(full))
            writer.writerow(row)

    counts = []
    for i in range(3):
        counts.append(sum(len(x["blocks"]) > i and _passes(x["blocks"][i]) for x in state.values()))
    lines = [
        "# Failed-selloff V7 higher-low structure",
        "",
        "Revealed-data research only. The frozen April/May external block is not loaded.",
        "",
        f"- Starting variants: {len(variants)}",
        f"- Block 1 survivors: {counts[0]}",
        f"- Block 2 survivors: {counts[1]}",
        f"- Block 3 survivors: {counts[2]}",
        f"- Full-rule survivors: {len(final)}",
        "- Structure: confirmed L1 -> B1 -> L2 higher low -> VWAP/B1 breakout; stop below L2.",
        "- All pivots and optional hold use finalized 1-minute bars only.",
        "",
    ]
    if final:
        lines.extend(["| Rank | Variant | B1 expR | B2 expR | B3 expR | Full trades | Full expR | P&L | Worst R |", "|---:|---|---:|---:|---:|---:|---:|---:|---:|"])
        for rank, v in enumerate(final, 1):
            bundle = state[v.variant_id]
            b1, b2, b3 = bundle["blocks"]
            full = bundle["full"]
            lines.append(f"| {rank} | `{v.variant_id}` | {b1['expectancy_r']} | {b2['expectancy_r']} | {b3['expectancy_r']} | {full['trade_count']} | {full['expectancy_r']} | {full['pnl']} | {_worst(full)} |")
        chosen = final[0]
        full = state[chosen.variant_id]["full"]
        lines.extend(["", "## V7 conclusion", "", f"Preliminary V7 candidate: `{chosen.variant_id}`.", f"Full revealed development: {full['trade_count']} trades, {full['expectancy_r']}R expectancy, P&L {full['pnl']}.", "Freeze this exact rule before using the April/May holdout."])
    else:
        lines.extend(["## V7 conclusion", "", "No V7 variant survives all three revealed regimes. Keep the April/May holdout untouched."])
    (output / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print((output / "summary.md").read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
