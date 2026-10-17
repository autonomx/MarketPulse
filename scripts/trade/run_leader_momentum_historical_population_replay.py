from __future__ import annotations

"""Run the cache-only Leader Momentum replay against historical populations.

This is research/shadow code.  It never imports an execution client or submits
orders.  The ranking tape is reconstructed from the per-session historical
population manifest before any winner labels are loaded.  The four research
arms then consume the same causal discovery, context, bars, and fills.
"""

import argparse
import csv
import hashlib
import json
import subprocess
import sys
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
for import_root in (SOURCE_ROOT, REPOSITORY_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from app.apps.trading.strategy_evolving_top_gainers import (
    EvolvingTopGainersConfig,
    observations_from_market_bars,
)
from app.apps.trading.strategy_evolving_top_gainers_research import (
    HistoricalPopulationManifest,
    leaderboard_trajectory_at,
    replay_evolving_top_gainers_strict,
)
from app.apps.trading.strategy_leader_momentum_continuation import POLICY_VERSION
from app.apps.trading.strategy_leader_momentum_research import (
    BASELINE_V1_2,
    CONTROLLED_PULLBACK_ONLY,
    CONTROLLED_PULLBACK_SINGLE_TRADE,
    SINGLE_TRADE_ONLY,
    LeaderMomentumResearchVariant,
    assert_baseline_parity,
    evaluate_leader_momentum_research_variant,
)
from scripts.trade import run_leader_momentum_evolving_top_gainers as cache_module


CACHE_ROOT = REPOSITORY_ROOT / "resources" / "cache" / "leader-momentum-evolving-top-gainers"
POPULATION_ROOT = CACHE_ROOT / "historical-population"
DEFAULT_ARTIFACT_ROOT = REPOSITORY_ROOT / "artifacts" / "trading" / "leader-momentum-historical-population-replay"
WINNER_CSV = REPOSITORY_ROOT / "docs" / "trading" / "HISTORICAL_TOP5_WINNERS_2026-06-11_TO_2026-09-11.csv"
FIXED_SLOT_NOTIONAL = Decimal("20000")
CAPACITY_SLOTS = 5
VARIANTS = (
    BASELINE_V1_2,
    CONTROLLED_PULLBACK_ONLY,
    SINGLE_TRADE_ONLY,
    CONTROLLED_PULLBACK_SINGLE_TRADE,
)


def _json_default(value: object) -> object:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=_json_default, sort_keys=True) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, object]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_sha() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _dec(value: object, default: Decimal = Decimal("0")) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:
        return default


def _pct(value: Decimal | None) -> str:
    return "" if value is None else f"{value:.6f}"


def _symbol(instrument_id: str) -> str:
    return instrument_id.rsplit(":", 1)[-1].upper()


def _sessions() -> list[date]:
    with WINNER_CSV.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    sessions = sorted({date.fromisoformat(str(row["session_date"])) for row in rows})
    if len(rows) != len(sessions) * 5:
        raise ValueError(f"winner CSV must contain five rows per session; got {len(rows)}")
    return sessions


def _winner_labels() -> dict[tuple[str, str], dict[str, str]]:
    labels: dict[tuple[str, str], dict[str, str]] = {}
    with WINNER_CSV.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            labels[(str(row["session_date"]), str(row["symbol"]).strip().upper())] = dict(row)
    return labels


def _load_population_report(population_root: Path, sessions: list[date]) -> dict[str, dict[str, Any]]:
    report_path = population_root / "manifest.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("schema") != "leader-momentum-historical-population-report-v1":
        raise RuntimeError(f"invalid historical population report: {report_path}")
    if report.get("point_in_time") is not True or report.get("outcome_conditioned") is not False:
        raise RuntimeError("historical population report is not strict point-in-time evidence")
    if report.get("valid_for_cached_replay") is not True:
        raise RuntimeError("historical population report is invalid for cached replay")

    envelopes: dict[str, dict[str, Any]] = {}
    for session_date in sessions:
        path = population_root / "sessions" / f"{session_date.isoformat()}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        manifest = HistoricalPopulationManifest.model_validate(payload.get("manifest"))
        if manifest.session_date != session_date:
            raise RuntimeError(f"population session mismatch: {path}")
        if not manifest.point_in_time or manifest.outcome_conditioned:
            raise RuntimeError(f"population manifest is not strict: {path}")
        if payload.get("manifest_fingerprint") != manifest.fingerprint:
            raise RuntimeError(f"population fingerprint mismatch: {path}")
        envelopes[session_date.isoformat()] = payload
    return envelopes


