from __future__ import annotations

"""Replay STOCH_RSI_5M_EARLY_SINGLE v2 trade-management and sizing arms.

Arm A (profit lock) is chosen from a small parameter grid on the discovery
sessions only. Arms B (A + partial take) and D (A + context sizing) reuse that
choice, and C (context sizing) has fixed rules. Validation and holdout are then
reported once without re-tuning. Accounting matches the existing research:
$100k per day in $20k slots funded in entry-time order, at most five trades a
day; context sizing funds a fraction of a slot. End-of-day benchmark labels are
never used by strategy rules, but the universe itself is end-of-day gainers.
"""

import argparse
import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import run_interday_winner_shadow_replay_core as core

from app.apps.trading.strategy_stoch_rsi_5m_early_single import evaluate_stoch_rsi_5m_early_single
from app.apps.trading.strategy_stoch_rsi_5m_early_single_v2 import (
    ExitPolicy,
    context_size_weight,
    manage_trade,
)

LOCK_TRIGGERS = (Decimal("3"), Decimal("5"))
TRAIL_MULTIPLES = (None, Decimal("1.5"), Decimal("2.5"), Decimal("3.5"))
PARTIAL_TARGET_PCT = Decimal("10")
PARTIAL_FRACTION = Decimal("1") / Decimal("3")
MAX_TRADES_PER_DAY = 5
COMPOUND_START = Decimal("10000")
BASELINE = "baseline"


@dataclass(frozen=True)
class Arm:
    name: str
    family: str
    policy: ExitPolicy | None
    sized: bool


def _policy_name(policy: ExitPolicy) -> str:
    trail = "be" if policy.trail_atr_multiple is None else f"atr{policy.trail_atr_multiple}"
    return f"lock{policy.lock_trigger_pct}-{trail}"


def _arms() -> list[Arm]:
    arms = [Arm(BASELINE, "baseline", None, False), Arm("C-context-sizing", "C", None, True)]
    for lock in LOCK_TRIGGERS:
        for trail in TRAIL_MULTIPLES:
            policy = ExitPolicy(lock_trigger_pct=lock, trail_atr_multiple=trail)
            name = _policy_name(policy)
            partial = policy.model_copy(
                update={"partial_target_pct": PARTIAL_TARGET_PCT, "partial_fraction": PARTIAL_FRACTION}
            )
            arms.append(Arm(f"A-{name}", "A", policy, False))
            arms.append(Arm(f"B-{name}", "B", partial, False))
            arms.append(Arm(f"D-{name}", "D", policy, True))
    return arms


def _evaluate(sessions, grouped, loaded, arms: list[Arm]) -> list[dict[str, object]]:
    observations: list[dict[str, object]] = []
    for symbol, data in loaded.items():
        rows_by_date = {d: r for d, rows in grouped.items() for r in rows if r["symbol"] == symbol}
        for session_date in sessions:
            source_row = rows_by_date.get(session_date)
            if source_row is None:
                continue
            five_raw = data.bars_5m.get(session_date, ())
            if data.five_minute_error or not five_raw:
                continue
            history = data.bars_5m_history.get(session_date) or five_raw
            bars = core._market_bars(history, f"equity:US:{symbol}", "5m")
            snapshot = evaluate_stoch_rsi_5m_early_single(bars)
            if not snapshot.trades:
                continue
            canonical = snapshot.trades[0]
            weight, size_reason = context_size_weight(bars, canonical)
            for arm in arms:
                trade, partial_price = canonical, None
                if arm.policy is not None:
                    trade, _, partial_price = manage_trade(bars, canonical, arm.policy)
                observations.append(
                    {
                        "arm": arm.name,
                        "session_date": session_date,
                        "symbol": symbol,
                        "rank": int(source_row["rank"]),
                        "entry_time": trade.entry_time,
                        "exit_time": trade.exit_time,
                        "entry_price": trade.entry_price,
                        "exit_price": trade.exit_price,
                        "partial_price": partial_price,
                        "reason": trade.exit_reason_code,
                        "return_pct": trade.return_pct,
                        "canonical_return_pct": canonical.return_pct,
                        "size_weight": weight if arm.sized else Decimal("1"),
                        "size_reason": size_reason if arm.sized else "",
                        "allocation": Decimal("0"),
                        "pnl": Decimal("0"),
                    }
                )
    return observations


