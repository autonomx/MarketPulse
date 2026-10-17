from __future__ import annotations

"""Replay the corrected cap150 parent and isolated early-single research arms.

The runner reuses the canonical interday Alpaca/Yahoo cache loader and the same
normalized $100k / five-$20k-slot accounting as the existing loss-control
ablation. All strategy decisions are causal; benchmark rank/gain labels are
output metadata only.
"""

import argparse
import csv
import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import run_interday_winner_shadow_replay_core as core

from app.apps.trading.strategy_stoch_rsi_5m_early_single_loss_controls import (
    evaluate_stoch_rsi_5m_early_single_loss_control,
)
from app.apps.trading.strategy_stoch_rsi_5m_early_single_research import (
    EARLY_FAILURE_ARM_SPECS,
    ONE_MINUTE_STOP_ARMS,
    PATTERN_ARMS,
    VWAP_RECLAIM_ARMS,
    evaluate_stoch_rsi_5m_early_single_research_arm,
)
from app.apps.trading.strategy_stoch_rsi_5m_research_exit import (
    recompute_stoch_rsi_5m_exit_from_entry,
)


@dataclass(frozen=True)
class ArmSpec:
    name: str
    label: str
    family: str
    evaluator: str
    control: str


ORIGINAL_ARM_SPECS = (
    ArmSpec("stoch-rsi-5min-early-single-cap150", "Baseline cap150", "original-eight", "legacy", "baseline_cap150"),
    ArmSpec("stoch-rsi-5min-early-single-cap150-structural-stop", "+ structural stop", "original-eight", "legacy", "structural_stop"),
    ArmSpec("stoch-rsi-5min-early-single-cap150-early-failure-exit", "+ original early-failure exit", "original-eight", "legacy", "early_failure_exit"),
    ArmSpec("stoch-rsi-5min-early-single-cap150-ema-slope-positive", "EMA slope > 0", "original-eight", "legacy", "ema_slope_positive"),
    ArmSpec("stoch-rsi-5min-early-single-cap150-above-vwap", "Above VWAP", "original-eight", "legacy", "above_vwap"),
    ArmSpec("stoch-rsi-5min-early-single-cap150-recovery-vol-1x", "Recovery volume >=1.0x", "original-eight", "legacy", "recovery_volume_1x"),
    ArmSpec("stoch-rsi-5min-early-single-cap150-recovery-vol-1_25x", "Recovery volume >=1.25x", "original-eight", "legacy", "recovery_volume_1_25x"),
    ArmSpec("stoch-rsi-5min-early-single-cap150-recovery-high-break", "Recovery-high break", "original-eight", "legacy", "recovery_high_break"),
)

NEW_ARM_SPECS = [
    ArmSpec("stoch-rsi-5min-early-single-cap150-no-entry-1030-1100", "No entry 10:30-11:00 ET", "time-of-day", "research", "no_entry_1030_1100"),
    ArmSpec("stoch-rsi-5min-early-single-cap150-hard-stop-2pct-1m", "1m hard stop -2%", "one-minute-stop", "research", "hard_stop_2pct"),
    ArmSpec("stoch-rsi-5min-early-single-cap150-hard-stop-3pct-1m", "1m hard stop -3%", "one-minute-stop", "research", "hard_stop_3pct"),
    ArmSpec("stoch-rsi-5min-early-single-cap150-hard-stop-4pct-1m", "1m hard stop -4%", "one-minute-stop", "research", "hard_stop_4pct"),
    ArmSpec("stoch-rsi-5min-early-single-cap150-hard-stop-5pct-1m", "1m hard stop -5%", "one-minute-stop", "research", "hard_stop_5pct"),
    ArmSpec("stoch-rsi-5min-early-single-cap150-atr14-2x-stop-1m", "1m ATR14 2x stop", "one-minute-stop", "research", "atr_stop_2x_14"),
    ArmSpec("stoch-rsi-5min-early-single-cap150-vwap-reclaim-1bar", "VWAP reclaim <=1 bar", "vwap-reclaim", "research", "vwap_reclaim_1bar"),
    ArmSpec("stoch-rsi-5min-early-single-cap150-vwap-reclaim-2bar", "VWAP reclaim <=2 bars", "vwap-reclaim", "research", "vwap_reclaim_2bar"),
    ArmSpec("stoch-rsi-5min-early-single-cap150-vwap-reclaim-3bar", "VWAP reclaim <=3 bars", "vwap-reclaim", "research", "vwap_reclaim_3bar"),
    ArmSpec("stoch-rsi-5min-early-single-cap150-failed-selloff", "Failed selloff", "reversal-pattern", "research", "failed_selloff"),
    ArmSpec("stoch-rsi-5min-early-single-cap150-higher-low", "Higher low", "reversal-pattern", "research", "higher_low"),
    ArmSpec("stoch-rsi-5min-early-single-cap150-lower-high-break", "Lower-high break", "reversal-pattern", "research", "lower_high_break"),
    ArmSpec("stoch-rsi-5min-early-single-cap150-double-bottom", "Double bottom", "reversal-pattern", "research", "double_bottom"),
    ArmSpec("stoch-rsi-5min-early-single-cap150-volume-exhaustion", "Volume exhaustion", "reversal-pattern", "research", "volume_exhaustion"),
    ArmSpec("stoch-rsi-5min-early-single-cap150-reversal-structure-v1", "Reversal structure v1", "reversal-pattern", "research", "reversal_structure_v1"),
    ArmSpec("stoch-rsi-5min-early-single-cap150-reversal-structure-aggressive-v1", "Reversal structure aggressive v1", "reversal-pattern", "research", "reversal_structure_aggressive_v1"),
]

