from __future__ import annotations

"""Replay early-single and its v2 arms on an intraday-evolving top-gainer universe.

Every ``cadence`` minutes from 09:30 ET to 15:30 ET, all cached symbols are
ranked by current price versus the prior regular close, using only 5m bars
that had finished by the scan. A symbol may trade only on entries at or after
the scan that admitted it (``sticky``), or only while it is in the most recent
scan's Top-N (``current``). Earlier bars still warm up the indicators. Trades,
sizing and accounting are otherwise the v2 replay's. The symbol pool is every
cached symbol, which came from historical top-gainer lists.
"""

import argparse
import bisect
import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

import build_causal_premarket_universe as scan_source
import run_interday_winner_shadow_replay_core as core
import run_stoch_rsi_early_single_v2_replay as v2run

from app.apps.trading import strategy_stoch_rsi_5m_early_single as early_single
from app.apps.trading.strategy_stoch_rsi_5m import evaluate_stoch_rsi_5m
from app.apps.trading.strategy_stoch_rsi_5m_early_single_v2 import context_size_weight, manage_trade

ET = scan_source.ET
FIVE_MINUTES = timedelta(minutes=5)
FIRST_SCAN = time(9, 30)
LAST_SCAN = time(15, 30)
MIN_PRICE = Decimal("0.50")
MAX_PRICE = Decimal("20")


def _scan_times(session: date, cadence: int) -> list[datetime]:
    current = datetime.combine(session, FIRST_SCAN, tzinfo=ET)
    last = datetime.combine(session, LAST_SCAN, tzinfo=ET)
    times = []
    while current <= last:
        times.append(current)
        current += timedelta(minutes=cadence)
    return times


def _symbol_day_series(bars, session: date):
    """(bar end times, closes, cumulative dollar volume) for the session from 04:00 ET."""

    ends, closes, cumulative = [], [], []
    total = Decimal("0")
    for bar in sorted(bars, key=lambda b: str(b["start"])):
        start = scan_source._start_et(bar)
        if start.date() != session or start.time() < scan_source.PREMARKET_OPEN:
            continue
        close = Decimal(str(bar["close"]))
        total += close * Decimal(str(bar["volume"]))
        ends.append(start + FIVE_MINUTES)
        closes.append(close)
        cumulative.append(total)
    return ends, closes, cumulative


def build_admissions(tapes, sessions, calendar, *, cadences, tops, min_dollar_volume):
    """Return {(cadence, top): {session: {symbol: [scan times in Top-N]}}}."""

    result = {(c, n): defaultdict(lambda: defaultdict(list)) for c in cadences for n in tops}
    for session in sessions:
        prior_days = [d for d in calendar if d < session]
        if not prior_days:
            continue
        prior = prior_days[-1]
        series = {}
        for symbol, by_date in tapes.items():
            prior_regular = scan_source._regular(by_date.get(prior, []))
            if not prior_regular or session not in by_date:
                continue
            prior_close = Decimal(str(prior_regular[-1]["close"]))
            if prior_close > 0:
                series[symbol] = (prior_close, *_symbol_day_series(by_date[session], session))
        for cadence in cadences:
            for scan in _scan_times(session, cadence):
                scored = []
                for symbol, (prior_close, ends, closes, cumulative) in series.items():
                    index = bisect.bisect_right(ends, scan) - 1
                    if index < 0:
                        continue
                    price = closes[index]
                    if not (MIN_PRICE <= price <= MAX_PRICE) or cumulative[index] < min_dollar_volume:
                        continue
                    scored.append(((price / prior_close - 1) * 100, cumulative[index], symbol))
                scored.sort(key=lambda item: (-item[0], -item[1], item[2]))
                for top in tops:
                    for _, _, symbol in scored[:top]:
                        result[(cadence, top)][session][symbol].append(scan)
    return result


