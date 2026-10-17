from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime, time
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from app.apps.trading.gapper_dataset import freeze_gapper_universe
from app.apps.trading.historical_gapper_reconstruction import (
    AlpacaHistoricalGapperReconstructor,
    reconstructed_strategy_config,
)
from app.apps.trading.paper import PaperExecutionPolicy
from app.apps.trading.providers.http_runtime import ProviderHttpRuntime
from app.apps.trading.strategy_backtest import freeze_backtest_session, run_gap_pullback_backtest
from app.apps.trading.strategy_historical_bars import alpaca_historical_session_bars
from scripts.run_trading_strategy_backtest import strict_v11_strategy
from scripts.run_trading_strategy_liquidity_sweep import (
    _dates,
    _label,
    _outcome_counts,
    _thresholds,
    _trade_dump,
    _trading_dates,
    _with_rate_limit_retry,
)


_ET = ZoneInfo("America/New_York")
_DEFAULT_THRESHOLDS = "250000,500000,1000000,2000000,5000000,10000000"
_FIDELITY_ADJUSTMENT = (
    "historical TOD RVOL baseline unavailable for some reconstructed candidates; "
    "missing TOD RVOL is neutrally imputed to the configured minimum only in this "
    "diagnostic replay; measured TOD RVOL values remain unchanged and must still pass"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Diagnose strict v1.1 structure gates after explicitly neutralizing only "
            "missing TOD-RVOL values in reconstructed historical evidence."
        )
    )
    parser.add_argument("--start-date", default="")
    parser.add_argument("--end-date", default="")
    parser.add_argument("--lookback-days", type=int, default=14)
    parser.add_argument("--initial-cash", default="100000")
    parser.add_argument("--assumed-spread-bps", default="40")
    parser.add_argument("--max-hold-minutes", type=int, default=90)
    parser.add_argument("--reconstruction-max-age-days", type=int, default=30)
    parser.add_argument("--max-sessions", type=int, default=60)
    parser.add_argument("--thresholds", default=_DEFAULT_THRESHOLDS)
    parser.add_argument("--output-dir", default="artifacts/structure-sweep")
    parser.add_argument("--require-covered-session", action="store_true")
    return parser.parse_args()


def _neutralize_missing_tod_rvol(universe, minimum_tod_rvol: Decimal):
    adjusted_count = 0
    candidates = []
    for candidate in universe.candidates:
        if candidate.tod_rvol is None:
            adjusted_count += 1
            candidates.append(candidate.model_copy(update={"tod_rvol": minimum_tod_rvol}))
        else:
            candidates.append(candidate)
    if adjusted_count == 0:
        return universe, 0
    adjusted = freeze_gapper_universe(
        universe_id=f"{universe.universe_id}-rvol-neutral",
        session_date=universe.session_date,
        evaluation_time=universe.evaluation_time,
        discovery_source=universe.discovery_source,
        candidates=candidates,
    )
    return adjusted, adjusted_count