for control, (minutes, condition, mfe) in sorted(EARLY_FAILURE_ARM_SPECS.items()):
    condition_label = "close < entry" if condition == "entry" else "close < VWAP"
    mfe_label = "MFE unrestricted" if mfe is None else f"MFE <= {mfe}%"
    NEW_ARM_SPECS.append(
        ArmSpec(
            f"stoch-rsi-5min-early-single-cap150-{control.replace('_', '-')}",
            f"Early failure {minutes}m: {condition_label}, {mfe_label}",
            "early-failure-grid",
            "research",
            control,
        )
    )

ARM_SPECS = (*ORIGINAL_ARM_SPECS, *NEW_ARM_SPECS)
ARM_NAMES = tuple(spec.name for spec in ARM_SPECS)
BASELINE_ARM = ORIGINAL_ARM_SPECS[0].name
FIXED_DAILY_CAPITAL = core.FIXED_DAILY_CAPITAL
_ONE_MINUTE_CONTROLS = {*ONE_MINUTE_STOP_ARMS, "atr_stop_2x_14"}
_DELAYED_ENTRY_CONTROLS = {*VWAP_RECLAIM_ARMS, *PATTERN_ARMS, "recovery_high_break"}


def _uses_one_minute_control(spec: ArmSpec) -> bool:
    return spec.evaluator == "research" and spec.control in _ONE_MINUTE_CONTROLS


def _one_minute_unavailable_reason(data: core.SymbolReplayData) -> str:
    if data.one_minute_errors:
        values = sorted({str(value) for value in data.one_minute_errors.values() if value})
        if values:
            return "; ".join(values)
    return "STOCH_RSI_5M_EARLY_SINGLE_1M_STOP_DATA_UNAVAILABLE"


def _replace_snapshot_trade(snapshot, trade):
    state = "force_flat" if trade.exit_reason_code == "STOCH_RSI_5M_FORCE_FLAT" else "exited"
    return snapshot.model_copy(
        update={
            "state": state,
            "reason_code": trade.exit_reason_code,
            "entry_time": trade.entry_time,
            "entry_price": trade.entry_price,
            "exit_signal_time": trade.exit_signal_time,
            "exit_time": trade.exit_time,
            "exit_price": trade.exit_price,
            "return_pct": trade.return_pct,
            "trades": (trade,),
        }
    )