def _entry_allowed(policy: str, in_scans: list[datetime], all_scans: list[datetime]):
    admitted_at = in_scans[0]
    members = set(in_scans)

    def allowed(entry_time: datetime) -> bool:
        if entry_time < admitted_at:
            return False
        if policy == "sticky":
            return True
        latest = all_scans[bisect.bisect_right(all_scans, entry_time) - 1]
        return latest in members

    return allowed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sessions-from", default="docs/trading/HISTORICAL_TOP8_GAINERS_2026-03-16_TO_2026-09-11.csv")
    parser.add_argument("--cache-dir", default=str(core.CACHE_DIR))
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--cadences", default="5,15,30")
    parser.add_argument("--tops", default="5,8")
    parser.add_argument("--policies", default="current,sticky")
    parser.add_argument("--min-dollar-volume", type=Decimal, default=Decimal("500000"))
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--discovery-sessions", type=int, default=40)
    parser.add_argument("--validation-sessions", type=int, default=12)
    args = parser.parse_args()

    cadences = [int(x) for x in args.cadences.split(",")]
    tops = [int(x) for x in args.tops.split(",")]
    policies = args.policies.split(",")
    core.ACTIVE_SOURCE = "alpaca-sip"
    core.CACHE_DIR = Path(args.cache_dir)
    core.CACHE_ONLY = True
    core.reset_cache_stats()

    _, sessions, _ = core._parse_source(Path(args.sessions_from), expected_symbols_per_session=None)
    tapes = scan_source._load(core.CACHE_DIR / core.ACTIVE_SOURCE)
    if not tapes:
        raise RuntimeError(f"no cached tapes under {core.CACHE_DIR / core.ACTIVE_SOURCE}")
    regular_days = defaultdict(int)
    for by_date in tapes.values():
        for day, bars in by_date.items():
            if scan_source._regular(bars):
                regular_days[day] += 1
    calendar = sorted(d for d, count in regular_days.items() if count >= len(tapes) // 4)
    print(f"Scanning {len(tapes)} symbols x {len(sessions)} sessions...", flush=True)
    admissions = build_admissions(
        tapes, sessions, calendar, cadences=cadences, tops=tops, min_dollar_volume=args.min_dollar_volume
    )
    pool_size = len(tapes)
    del tapes

    needed: dict[str, dict[date, dict[str, object]]] = defaultdict(dict)
    for by_session in admissions.values():
        for session, members in by_session.items():
            for symbol in members:
                needed[symbol][session] = {"session_date": session, "rank": 1, "symbol": symbol, "gain_pct": None, "source_url": ""}
    symbols = sorted(needed)
    print(f"Loading tapes for {len(symbols)} admitted symbols...", flush=True)
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        loaded = dict(zip(symbols, pool.map(lambda s: core._load_symbol(s, needed[s], sessions), symbols)))
    failed = {s: d.five_minute_error for s, d in loaded.items() if d.five_minute_error}
    if failed:
        raise RuntimeError(f"{len(failed)} symbols failed to load, e.g. {next(iter(failed.items()))}")

    # One canonical evaluation per symbol-day serves every universe variant.
    canonical_cache: dict[int, object] = {}

    def cached_canonical(bars, config=None):
        key = id(bars)
        if key not in canonical_cache:
            canonical_cache.clear()
            canonical_cache[key] = evaluate_stoch_rsi_5m(bars, config)
        return canonical_cache[key]

    early_single.evaluate_stoch_rsi_5m = cached_canonical
    arms = v2run._arms()
    observations: list[dict[str, object]] = []
    variants = [(p, c, n) for p in policies for c in cadences for n in tops]
    for symbol in symbols:
        data = loaded[symbol]
        for session in sessions:
            if session not in needed[symbol] or data.five_minute_error or not data.bars_5m.get(session):
                continue
            history = data.bars_5m_history.get(session) or data.bars_5m[session]
            bars = core._market_bars(history, f"equity:US:{symbol}", "5m")
            for policy, cadence, top in variants:
                in_scans = admissions[(cadence, top)][session].get(symbol)
                if not in_scans:
                    continue
                all_scans = _scan_times(session, cadence)
                snapshot = early_single.evaluate_stoch_rsi_5m_early_single(
                    bars, entry_allowed=_entry_allowed(policy, in_scans, all_scans)
                )
                if not snapshot.trades:
                    continue
                trade0 = snapshot.trades[0]
                weight, size_reason = context_size_weight(bars, trade0)
                universe = f"{policy}-top{top}-every{cadence}m"
                rank = 1 + sorted(admissions[(cadence, top)][session], key=lambda s: admissions[(cadence, top)][session][s][0]).index(symbol)
                for arm in arms:
                    trade, partial_price = trade0, None
                    if arm.policy is not None:
                        trade, _, partial_price = manage_trade(bars, trade0, arm.policy)
                    observations.append(
                        {
                            "arm": f"{universe}|{arm.name}",
                            "universe": universe,
                            "session_date": session,
                            "symbol": symbol,
                            "rank": rank,
                            "admitted_at": in_scans[0],
                            "entry_time": trade.entry_time,
                            "exit_time": trade.exit_time,
                            "entry_price": trade.entry_price,
                            "exit_price": trade.exit_price,
                            "partial_price": partial_price,
                            "reason": trade.exit_reason_code,
                            "return_pct": trade.return_pct,
                            "canonical_return_pct": trade0.return_pct,
                            "size_weight": weight if arm.sized else Decimal("1"),
                            "size_reason": size_reason if arm.sized else "",
                            "allocation": Decimal("0"),
                            "pnl": Decimal("0"),
                        }
                    )

    v2run._allocate(observations)
    parts = v2run._partitions(sessions, args.discovery_sessions, args.validation_sessions)
    by_arm: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in observations:
        by_arm[str(row["arm"])].append(row)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    table_rows = []
    lines = [
        "# STOCH_RSI_5M_EARLY_SINGLE on an intraday-evolving top-gainer universe",
        "",
        f"- Sessions: {len(sessions)} ({sessions[0]} to {sessions[-1]}); partitions {', '.join(f'{k} {len(v)}' for k, v in parts.items() if k != 'full')}",
        f"- Scans every 5/15/30 minutes from {FIRST_SCAN:%H:%M} to {LAST_SCAN:%H:%M} ET; price ${MIN_PRICE}-${MAX_PRICE}; session dollar volume >= ${args.min_dollar_volume:,.0f}",
        f"- Symbol pool: {pool_size} cached symbols, assembled from historical top-gainer lists",
        "- Arm A is chosen per universe on discovery only.",
        "",
        "| Universe | Arm | Discovery | Validation | Holdout | Full | Trades | W/L | $10k compounded | Max DD |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for policy, cadence, top in variants:
        universe = f"{policy}-top{top}-every{cadence}m"
        table = {
            arm.name: {
                part: v2run._metrics(
                    [r for r in by_arm[f"{universe}|{arm.name}"] if r["session_date"] in set(days)], days
                )
                for part, days in parts.items()
            }
            for arm in arms
        }
        a_grid = [arm for arm in arms if arm.family == "A"]
        chosen = max(a_grid, key=lambda arm: table[arm.name]["discovery"]["pnl"])
        suffix = chosen.name.removeprefix("A-")
        for name in (v2run.BASELINE, chosen.name, "C-context-sizing", f"D-{suffix}"):
            t = table[name]
            full = t["full"]
            lines.append(
                f"| {universe} | {name} | "
                + " | ".join(f"{v2run._fmt(t[p]['return_pct'], 1)}%" for p in ("discovery", "validation", "holdout", "full"))
                + f" | {full['funded_trades']} | {full['wins']}/{full['losses']} | ${v2run._fmt(full['compound_end'], 0)} | {v2run._fmt(full['max_drawdown_pct'], 1)}% |"
            )
            for part, values in t.items():
                table_rows.append({"universe": universe, "arm": name, "partition": part, **values})

    core._write_csv(
        output_dir / "universe-arm-partition-summary.csv",
        table_rows,
        ["universe", "arm", "partition", "sessions", "funded_trades", "wins", "losses", "pnl", "return_pct",
         "pnl_ex_top5", "compound_end", "max_drawdown_pct", "deployed_capital"],
    )
    baseline_rows = [r for r in observations if str(r["arm"]).endswith(f"|{v2run.BASELINE}")]
    core._write_csv(
        output_dir / "baseline-observations.csv",
        sorted(baseline_rows, key=lambda r: (str(r["universe"]), r["session_date"], str(r["symbol"]))),
        ["universe", "session_date", "symbol", "rank", "admitted_at", "entry_time", "exit_time", "entry_price",
         "exit_price", "reason", "return_pct", "size_weight", "allocation", "pnl"],
    )
    (output_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (output_dir / "run-config.json").write_text(
        json.dumps(
            {
                "research_version": "stoch-rsi-5m-early-single-evolving-universe-v1",
                "sessions": [s.isoformat() for s in sessions],
                "cadences_minutes": cadences,
                "tops": tops,
                "policies": policies,
                "first_scan_et": FIRST_SCAN.isoformat(),
                "last_scan_et": LAST_SCAN.isoformat(),
                "min_price": str(MIN_PRICE),
                "max_price": str(MAX_PRICE),
                "min_session_dollar_volume": str(args.min_dollar_volume),
                "admitted_symbols": len(symbols),
                "market_data_cache_stats": core.cache_stats(),
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
