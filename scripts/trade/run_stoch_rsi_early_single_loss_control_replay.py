from __future__ import annotations

"""Replay isolated STOCH_RSI_5M_EARLY_SINGLE loss-control arms.

This runner intentionally reuses the canonical interday cache/loader and the
same normalized $100k / five-$20k-slot accounting as the broader deterministic
shadow replay.  The supplied cohort may be hindsight-selected; no end-of-day
rank or gain label is used by any strategy rule.
"""

import argparse
import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import run_interday_winner_shadow_replay_core as core

from app.apps.trading.strategy_stoch_rsi_5m_early_single_loss_controls import (
    StochRsiEarlySingleLossControlArm,
    evaluate_stoch_rsi_5m_early_single_loss_control,
)


ARM_SPECS: tuple[tuple[str, StochRsiEarlySingleLossControlArm], ...] = (
    ("stoch-rsi-5min-early-single-cap150", "baseline_cap150"),
    ("stoch-rsi-5min-early-single-cap150-structural-stop", "structural_stop"),
    ("stoch-rsi-5min-early-single-cap150-early-failure-exit", "early_failure_exit"),
    ("stoch-rsi-5min-early-single-cap150-ema-slope-positive", "ema_slope_positive"),
    ("stoch-rsi-5min-early-single-cap150-above-vwap", "above_vwap"),
    ("stoch-rsi-5min-early-single-cap150-recovery-vol-1x", "recovery_volume_1x"),
    ("stoch-rsi-5min-early-single-cap150-recovery-vol-1_25x", "recovery_volume_1_25x"),
    ("stoch-rsi-5min-early-single-cap150-recovery-high-break", "recovery_high_break"),
)
ARM_NAMES = tuple(name for name, _arm in ARM_SPECS)
BASELINE_ARM = ARM_NAMES[0]
FIXED_DAILY_CAPITAL = core.FIXED_DAILY_CAPITAL


def _evaluate(
    sessions: list[date],
    grouped: dict[date, list[dict[str, object]]],
    loaded: dict[str, core.SymbolReplayData],
) -> list[dict[str, object]]:
    observations: list[dict[str, object]] = []
    for symbol, data in loaded.items():
        source_by_date = {
            session_date: row
            for session_date, rows in grouped.items()
            for row in rows
            if row["symbol"] == symbol
        }
        for session_date in sessions:
            source_row = source_by_date.get(session_date)
            if source_row is None:
                continue
            five_raw = data.bars_5m.get(session_date, ())
            if data.five_minute_error:
                for arm_name, _control in ARM_SPECS:
                    observations.append(
                        core._base_observation(
                            arm_name,
                            session_date,
                            source_row,
                            symbol=symbol,
                            status="data_unavailable",
                            reason=data.five_minute_error,
                            data_source=core._data_source("5m"),
                        )
                    )
                continue
            if not five_raw:
                for arm_name, _control in ARM_SPECS:
                    observations.append(
                        core._base_observation(
                            arm_name,
                            session_date,
                            source_row,
                            symbol=symbol,
                            status="data_unavailable",
                            reason="STOCH_RSI_5M_REGULAR_BARS_UNAVAILABLE",
                            data_source=core._data_source("5m"),
                        )
                    )
                continue

            five_history_raw = data.bars_5m_history.get(session_date) or five_raw
            market_bars = core._market_bars(
                five_history_raw,
                f"equity:US:{symbol}",
                "5m",
            )
            for arm_name, control in ARM_SPECS:
                snapshot = evaluate_stoch_rsi_5m_early_single_loss_control(
                    market_bars,
                    control,
                )
                trades = tuple(snapshot.trades)
                trade = trades[0] if trades else None
                return_pct = trade.return_pct if trade is not None else None
                observations.append(
                    core._base_observation(
                        arm_name,
                        session_date,
                        source_row,
                        symbol=symbol,
                        status="completed" if trade is not None else snapshot.state,
                        reason=snapshot.reason_code,
                        entry_time=trade.entry_time if trade is not None else snapshot.entry_time,
                        exit_time=trade.exit_time if trade is not None else snapshot.exit_time,
                        entry_price=trade.entry_price if trade is not None else snapshot.entry_price,
                        exit_price=trade.exit_price if trade is not None else snapshot.exit_price,
                        return_pct=return_pct,
                        trade_count=1 if trade is not None else 0,
                        win_count=1 if trade is not None and trade.return_pct > 0 else 0,
                        loss_count=1 if trade is not None and trade.return_pct < 0 else 0,
                        data_source=core._data_source("5m"),
                    )
                )
    return observations