def _allocate(observations: list[dict[str, object]]) -> None:
    by_arm_day: dict[tuple[str, date], list[dict[str, object]]] = defaultdict(list)
    for row in observations:
        by_arm_day[(str(row["arm"]), row["session_date"])].append(row)
    for rows in by_arm_day.values():
        remaining = core.FIXED_DAILY_CAPITAL
        ordered = sorted(rows, key=lambda r: (r["entry_time"], int(r["rank"]), str(r["symbol"])))
        for row in ordered[:MAX_TRADES_PER_DAY]:
            allocation = min(core.FIXED_SLOT_NOTIONAL * Decimal(str(row["size_weight"])), remaining)
            if allocation <= 0:
                break
            row["allocation"] = allocation
            row["pnl"] = allocation * Decimal(str(row["return_pct"])) / Decimal("100")
            remaining -= allocation


def _partitions(sessions: list[date], discovery: int, validation: int) -> dict[str, list[date]]:
    d_end = min(discovery, len(sessions))
    v_end = min(d_end + validation, len(sessions))
    return {
        "discovery": sessions[:d_end],
        "validation": sessions[d_end:v_end],
        "holdout": sessions[v_end:],
        "full": sessions,
    }


def _metrics(rows: list[dict[str, object]], days: list[date]) -> dict[str, object]:
    funded = [r for r in rows if r["allocation"]]
    pnl = sum((r["pnl"] for r in funded), Decimal("0"))
    top5 = sum(sorted((r["pnl"] for r in funded), reverse=True)[:5], Decimal("0"))
    daily = defaultdict(Decimal)
    for r in funded:
        daily[r["session_date"]] += r["pnl"]
    equity = peak = COMPOUND_START
    max_dd = Decimal("0")
    for day in days:
        equity *= Decimal("1") + daily[day] / core.FIXED_DAILY_CAPITAL
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak)
    return {
        "sessions": len(days),
        "funded_trades": len(funded),
        "wins": sum(r["return_pct"] > 0 for r in funded),
        "losses": sum(r["return_pct"] < 0 for r in funded),
        "pnl": pnl,
        "return_pct": pnl / core.FIXED_DAILY_CAPITAL * Decimal("100"),
        "pnl_ex_top5": pnl - top5,
        "compound_end": equity,
        "max_drawdown_pct": max_dd * Decimal("100"),
        "deployed_capital": sum((r["allocation"] for r in funded), Decimal("0")),
    }


