from __future__ import annotations

import argparse
import csv
import hashlib
import json
import time as time_module
from collections import Counter
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from app.apps.trading.historical_gapper_reconstruction import (
    AlpacaHistoricalGapperReconstructor,
    reconstructed_strategy_config,
)
from app.apps.trading.paper import PaperExecutionPolicy
from app.apps.trading.providers.errors import ProviderContractError, ProviderDataUnavailableError, ProviderRateLimitedError
from app.apps.trading.providers.http_runtime import ProviderHttpRuntime
from app.apps.trading.strategy_backtest import (
    BacktestSessionDataset,
    freeze_backtest_session,
    run_gap_pullback_backtest,
)
from app.apps.trading.strategy_historical_bars import alpaca_historical_session_bars
from app.apps.trading.us_equity_calendar import regular_holidays
from scripts.run_trading_strategy_backtest import strict_v11_strategy


_ET = ZoneInfo("America/New_York")
_DATASET_CACHE_VERSION = "liquidity-sweep-dataset-v2-causal-scan"
_DEFAULT_THRESHOLDS = (
    Decimal("250000"),
    Decimal("500000"),
    Decimal("1000000"),
    Decimal("2000000"),
    Decimal("5000000"),
    Decimal("10000000"),
)


def _dates(start_raw: str | None, end_raw: str | None, lookback_days: int) -> tuple[date, date]:
    today_et = datetime.now(_ET).date()
    end = date.fromisoformat(end_raw) if end_raw else today_et - timedelta(days=1)
    start = date.fromisoformat(start_raw) if start_raw else end - timedelta(days=lookback_days - 1)
    if end < start:
        raise ValueError("end date must be on or after start date")
    return start, end


def _trading_dates(start_date: date, end_date: date) -> list[date]:
    output: list[date] = []
    cursor = start_date
    while cursor <= end_date:
        if cursor.weekday() < 5 and cursor not in regular_holidays(cursor.year):
            output.append(cursor)
        cursor += timedelta(days=1)
    return output


def _thresholds(raw: str) -> tuple[Decimal, ...]:
    values = tuple(Decimal(item.strip()) for item in raw.split(",") if item.strip())
    if not values:
        raise ValueError("at least one liquidity threshold is required")
    if any(value < 0 for value in values):
        raise ValueError("liquidity thresholds cannot be negative")
    return tuple(sorted(set(values)))


def _label(threshold: Decimal) -> str:
    value = int(threshold)
    if value >= 1_000_000 and value % 1_000_000 == 0:
        return f"{value // 1_000_000}m"
    if value >= 1_000 and value % 1_000 == 0:
        return f"{value // 1_000}k"
    return str(value)


def _with_rate_limit_retry(label: str, function):
    delays = (20, 40, 80, 120)
    for attempt in range(len(delays) + 1):
        try:
            return function()
        except ProviderRateLimitedError:
            if attempt >= len(delays):
                raise
            delay = delays[attempt]
            print(f"{label}: Alpaca rate limited the request; retrying after {delay}s")
            time_module.sleep(delay)
    raise AssertionError("unreachable")


def _outcome_counts(backtest_result) -> Counter[str]:
    counts: Counter[str] = Counter()
    for decision in backtest_result.candidate_decisions:
        reason = decision.rejection_reason or ("TRIGGERED" if decision.triggered else decision.state.upper())
        counts[str(reason)] += 1
    return counts


def _trade_dump(trade) -> dict[str, object]:
    return trade.model_dump(mode="json")