def _gross_positive(rows: list[dict[str, object]]) -> Decimal:
    return sum(
        (
            Decimal(str(row["normalized_pnl"]))
            for row in rows
            if Decimal(str(row["normalized_pnl"])) > 0
        ),
        Decimal("0"),
    )


def _gross_negative(rows: list[dict[str, object]]) -> Decimal:
    return -sum(
        (
            Decimal(str(row["normalized_pnl"]))
            for row in rows
            if Decimal(str(row["normalized_pnl"])) < 0
        ),
        Decimal("0"),
    )


def _comparison_rows(observations: list[dict[str, object]]) -> list[dict[str, object]]:
    by_arm: dict[str, list[dict[str, object]]] = {
        arm: [row for row in observations if row["arm"] == arm]
        for arm in ARM_NAMES
    }
    baseline = by_arm[BASELINE_ARM]
    baseline_by_key = {
        (row["session_date"], row["symbol"]): row
        for row in baseline
    }
    baseline_gross_loss = _gross_negative(baseline)
    baseline_gross_win = _gross_positive(baseline)
    baseline_net = sum(
        (Decimal(str(row["normalized_pnl"])) for row in baseline),
        Decimal("0"),
    )

    output: list[dict[str, object]] = []
    for arm in ARM_NAMES:
        rows = by_arm[arm]
        current_by_key = {
            (row["session_date"], row["symbol"]): row
            for row in rows
        }
        loss_trades_removed = 0
        winners_removed = 0
        for key, baseline_row in baseline_by_key.items():
            baseline_return = baseline_row["return_pct"]
            if baseline_return is None:
                continue
            current = current_by_key.get(key)
            current_return = current.get("return_pct") if current is not None else None
            if Decimal(str(baseline_return)) < 0 and (
                current_return is None or Decimal(str(current_return)) >= 0
            ):
                loss_trades_removed += 1
            if Decimal(str(baseline_return)) > 0 and (
                current_return is None or Decimal(str(current_return)) <= 0
            ):
                winners_removed += 1

        gross_loss = _gross_negative(rows)
        gross_win = _gross_positive(rows)
        net = sum(
            (Decimal(str(row["normalized_pnl"])) for row in rows),
            Decimal("0"),
        )
        output.append(
            {
                "arm": arm,
                "loss_trades_removed": loss_trades_removed if arm != BASELINE_ARM else 0,
                "gross_loss_saved": baseline_gross_loss - gross_loss if arm != BASELINE_ARM else Decimal("0"),
                "winners_removed": winners_removed if arm != BASELINE_ARM else 0,
                "winning_pnl_retained_pct": (
                    gross_win / baseline_gross_win * Decimal("100")
                    if baseline_gross_win > 0
                    else Decimal("0")
                ),
                "gross_winning_pnl": gross_win,
                "gross_losing_pnl": -gross_loss,
                "net_pnl": net,
                "net_change_vs_baseline": net - baseline_net,
            }
        )
    return output


