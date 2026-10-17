from __future__ import annotations

"""Cache-only walk-forward research for a simpler failed-selloff definition.

The v1.1 L1/B1/L2 state machine requires a post-open impulse, confirmed first
low, confirmed bounce high and confirmed second higher low before it can even
inspect VWAP/breakout. For a gapper, however, the *premarket gap itself* is
already the impulse we care about. This diagnostic therefore asks a narrower
causal question:

    gap -> first regular-session selloff -> recovery -> VWAP reclaim ->
    break of the immediately preceding observed high -> next-bar entry

Production strategy code/defaults are NOT changed by this script. The evaluator
is monkey-patched only inside this cache-only research process. Missing TOD RVOL
uses the explicit diagnostic fallback already studied: it may continue only
after the absolute premarket-liquidity gate passes; a numeric RVOL must still
meet its configured minimum.
"""

import argparse
import csv
import itertools
import json
from collections import Counter
from dataclasses import dataclass
from datetime import date, time
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import app.apps.trading.strategy_backtest as _backtest_module
from app.apps.trading.historical_gapper_reconstruction import reconstructed_strategy_config
from app.apps.trading.paper import PaperExecutionPolicy
from app.apps.trading.strategies.gap_pullback import session_vwap
from app.apps.trading.strategies.models import (
    GapPullbackConfig,
    GapPullbackFeatures,
    GapPullbackResult,
    StrategySignal,
)
from app.apps.trading.strategy_backtest import BacktestSessionDataset, run_gap_pullback_backtest
from scripts.run_trading_strategy_backtest import strict_v11_strategy
from scripts.run_trading_strategy_liquidity_sweep import (
    _cache_namespace,
    _dataset_cache_path,
    _load_cached_dataset,
    _trading_dates,
)

_ET = ZoneInfo("America/New_York")
_REGULAR_OPEN = time(9, 30)
_REGULAR_CLOSE = time(16, 0)


@dataclass(frozen=True)
class Variant:
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

    @property
    def variant_id(self) -> str:
        return (
            f"v2-liq{int(self.minimum_premarket_dollar_volume)}"
            f"-rv{self.minimum_tod_rvol}"
            f"-sell{self.selloff_min_pct}-{self.selloff_max_pct}"
            f"-rec{self.recovery_min_pct}"
            f"-br{self.breakout_lookback_bars}"
            f"-after{self.bars_after_low}"
            f"-vol{self.breakout_volume_ratio}"
            f"-last{self.last_entry_et.strftime('%H%M')}"
            f"-r{self.reward_multiple}"
        )


def _grid():
    for (
        liquidity,
        selloff_min,
        selloff_max,
        recovery,
        lookback,
        volume_ratio,
        last_entry,
        reward,
    ) in itertools.product(
        (50_000, 100_000, 250_000),
        (5, 8, 10),
        (18, 20, 25),
        (3, 5),
        (1, 2),
        (0, 0.8),
        (time(10, 30), time(11, 30)),
        (1.5, 2.0),
    ):
        if selloff_max <= selloff_min:
            continue
        yield Variant(
            minimum_premarket_dollar_volume=Decimal(str(liquidity)),
            minimum_tod_rvol=Decimal("3"),
            selloff_min_pct=Decimal(str(selloff_min)),
            selloff_max_pct=Decimal(str(selloff_max)),
            recovery_min_pct=Decimal(str(recovery)),
            breakout_lookback_bars=int(lookback),
            bars_after_low=1,
            breakout_volume_ratio=Decimal(str(volume_ratio)),
            last_entry_et=last_entry,
            reward_multiple=Decimal(str(reward)),
        )


def _regular_bars(bars):
    if not bars:
        return []
    latest_date = bars[-1].start_time.astimezone(_ET).date()
    return [
        bar
        for bar in bars
        if bar.start_time.astimezone(_ET).date() == latest_date
        and _REGULAR_OPEN <= bar.start_time.astimezone(_ET).time() < _REGULAR_CLOSE
    ]