def _normalize_one_minute_stop_snapshot(snapshot):
    """Keep stop-event evidence causal when exact intraminute trigger time is unknown.

    A standing hard/ATR stop can fill within a one-minute bar. OHLCV does not
    identify the exact second of the trigger, so the replay uses that minute's
    start as both the fill timestamp and the conservative signal/event timestamp
    instead of recording ``bar.end_time`` after an already-recorded fill.
    """

    if not snapshot.trades:
        return snapshot
    trade = snapshot.trades[0]
    if not trade.exit_reason_code.startswith("STOCH_RSI_5M_EARLY_SINGLE_1M_"):
        return snapshot
    if trade.exit_signal_time is None or trade.exit_signal_time <= trade.exit_time:
        return snapshot
    corrected = trade.model_copy(update={"exit_signal_time": trade.exit_time})
    return _replace_snapshot_trade(snapshot, corrected)


def _recompute_delayed_entry_exit(snapshot, bars_5m, spec: ArmSpec):
    if spec.control not in _DELAYED_ENTRY_CONTROLS or not snapshot.trades:
        return snapshot
    corrected = recompute_stoch_rsi_5m_exit_from_entry(
        bars_5m,
        snapshot.trades[0],
    )
    return _replace_snapshot_trade(snapshot, corrected)


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
            if data.five_minute_error or not five_raw:
                reason = data.five_minute_error or "STOCH_RSI_5M_REGULAR_BARS_UNAVAILABLE"
                for spec in ARM_SPECS:
                    observations.append(
                        core._base_observation(
                            spec.name,
                            session_date,
                            source_row,
                            symbol=symbol,
                            status="data_unavailable",
                            reason=reason,
                            data_source=core._data_source("5m"),
                        )
                    )
                continue

            five_history_raw = data.bars_5m_history.get(session_date) or five_raw
            bars_5m = core._market_bars(five_history_raw, f"equity:US:{symbol}", "5m")
            one_raw = data.bars_1m.get(session_date, ())
            bars_1m = core._market_bars(one_raw, f"equity:US:{symbol}", "1m") if one_raw else []

            for spec in ARM_SPECS:
                uses_one_minute = _uses_one_minute_control(spec)
                if uses_one_minute and not one_raw:
                    observations.append(
                        core._base_observation(
                            spec.name,
                            session_date,
                            source_row,
                            symbol=symbol,
                            status="data_unavailable",
                            reason=_one_minute_unavailable_reason(data),
                            data_source=f"{core._data_source('5m')}+{core._data_source('1m')}",
                        )
                    )
                    continue

                if spec.evaluator == "legacy":
                    snapshot = evaluate_stoch_rsi_5m_early_single_loss_control(
                        bars_5m,
                        spec.control,
                    )
                    data_source = core._data_source("5m")
                else:
                    snapshot = evaluate_stoch_rsi_5m_early_single_research_arm(
                        bars_5m,
                        spec.control,
                        one_minute_bars=bars_1m,
                    )
                    if uses_one_minute:
                        snapshot = _normalize_one_minute_stop_snapshot(snapshot)
                    data_source = (
                        f"{core._data_source('5m')}+{core._data_source('1m')}"
                        if uses_one_minute
                        else core._data_source("5m")
                    )

                snapshot = _recompute_delayed_entry_exit(snapshot, bars_5m, spec)
                trades = tuple(snapshot.trades)
                trade = trades[0] if trades else None
                observations.append(
                    core._base_observation(
                        spec.name,
                        session_date,
                        source_row,
                        symbol=symbol,
                        status="completed" if trade is not None else snapshot.state,
                        reason=snapshot.reason_code,
                        entry_time=trade.entry_time if trade is not None else snapshot.entry_time,
                        exit_time=trade.exit_time if trade is not None else snapshot.exit_time,
                        entry_price=trade.entry_price if trade is not None else snapshot.entry_price,
                        exit_price=trade.exit_price if trade is not None else snapshot.exit_price,
                        return_pct=trade.return_pct if trade is not None else None,
                        trade_count=1 if trade is not None else 0,
                        win_count=1 if trade is not None and trade.return_pct > 0 else 0,
                        loss_count=1 if trade is not None and trade.return_pct < 0 else 0,
                        data_source=data_source,
                    )
                )
    return observations


def _gross_positive(rows: list[dict[str, object]]) -> Decimal:
    return sum(
        (Decimal(str(row["normalized_pnl"])) for row in rows if Decimal(str(row["normalized_pnl"])) > 0),
        Decimal("0"),
    )


def _gross_negative(rows: list[dict[str, object]]) -> Decimal:
    return -sum(
        (Decimal(str(row["normalized_pnl"])) for row in rows if Decimal(str(row["normalized_pnl"])) < 0),
        Decimal("0"),
    )


