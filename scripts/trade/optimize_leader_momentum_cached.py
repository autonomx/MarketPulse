from __future__ import annotations

"""Research-only cached parameter sweep for leader momentum continuation.

The benchmark's rank and eventual gain are metadata only.  Strategy inputs are
limited to cached causal bars plus the same 09:15 ET context used by the normal
replay.  This script never imports broker execution code or creates orders.
"""

import argparse
import csv
import json
import random
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from time import perf_counter


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
for import_root in (SOURCE_ROOT, REPOSITORY_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from app.apps.trading import strategy_leader_momentum_continuation as leader
from app.apps.trading.models import MarketBar
from scripts.trade import run_interday_winner_shadow_replay as replay


@dataclass(frozen=True)
class CohortItem:
    session_date: date
    symbol: str
    rank: int
    eventual_gain_pct: Decimal
    bars: tuple[MarketBar, ...]
    context: leader.LeaderMomentumContext


DEFAULT_PARAMETERS: dict[str, object] = {
    "MIN_IMPULSE_PCT": Decimal("8"),
    "MIN_RUNAWAY_IMPULSE_PCT": Decimal("10"),
    "MIN_BREAKOUT_VOLUME_RATIO": Decimal("1.25"),
    "MIN_COMPRESSION_VOLUME_RATIO": Decimal("1.10"),
    "MAX_PULLBACK_RETRACE": Decimal("0.45"),
    "MIN_PULLBACK_RETRACE": Decimal("0.15"),
    "MAX_PULLBACK_VOLUME_RATIO": Decimal("0.70"),
    "MAX_ENTRY_RISK_PCT": Decimal("8"),
    "MAX_EMA9_EXTENSION_PCT": Decimal("12"),
    "MAX_ATR_EXTENSION": Decimal("2"),
    "MIN_BREAKOUT_CLOSE_LOCATION": Decimal("0.60"),
    "MAX_COMPRESSION_WIDTH_RATIO": Decimal("0.35"),
    "REQUIRE_PULLBACK_NO_NEW_HIGH": True,
    "REQUIRE_COMPRESSION_ABOVE_EMA20": True,
    "REQUIRE_COMPRESSION_HOD_BREAK": True,
    "PARTIAL_TRIGGER_R": Decimal("3"),
    "STRUCTURAL_BUFFER_ATR": Decimal("0.50"),
    "BELOW_TREND_EXIT_BARS": 2,
    "DISTRIBUTION_RANGE_ATR": Decimal("1.5"),
    "DISTRIBUTION_VOLUME_RATIO": Decimal("1.5"),
    "ENABLE_STRUCTURAL_EXIT": True,
    "ENABLE_TREND_EXIT": True,
    "ENABLE_DISTRIBUTION_EXIT": True,
    "LEADER_LATCH_TTL": timedelta(minutes=30),
}


SEARCH_SPACE: dict[str, tuple[object, ...]] = {
    "MIN_IMPULSE_PCT": (Decimal("4"), Decimal("6"), Decimal("8")),
    "MIN_RUNAWAY_IMPULSE_PCT": (Decimal("5"), Decimal("7.5"), Decimal("10")),
    "MIN_BREAKOUT_VOLUME_RATIO": (Decimal("0.75"), Decimal("1"), Decimal("1.25")),
    "MIN_COMPRESSION_VOLUME_RATIO": (Decimal("0.75"), Decimal("1"), Decimal("1.10")),
    "MAX_PULLBACK_RETRACE": (Decimal("0.45"), Decimal("0.60"), Decimal("0.80"), Decimal("1")),
    "MIN_PULLBACK_RETRACE": (Decimal("0.05"), Decimal("0.10"), Decimal("0.15")),
    "MAX_PULLBACK_VOLUME_RATIO": (Decimal("0.70"), Decimal("1"), Decimal("1.50")),
    "MAX_ENTRY_RISK_PCT": (Decimal("8"), Decimal("12"), Decimal("16")),
    "MAX_ATR_EXTENSION": (Decimal("2"), Decimal("3"), Decimal("4")),
    "MIN_BREAKOUT_CLOSE_LOCATION": (Decimal("0.40"), Decimal("0.50"), Decimal("0.60")),
    "MAX_COMPRESSION_WIDTH_RATIO": (Decimal("0.35"), Decimal("0.50"), Decimal("0.75")),
    "REQUIRE_PULLBACK_NO_NEW_HIGH": (True, False),
    "REQUIRE_COMPRESSION_ABOVE_EMA20": (True, False),
    "REQUIRE_COMPRESSION_HOD_BREAK": (True, False),
    "STRUCTURAL_BUFFER_ATR": (Decimal("0.50"), Decimal("1"), Decimal("1.50")),
    "BELOW_TREND_EXIT_BARS": (2, 3, 4),
    "ENABLE_DISTRIBUTION_EXIT": (True, False),
    "LEADER_LATCH_TTL": (
        timedelta(minutes=30),
        timedelta(minutes=60),
        timedelta(minutes=120),
    ),
}


def _load_cohort(input_path: Path, cache_dir: Path, workers: int) -> list[CohortItem]:
    replay.ACTIVE_SOURCE = "alpaca-sip"
    replay.CACHE_DIR = cache_dir
    replay.CACHE_ONLY = True
    replay.reset_cache_stats()
    rows, sessions, _grouped = replay._parse_source(input_path)
    source_by_symbol: dict[str, dict[date, dict[str, object]]] = {}
    for row in rows:
        source_by_symbol.setdefault(str(row["symbol"]), {})[row["session_date"]] = row

    loaded: dict[str, replay.SymbolReplayData] = {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {
            executor.submit(replay._load_symbol, symbol, source_rows, sessions): symbol
            for symbol, source_rows in source_by_symbol.items()
        }
        for future in as_completed(futures):
            symbol = futures[future]
            loaded[symbol] = future.result()

    items: list[CohortItem] = []
    for row in rows:
        session_date = row["session_date"]
        assert isinstance(session_date, date)
        symbol = str(row["symbol"])
        source = loaded[symbol]
        candidate = source.candidates.get(session_date)
        raw_bars = source.bars_1m.get(session_date, ())
        if candidate is None or not raw_bars:
            raise RuntimeError(f"cached cohort is incomplete: {session_date} {symbol}")
        items.append(
            CohortItem(
                session_date=session_date,
                symbol=symbol,
                rank=int(row["rank"]),
                eventual_gain_pct=Decimal(str(row["gain_pct"])),
                bars=tuple(
                    replay._market_bars(
                        raw_bars,
                        f"equity:US:{symbol}",
                        "1m",
                    )
                ),
                context=leader.LeaderMomentumContext(
                    tod_rvol=candidate.tod_rvol,
                    spread_bps=candidate.spread_bps,
                    dollar_volume=candidate.premarket_dollar_volume,
                ),
            )
        )
    if len(items) != 105:
        raise AssertionError(f"expected 105 cached observations, got {len(items)}")
    stats = replay.cache_stats()
    if stats.get("network_fetches", 0):
        raise AssertionError("optimization contacted market-data network")
    return items


def _set_parameters(parameters: dict[str, object]) -> None:
    for name, value in DEFAULT_PARAMETERS.items():
        setattr(leader, name, parameters.get(name, value))


def _compound_return(trades: tuple[leader.LeaderMomentumTrade, ...]) -> Decimal | None:
    if not trades:
        return None
    factor = Decimal("1")
    for trade in trades:
        factor *= Decimal("1") + trade.return_pct / Decimal("100")
    return (factor - Decimal("1")) * Decimal("100")


def evaluate_variant(
    items: list[CohortItem],
    parameters: dict[str, object],
    *,
    include_trades: bool = False,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    _set_parameters(parameters)
    normalized_pnl = Decimal("0")
    first_half_pnl = Decimal("0")
    second_half_pnl = Decimal("0")
    wins = 0
    losses = 0
    trade_count = 0
    trade_rows: list[dict[str, object]] = []
    midpoint = sorted({item.session_date for item in items})[10]
    started = perf_counter()
    for item in items:
        snapshot = leader.evaluate_leader_momentum_continuation(
            item.bars,
            context=item.context,
        )
        trades = tuple(snapshot.trades)
        return_pct = _compound_return(trades)
        if return_pct is not None:
            pnl = replay.FIXED_SLOT_NOTIONAL * return_pct / Decimal("100")
            normalized_pnl += pnl
            if item.session_date <= midpoint:
                first_half_pnl += pnl
            else:
                second_half_pnl += pnl
        trade_count += len(trades)
        wins += sum(trade.return_pct > 0 for trade in trades)
        losses += sum(trade.return_pct < 0 for trade in trades)
        if include_trades:
            for trade in trades:
                trade_rows.append(
                    {
                        "session_date": item.session_date,
                        "symbol": item.symbol,
                        "rank": item.rank,
                        "eventual_gain_pct": item.eventual_gain_pct,
                        **trade.model_dump(mode="json"),
                    }
                )
    return (
        {
            "normalized_pnl": normalized_pnl,
            "normalized_return_pct": normalized_pnl / replay.FIXED_DAILY_CAPITAL * Decimal("100"),
            "first_half_return_pct": first_half_pnl / replay.FIXED_DAILY_CAPITAL * Decimal("100"),
            "second_half_return_pct": second_half_pnl / replay.FIXED_DAILY_CAPITAL * Decimal("100"),
            "trade_count": trade_count,
            "wins": wins,
            "losses": losses,
            "win_rate_pct": Decimal(wins) / Decimal(trade_count) * Decimal("100") if trade_count else Decimal("0"),
            "elapsed_seconds": Decimal(str(perf_counter() - started)),
        },
        trade_rows,
    )


def _random_variants(count: int, seed: int) -> list[dict[str, object]]:
    rng = random.Random(seed)
    variants = [dict(DEFAULT_PARAMETERS)]
    seen = {json.dumps(DEFAULT_PARAMETERS, default=str, sort_keys=True)}
    while len(variants) < count + 1:
        candidate = dict(DEFAULT_PARAMETERS)
        for name, values in SEARCH_SPACE.items():
            candidate[name] = rng.choice(values)
        key = json.dumps(candidate, default=str, sort_keys=True)
        if key not in seen:
            seen.add(key)
            variants.append(candidate)
    return variants


def _csv_value(value: object) -> object:
    if isinstance(value, timedelta):
        return int(value.total_seconds() // 60)
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Optimize leader momentum from cached SIP bars.")
    parser.add_argument(
        "--input",
        default="docs/trading/HISTORICAL_TOP5_WINNERS_2026-08-13_TO_2026-09-11.csv",
    )
    parser.add_argument(
        "--cache-dir",
        default="resources/cache/interday-market-data",
    )
    parser.add_argument(
        "--output-dir",
        default="docs/trading/leader-momentum-optimization",
    )
    parser.add_argument("--variants", type=int, default=80)
    parser.add_argument("--seed", type=int, default=20260914)
    parser.add_argument("--workers", type=int, default=8)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_started = perf_counter()
    items = _load_cohort(Path(args.input), Path(args.cache_dir), args.workers)
    print(
        f"Loaded {len(items)} cached observations in {perf_counter() - load_started:.2f}s; "
        f"cache stats={replay.cache_stats()}",
        flush=True,
    )
    variants = _random_variants(max(0, args.variants), args.seed)
    results: list[dict[str, object]] = []
    best_result: dict[str, object] | None = None
    best_parameters: dict[str, object] | None = None
    for index, parameters in enumerate(variants):
        metrics, _trades = evaluate_variant(items, parameters)
        row = {
            "variant": index,
            **metrics,
            **{name: _csv_value(value) for name, value in parameters.items()},
        }
        results.append(row)
        if best_result is None or Decimal(str(row["normalized_return_pct"])) > Decimal(
            str(best_result["normalized_return_pct"])
        ):
            best_result = row
            best_parameters = parameters
            print(
                f"new best variant={index} return={row['normalized_return_pct']:.4f}% "
                f"trades={row['trade_count']} wins/losses={row['wins']}/{row['losses']}",
                flush=True,
            )
    assert best_result is not None and best_parameters is not None
    best_metrics, best_trades = evaluate_variant(
        items,
        best_parameters,
        include_trades=True,
    )
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    ordered = sorted(
        results,
        key=lambda item: Decimal(str(item["normalized_return_pct"])),
        reverse=True,
    )
    with (output_dir / "sweep-results.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(ordered[0]))
        writer.writeheader()
        writer.writerows(ordered)
    (output_dir / "best-variant.json").write_text(
        json.dumps(
            {
                "metrics": best_metrics,
                "parameters": best_parameters,
                "trades": best_trades,
                "cache_stats": replay.cache_stats(),
                "execution_authority": False,
                "in_sample_warning": "Winner cohort and parameters are in-sample research evidence.",
            },
            default=str,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"metrics": best_metrics, "parameters": best_parameters}, default=str, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