def _quality(features: GapPullbackFeatures) -> int:
    return (
        features.catalyst_score
        + features.supply_score
        + features.opening_structure_score
        + features.pullback_quality_score
        + features.reclaim_break_score
    )


def _result(candidate, state, reason, transitions, features, bars, signal=None):
    scored = features.model_copy(update={"quality_score": _quality(features)})
    return GapPullbackResult(
        instrument_id=candidate.instrument_id,
        state=state,
        reason_code=reason,
        features=scored,
        transitions=tuple(transitions),
        signal=signal,
        evaluated_bar_count=len(bars),
    )


def _failed_selloff_v2_evaluate(candidate, bars, config: GapPullbackConfig | None = None):
    active = config or GapPullbackConfig()
    regular = _regular_bars(list(bars))
    transitions = ["discovered"]

    severe_dilution = tuple(
        flag for flag in candidate.dilution_flags if flag in set(active.reject_dilution_flags)
    )
    float_in_preferred = (
        candidate.float_shares is not None
        and active.preferred_float_min_shares <= candidate.float_shares <= active.preferred_float_max_shares
    )
    catalyst_score = 2 if candidate.catalyst_evidence_ids else 0
    supply_score = 0 if severe_dilution else (2 if float_in_preferred else 1)
    base = GapPullbackFeatures(
        gap_pct=candidate.gap_pct,
        spread_bps=candidate.spread_bps,
        tod_rvol=candidate.tod_rvol,
        float_shares=candidate.float_shares,
        catalyst_evidence_count=len(candidate.catalyst_evidence_ids),
        dilution_flags=tuple(candidate.dilution_flags),
        catalyst_score=catalyst_score,
        supply_score=supply_score,
        opening_structure_score=2 if candidate.gap_pct >= Decimal("40") else 1,
    )

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
    elif severe_dilution:
        rejection = "DILUTION_SUPPLY_RISK"
    elif active.float_preference_mode == "require" and not float_in_preferred:
        rejection = "FLOAT_OUTSIDE_REQUIRED_RANGE"
    if rejection:
        transitions.append("rejected")
        return _result(candidate, "rejected", rejection, transitions, base, regular)

    transitions.append("qualified_gap")
    if not regular:
        return _result(candidate, "qualified_gap", "WAITING_FOR_REGULAR_SESSION", transitions, base, regular)

    current = regular[-1]
    current_et = current.end_time.astimezone(_ET)
    minutes_since_open = max(0, current_et.hour * 60 + current_et.minute - 570)
    base = base.model_copy(update={"minutes_since_open": minutes_since_open})
    if current_et.time() < active.entry_start_et:
        return _result(candidate, "qualified_gap", "ENTRY_WINDOW_NOT_OPEN", transitions, base, regular)
    if current_et.time() > active.last_entry_et:
        transitions.append("expired")
        return _result(candidate, "expired", "ENTRY_WINDOW_CLOSED", transitions, base, regular)

    # The premarket snapshot/open is the impulse reference. We intentionally do
    # not require an additional post-open rally before recognizing the selloff.
    reference = max(candidate.premarket_price, regular[0].open)
    low_idx = min(range(len(regular)), key=lambda idx: regular[idx].low)
    selloff_low = regular[low_idx].low
    selloff_depth = (reference - selloff_low) / reference * Decimal("100")
    features = base.model_copy(update={"l1": selloff_low, "pullback_depth_pct": selloff_depth})
    if selloff_depth < active.pullback_min_pct:
        transitions.append("first_pullback")
        return _result(candidate, "first_pullback", "WAITING_FOR_OPENING_SELLOFF", transitions, features, regular)
    if selloff_depth > active.pullback_max_pct:
        transitions.append("rejected")
        return _result(candidate, "rejected", "OPENING_SELLOFF_TOO_DEEP", transitions, features, regular)
    transitions.extend(["first_pullback", "first_low_confirmed"])

    bars_after_low = len(regular) - low_idx - 1
    required_after_low = max(1, active.pivot_right_bars)
    if bars_after_low < required_after_low:
        return _result(candidate, "first_low_confirmed", "WAITING_FOR_SELLOFF_FAILURE", transitions, features, regular)

    recovery_pct = (current.close / selloff_low - Decimal("1")) * Decimal("100")
    required_recovery_pct = active.higher_low_buffer_bps / Decimal("100")
    if recovery_pct < required_recovery_pct:
        return _result(candidate, "first_low_confirmed", "WAITING_FOR_RECOVERY", transitions, features, regular)

    current_vwap = session_vwap(regular)
    if current_vwap is None:
        return _result(candidate, "first_low_confirmed", "VWAP_UNAVAILABLE", transitions, features, regular)
    features = features.model_copy(
        update={
            "session_vwap": current_vwap,
            "vwap_distance_pct": (current.close / current_vwap - Decimal("1")) * Decimal("100"),
        }
    )
    if current.close <= current_vwap:
        return _result(candidate, "first_low_confirmed", "WAITING_FOR_VWAP_RECLAIM", transitions, features, regular)
    transitions.append("vwap_reclaim")

    if current.close <= current.open:
        return _result(candidate, "vwap_reclaim", "WAITING_FOR_GREEN_REVERSAL", transitions, features, regular)

    lookback = max(1, active.pivot_left_bars)
    prior_start = max(low_idx, len(regular) - 1 - lookback)
    prior = regular[prior_start:-1]
    if not prior:
        return _result(candidate, "vwap_reclaim", "WAITING_FOR_BREAKOUT_REFERENCE", transitions, features, regular)
    breakout_level = max(bar.high for bar in prior)
    features = features.model_copy(update={"b1": breakout_level})
    if current.close <= breakout_level:
        return _result(candidate, "vwap_reclaim", "WAITING_FOR_LOCAL_HIGH_BREAK", transitions, features, regular)

    previous = regular[max(0, len(regular) - 6):-1]
    average_volume = (
        sum((bar.volume for bar in previous), Decimal("0")) / Decimal(len(previous))
        if previous
        else Decimal("0")
    )
    volume_ratio = current.volume / average_volume if average_volume > 0 else Decimal("0")
    features = features.model_copy(update={"breakout_volume_ratio": volume_ratio})
    if active.breakout_volume_ratio > 0 and volume_ratio < active.breakout_volume_ratio:
        return _result(candidate, "vwap_reclaim", "BREAKOUT_VOLUME_TOO_LOW", transitions, features, regular)

    transitions.append("lower_high_break")
    pullback_score = 2 if recovery_pct >= required_recovery_pct * Decimal("1.5") else 1
    features = features.model_copy(update={"pullback_quality_score": pullback_score, "reclaim_break_score": 2})
    quality = _quality(features)
    if quality < active.minimum_quality_score:
        transitions.append("rejected")
        return _result(candidate, "rejected", "QUALITY_SCORE_BELOW_MINIMUM", transitions, features, regular)

    stop = selloff_low * (Decimal("1") - active.stop_buffer_bps / Decimal("10000"))
    entry = current.close
    risk = entry - stop
    if risk <= 0:
        transitions.append("rejected")
        return _result(candidate, "rejected", "NON_POSITIVE_RISK_DISTANCE", transitions, features, regular)
    target = entry + risk * active.reward_multiple
    transitions.append("entry_ready")
    signal = StrategySignal(
        instrument_id=candidate.instrument_id,
        state="entry_ready",
        entry_price=entry,
        stop_price=stop,
        target_price=target,
        risk_per_share=risk,
        reason_code="OPENING_SELLOFF_FAILED",
        quality_score=quality,
    )
    return _result(candidate, "entry_ready", "OPENING_SELLOFF_FAILED", transitions, features, regular, signal)