def _comparison_rows(observations: list[dict[str, object]]) -> list[dict[str, object]]:
    by_arm = {arm: [row for row in observations if row["arm"] == arm] for arm in ARM_NAMES}
    baseline = by_arm[BASELINE_ARM]
    baseline_by_key = {(row["session_date"], row["symbol"]): row for row in baseline}
    baseline_gross_loss = _gross_negative(baseline)
    baseline_gross_win = _gross_positive(baseline)
    baseline_net = sum((Decimal(str(row["normalized_pnl"])) for row in baseline), Decimal("0"))
    spec_by_name = {spec.name: spec for spec in ARM_SPECS}

    output: list[dict[str, object]] = []
    for arm in ARM_NAMES:
        rows = by_arm[arm]
        current_by_key = {(row["session_date"], row["symbol"]): row for row in rows}
        losses_removed = 0
        winners_removed = 0
        affected = 0
        for key, baseline_row in baseline_by_key.items():
            baseline_return = baseline_row["return_pct"]
            current = current_by_key.get(key)
            current_return = current.get("return_pct") if current is not None else None
            if baseline_return is None:
                continue
            baseline_decimal = Decimal(str(baseline_return))
            current_decimal = Decimal(str(current_return)) if current_return is not None else None
            if current_decimal != baseline_decimal:
                affected += 1
            if baseline_decimal < 0 and (current_decimal is None or current_decimal >= 0):
                losses_removed += 1
            if baseline_decimal > 0 and (current_decimal is None or current_decimal <= 0):
                winners_removed += 1

        gross_loss = _gross_negative(rows)
        gross_win = _gross_positive(rows)
        net = sum((Decimal(str(row["normalized_pnl"])) for row in rows), Decimal("0"))
        spec = spec_by_name[arm]
        output.append(
            {
                "arm": arm,
                "label": spec.label,
                "family": spec.family,
                "affected_trades": affected if arm != BASELINE_ARM else 0,
                "loss_trades_removed": losses_removed if arm != BASELINE_ARM else 0,
                "gross_loss_saved": baseline_gross_loss - gross_loss if arm != BASELINE_ARM else Decimal("0"),
                "winners_removed": winners_removed if arm != BASELINE_ARM else 0,
                "winning_pnl_retained_pct": gross_win / baseline_gross_win * Decimal("100") if baseline_gross_win > 0 else Decimal("0"),
                "gross_winning_pnl": gross_win,
                "gross_losing_pnl": -gross_loss,
                "net_pnl": net,
                "net_change_vs_baseline": net - baseline_net,
            }
        )
    return output


def _split_rows(
    observations: list[dict[str, object]],
    sessions: list[date],
    *,
    discovery_count: int,
    validation_count: int,
) -> list[dict[str, object]]:
    discovery_end = min(discovery_count, len(sessions))
    validation_end = min(discovery_end + validation_count, len(sessions))
    partitions = (
        ("discovery", set(sessions[:discovery_end])),
        ("validation", set(sessions[discovery_end:validation_end])),
        ("holdout", set(sessions[validation_end:])),
    )
    output: list[dict[str, object]] = []
    for partition, dates in partitions:
        if not dates:
            continue
        for arm in ARM_NAMES:
            rows = [row for row in observations if row["arm"] == arm and row["session_date"] in dates]
            pnl = sum((Decimal(str(row["normalized_pnl"])) for row in rows), Decimal("0"))
            output.append(
                {
                    "partition": partition,
                    "arm": arm,
                    "sessions": len(dates),
                    "completed_trades": sum(int(row["trade_count"]) for row in rows),
                    "wins": sum(int(row["win_count"]) for row in rows),
                    "losses": sum(int(row["loss_count"]) for row in rows),
                    "normalized_pnl": pnl,
                    "normalized_return_pct": pnl / FIXED_DAILY_CAPITAL * Decimal("100"),
                }
            )
    return output


def _write_csv(path: Path, rows: list[dict[str, object]], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: core._csv_value(row.get(field)) for field in fields})