def _write_summary(
    path: Path,
    *,
    input_path: Path,
    sessions: list[date],
    observations: list[dict[str, object]],
    comparisons: list[dict[str, object]],
) -> None:
    baseline_rejects = sum(
        row["reason"] == "STOCH_RSI_5M_EARLY_SINGLE_PRE_ENTRY_RANGE_ABOVE_150"
        for row in observations
        if row["arm"] == BASELINE_ARM
    )
    lines = [
        "# STOCH_RSI_5M_EARLY_SINGLE loss-control ablation",
        "",
        f"- Input: `{input_path.as_posix()}`",
        f"- Period: {sessions[0]} through {sessions[-1]} ({len(sessions)} sessions)",
        f"- Baseline: `{BASELINE_ARM}`",
        f"- Baseline pre-entry range-cap rejections: {baseline_rejects}",
        "- Every child arm inherits cap150 and changes exactly one causal rule.",
        "- Normalization: same reused $100,000 daily basket / $20,000 slot / five-slot cap as the canonical interday replay.",
        "- The input cohort can be hindsight-selected; end-of-day rank/gain labels are never used by these rules.",
        "",
        "| Rule | Loss trades removed | Gross loss saved | Winners removed | Winning P/L retained | New net P/L |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    labels = {
        ARM_NAMES[0]: "Baseline cap150",
        ARM_NAMES[1]: "+ structural stop",
        ARM_NAMES[2]: "+ early-failure exit",
        ARM_NAMES[3]: "EMA slope > 0",
        ARM_NAMES[4]: "Above VWAP",
        ARM_NAMES[5]: "recovery vol >=1.0x",
        ARM_NAMES[6]: "recovery vol >=1.25x",
        ARM_NAMES[7]: "recovery-high break",
    }
    for row in comparisons:
        lines.append(
            f"| {labels[str(row['arm'])]} | {row['loss_trades_removed']} | "
            f"${Decimal(str(row['gross_loss_saved'])):.2f} | {row['winners_removed']} | "
            f"{Decimal(str(row['winning_pnl_retained_pct'])):.2f}% | "
            f"${Decimal(str(row['net_pnl'])):.2f} |"
        )
    lines.extend(
        [
            "",
            "## Rule semantics",
            "",
            "- **Structural stop:** guarded-v1 geometry: minimum low from oversold arm through recovery signal; after a finalized 5m close below it, exit on the next 5m open if that is earlier than the canonical exit.",
            "- **Early-failure exit:** after two completed 5m bars (10 minutes), exit next open only when MFE is <=1%, price is below entry, and price is below session VWAP.",
            "- **EMA slope > 0:** guarded-v1 50-period 5m EMA, three-bar slope, evaluated at the canonical recovery signal.",
            "- **Above VWAP:** recovery-signal close and canonical entry open must both be above causal session VWAP.",
            "- **Recovery volume:** recovery-signal volume divided by average volume from oversold arm up to (excluding) recovery signal, with guarded-v1's three-bar fallback.",
            "- **Recovery-high break:** require a later finalized close above the recovery candle high while Stoch RSI remains recovered; enter the next 5m open if the target branch's v15 EMA price confirmation still passes.",
            "",
            "`Winning P/L retained` is normalized positive P/L for the arm divided by normalized positive P/L for cap150. Because removing an earlier trade can free one of the five daily slots for a later winner, this value can exceed 100%.",
            "",
            "Research/shadow evidence only; no broker or order side effects.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay isolated loss controls for STOCH_RSI_5M_EARLY_SINGLE."
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--source", choices=("yahoo", "alpaca-sip"), default="alpaca-sip")
    parser.add_argument("--cache-dir", default=str(core.CACHE_DIR))
    parser.add_argument("--cohort-size", type=int, default=8)
    parser.add_argument("--cache-only", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.cohort_size < 0:
        raise ValueError("--cohort-size must be non-negative")

    core.ACTIVE_SOURCE = args.source
    core.CACHE_DIR = Path(args.cache_dir)
    core.CACHE_ONLY = args.cache_only
    core.ARMS = ARM_NAMES
    core.reset_cache_stats()

    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    rows, sessions, grouped = core._parse_source(
        input_path,
        expected_symbols_per_session=args.cohort_size or None,
    )
    symbols = sorted({str(row["symbol"]) for row in rows})
    source_by_symbol: dict[str, dict[date, dict[str, object]]] = defaultdict(dict)
    for row in rows:
        source_by_symbol[str(row["symbol"])][row["session_date"]] = row

    loaded: dict[str, core.SymbolReplayData] = {}
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {
            executor.submit(core._load_symbol, symbol, source_by_symbol[symbol], sessions): symbol
            for symbol in symbols
        }
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                loaded[symbol] = future.result()
            except Exception as exc:
                if core.CACHE_ONLY:
                    raise RuntimeError(
                        f"cache-only replay could not load {symbol}: {exc}"
                    ) from exc
                loaded[symbol] = core.SymbolReplayData(
                    symbol=symbol,
                    candidates={},
                    bars_5m={},
                    bars_1m={},
                    five_minute_error=f"{type(exc).__name__}: {exc}",
                    one_minute_errors={},
                )

    observations = _evaluate(sessions, grouped, loaded)
    core._apply_normalized_allocations(observations, sessions)
    arm_summary, daily = core._summary_rows(observations, sessions)
    comparisons = _comparison_rows(observations)

    output_dir.mkdir(parents=True, exist_ok=True)
    observation_fields = [
        "arm", "session_date", "symbol", "rank", "benchmark_gain_pct", "status", "reason",
        "entry_time", "exit_time", "entry_price", "exit_price", "return_pct", "trade_count",
        "win_count", "loss_count", "risk_managed_pnl", "normalized_allocation", "normalized_pnl", "data_source",
    ]
    core._write_csv(
        output_dir / "observations.csv",
        sorted(
            observations,
            key=lambda row: (
                str(row["arm"]),
                row["session_date"],
                int(row["rank"]),
                str(row["symbol"]),
            ),
        ),
        observation_fields,
    )
    core._write_csv(
        output_dir / "arm-summary.csv",
        arm_summary,
        [
            "arm", "evaluable_sessions", "benchmark_observations", "completed_trades",
            "wins", "losses", "normalized_pnl", "normalized_return_pct",
        ],
    )
    daily_fields = ["session_date"]
    for arm in ARM_NAMES:
        daily_fields.extend([f"{arm}_pnl", f"{arm}_return_pct", f"{arm}_trades"])
    core._write_csv(output_dir / "daily-summary.csv", daily, daily_fields)
    core._write_csv(
        output_dir / "loss-control-summary.csv",
        comparisons,
        [
            "arm", "loss_trades_removed", "gross_loss_saved", "winners_removed",
            "winning_pnl_retained_pct", "gross_winning_pnl", "gross_losing_pnl",
            "net_pnl", "net_change_vs_baseline",
        ],
    )
    (output_dir / "run-config.json").write_text(
        json.dumps(
            {
                "strategy": "STOCH_RSI_5M_EARLY_SINGLE",
                "arms": list(ARM_NAMES),
                "input": input_path.as_posix(),
                "sessions": [item.isoformat() for item in sessions],
                "cohort_size": args.cohort_size or None,
                "market_data_source": core.ACTIVE_SOURCE,
                "cache_dir": core.CACHE_DIR.as_posix(),
                "cache_only": core.CACHE_ONLY,
                "cache_stats": core.cache_stats(),
                "normalized_daily_capital": str(core.FIXED_DAILY_CAPITAL),
                "normalized_slot_notional": str(core.FIXED_SLOT_NOTIONAL),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    _write_summary(
        output_dir / "summary.md",
        input_path=input_path,
        sessions=sessions,
        observations=observations,
        comparisons=comparisons,
    )
    print((output_dir / "summary.md").read_text(encoding="utf-8"), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