def _active_config(variant: Variant):
    strategy = strict_v11_strategy(
        minimum_premarket_dollar_volume=variant.minimum_premarket_dollar_volume
    )
    config = strategy.config.model_copy(
        update={
            "structure_interval": "1m",
            "execution_interval": "1m",
            "minimum_tod_rvol": variant.minimum_tod_rvol,
            "pullback_min_pct": variant.selloff_min_pct,
            "pullback_max_pct": variant.selloff_max_pct,
            # Diagnostic-only field mapping: higher_low_buffer_bps stores the
            # required recovery from the selloff low (3% => 300 bps).
            "higher_low_buffer_bps": variant.recovery_min_pct * Decimal("100"),
            "pivot_left_bars": variant.breakout_lookback_bars,
            "pivot_right_bars": variant.bars_after_low,
            "breakout_volume_ratio": variant.breakout_volume_ratio,
            "require_breakout_hold": False,
            "minimum_quality_score": 0,
            "last_entry_et": variant.last_entry_et,
            "reward_multiple": variant.reward_multiple,
        }
    )
    active, _ = reconstructed_strategy_config(config)
    risk = strategy.risk.model_copy(update={"last_entry_et": variant.last_entry_et})
    return active, risk


def _outcomes(result) -> Counter[str]:
    counts: Counter[str] = Counter()
    for decision in result.candidate_decisions:
        reason = decision.rejection_reason or ("TRIGGERED" if decision.triggered else decision.state.upper())
        counts[str(reason)] += 1
    return counts