def _cache_namespace(strategy, assumed_spread_bps: Decimal) -> tuple[str, dict[str, object]]:
    config = strategy.config
    basis = {
        "cache_version": _DATASET_CACHE_VERSION,
        "scan_time_et": config.universe_scan_time_et.isoformat(),
        "minimum_gap_pct": str(config.minimum_gap_pct),
        "minimum_price": str(config.minimum_price),
        "maximum_price": str(config.maximum_price),
        "universe_discovery_count": config.universe_discovery_count,
        "assumed_spread_bps": str(assumed_spread_bps),
        "feed": "alpaca_iex",
        "bar_interval": "1m",
    }
    digest = hashlib.sha256(
        json.dumps(basis, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:20]
    return digest, basis


def _dataset_cache_path(cache_dir: Path, session_date: date) -> Path:
    return cache_dir / f"session-{session_date.isoformat()}.json"


def _no_candidate_cache_path(cache_dir: Path, session_date: date) -> Path:
    return cache_dir / f"session-{session_date.isoformat()}.no-candidates.json"


def _load_cached_dataset(path: Path, session_date: date) -> BacktestSessionDataset:
    loaded = BacktestSessionDataset.model_validate_json(path.read_text(encoding="utf-8"))
    if loaded.session_date != session_date:
        raise ValueError(f"cached dataset session mismatch: expected {session_date}, got {loaded.session_date}")
    # Re-freeze the parsed payload so the stored fingerprint is verified from the
    # actual immutable universe + bars rather than trusted as opaque cache metadata.
    verified = freeze_backtest_session(
        session_date=loaded.session_date,
        universe=loaded.universe,
        bars_by_instrument=loaded.bars_by_instrument,
    )
    if verified.dataset_fingerprint != loaded.dataset_fingerprint:
        raise ValueError(f"cached dataset fingerprint mismatch for {session_date}")
    return verified


def _write_cached_dataset(path: Path, dataset: BacktestSessionDataset) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dataset.model_dump_json(indent=2), encoding="utf-8")