def _trade_pnl(return_pct: object) -> Decimal:
    return FIXED_SLOT_NOTIONAL * _dec(return_pct) / Decimal("100")


def _apply_capacity(rows: list[dict[str, object]]) -> None:
    for variant in {str(row["variant"]) for row in rows}:
        for session_date in sorted({str(row["session_date"]) for row in rows if str(row["variant"]) == variant}):
            day = sorted(
                (row for row in rows if str(row["variant"]) == variant and str(row["session_date"]) == session_date),
                key=lambda row: (str(row["entry_time"]), str(row["symbol"])),
            )
            accepted: list[dict[str, object]] = []
            for row in day:
                entry = datetime.fromisoformat(str(row["entry_time"]))
                active = [item for item in accepted if datetime.fromisoformat(str(item["exit_time"])) > entry]
                row["capacity_active_before_entry"] = len(active)
                row["capacity_accepted"] = len(active) < CAPACITY_SLOTS
                if row["capacity_accepted"]:
                    accepted.append(row)


def _trajectory_fields(replay: Any, instrument_id: str, signal_time: datetime) -> dict[str, object]:
    features = leaderboard_trajectory_at(replay, instrument_id=instrument_id, observed_at=signal_time)
    return {
        "trajectory_currently_top_n": features.currently_top_n,
        "trajectory_current_rank": features.current_rank if features.current_rank is not None else "",
        "trajectory_current_gain_pct": _pct(features.current_gain_pct),
        "trajectory_best_rank_so_far": features.best_rank_so_far if features.best_rank_so_far is not None else "",
        "trajectory_best_gain_pct_so_far": _pct(features.best_gain_pct_so_far),
        "trajectory_rank_improvement_5m": features.rank_improvement_5m if features.rank_improvement_5m is not None else "",
        "trajectory_rank_improvement_15m": features.rank_improvement_15m if features.rank_improvement_15m is not None else "",
        "trajectory_gain_change_5m_pct_points": _pct(features.gain_change_5m_pct_points),
        "trajectory_gain_change_15m_pct_points": _pct(features.gain_change_15m_pct_points),
        "trajectory_minutes_since_first_top_n": _pct(features.minutes_since_first_top_n),
        "trajectory_snapshots_in_top_10": features.snapshots_in_top_10,
        "trajectory_consecutive_top_10_snapshots": features.consecutive_top_10_snapshots,
        "trajectory_top_10_minutes_observed": _pct(features.top_10_minutes_observed),
        "trajectory_ever_top_5_before_signal": features.ever_top_5_before_observation,
    }