def _run_variant(variant, datasets, *, initial_cash, spread, max_hold_minutes):
    config, risk = _active_config(variant)
    policy = PaperExecutionPolicy(max_volume_participation_pct=Decimal("1"))
    cash = initial_cash
    peak = cash
    max_dd = Decimal("0")
    trades = []
    outcomes: Counter[str] = Counter()
    trigger_count = 0
    candidate_count = 0
    days = []
    for dataset in datasets:
        result = run_gap_pullback_backtest(
            dataset,
            config,
            policy,
            assumed_spread_bps=spread,
            max_hold_minutes=max_hold_minutes,
            max_concurrent_positions=risk.max_positions,
            risk_profile=risk,
            initial_cash=cash,
        )
        pnl = sum((t.pnl_per_share * t.entry_fill_quantity for t in result.trades), Decimal("0"))
        cash += pnl
        peak = max(peak, cash)
        if peak > 0:
            max_dd = max(max_dd, (peak - cash) / peak * Decimal("100"))
        trades.extend(result.trades)
        candidate_count += result.summary.candidate_count
        trigger_count += result.summary.trigger_count
        day_outcomes = _outcomes(result)
        outcomes.update(day_outcomes)
        days.append({
            "session_date": dataset.session_date.isoformat(),
            "pnl": str(pnl),
            "trade_count": result.summary.trade_count,
            "trigger_count": result.summary.trigger_count,
            "candidate_outcomes": dict(day_outcomes),
            "trades": [t.model_dump(mode="json") for t in result.trades],
        })
    rvals = [t.r_multiple for t in trades]
    expectancy = sum(rvals, Decimal("0")) / Decimal(len(rvals)) if rvals else None
    wins = sum(1 for r in rvals if r > 0)
    losses = sum(1 for r in rvals if r < 0)
    return {
        "variant_id": variant.variant_id,
        "parameters": {
            "minimum_premarket_dollar_volume": str(variant.minimum_premarket_dollar_volume),
            "minimum_tod_rvol": str(variant.minimum_tod_rvol),
            "selloff_min_pct": str(variant.selloff_min_pct),
            "selloff_max_pct": str(variant.selloff_max_pct),
            "recovery_min_pct": str(variant.recovery_min_pct),
            "breakout_lookback_bars": variant.breakout_lookback_bars,
            "bars_after_low": variant.bars_after_low,
            "breakout_volume_ratio": str(variant.breakout_volume_ratio),
            "last_entry_et": variant.last_entry_et.isoformat(),
            "reward_multiple": str(variant.reward_multiple),
            "structure_interval": "1m",
        },
        "session_count": len(datasets),
        "candidate_count": candidate_count,
        "trigger_count": trigger_count,
        "trade_count": len(trades),
        "win_count": wins,
        "loss_count": losses,
        "pnl": str(cash - initial_cash),
        "return_pct": str((cash - initial_cash) / initial_cash * Decimal("100")),
        "expectancy_r": None if expectancy is None else str(expectancy),
        "max_drawdown_pct": str(max_dd),
        "candidate_outcomes": dict(outcomes),
        "trades": [t.model_dump(mode="json") for t in trades],
        "days": days,
    }