def main() -> int:
    args = parse_args()
    start, end = _dates(args.start_date or None, args.end_date or None, args.lookback_days)
    sessions = _trading_dates(start, end)
    if len(sessions) > args.max_sessions:
        raise ValueError(f"backtest session limit exceeded: {len(sessions)}>{args.max_sessions}")

    thresholds = _thresholds(args.thresholds)
    initial_cash = Decimal(args.initial_cash)
    assumed_spread_bps = Decimal(args.assumed_spread_bps)
    if initial_cash <= 0:
        raise ValueError("initial cash must be positive")

    base_strategy = strict_v11_strategy(minimum_premarket_dollar_volume=min(thresholds))
    reconstructor = AlpacaHistoricalGapperReconstructor(
        start_date=start,
        end_date=end,
        config=base_strategy.config,
        assumed_spread_bps=assumed_spread_bps,
        max_age_days=args.reconstruction_max_age_days,
    )
    regular_runtime = ProviderHttpRuntime("alpaca_strategy_structure_sweep", max_concurrency=2)
    execution_policy = PaperExecutionPolicy(max_volume_participation_pct=Decimal("1"))

    state: dict[Decimal, dict[str, object]] = {
        threshold: {
            "cash": initial_cash,
            "candidate_count": 0,
            "trigger_count": 0,
            "trades": [],
            "outcomes": Counter(),
            "days": [],
        }
        for threshold in thresholds
    }
    covered_sessions = 0
    no_candidate_sessions = 0
    unavailable_sessions: list[dict[str, object]] = []
    dataset_fingerprints: dict[str, str] = {}
    total_missing_rvol_adjustments = 0

    for session_date in sessions:
        print(f"Reconstructing {session_date} once for {len(thresholds)} structure variants", flush=True)
        reconstruction = _with_rate_limit_retry(
            f"reconstruct {session_date}",
            lambda session_date=session_date: reconstructor(
                session_date=session_date,
                scan_time=time(9, 20),
                config=base_strategy.config,
                assumed_spread_bps=assumed_spread_bps,
                max_age_days=args.reconstruction_max_age_days,
            ),
        )
        universe = reconstruction.snapshot
        if universe is None:
            if reconstruction.fidelity == "reconstructed_current_listings_iex":
                covered_sessions += 1
                no_candidate_sessions += 1
                for threshold in thresholds:
                    state[threshold]["days"].append(
                        {
                            "session_date": session_date.isoformat(),
                            "status": "no_candidates",
                            "detail": reconstruction.detail,
                        }
                    )
            else:
                unavailable_sessions.append(
                    {
                        "session_date": session_date.isoformat(),
                        "fidelity": reconstruction.fidelity,
                        "detail": reconstruction.detail,
                    }
                )
            continue

        adjusted_universe, missing_rvol_count = _neutralize_missing_tod_rvol(
            universe,
            base_strategy.config.minimum_tod_rvol,
        )
        total_missing_rvol_adjustments += missing_rvol_count
        bars_by_instrument = _with_rate_limit_retry(
            f"regular bars {session_date}",
            lambda adjusted_universe=adjusted_universe, session_date=session_date: alpaca_historical_session_bars(
                adjusted_universe.candidates,
                session_date,
                runtime=regular_runtime,
            ),
        )
        dataset = freeze_backtest_session(
            session_date=session_date,
            universe=adjusted_universe,
            bars_by_instrument=bars_by_instrument,
        )
        covered_sessions += 1
        dataset_fingerprints[session_date.isoformat()] = dataset.dataset_fingerprint

        for threshold in thresholds:
            strategy = strict_v11_strategy(minimum_premarket_dollar_volume=threshold)
            active_config, fidelity_adjustments = reconstructed_strategy_config(strategy.config)
            current_cash: Decimal = state[threshold]["cash"]
            result = run_gap_pullback_backtest(
                dataset,
                active_config,
                execution_policy,
                assumed_spread_bps=assumed_spread_bps,
                max_hold_minutes=args.max_hold_minutes,
                max_concurrent_positions=strategy.risk.max_positions,
                risk_profile=strategy.risk,
                initial_cash=current_cash,
            )
            pnl = sum(
                (trade.pnl_per_share * trade.entry_fill_quantity for trade in result.trades),
                Decimal("0"),
            )
            outcomes = _outcome_counts(result)
            state[threshold]["cash"] = current_cash + pnl
            state[threshold]["candidate_count"] += result.summary.candidate_count
            state[threshold]["trigger_count"] += result.summary.trigger_count
            state[threshold]["trades"].extend(result.trades)
            state[threshold]["outcomes"].update(outcomes)
            state[threshold]["days"].append(
                {
                    "session_date": session_date.isoformat(),
                    "status": "backtested",
                    "dataset_fingerprint": result.dataset_fingerprint,
                    "candidate_count": result.summary.candidate_count,
                    "trigger_count": result.summary.trigger_count,
                    "trade_count": result.summary.trade_count,
                    "pnl": str(pnl),
                    "missing_tod_rvol_neutralized": missing_rvol_count,
                    "fidelity_adjustments": [*fidelity_adjustments, _FIDELITY_ADJUSTMENT],
                    "candidate_outcomes": dict(outcomes),
                    "trades": [_trade_dump(trade) for trade in result.trades],
                }
            )

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    comparison: list[dict[str, object]] = []

    for threshold in thresholds:
        threshold_state = state[threshold]
        trades = list(threshold_state["trades"])
        outcomes: Counter[str] = threshold_state["outcomes"]
        ending_cash: Decimal = threshold_state["cash"]
        pnl = ending_cash - initial_cash if covered_sessions else None
        return_pct = (pnl / initial_cash) * Decimal("100") if pnl is not None else None
        expectancy_r = (
            sum((trade.r_multiple for trade in trades), Decimal("0")) / Decimal(len(trades))
            if trades
            else None
        )
        win_count = sum(trade.r_multiple > 0 for trade in trades)
        loss_count = sum(trade.r_multiple < 0 for trade in trades)
        candidate_count = int(threshold_state["candidate_count"])
        liquidity_rejected = int(outcomes.get("PREMARKET_DOLLAR_VOLUME_LOW", 0))
        comparison_row = {
            "threshold": int(threshold),
            "label": _label(threshold),
            "covered_sessions": covered_sessions,
            "requested_sessions": len(sessions),
            "candidate_count": candidate_count,
            "not_rejected_by_liquidity_gate": candidate_count - liquidity_rejected,
            "trigger_count": int(threshold_state["trigger_count"]),
            "trade_count": len(trades),
            "win_count": win_count,
            "loss_count": loss_count,
            "pnl": None if pnl is None else str(pnl),
            "return_pct": None if return_pct is None else str(return_pct),
            "expectancy_r": None if expectancy_r is None else str(expectancy_r),
            "premarket_volume_low": liquidity_rejected,
            "no_market_data": int(outcomes.get("NO_MARKET_DATA", 0)),
            "candidate_outcomes": dict(outcomes),
        }
        comparison.append(comparison_row)
        variant = {
            **comparison_row,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "result_quality": "approximate_reconstructed_rvol_neutralized" if covered_sessions else "unavailable",
            "fidelity_adjustment": _FIDELITY_ADJUSTMENT,
            "days": threshold_state["days"],
            "trades": [_trade_dump(trade) for trade in trades],
        }
        (output_dir / f"variant-{_label(threshold)}.json").write_text(
            json.dumps(variant, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    (output_dir / "comparison.json").write_text(
        json.dumps(comparison, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    fieldnames = [
        "threshold",
        "label",
        "covered_sessions",
        "requested_sessions",
        "candidate_count",
        "not_rejected_by_liquidity_gate",
        "trigger_count",
        "trade_count",
        "win_count",
        "loss_count",
        "pnl",
        "return_pct",
        "expectancy_r",
        "premarket_volume_low",
        "no_market_data",
    ]
    with (output_dir / "comparison.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in comparison:
            writer.writerow({field: row[field] for field in fieldnames})
    (output_dir / "dataset-fingerprints.json").write_text(
        json.dumps(dataset_fingerprints, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "unavailable-sessions.json").write_text(
        json.dumps(unavailable_sessions, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    lines = [
        "# Strict v1.1 structure diagnostic after TOD-RVOL missing-data neutralization",
        "",
        f"- Period: {start} through {end}",
        f"- Coverage: {covered_sessions}/{len(sessions)} trading sessions",
        f"- Valid no-candidate sessions: {no_candidate_sessions}",
        f"- Missing TOD-RVOL candidate observations neutralized: {total_missing_rvol_adjustments}",
        "- Fidelity: **approximate reconstructed Alpaca IEX + explicit missing-RVOL neutralization**",
        "- Measured TOD-RVOL below 5x is NOT relaxed.",
        "- All variants reuse the same derived frozen universe and regular-session bars for each date.",
        "- Only `minimum_premarket_dollar_volume` changes between variants.",
        "",
        "| Premarket $ gate | Candidates | Pass $ gate | Triggers | Trades | W-L | P&L | Expectancy R |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in comparison:
        pnl_text = "N/A" if row["pnl"] is None else str(row["pnl"])
        expectancy_text = "N/A" if row["expectancy_r"] is None else str(row["expectancy_r"])
        lines.append(
            f"| ${row['threshold']:,} | {row['candidate_count']} | {row['not_rejected_by_liquidity_gate']} | "
            f"{row['trigger_count']} | {row['trade_count']} | {row['win_count']}-{row['loss_count']} | "
            f"{pnl_text} | {expectancy_text} |"
        )
    lines.extend(
        [
            "",
            "## Fidelity adjustment",
            "",
            f"- {_FIDELITY_ADJUSTMENT}",
            "- Historical catalyst, dilution/supply, float, and true historical spread evidence remain unavailable in reconstruction and use the existing explicit fidelity downgrades.",
            "- This diagnostic is for locating the next structural bottleneck; it does not change live or AUTO PAPER strategy semantics.",
        ]
    )
    summary = "\n".join(lines) + "\n"
    (output_dir / "summary.md").write_text(summary, encoding="utf-8")
    print(summary, flush=True)

    if args.require_covered_session and covered_sessions == 0:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