def _write_summary(
    path: Path,
    *,
    input_path: Path,
    sessions: list[date],
    comparisons: list[dict[str, object]],
    split_rows: list[dict[str, object]],
) -> None:
    by_family: defaultdict[str, list[dict[str, object]]] = defaultdict(list)
    for row in comparisons:
        by_family[str(row["family"])].append(row)

    lines = [
        "# STOCH_RSI_5M_EARLY_SINGLE corrected loss-control and reversal research",
        "",
        f"- Input: `{input_path.as_posix()}`",
        f"- Period: {sessions[0]} through {sessions[-1]} ({len(sessions)} sessions)",
        f"- Corrected parent: `{BASELINE_ARM}`",
        "- Cross-session early-single leakage is fixed before every arm is evaluated.",
        "- Every child starts from cap150 and changes only its named research hypothesis.",
        "- The 1-minute stop arms preserve the canonical 5-minute entry exactly.",
        "- Missing one-minute tape is `data_unavailable` for 1-minute stop arms; it is never treated as a strategy rejection.",
        "- One-minute stop event timestamps are normalized to the fill minute start because OHLCV does not reveal the exact intraminute trigger second.",
        "- VWAP-reclaim, reversal-pattern, and recovery-high-break exits are recomputed from their actual delayed entry using canonical v15 five-minute exit semantics.",
        "- End-of-day benchmark rank/gain labels are never used by strategy rules.",
        "",
        "## Original eight rerun",
        "",
        "| Rule | Affected trades | Loss trades removed | Gross loss saved | Winners removed | Winning P/L retained | Net P/L | Change vs baseline |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in by_family["original-eight"]:
        lines.append(
            f"| {row['label']} | {row['affected_trades']} | {row['loss_trades_removed']} | "
            f"${Decimal(str(row['gross_loss_saved'])):.2f} | {row['winners_removed']} | "
            f"{Decimal(str(row['winning_pnl_retained_pct'])):.2f}% | "
            f"${Decimal(str(row['net_pnl'])):.2f} | "
            f"${Decimal(str(row['net_change_vs_baseline'])):.2f} |"
        )

    for family in ("time-of-day", "one-minute-stop", "vwap-reclaim", "early-failure-grid", "reversal-pattern"):
        lines.extend(
            [
                "",
                f"## {family.replace('-', ' ').title()}",
                "",
                "| Arm | Affected | Losses removed | Gross loss saved | Winners removed | Winning P/L retained | Net P/L | Delta |",
                "|---|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for row in by_family[family]:
            lines.append(
                f"| {row['label']} | {row['affected_trades']} | {row['loss_trades_removed']} | "
                f"${Decimal(str(row['gross_loss_saved'])):.2f} | {row['winners_removed']} | "
                f"{Decimal(str(row['winning_pnl_retained_pct'])):.2f}% | "
                f"${Decimal(str(row['net_pnl'])):.2f} | "
                f"${Decimal(str(row['net_change_vs_baseline'])):.2f} |"
            )

    lines.extend(
        [
            "",
            "## Chronological partitions",
            "",
            "The default partition is the first 40 sessions for discovery, next 12 for validation, and the remainder as holdout. This is diagnostic only; a rule discovered on this 62-session dataset still needs a genuinely untouched future period.",
            "",
            "| Partition | Arm | Trades | W/L | Net P/L | Return |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    label_by_name = {spec.name: spec.label for spec in ARM_SPECS}
    for row in split_rows:
        lines.append(
            f"| {row['partition']} | {label_by_name[str(row['arm'])]} | "
            f"{row['completed_trades']} | {row['wins']}/{row['losses']} | "
            f"${Decimal(str(row['normalized_pnl'])):.2f} | "
            f"{Decimal(str(row['normalized_return_pct'])):.2f}% |"
        )

    lines.extend(
        [
            "",
            "## Frozen research semantics",
            "",
            "- **10:30-11:00 ET:** veto only canonical entries with start time in `[10:30, 11:00)` ET.",
            "- **1m hard stops:** trigger intraminute when a one-minute low reaches the stop. A gap through the stop fills at the one-minute open; otherwise the replay fills at the stop price.",
            "- **ATR stop:** 14-period one-minute ATR using only finalized one-minute bars ending before entry; stop distance is `2 x ATR`.",
            "- **VWAP reclaim:** entries already above causal VWAP are unchanged. Below-VWAP entries get 1, 2, or 3 completed five-minute bars to close above VWAP; entry is the following five-minute open only if it remains above that causal VWAP and the canonical EMA price gate still passes.",
            "- **Early-failure grid:** 5/10/15-minute checkpoints. `close < entry` and `close < VWAP` are tested separately. Each is paired with unrestricted MFE or MFE <=1%, <=2%, <=3%; the two price conditions are never stacked.",
            "- **Failed selloff:** a post-arm bar tests/undercuts the prior three-bar low within 0.5% and closes back above that prior low.",
            "- **Higher low:** after the selloff low, price bounces at least 2%, then a pullback low remains at least 0.5% above L1 and the next bar closes higher.",
            "- **Lower-high break:** close above the most recent causal pivot high preceding the selloff low.",
            "- **Double bottom:** second low within +/-3% of L1 followed by a close through the intervening neckline.",
            "- **Volume exhaustion:** late pre-signal volume is <=80% of earlier pre-signal volume.",
            "- **Reversal structure v1:** failed selloff + higher low + lower-high break.",
            "- **Aggressive reversal structure:** failed selloff + higher low, without waiting for the lower-high break.",
            "",
            "Research/shadow evidence only; no broker or order side effects.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay corrected cap150 and isolated early-single research arms.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--source", choices=("yahoo", "alpaca-sip"), default="alpaca-sip")
    parser.add_argument("--cache-dir", default=str(core.CACHE_DIR))
    parser.add_argument("--cohort-size", type=int, default=8)
    parser.add_argument("--cache-only", action="store_true")
    parser.add_argument("--start-date", help="Optional first session date (YYYY-MM-DD), inclusive.")
    parser.add_argument("--end-date", help="Optional last session date (YYYY-MM-DD), inclusive.")
    parser.add_argument("--discovery-sessions", type=int, default=40)
    parser.add_argument("--validation-sessions", type=int, default=12)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.cohort_size < 0:
        raise ValueError("--cohort-size must be non-negative")
    if args.discovery_sessions < 0 or args.validation_sessions < 0:
        raise ValueError("partition session counts must be non-negative")

    start_date = date.fromisoformat(args.start_date) if args.start_date else None
    end_date = date.fromisoformat(args.end_date) if args.end_date else None
    if start_date is not None and end_date is not None and start_date > end_date:
        raise ValueError("--start-date must be on or before --end-date")

    core.ACTIVE_SOURCE = args.source
    core.CACHE_DIR = Path(args.cache_dir)
    core.CACHE_ONLY = args.cache_only
    core.ARMS = ARM_NAMES
    core.reset_cache_stats()

    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    rows, sessions, grouped = core._parse_source(input_path, expected_symbols_per_session=args.cohort_size or None)
    sessions = [
        session
        for session in sessions
        if (start_date is None or session >= start_date)
        and (end_date is None or session <= end_date)
    ]
    if not sessions:
        raise ValueError("no sessions remain after applying the requested date window")
    selected_sessions = set(sessions)
    rows = [row for row in rows if row["session_date"] in selected_sessions]
    grouped = {session: grouped[session] for session in sessions}

    symbols = sorted({str(row["symbol"]) for row in rows})
    source_by_symbol: dict[str, dict[date, dict[str, object]]] = defaultdict(dict)
    for row in rows:
        source_by_symbol[str(row["symbol"])][row["session_date"]] = row

    loaded: dict[str, core.SymbolReplayData] = {}
    print(f"Loading {args.source} cached tapes for {len(symbols)} symbols...", flush=True)
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {
            executor.submit(core._load_symbol, symbol, source_by_symbol[symbol], sessions): symbol
            for symbol in symbols
        }
        for index, future in enumerate(as_completed(futures), start=1):
            symbol = futures[future]
            loaded[symbol] = future.result()
            data = loaded[symbol]
            print(
                f"[{index}/{len(symbols)}] {symbol}: "
                f"5m={sum(bool(v) for v in data.bars_5m.values())}/{len(sessions)}, "
                f"1m={sum(bool(v) for v in data.bars_1m.values())}/{len(sessions)}",
                flush=True,
            )

    observations = _evaluate(sessions, grouped, loaded)
    core._apply_normalized_allocations(observations, sessions)
    arm_summary, daily = core._summary_rows(observations, sessions)
    comparisons = _comparison_rows(observations)
    partitions = _split_rows(
        observations,
        sessions,
        discovery_count=args.discovery_sessions,
        validation_count=args.validation_sessions,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    observation_fields = [
        "arm", "session_date", "symbol", "rank", "benchmark_gain_pct", "status", "reason",
        "entry_time", "exit_time", "entry_price", "exit_price", "return_pct", "trade_count",
        "win_count", "loss_count", "risk_managed_pnl", "normalized_allocation", "normalized_pnl", "data_source",
    ]
    _write_csv(
        output_dir / "observations.csv",
        sorted(observations, key=lambda row: (str(row["arm"]), row["session_date"], int(row["rank"]), str(row["symbol"]))),
        observation_fields,
    )
    _write_csv(
        output_dir / "arm-summary.csv",
        arm_summary,
        ["arm", "evaluable_sessions", "benchmark_observations", "completed_trades", "wins", "losses", "normalized_pnl", "normalized_return_pct"],
    )
    daily_fields = ["session_date"]
    for arm in ARM_NAMES:
        daily_fields.extend([f"{arm}_pnl", f"{arm}_return_pct", f"{arm}_trades"])
    _write_csv(output_dir / "daily-summary.csv", daily, daily_fields)
    _write_csv(
        output_dir / "comparison.csv",
        comparisons,
        [
            "arm", "label", "family", "affected_trades", "loss_trades_removed", "gross_loss_saved",
            "winners_removed", "winning_pnl_retained_pct", "gross_winning_pnl", "gross_losing_pnl",
            "net_pnl", "net_change_vs_baseline",
        ],
    )
    _write_csv(
        output_dir / "chronological-split-summary.csv",
        partitions,
        ["partition", "arm", "sessions", "completed_trades", "wins", "losses", "normalized_pnl", "normalized_return_pct"],
    )

    (output_dir / "run-config.json").write_text(
        json.dumps(
            {
                "research_version": "stoch-rsi-5m-early-single-loss-reversal-v2",
                "parent_arm": BASELINE_ARM,
                "arms": [
                    {"name": spec.name, "label": spec.label, "family": spec.family, "control": spec.control}
                    for spec in ARM_SPECS
                ],
                "input": input_path.as_posix(),
                "start_date": sessions[0].isoformat(),
                "end_date": sessions[-1].isoformat(),
                "sessions": [session.isoformat() for session in sessions],
                "benchmark_observations": len(rows),
                "cohort_size": args.cohort_size or None,
                "market_data_source": args.source,
                "market_data_cache_dir": core.CACHE_DIR.as_posix(),
                "market_data_cache_only": args.cache_only,
                "market_data_cache_stats": core.cache_stats(),
                "normalization": {
                    "daily_capital": str(core.FIXED_DAILY_CAPITAL),
                    "slot_notional": str(core.FIXED_SLOT_NOTIONAL),
                    "max_slots": int(core.FIXED_DAILY_CAPITAL / core.FIXED_SLOT_NOTIONAL),
                },
                "chronological_partitions": {
                    "discovery_sessions": args.discovery_sessions,
                    "validation_sessions": args.validation_sessions,
                    "holdout_sessions": max(0, len(sessions) - args.discovery_sessions - args.validation_sessions),
                },
                "one_minute_stop_controls": sorted(_ONE_MINUTE_CONTROLS),
                "one_minute_stop_missing_data_policy": "data_unavailable",
                "one_minute_stop_event_timestamp_policy": "fill_minute_start",
                "delayed_entry_exit_recompute_controls": sorted(_DELAYED_ENTRY_CONTROLS),
                "vwap_reclaim_controls": sorted(VWAP_RECLAIM_ARMS),
                "early_failure_controls": sorted(EARLY_FAILURE_ARM_SPECS),
                "pattern_controls": sorted(PATTERN_ARMS),
            },
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    _write_summary(
        output_dir / "summary.md",
        input_path=input_path,
        sessions=sessions,
        comparisons=comparisons,
        split_rows=partitions,
    )
    print((output_dir / "summary.md").read_text(encoding="utf-8"), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