def _rank_key(row):
    n = int(row["trade_count"])
    exp = Decimal(str(row["expectancy_r"])) if row["expectancy_r"] is not None else Decimal("-999")
    pnl = Decimal(str(row["pnl"]))
    dd = Decimal(str(row["max_drawdown_pct"]))
    return (
        1 if n >= 5 and exp > 0 else 0,
        1 if n >= 3 and exp > 0 else 0,
        min(n, 12),
        exp,
        pnl,
        -dd,
    )


def _write_csv(path: Path, rows):
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = ["split", "variant_id", "trade_count", "win_count", "loss_count", "expectancy_r", "pnl", "return_pct", "max_drawdown_pct"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for split, row in rows:
            writer.writerow({"split": split, **{key: row[key] for key in fieldnames if key != "split"}})


def _summary(path, train_dates, holdout_dates, ranked, validation):
    holdout = {row["variant_id"]: row for row in validation}
    lines = [
        "# Failed-selloff v2 strategy evolution",
        "",
        "Cache-only research over fingerprint-verified reconstructed Alpaca IEX sessions. Production strategy code/defaults are unchanged.",
        "",
        f"- Training: {', '.join(d.isoformat() for d in train_dates)}",
        f"- Holdout: {', '.join(d.isoformat() for d in holdout_dates)}",
        f"- Training variants: {len(ranked)}",
        "- Definition: premarket gap is the impulse; first regular-session selloff must recover, reclaim VWAP, print a green reversal and break the immediately preceding observed high.",
        "- Missing TOD RVOL is accepted only after absolute liquidity passes; numeric TOD RVOL must be >=3x.",
        "- 1m structure / 1m execution; stop below opening selloff low; pessimistic Omnix paper fills/protection remain unchanged.",
        "",
        "## Top training variants vs untouched holdout",
        "",
        "| Rank | Variant | Train trades | Train W-L | Train exp R | Train P&L | Holdout trades | Holdout W-L | Holdout exp R | Holdout P&L |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for rank, train in enumerate(ranked[:30], 1):
        h = holdout[train["variant_id"]]
        lines.append(
            f"| {rank} | `{train['variant_id']}` | {train['trade_count']} | {train['win_count']}-{train['loss_count']} | "
            f"{train['expectancy_r'] if train['expectancy_r'] is not None else 'N/A'} | {train['pnl']} | "
            f"{h['trade_count']} | {h['win_count']}-{h['loss_count']} | "
            f"{h['expectancy_r'] if h['expectancy_r'] is not None else 'N/A'} | {h['pnl']} |"
        )
    robust = []
    for train in ranked[:30]:
        h = holdout[train["variant_id"]]
        te = Decimal(str(train["expectancy_r"])) if train["expectancy_r"] is not None else None
        he = Decimal(str(h["expectancy_r"])) if h["expectancy_r"] is not None else None
        if int(train["trade_count"]) >= 5 and te is not None and te > 0 and int(h["trade_count"]) >= 1 and he is not None and he > 0:
            robust.append((train, h))
    lines.extend(["", "## V2 conclusion", ""])
    if robust:
        train, h = robust[0]
        lines.extend([
            f"Preliminary candidate: `{train['variant_id']}`.",
            f"Training: {train['trade_count']} trades, {train['expectancy_r']}R expectancy, P&L {train['pnl']}.",
            f"Holdout: {h['trade_count']} trades, {h['expectancy_r']}R expectancy, P&L {h['pnl']}.",
            "This is still a tiny reconstructed sample. Treat it as a model-selection lead, not a profitability claim; next expand the frozen validation window before production promotion.",
        ])
    else:
        lines.append("No v2 variant meets the preliminary positive train+holdout rule. Continue evolving without changing production defaults.")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args():
    p = argparse.ArgumentParser(description="Run cache-only failed-selloff v2 evolution.")
    p.add_argument("--start-date", default="2026-08-10")
    p.add_argument("--end-date", default="2026-08-21")
    p.add_argument("--train-sessions", type=int, default=6)
    p.add_argument("--top-k", type=int, default=30)
    p.add_argument("--dataset-cache-dir", default=".cache/trading-liquidity-datasets")
    p.add_argument("--output-dir", default="artifacts/failed-selloff-v2")
    p.add_argument("--initial-cash", default="100000")
    p.add_argument("--assumed-spread-bps", default="40")
    p.add_argument("--max-hold-minutes", type=int, default=90)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    _backtest_module.evaluate_gap_pullback = _failed_selloff_v2_evaluate
    start = date.fromisoformat(args.start_date)
    end = date.fromisoformat(args.end_date)
    dates = _trading_dates(start, end)
    if args.train_sessions <= 0 or args.train_sessions >= len(dates):
        raise ValueError("train-sessions must leave holdout sessions")
    spread = Decimal(args.assumed_spread_bps)
    initial_cash = Decimal(args.initial_cash)
    namespace, _ = _cache_namespace(strict_v11_strategy(minimum_premarket_dollar_volume=Decimal("250000")), spread)
    cache = Path(args.dataset_cache_dir) / namespace
    datasets: list[BacktestSessionDataset] = []
    for session_date in dates:
        path = _dataset_cache_path(cache, session_date)
        if not path.exists():
            raise FileNotFoundError(f"missing frozen dataset: {path}")
        datasets.append(_load_cached_dataset(path, session_date))
    train = datasets[:args.train_sessions]
    holdout = datasets[args.train_sessions:]
    variants = list(_grid())
    print(f"Failed-selloff v2: searching {len(variants)} variants on {len(train)} train sessions; {len(holdout)} holdout.")
    training = []
    for idx, variant in enumerate(variants, 1):
        training.append(_run_variant(variant, train, initial_cash=initial_cash, spread=spread, max_hold_minutes=args.max_hold_minutes))
        if idx % 100 == 0 or idx == len(variants):
            print(f"V2 progress: {idx}/{len(variants)}")
    ranked = sorted(training, key=_rank_key, reverse=True)
    by_id = {variant.variant_id: variant for variant in variants}
    selected = [row["variant_id"] for row in ranked[:args.top_k]]
    validation = [_run_variant(by_id[variant_id], holdout, initial_cash=initial_cash, spread=spread, max_hold_minutes=args.max_hold_minutes) for variant_id in selected]
    full = [_run_variant(by_id[variant_id], datasets, initial_cash=initial_cash, spread=spread, max_hold_minutes=args.max_hold_minutes) for variant_id in selected]

    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    (output / "training-results.json").write_text(json.dumps(training, indent=2, default=str) + "\n", encoding="utf-8")
    (output / "top-training.json").write_text(json.dumps(ranked[:args.top_k], indent=2, default=str) + "\n", encoding="utf-8")
    (output / "holdout-results.json").write_text(json.dumps(validation, indent=2, default=str) + "\n", encoding="utf-8")
    (output / "full-results.json").write_text(json.dumps(full, indent=2, default=str) + "\n", encoding="utf-8")
    _write_csv(output / "top-comparison.csv", [("train", row) for row in ranked[:args.top_k]] + [("holdout", row) for row in validation] + [("full", row) for row in full])
    _summary(output / "summary.md", [d.session_date for d in train], [d.session_date for d in holdout], ranked, validation)
    print((output / "summary.md").read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