def _evaluate_session(
    *,
    session_date: date,
    envelope: dict[str, Any],
    cache: Any,
    config: EvolvingTopGainersConfig,
    observations: list[dict[str, object]],
    trades: list[dict[str, object]],
    trajectories: list[dict[str, object]],
    coverage: list[dict[str, object]],
    integrity: list[dict[str, object]],
) -> None:
    manifest = HistoricalPopulationManifest.model_validate(envelope["manifest"])
    allowed = set(manifest.instrument_ids)
    cached_five = cache.load_session("5m", session_date)
    cached_one = cache.load_session("1m", session_date)
    five_by_instrument: dict[str, list[Any]] = defaultdict(list)
    one_by_instrument: dict[str, list[Any]] = defaultdict(list)
    five_outside = 0
    one_outside = 0
    for raw in cached_five:
        instrument_id = f"equity:US:{raw.symbol}"
        if instrument_id in allowed:
            five_by_instrument[instrument_id].append(cache_module._market_bar(raw, instrument_id))
        else:
            five_outside += 1
    for raw in cached_one:
        instrument_id = f"equity:US:{raw.symbol}"
        if instrument_id in allowed:
            one_by_instrument[instrument_id].append(cache_module._market_bar(raw, instrument_id))
        else:
            one_outside += 1

    previous_close = {
        f"equity:US:{symbol}": _dec(item["close"])
        for symbol, item in envelope.get("previous_closes", {}).items()
    }
    tape = observations_from_market_bars(
        session_date=session_date,
        bars_by_instrument=five_by_instrument,
        previous_close_by_instrument=previous_close,
        config=config,
    )
    replay, report = replay_evolving_top_gainers_strict(
        manifest=manifest,
        observations=tape,
        config=config,
    )
    discovered = {member.instrument_id: member for member in replay.membership}
    missing_one_minute: list[str] = []
    parity_checks = 0
    strategy_errors: list[str] = []

    for instrument_id, member in discovered.items():
        symbol = _symbol(instrument_id)
        five_market = sorted(five_by_instrument.get(instrument_id, ()), key=lambda bar: bar.start_time)
        one_market = sorted(one_by_instrument.get(instrument_id, ()), key=lambda bar: bar.start_time)
        observation_row = {
            "session_date": session_date.isoformat(),
            "symbol": symbol,
            "instrument_id": instrument_id,
            "first_top20_at": member.first_top_n_at.isoformat(),
            "first_top10_at": member.first_top_10_at.isoformat() if member.first_top_10_at else "",
            "first_top5_at": member.first_top_5_at.isoformat() if member.first_top_5_at else "",
            "best_rank": member.best_rank,
            "entry_count": member.entry_count,
            "last_seen_at": member.last_seen_at.isoformat(),
            "one_minute_data_status": "available" if one_market else "missing",
        }
        observations.append(observation_row)
        if not one_market:
            missing_one_minute.append(symbol)
            continue
        context = cache_module._context_at(five_market, member.first_top_n_at)
        try:
            assert_baseline_parity(
                one_market,
                context=context,
                discovered_at=member.first_top_n_at,
            )
            parity_checks += 1
            results = {
                variant.name: evaluate_leader_momentum_research_variant(
                    one_market,
                    variant=variant,
                    context=context,
                    discovered_at=member.first_top_n_at,
                )
                for variant in VARIANTS
            }
        except Exception as exc:
            strategy_errors.append(f"{symbol}:{type(exc).__name__}: {exc}")
            continue

        for variant in VARIANTS:
            result = results[variant.name]
            for trade_index, trade in enumerate(result.snapshot.trades, start=1):
                trajectory = _trajectory_fields(replay, instrument_id, trade.signal_time)
                trade_row = {
                    "session_date": session_date.isoformat(),
                    "symbol": symbol,
                    "instrument_id": instrument_id,
                    "variant": variant.name,
                    "trade_index": trade_index,
                    "mode": trade.mode,
                    "signal_time": trade.signal_time.isoformat(),
                    "entry_time": trade.entry_time.isoformat(),
                    "entry_price": _pct(trade.entry_price),
                    "initial_stop_price": _pct(trade.initial_stop_price),
                    "exit_time": trade.exit_time.isoformat(),
                    "exit_price": _pct(trade.exit_price),
                    "exit_reason_code": trade.exit_reason_code,
                    "return_pct": _pct(trade.return_pct),
                    "pnl": _pct(_trade_pnl(trade.return_pct)),
                    "mfe_pct": _pct(trade.mfe_pct),
                    "mae_pct": _pct(trade.mae_pct),
                    **trajectory,
                }
                trades.append(trade_row)
                trajectories.append({
                    "session_date": session_date.isoformat(),
                    "symbol": symbol,
                    "variant": variant.name,
                    "trade_index": trade_index,
                    "signal_time": trade.signal_time.isoformat(),
                    **trajectory,
                })

    _apply_capacity(trades)
    integrity.append(
        {
            "session_date": session_date.isoformat(),
            "point_in_time": report.point_in_time,
            "outcome_conditioned": report.outcome_conditioned,
            "population_fingerprint": report.population_fingerprint,
            "population_size": report.population_size,
            "observation_count": report.observation_symbol_count,
            "leaderboard_count": report.leaderboard_symbol_count,
            "five_minute_cached_symbols": len(five_by_instrument),
            "one_minute_cached_symbols": len(one_by_instrument),
            "cached_five_minute_bars_outside_population": five_outside,
            "cached_one_minute_bars_outside_population": one_outside,
            "missing_one_minute_for_leaderboard": len(missing_one_minute),
            "missing_one_minute_symbols": ",".join(sorted(missing_one_minute)),
            "baseline_parity_checks": parity_checks,
            "strategy_errors": "; ".join(strategy_errors),
            "strict_population_valid": report.valid_for_inference,
            "session_replay_usable": report.valid_for_inference and not missing_one_minute and not strategy_errors,
            "ranking_fingerprint": replay.fingerprint,
        }
    )
    coverage.append(
        {
            "session_date": session_date.isoformat(),
            "population_size": len(allowed),
            "cached_five_minute_bars": len(cached_five),
            "cached_one_minute_bars": len(cached_one),
            "five_minute_symbols_in_population": len(five_by_instrument),
            "one_minute_symbols_in_population": len(one_by_instrument),
            "leaderboard_members": len(discovered),
            "missing_one_minute_for_leaderboard": len(missing_one_minute),
        }
    )