def _load_no_candidate_marker(path: Path, session_date: date) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("session_date") != session_date.isoformat():
        raise ValueError(f"cached no-candidate session mismatch for {session_date}")
    if payload.get("cache_version") != _DATASET_CACHE_VERSION:
        raise ValueError(f"cached no-candidate format mismatch for {session_date}")
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Sweep the strict v1.1 premarket dollar-volume gate while reusing identical "
            "frozen historical datasets and optionally persisting them between runs."
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
    parser.add_argument(
        "--thresholds",
        default=",".join(str(value) for value in _DEFAULT_THRESHOLDS),
        help="Comma-separated premarket dollar-volume thresholds.",
    )
    parser.add_argument("--dataset-cache-dir", default="")
    parser.add_argument("--output-dir", default="artifacts/liquidity-sweep")
    parser.add_argument("--require-covered-session", action="store_true")
    return parser.parse_args()


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

    # Reconstruction does not filter on premarket dollar volume. The lowest sweep
    # threshold is used only to construct a config object; every threshold later
    # replays the exact same frozen session dataset.
    reconstruction_strategy = strict_v11_strategy(
        minimum_premarket_dollar_volume=min(thresholds),
    )
    cache_namespace, cache_basis = _cache_namespace(reconstruction_strategy, assumed_spread_bps)
    cache_root = Path(args.dataset_cache_dir) if args.dataset_cache_dir else None
    session_cache_dir = cache_root / cache_namespace if cache_root is not None else None
    if session_cache_dir is not None:
        session_cache_dir.mkdir(parents=True, exist_ok=True)
        (session_cache_dir / "cache-basis.json").write_text(
            json.dumps(cache_basis, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    reconstructor: AlpacaHistoricalGapperReconstructor | None = None
    regular_runtime: ProviderHttpRuntime | None = None
    execution_policy = PaperExecutionPolicy(max_volume_participation_pct=Decimal("1"))

    state: dict[Decimal, dict[str, object]] = {}
    for threshold in thresholds:
        state[threshold] = {
            "cash": initial_cash,
            "candidate_count": 0,
            "trigger_count": 0,
            "trades": [],
            "outcomes": Counter(),
            "days": [],
        }

    covered_sessions = 0
    no_candidate_sessions = 0
    cache_hits = 0
    cache_misses = 0
    unavailable_sessions: list[dict[str, object]] = []
    dataset_fingerprints: dict[str, str] = {}

    def ensure_provider_clients() -> tuple[AlpacaHistoricalGapperReconstructor, ProviderHttpRuntime]:
        nonlocal reconstructor, regular_runtime
        if reconstructor is None:
            reconstructor = AlpacaHistoricalGapperReconstructor(
                start_date=start,
                end_date=end,
                config=reconstruction_strategy.config,
                assumed_spread_bps=assumed_spread_bps,
                max_age_days=args.reconstruction_max_age_days,
            )
        if regular_runtime is None:
            regular_runtime = ProviderHttpRuntime("alpaca_strategy_liquidity_sweep", max_concurrency=1)
        return reconstructor, regular_runtime

    for session_date in sessions:
        dataset: BacktestSessionDataset | None = None
        no_candidate_detail: str | None = None
        dataset_path = (
            _dataset_cache_path(session_cache_dir, session_date)
            if session_cache_dir is not None
            else None
        )
        no_candidate_path = (
            _no_candidate_cache_path(session_cache_dir, session_date)
            if session_cache_dir is not None
            else None
        )

        if dataset_path is not None and dataset_path.exists():
            dataset = _load_cached_dataset(dataset_path, session_date)
            cache_hits += 1
            print(f"Using cached frozen dataset for {session_date}: {dataset.dataset_fingerprint}")
        elif no_candidate_path is not None and no_candidate_path.exists():
            marker = _load_no_candidate_marker(no_candidate_path, session_date)
            no_candidate_detail = str(marker.get("detail") or "cached no-candidate session")
            cache_hits += 1
            print(f"Using cached no-candidate marker for {session_date}")
        else:
            cache_misses += 1
            active_reconstructor, active_regular_runtime = ensure_provider_clients()
            print(f"Cache miss for {session_date}; reconstructing once for {len(thresholds)} variants")
            try:
                reconstruction = _with_rate_limit_retry(
                    f"reconstruct {session_date}",
                    lambda session_date=session_date: active_reconstructor(
                        session_date=session_date,
                        scan_time=time(9, 20),
                        config=reconstruction_strategy.config,
                        assumed_spread_bps=assumed_spread_bps,
                        max_age_days=args.reconstruction_max_age_days,
                    ),
                )
                universe = reconstruction.snapshot
                if universe is None:
                    if reconstruction.fidelity == "reconstructed_current_listings_iex":
                        no_candidate_detail = reconstruction.detail or "Historical scan found no candidates."
                        if no_candidate_path is not None:
                            no_candidate_path.write_text(
                                json.dumps(
                                    {
                                        "cache_version": _DATASET_CACHE_VERSION,
                                        "session_date": session_date.isoformat(),
                                        "fidelity": reconstruction.fidelity,
                                        "detail": no_candidate_detail,
                                        "warnings": list(reconstruction.warnings),
                                    },
                                    indent=2,
                                    sort_keys=True,
                                )
                                + "\n",
                                encoding="utf-8",
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
                else:
                    bars_by_instrument = _with_rate_limit_retry(
                        f"regular bars {session_date}",
                        lambda universe=universe, session_date=session_date: alpaca_historical_session_bars(
                            universe.candidates,
                            session_date,
                            runtime=active_regular_runtime,
                        ),
                    )
                    dataset = freeze_backtest_session(
                        session_date=session_date,
                        universe=universe,
                        bars_by_instrument=bars_by_instrument,
                    )
                    if dataset_path is not None:
                        _write_cached_dataset(dataset_path, dataset)
                        print(f"Cached frozen dataset for {session_date}: {dataset.dataset_fingerprint}")
            except (ProviderContractError, ProviderDataUnavailableError, OSError) as exc:
                unavailable_sessions.append(
                    {
                        "session_date": session_date.isoformat(),
                        "fidelity": "provider_data_unavailable",
                        "detail": f"{type(exc).__name__}: {exc}",
                    }
                )
                print(f"{session_date}: provider data unavailable; preserving completed cache and continuing: {exc}")
                continue

        if dataset is None:
            covered_sessions += 1
            no_candidate_sessions += 1
            for threshold in thresholds:
                state[threshold]["days"].append(
                    {
                        "session_date": session_date.isoformat(),
                        "status": "no_candidates",
                        "detail": no_candidate_detail,
                    }
                )
            continue

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
            state[threshold]["cash"] = current_cash + pnl
            state[threshold]["candidate_count"] += result.summary.candidate_count
            state[threshold]["trigger_count"] += result.summary.trigger_count
            state[threshold]["trades"].extend(result.trades)
            outcome_counts = _outcome_counts(result)
            state[threshold]["outcomes"].update(outcome_counts)
            state[threshold]["days"].append(
                {
                    "session_date": session_date.isoformat(),
                    "status": "backtested",
                    "dataset_fingerprint": result.dataset_fingerprint,
                    "candidate_count": result.summary.candidate_count,
                    "trigger_count": result.summary.trigger_count,
                    "trade_count": result.summary.trade_count,
                    "pnl": str(pnl),
                    "fidelity_adjustments": list(fidelity_adjustments),
                    "candidate_outcomes": dict(outcome_counts),
                    "trades": [_trade_dump(trade) for trade in result.trades],
                }
            )

    comparison: list[dict[str, object]] = []
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

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
            "result_quality": "approximate" if covered_sessions else "unavailable",
            "cache_namespace": cache_namespace,
            "days": threshold_state["days"],
            "trades": [_trade_dump(trade) for trade in trades],
        }
        (output_dir / f"variant-{_label(threshold)}.json").write_text(
            json.dumps(variant, indent=2, sort_keys=True) + "\n",
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

    (output_dir / "comparison.json").write_text(
        json.dumps(comparison, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "dataset-fingerprints.json").write_text(
        json.dumps(dataset_fingerprints, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "unavailable-sessions.json").write_text(
        json.dumps(unavailable_sessions, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "dataset-cache.json").write_text(
        json.dumps(
            {
                "cache_version": _DATASET_CACHE_VERSION,
                "cache_namespace": cache_namespace,
                "cache_basis": cache_basis,
                "cache_enabled": session_cache_dir is not None,
                "cache_hits": cache_hits,
                "cache_misses": cache_misses,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    lines = [
        "# Premarket liquidity sensitivity sweep",
        "",
        f"- Period: {start} through {end}",
        f"- Coverage: {covered_sessions}/{len(sessions)} trading sessions",
        f"- Valid no-candidate sessions: {no_candidate_sessions}",
        "- Fidelity: **approximate reconstructed Alpaca IEX**",
        f"- Dataset cache: {cache_hits} hit(s), {cache_misses} miss(es); namespace `{cache_namespace}`",
        "- All variants reuse the same frozen reconstructed universe and regular-session bars for each date.",
        "- Only `minimum_premarket_dollar_volume` changes; all other strict v1.1 structure and risk rules remain fixed.",
        "",
        "| Premarket $ gate | Candidates | Pass $ gate | Triggers | Trades | W-L | P&L | Expectancy R |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in comparison:
        threshold_text = f"${row['threshold']:,}"
        pnl_text = "N/A" if row["pnl"] is None else str(row["pnl"])
        expectancy_text = "N/A" if row["expectancy_r"] is None else str(row["expectancy_r"])
        lines.append(
            f"| {threshold_text} | {row['candidate_count']} | {row['not_rejected_by_liquidity_gate']} | "
            f"{row['trigger_count']} | {row['trade_count']} | {row['win_count']}-{row['loss_count']} | "
            f"{pnl_text} | {expectancy_text} |"
        )
    lines.extend(
        [
            "",
            "## Guardrails",
            "",
            "- Reconstruction uses today's active listing set and Alpaca IEX partial-market history, so survivorship/listing bias remains possible.",
            "- Historical catalyst, dilution/supply, float, and true historical spread evidence are unavailable and are explicitly downgraded for reconstructed sessions.",
            "- Cached datasets are immutable replay inputs. Change the reconstruction basis or bump the cache version when the historical-data contract changes.",
            "- This sweep diagnoses the liquidity gate; it is not sufficient evidence by itself to promote a production threshold.",
        ]
    )
    summary = "\n".join(lines) + "\n"
    (output_dir / "summary.md").write_text(summary, encoding="utf-8")
    print(summary)

    if args.require_covered_session and covered_sessions == 0:
        print("No sessions were covered; refusing to treat this as a successful sweep.")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