def _fmt(value: object, places: int = 2) -> str:
    if isinstance(value, Decimal):
        return f"{value:,.{places}f}"
    return str(value)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="docs/trading/HISTORICAL_TOP8_GAINERS_2026-03-16_TO_2026-09-11.csv")
    parser.add_argument("--cohort-size", type=int, default=8)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--source", choices=("yahoo", "alpaca-sip"), default="alpaca-sip")
    parser.add_argument("--cache-dir", default=str(core.CACHE_DIR))
    parser.add_argument("--cache-only", action="store_true")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--discovery-sessions", type=int, default=40)
    parser.add_argument("--validation-sessions", type=int, default=12)
    args = parser.parse_args()

    core.ACTIVE_SOURCE = args.source
    core.CACHE_DIR = Path(args.cache_dir)
    core.CACHE_ONLY = args.cache_only
    core.reset_cache_stats()
    input_path = Path(args.input)
    rows, sessions, grouped = core._parse_source(input_path, expected_symbols_per_session=args.cohort_size or None)
    by_symbol: dict[str, dict[date, dict[str, object]]] = defaultdict(dict)
    for row in rows:
        by_symbol[str(row["symbol"])][row["session_date"]] = row
    symbols = sorted(by_symbol)
    print(f"Loading {args.source} tapes for {len(symbols)} symbols...", flush=True)
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        loaded = dict(zip(symbols, pool.map(lambda s: core._load_symbol(s, by_symbol[s], sessions), symbols)))

    arms = _arms()
    observations = _evaluate(sessions, grouped, loaded, arms)
    _allocate(observations)
    parts = _partitions(sessions, args.discovery_sessions, args.validation_sessions)
    by_arm: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in observations:
        by_arm[str(row["arm"])].append(row)
    table = {
        arm.name: {
            part: _metrics([r for r in by_arm[arm.name] if r["session_date"] in set(days)], days)
            for part, days in parts.items()
        }
        for arm in arms
    }

    a_grid = [arm for arm in arms if arm.family == "A"]
    chosen = max(a_grid, key=lambda arm: table[arm.name]["discovery"]["pnl"])
    suffix = chosen.name.removeprefix("A-")
    headline = [BASELINE, chosen.name, f"B-{suffix}", "C-context-sizing", f"D-{suffix}"]

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    fields = ["arm", "session_date", "symbol", "rank", "entry_time", "exit_time", "entry_price", "exit_price",
              "partial_price", "reason", "return_pct", "canonical_return_pct", "size_weight", "size_reason",
              "allocation", "pnl"]
    core._write_csv(
        output_dir / "observations.csv",
        sorted(observations, key=lambda r: (str(r["arm"]), r["session_date"], int(r["rank"]))),
        fields,
    )
    metric_fields = ["arm", "partition", "sessions", "funded_trades", "wins", "losses", "pnl", "return_pct",
                     "pnl_ex_top5", "compound_end", "max_drawdown_pct", "deployed_capital"]
    core._write_csv(
        output_dir / "arm-partition-summary.csv",
        [{"arm": name, "partition": part, **values} for name, by_part in table.items() for part, values in by_part.items()],
        metric_fields,
    )

    labels = {
        BASELINE: "Baseline early-single",
        chosen.name: f"A · profit lock ({suffix})",
        f"B-{suffix}": f"B · A + 1/3 at +{PARTIAL_TARGET_PCT}%",
        "C-context-sizing": "C · context sizing",
        f"D-{suffix}": "D · A + C",
    }
    lines = [
        "# STOCH_RSI_5M_EARLY_SINGLE v2 research",
        "",
        f"- Input: `{input_path.as_posix()}` ({len(sessions)} sessions, {sessions[0]} to {sessions[-1]})",
        f"- Partitions: discovery {len(parts['discovery'])}, validation {len(parts['validation'])}, holdout {len(parts['holdout'])} sessions",
        f"- Arm A chosen on discovery only from {len(a_grid)} settings: `{suffix}`",
        "- The universe is end-of-day top gainers, so absolute returns are an upper bound; compare arms with the baseline.",
        "- Trade management only exits at or before the canonical exit.",
        "",
        "## Headline arms",
        "",
        "| Arm | Partition | Trades | W/L | P/L | Return | P/L ex top 5 | $10k compounded | Max DD |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name in headline:
        for part in ("discovery", "validation", "holdout", "full"):
            m = table[name][part]
            lines.append(
                f"| {labels[name]} | {part} | {m['funded_trades']} | {m['wins']}/{m['losses']} | "
                f"${_fmt(m['pnl'], 0)} | {_fmt(m['return_pct'])}% | ${_fmt(m['pnl_ex_top5'], 0)} | "
                f"${_fmt(m['compound_end'], 0)} | {_fmt(m['max_drawdown_pct'], 1)}% |"
            )
    lines.extend(
        [
            "",
            "## Arm A grid (selection uses discovery only)",
            "",
            "| Setting | Discovery | Validation | Holdout | Full |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for arm in a_grid:
        t = table[arm.name]
        lines.append(
            f"| {arm.name.removeprefix('A-')} | "
            + " | ".join(f"{_fmt(t[p]['return_pct'])}%" for p in ("discovery", "validation", "holdout", "full"))
            + " |"
        )
    (output_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (output_dir / "run-config.json").write_text(
        json.dumps(
            {
                "research_version": "stoch-rsi-5m-early-single-v2",
                "input": input_path.as_posix(),
                "sessions": [s.isoformat() for s in sessions],
                "partitions": {k: len(v) for k, v in parts.items()},
                "lock_triggers_pct": [str(x) for x in LOCK_TRIGGERS],
                "trail_atr_multiples": [None if x is None else str(x) for x in TRAIL_MULTIPLES],
                "partial_target_pct": str(PARTIAL_TARGET_PCT),
                "partial_fraction": str(PARTIAL_FRACTION),
                "chosen_arm_a": chosen.name,
                "selection_partition": "discovery",
                "max_trades_per_day": MAX_TRADES_PER_DAY,
                "daily_capital": str(core.FIXED_DAILY_CAPITAL),
                "slot_notional": str(core.FIXED_SLOT_NOTIONAL),
                "market_data_source": args.source,
                "market_data_cache_only": args.cache_only,
                "market_data_cache_stats": core.cache_stats(),
                "generated_at": datetime.now().isoformat(timespec="seconds"),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print((output_dir / "summary.md").read_text(encoding="utf-8"), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