def _merge_labels(observations: list[dict[str, object]], trades: list[dict[str, object]], labels: dict[tuple[str, str], dict[str, str]]) -> None:
    for row in observations:
        label = labels.get((str(row["session_date"]), str(row["symbol"])))
        row["winner_final_top5"] = bool(label)
        row["winner_rank"] = label.get("rank", "") if label else ""
        row["winner_final_gain_pct"] = label.get("gain_pct", "") if label else ""
    for row in trades:
        label = labels.get((str(row["session_date"]), str(row["symbol"])))
        row["winner_final_top5"] = bool(label)


def _period(session_date: str) -> str:
    if session_date <= "2026-08-12":
        return "june_through_august_12"
    if session_date >= "2026-08-13":
        return "august_13_through_september_11"
    return session_date[:7]


def _summary_rows(trades: list[dict[str, object]]) -> list[dict[str, object]]:
    groups: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for row in trades:
        groups[(str(row["variant"]), _period(str(row["session_date"])))].append(row)
    output: list[dict[str, object]] = []
    for (variant, period), rows in sorted(groups.items()):
        returns = [_dec(row["return_pct"]) for row in rows]
        accepted = [row for row in rows if row.get("capacity_accepted") is True]
        accepted_pnl = sum((_trade_pnl(row["return_pct"]) for row in accepted), Decimal("0"))
        total_pnl = sum((_trade_pnl(row["return_pct"]) for row in rows), Decimal("0"))
        wins = sum(value > 0 for value in returns)
        losses = sum(value < 0 for value in returns)
        output.append(
            {
                "variant": variant,
                "period": period,
                "trades": len(rows),
                "capacity_accepted": len(accepted),
                "capacity_rejected": len(rows) - len(accepted),
                "wins": wins,
                "losses": losses,
                "flat": len(rows) - wins - losses,
                "mean_return_pct": _pct(sum(returns, Decimal("0")) / Decimal(len(returns)) if returns else None),
                "median_return_pct": _pct(sorted(returns)[len(returns) // 2] if returns else None),
                "total_pnl": _pct(total_pnl),
                "capacity_pnl": _pct(accepted_pnl),
                "profit_factor": _pct(sum((value for value in returns if value > 0), Decimal("0")) / abs(sum((value for value in returns if value < 0), Decimal("0"))) if any(value < 0 for value in returns) else None),
            }
        )
    return output


def _daily_rows(trades: list[dict[str, object]]) -> list[dict[str, object]]:
    groups: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for row in trades:
        groups[(str(row["variant"]), str(row["session_date"]))].append(row)
    output: list[dict[str, object]] = []
    for (variant, session_date), rows in sorted(groups.items()):
        accepted = [row for row in rows if row.get("capacity_accepted") is True]
        output.append(
            {
                "variant": variant,
                "session_date": session_date,
                "trades": len(rows),
                "capacity_accepted": len(accepted),
                "capacity_rejected": len(rows) - len(accepted),
                "pnl": _pct(sum((_trade_pnl(row["return_pct"]) for row in rows), Decimal("0"))),
                "capacity_pnl": _pct(sum((_trade_pnl(row["return_pct"]) for row in accepted), Decimal("0"))),
            }
        )
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Run strict cache-only Leader Momentum research replay.")
    parser.add_argument("--cache-root", type=Path, default=CACHE_ROOT)
    parser.add_argument("--population-root", type=Path, default=POPULATION_ROOT)
    parser.add_argument("--artifact-root", type=Path, default=DEFAULT_ARTIFACT_ROOT)
    args = parser.parse_args()

    sessions = _sessions()
    cache_root = args.cache_root.resolve()
    population_root = args.population_root.resolve()
    artifact_root = args.artifact_root.resolve()
    cache = cache_module.CompactSipCache(cache_root)
    if cache.load_manifest("5m") is None or cache.load_manifest("1m") is None:
        raise RuntimeError("cache-only replay requires completed 5m and 1m manifests")
    envelopes = _load_population_report(population_root, sessions)
    strategy_path = REPOSITORY_ROOT / "src" / "app" / "apps" / "trading" / "strategy_leader_momentum_continuation.py"
    strategy_sha_before = _sha256(strategy_path)
    git_sha_before = _git_sha()
    if POLICY_VERSION != "leader-momentum-continuation-v1.2":
        raise RuntimeError(f"unexpected strategy policy: {POLICY_VERSION}")

    config = EvolvingTopGainersConfig(top_n=20, cadence_minutes=5)
    observations: list[dict[str, object]] = []
    trades: list[dict[str, object]] = []
    trajectories: list[dict[str, object]] = []
    coverage: list[dict[str, object]] = []
    integrity: list[dict[str, object]] = []
    ranking_fingerprints: dict[str, str] = {}

    print(f"Running strict cache-only replay for {len(sessions)} sessions", flush=True)
    for index, session_date in enumerate(sessions, start=1):
        before = len(trades)
        _evaluate_session(
            session_date=session_date,
            envelope=envelopes[session_date.isoformat()],
            cache=cache,
            config=config,
            observations=observations,
            trades=trades,
            trajectories=trajectories,
            coverage=coverage,
            integrity=integrity,
        )
        # Reconstruct the fingerprint independently of outcome labels and keep
        # it in the run metadata.  The detailed strict report is written below.
        ranking_fingerprints[session_date.isoformat()] = str(integrity[-1]["ranking_fingerprint"])
        print(
            f"[{index}/{len(sessions)}] {session_date}: "
            f"{len(observations)} observations, {len(trades) - before} trades added",
            flush=True,
        )

    # Labels are deliberately attached only after all causal ranking and
    # strategy evaluations have completed.
    _merge_labels(observations, trades, _winner_labels())
    strategy_sha_after = _sha256(strategy_path)
    git_sha_after = _git_sha()
    if strategy_sha_after != strategy_sha_before or git_sha_after != git_sha_before:
        raise RuntimeError("strategy source or repository changed during replay")

    artifact_root.mkdir(parents=True, exist_ok=True)
    _write_csv(artifact_root / "observations.csv", observations, list(observations[0]) if observations else ["session_date"])
    _write_csv(artifact_root / "trades.csv", trades, list(trades[0]) if trades else ["session_date"])
    _write_csv(artifact_root / "trajectory.csv", trajectories, list(trajectories[0]) if trajectories else ["session_date"])
    _write_csv(artifact_root / "coverage.csv", coverage, list(coverage[0]) if coverage else ["session_date"])
    _write_csv(artifact_root / "population-integrity.csv", integrity, list(integrity[0]) if integrity else ["session_date"])
    summary = _summary_rows(trades)
    daily = _daily_rows(trades)
    _write_csv(artifact_root / "variant-summary.csv", summary, list(summary[0]) if summary else ["variant"])
    _write_csv(artifact_root / "daily-pnl.csv", daily, list(daily[0]) if daily else ["variant"])

    usable_sessions = sum(bool(row["session_replay_usable"]) for row in integrity)
    missing_sessions = [str(row["session_date"]) for row in integrity if not row["session_replay_usable"]]
    metadata = {
        "experiment": "leader-momentum-historical-population-replay",
        "policy_version": POLICY_VERSION,
        "sessions": [item.isoformat() for item in sessions],
        "session_count": len(sessions),
        "usable_session_count": usable_sessions,
        "invalid_sessions": missing_sessions,
        "population_report": str(population_root / "manifest.json"),
        "cache_root": str(cache_root),
        "cache_only": True,
        "network_fetches": 0,
        "winner_labels_attached_after_ranking": True,
        "git_sha": git_sha_before,
        "strategy_sha256": strategy_sha_before,
        "ranking_fingerprints": ranking_fingerprints,
        "variants": [variant.model_dump(mode="json") for variant in VARIANTS],
        "observation_count": len(observations),
        "trade_count": len(trades),
        "integrity_valid_for_inference": usable_sessions == len(sessions),
    }
    _write_json(artifact_root / "run-metadata.json", metadata)
    _write_json(artifact_root / "population-integrity.json", integrity)
    print(json.dumps({
        "artifact_root": str(artifact_root),
        "sessions": len(sessions),
        "usable_sessions": usable_sessions,
        "observations": len(observations),
        "trades": len(trades),
        "invalid_sessions": missing_sessions,
        "network_fetches": 0,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
