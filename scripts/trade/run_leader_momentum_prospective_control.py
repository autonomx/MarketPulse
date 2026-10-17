from __future__ import annotations

"""Evaluate frozen Leader Momentum v1.2 on a causal winner/control universe.

This is deliberately a research runner.  It freezes the strategy module before
doing any outcome joins, uses the existing Alpaca SIP cache for evaluation, and
never imports an order or broker-execution path.  Historical scanner archives
are preferred when available; missing sessions use the repository's causal
Alpaca historical reconstruction with ``feed='sip'``.
"""

import argparse
import csv
import hashlib
import json
import os
import subprocess
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from statistics import median
from typing import Any, Iterable
from zoneinfo import ZoneInfo

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
for import_root in (SOURCE_ROOT, REPOSITORY_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from app.apps.trading import strategy_leader_momentum_continuation as leader
from app.apps.trading.historical_gapper_reconstruction import (
    AlpacaHistoricalGapperReconstructor,
)
from app.apps.trading.strategy_replay_reliability import historical_replay_http_runtime
from app.apps.trading.strategy_leader_momentum_diagnostics import (
    diagnose_leader_momentum_continuation,
)
from app.apps.trading.strategy_replay_reliability import (
    ReplayExpectedObservation,
    assess_replay_completeness,
    replay_observation_from_result,
)
from app.apps.trading.strategy_v2_qualification import managed_finviz_v2_config
from scripts.trade import run_interday_winner_shadow_replay as replay


ET = ZoneInfo("America/New_York")
UTC = timezone.utc
EXPECTED_COMMIT_SHA = "681f2fb3f8364115409760dce2703dbd01b9b3c3"
EXPECTED_POLICY_VERSION = "leader-momentum-continuation-v1.2"
EXPECTED_STRATEGY_SHA256 = (
    "8cdf2f858b19003feb6176608dcd45318500c0772255e3db64e6b0a3367c428a"
)
FIXED_DAILY_CAPITAL = Decimal("100000")
FIXED_SLOT_NOTIONAL = Decimal("20000")
ASSUMED_SPREAD_BPS = Decimal("40")
SCAN_TIME_ET = time(9, 15)
MATCHED_CONTROLS_PER_WINNER = 3

FROZEN_PARAMETER_NAMES = (
    "MIN_PRICE",
    "MAX_PRICE",
    "MIN_LEADER_SCORE",
    "MIN_SESSION_RETURN_PCT",
    "MIN_IMPULSE_PCT",
    "MIN_RUNAWAY_IMPULSE_PCT",
    "MIN_BREAKOUT_VOLUME_RATIO",
    "MIN_COMPRESSION_VOLUME_RATIO",
    "MAX_PULLBACK_RETRACE",
    "MIN_PULLBACK_RETRACE",
    "MAX_PULLBACK_VOLUME_RATIO",
    "MAX_ENTRY_RISK_PCT",
    "MAX_EMA9_EXTENSION_PCT",
    "MAX_ATR_EXTENSION",
    "MIN_BREAKOUT_CLOSE_LOCATION",
    "MAX_COMPRESSION_WIDTH_RATIO",
    "REQUIRE_PULLBACK_NO_NEW_HIGH",
    "REQUIRE_COMPRESSION_ABOVE_EMA20",
    "REQUIRE_COMPRESSION_HOD_BREAK",
    "PARTIAL_TRIGGER_R",
    "PARTIAL_FRACTION",
    "STRUCTURAL_BUFFER_ATR",
    "INITIAL_STOP_BUFFER_ATR",
    "BELOW_TREND_EXIT_BARS",
    "DISTRIBUTION_RANGE_ATR",
    "DISTRIBUTION_VOLUME_RATIO",
    "ENABLE_STRUCTURAL_EXIT",
    "ENABLE_TREND_EXIT",
    "ENABLE_DISTRIBUTION_EXIT",
    "LEADER_LATCH_TTL",
    "REENTRY_COOLDOWN",
    "MAX_TRADES",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _frozen_parameters() -> dict[str, object]:
    values: dict[str, object] = {}
    for name in FROZEN_PARAMETER_NAMES:
        value = getattr(leader, name)
        if isinstance(value, timedelta):
            values[name] = str(value)
        elif isinstance(value, Decimal):
            values[name] = str(value)
        else:
            values[name] = value
    return values


def _verify_frozen_policy(*, expected_sha: str = EXPECTED_STRATEGY_SHA256) -> dict[str, object]:
    current_sha = _sha256(REPOSITORY_ROOT / "src/app/apps/trading/strategy_leader_momentum_continuation.py")
    if current_sha != expected_sha:
        raise RuntimeError(f"frozen strategy SHA-256 mismatch: {current_sha}")
    if leader.POLICY_VERSION != EXPECTED_POLICY_VERSION:
        raise RuntimeError(f"frozen policy mismatch: {leader.POLICY_VERSION}")
    return {
        "git_sha": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip(),
        "strategy_sha256": current_sha,
        "policy_version": leader.POLICY_VERSION,
        "parameters": _frozen_parameters(),
    }


def _csv_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _write_csv(path: Path, rows: list[dict[str, object]], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fields is None:
        fields = []
        seen: set[str] = set()
        for row in rows:
            for key in row:
                if key not in seen:
                    seen.add(key)
                    fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _csv_value(row.get(field)) for field in fields})


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, default=str, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _symbol(instrument_id: str) -> str:
    return str(instrument_id).rsplit(":", 1)[-1].strip().upper()


def _winner_rows(path: Path) -> tuple[list[dict[str, object]], list[date], dict[date, list[dict[str, object]]]]:
    rows, sessions, grouped = replay._parse_source(path)
    if len(rows) != len(sessions) * 5:
        raise RuntimeError("winner benchmark is not exactly five observations per session")
    return rows, sessions, grouped


def _archive_snapshots(sessions: list[date]) -> tuple[dict[date, Any], dict[str, object]]:
    """Read point-in-time archives when the credential-aware runtime is available."""

    result: dict[date, Any] = {}
    metadata: dict[str, object] = {
        "available": bool(os.environ.get("OMNIX_DATABASE_URL")),
        "error": None,
        "sessions": [],
    }
    if not os.environ.get("OMNIX_DATABASE_URL"):
        metadata["error"] = "OMNIX_DATABASE_URL not injected; archive lookup skipped"
        return result, metadata
    try:
        from app.apps.trading.strategy_repository import default_strategy_repository

        snapshots = default_strategy_repository().list_universes(
            start_date=min(sessions), end_date=max(sessions)
        )
    except Exception as exc:
        metadata["error"] = f"{type(exc).__name__}: {exc}"
        return result, metadata

    for session_date in sessions:
        scan_at = datetime.combine(session_date, SCAN_TIME_ET, tzinfo=ET)
        eligible = [
            item
            for item in snapshots
            if item.session_date == session_date
            and item.discovery_source in {"finviz", "scanner", "provider"}
            and (
                bool(item.candidates)
                or bool(item.source_candidate_symbols)
                or bool(item.source_member_dispositions)
            )
            and item.evaluation_time.astimezone(ET).time()
            <= (scan_at + timedelta(minutes=10)).timetz().replace(tzinfo=None)
        ]
        if not eligible:
            continue
        # Prefer the configured five-member capture, then the richest source
        # membership, and finally the latest capture inside the causal window.
        eligible.sort(
            key=lambda item: (
                len(item.source_candidate_symbols) == 5,
                len(item.source_candidate_symbols),
                item.evaluation_time,
            ),
            reverse=True,
        )
        result[session_date] = eligible[0]
    metadata["sessions"] = sorted(item.isoformat() for item in result)
    metadata["snapshot_count"] = len(snapshots)
    return result, metadata


def _candidate_by_symbol(snapshot: Any) -> dict[str, Any]:
    return {_symbol(item.instrument_id): item for item in snapshot.candidates}


def _causal_rows_from_snapshot(snapshot: Any, *, source: str) -> list[dict[str, object]]:
    candidates = _candidate_by_symbol(snapshot)
    members = list(snapshot.source_member_dispositions)
    if not members:
        members = []
        symbols = list(snapshot.source_candidate_symbols)
        for rank, item in enumerate(snapshot.candidates, start=1):
            symbols.append(_symbol(item.instrument_id))
        for rank, item in enumerate(dict.fromkeys(symbols), start=1):
            members.append(type("Member", (), {"symbol": item, "source_rank": rank, "status": "materialized", "instrument_id": f"equity:US:{item}"})())

    rows: list[dict[str, object]] = []
    for member in members:
        symbol = str(member.symbol).upper()
        candidate = candidates.get(symbol)
        rows.append(
            {
                "session_date": snapshot.session_date,
                "symbol": symbol,
                "instrument_id": candidate.instrument_id if candidate else (member.instrument_id or f"equity:US:{symbol}"),
                "causal_universe_source": source,
                "source_locator": snapshot.source_locator or snapshot.universe_id,
                "universe_id": snapshot.universe_id,
                "universe_source_fingerprint": snapshot.source_fingerprint,
                "discovery_candidate_timestamp": candidate.observed_at if candidate else snapshot.evaluation_time,
                "discovery_timestamp": snapshot.evaluation_time,
                "causal_source_rank": member.source_rank,
                "causal_source_status": member.status,
                "candidate_materialized": candidate is not None,
                "premarket_price": candidate.premarket_price if candidate else None,
                "gap_pct": candidate.gap_pct if candidate else None,
                "premarket_volume": candidate.premarket_volume if candidate else None,
                "premarket_dollar_volume": candidate.premarket_dollar_volume if candidate else None,
                "tod_rvol": candidate.tod_rvol if candidate else None,
                "spread_bps": candidate.spread_bps if candidate else None,
                "market_data_complete": candidate.market_data_complete if candidate else None,
                "data_quality_flags": json.dumps(list(candidate.data_quality_flags)) if candidate else "",
                "discovery_tier": None,
            }
        )
    return rows


def _build_causal_universe(
    sessions: list[date],
    archives: dict[date, Any],
) -> tuple[list[dict[str, object]], dict[str, object]]:
    config = managed_finviz_v2_config()
    reconstructor = AlpacaHistoricalGapperReconstructor(
        start_date=min(sessions),
        end_date=max(sessions),
        config=config,
        assumed_spread_bps=ASSUMED_SPREAD_BPS,
        max_age_days=365,
        clock=datetime.now(UTC),
        feed="sip",
        runtime=historical_replay_http_runtime("alpaca_historical_gapper_reconstruction"),
    )
    rows: list[dict[str, object]] = []
    metadata: dict[str, object] = {
        "scan_time_et": SCAN_TIME_ET.isoformat(),
        "config": config.model_dump(mode="json"),
        "archive_sessions": sorted(item.isoformat() for item in archives),
        "fallback_sessions": [],
        "reconstruction": [],
    }
    for session_date in sessions:
        if session_date in archives:
            rows.extend(_causal_rows_from_snapshot(archives[session_date], source="historical_archive"))
            continue
        try:
            reconstructed = reconstructor(
                session_date=session_date,
                scan_time=SCAN_TIME_ET,
                config=config,
                assumed_spread_bps=ASSUMED_SPREAD_BPS,
                max_age_days=365,
            )
        except Exception as exc:
            metadata["fallback_sessions"].append(session_date.isoformat())
            metadata["reconstruction"].append(
                {"session_date": session_date.isoformat(), "status": "error", "error": f"{type(exc).__name__}: {exc}"}
            )
            continue
        metadata["fallback_sessions"].append(session_date.isoformat())
        metadata["reconstruction"].append(
            {
                "session_date": session_date.isoformat(),
                "status": "ok" if reconstructed.snapshot is not None else "unavailable",
                "fidelity": reconstructed.fidelity,
                "candidate_seed_count": reconstructed.candidate_seed_count,
                "active_asset_count": reconstructed.active_asset_count,
                "warnings": list(reconstructed.warnings),
                "detail": reconstructed.detail,
            }
        )
        if reconstructed.snapshot is not None:
            rows.extend(_causal_rows_from_snapshot(reconstructed.snapshot, source="causal_reconstructed_alpaca_sip"))
    deduped: dict[tuple[date, str], dict[str, object]] = {}
    for row in rows:
        deduped[(row["session_date"], str(row["symbol"]))] = row
    ordered = [deduped[key] for key in sorted(deduped)]
    metadata["row_count"] = len(ordered)
    metadata["session_count"] = len({row["session_date"] for row in ordered})
    metadata["missing_sessions"] = [
        item.isoformat() for item in sessions if item not in {row["session_date"] for row in ordered}
    ]
    return ordered, metadata


CAUSAL_FIELDS = [
    "session_date", "symbol", "instrument_id", "causal_universe_source", "source_locator",
    "universe_id", "universe_source_fingerprint", "discovery_candidate_timestamp",
    "discovery_timestamp", "causal_source_rank", "causal_source_status", "candidate_materialized",
    "premarket_price", "gap_pct", "premarket_volume", "premarket_dollar_volume", "tod_rvol",
    "spread_bps", "market_data_complete", "data_quality_flags", "discovery_tier",
]


def _winner_lookup(rows: list[dict[str, object]]) -> dict[tuple[date, str], dict[str, object]]:
    return {(row["session_date"], str(row["symbol"]).upper()): row for row in rows}


def _label_causal_rows(
    rows: list[dict[str, object]],
    winners: dict[tuple[date, str], dict[str, object]],
) -> list[dict[str, object]]:
    output: list[dict[str, object]] = []
    for row in rows:
        winner = winners.get((row["session_date"], str(row["symbol"]).upper()))
        output.append(
            {
                **row,
                "cohort": "winner" if winner else "control",
                "eventual_winner_rank": winner["rank"] if winner else None,
                "eventual_winner_gain_pct": winner["gain_pct"] if winner else None,
            }
        )
    return output


def _match_controls(
    causal_rows: list[dict[str, object]],
    winners: list[dict[str, object]],
) -> set[tuple[date, str]]:
    """Greedy causal-characteristic matching; no intraday outcome is used."""

    controls_by_day: dict[date, list[dict[str, object]]] = defaultdict(list)
    for row in causal_rows:
        if row["cohort"] == "control":
            controls_by_day[row["session_date"]].append(row)
    used: set[tuple[date, str]] = set()

    def numeric(row: dict[str, object], field: str) -> Decimal | None:
        value = row.get(field)
        if value in {None, ""}:
            return None
        try:
            return Decimal(str(value))
        except Exception:
            return None

    fields = ("premarket_price", "gap_pct", "tod_rvol", "premarket_dollar_volume", "premarket_volume")
    scales = {
        "premarket_price": Decimal("10"),
        "gap_pct": Decimal("100"),
        "tod_rvol": Decimal("20"),
        "premarket_dollar_volume": Decimal("10000000"),
        "premarket_volume": Decimal("1000000"),
    }
    for winner in sorted(winners, key=lambda item: (item["session_date"], int(item["rank"]), str(item["symbol"]))):
        candidates = []
        for control in controls_by_day.get(winner["session_date"], []):
            key = (control["session_date"], str(control["symbol"]))
            if key in used:
                continue
            distance = Decimal("0")
            observed_fields = 0
            for field in fields:
                left, right = numeric(winner, field), numeric(control, field)
                if left is None or right is None:
                    continue
                observed_fields += 1
                distance += abs(left - right) / scales[field]
            if observed_fields:
                distance /= Decimal(observed_fields)
            else:
                distance = Decimal("999")
            candidates.append((distance, str(control["symbol"]), key))
        candidates.sort()
        for _distance, _symbol, key in candidates[:MATCHED_CONTROLS_PER_WINNER]:
            used.add(key)
    return used


def _load_symbols(
    symbols: list[str],
    source_rows_by_symbol: dict[str, dict[date, dict[str, object]]],
    sessions: list[date],
    *,
    workers: int,
) -> dict[str, replay.SymbolReplayData]:
    loaded: dict[str, replay.SymbolReplayData] = {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {
            executor.submit(replay._load_symbol, symbol, source_rows_by_symbol[symbol], sessions): symbol
            for symbol in symbols
        }
        for index, future in enumerate(as_completed(futures), start=1):
            symbol = futures[future]
            try:
                loaded[symbol] = future.result()
            except Exception as exc:
                loaded[symbol] = replay.SymbolReplayData(
                    symbol=symbol,
                    candidates={},
                    bars_5m={},
                    bars_1m={},
                    five_minute_error=f"{type(exc).__name__}: {exc}",
                    one_minute_errors={},
                )
            if index % 25 == 0 or index == len(symbols):
                print(f"Loaded {index}/{len(symbols)} symbols", flush=True)
    return loaded


def _context(candidate: Any) -> leader.LeaderMomentumContext | None:
    if candidate is None:
        return None
    return leader.LeaderMomentumContext(
        tod_rvol=candidate.tod_rvol,
        spread_bps=candidate.spread_bps,
        dollar_volume=candidate.premarket_dollar_volume,
    )


def _strategy_return(trades: Iterable[Any]) -> Decimal | None:
    trades = tuple(trades)
    if not trades:
        return None
    factor = Decimal("1")
    for trade in trades:
        factor *= Decimal("1") + trade.return_pct / Decimal("100")
    return (factor - Decimal("1")) * Decimal("100")


def _observation_and_trades(
    *,
    row: dict[str, object],
    data: replay.SymbolReplayData,
    matched_keys: set[tuple[date, str]],
) -> tuple[dict[str, object], list[dict[str, object]]]:
    session_date = row["session_date"]
    symbol = str(row["symbol"])
    raw_bars = data.bars_1m.get(session_date, ())
    raw_5m = data.bars_5m.get(session_date, ())
    candidate = data.candidates.get(session_date)
    context = _context(candidate)
    loader_reason = data.five_minute_error or ("; ".join((data.one_minute_errors or {}).values()) or None)
    availability = "evaluated"
    status = "evaluated"
    reason = ""
    snapshot = None
    trace = None
    if data.five_minute_error:
        availability, status, reason = "bars_unavailable", "data_unavailable", data.five_minute_error
    elif not raw_5m or candidate is None:
        availability, status, reason = "bars_unavailable", "data_unavailable", "LEADER_MOMENTUM_CANDIDATE_METADATA_UNAVAILABLE"
    elif not raw_bars:
        availability, status, reason = "bars_unavailable", "data_unavailable", "LEADER_MOMENTUM_1M_REGULAR_BARS_UNAVAILABLE"
    else:
        market_bars = replay._market_bars(raw_bars, f"equity:US:{symbol}", "1m")
        snapshot = leader.evaluate_leader_momentum_continuation(market_bars, context=context)
        trace = diagnose_leader_momentum_continuation(market_bars, context=context)
        if trace.strategy_snapshot != snapshot:
            raise RuntimeError(f"diagnostic strategy snapshot drift for {session_date} {symbol}")
        if snapshot.execution_authority or trace.execution_authority:
            raise RuntimeError(f"execution authority unexpectedly enabled for {session_date} {symbol}")
        status, reason = ("completed", snapshot.reason_code) if snapshot.trades else (snapshot.state, snapshot.reason_code)
    trades = list(snapshot.trades) if snapshot is not None else []
    observation: dict[str, object] = {
        **row,
        "cohort": row["cohort"],
        "matched_control": (row["cohort"] == "control" and (session_date, symbol) in matched_keys),
        "arm": "leader-momentum-continuation",
        "availability": availability,
        "status": status,
        "reason": reason,
        "loader_reason": loader_reason,
        "raw_regular_5m_bar_count": len(raw_5m),
        "raw_regular_1m_bar_count": len(raw_bars),
        "candidate_context_available": candidate is not None,
        "strategy_execution_authority": False,
        "policy_version": leader.POLICY_VERSION,
        "leader_confirmed": bool(trace and trace.first_leader_confirmed_at is not None),
        "leader_confirmation_timestamp": trace.first_leader_confirmed_at if trace else None,
        "leader_score": trace.max_score.total_score if trace and trace.max_score else None,
        "leader_score_timestamp": trace.max_score.observed_at if trace and trace.max_score else None,
        "leader_score_first_confirmation": trace.first_confirmed_score.total_score if trace and trace.first_confirmed_score else None,
        "setup_mode": trades[0].mode if trades else (snapshot.setup_mode if snapshot else None),
        "trade_count": len(trades),
        "win_count": sum(trade.return_pct > 0 for trade in trades),
        "loss_count": sum(trade.return_pct < 0 for trade in trades),
        "strategy_return_pct": _strategy_return(trades),
        "mfe_pct": max((trade.mfe_pct for trade in trades), default=None),
        "mae_pct": min((trade.mae_pct for trade in trades), default=None),
        "first_signal_timestamp": trades[0].signal_time if trades else None,
        "first_entry_timestamp": trades[0].entry_time if trades else None,
        "first_entry_price": trades[0].entry_price if trades else None,
        "last_exit_timestamp": trades[-1].exit_time if trades else None,
        "last_exit_price": trades[-1].exit_price if trades else None,
        "final_state": snapshot.state if snapshot else None,
        "final_reason": snapshot.reason_code if snapshot else None,
        "recovered_gap_count": snapshot.recovered_gap_count if snapshot else None,
        "data_gap_start": snapshot.data_gap_start if snapshot else None,
        "data_gap_resume": snapshot.data_gap_resume if snapshot else None,
        "diagnostic_trace_json": trace.model_dump_json() if trace else "",
    }
    trade_rows: list[dict[str, object]] = []
    for trade_index, trade in enumerate(trades):
        risk_pct = (trade.entry_price - trade.initial_stop_price) / trade.entry_price * Decimal("100")
        trade_rows.append(
            {
                "session_date": session_date,
                "symbol": symbol,
                "cohort": row["cohort"],
                "matched_control": observation["matched_control"],
                "causal_universe_source": row["causal_universe_source"],
                "source_locator": row["source_locator"],
                "discovery_candidate_timestamp": row["discovery_candidate_timestamp"],
                "setup_mode": trade.mode,
                "signal_timestamp": trade.signal_time,
                "entry_timestamp": trade.entry_time,
                "entry_price": trade.entry_price,
                "initial_stop": trade.initial_stop_price,
                "risk_pct": risk_pct,
                "exit_timestamp": trade.exit_time,
                "exit_price": trade.exit_price,
                "exit_reason": trade.exit_reason_code,
                "raw_return_pct": trade.return_pct,
                "mfe_pct": trade.mfe_pct,
                "mae_pct": trade.mae_pct,
                "partial_exit_timestamp": trade.partial_exit_time,
                "partial_exit_price": trade.partial_exit_price,
                "first_trade_vs_reentry": "first_trade" if trade_index == 0 else "reentry",
                "leader_confirmation_timestamp": trace.first_leader_confirmed_at if trace else None,
                "leader_score": trace.first_confirmed_score.total_score if trace and trace.first_confirmed_score else (trace.max_score.total_score if trace and trace.max_score else None),
                "execution_authority": False,
            }
        )
    return observation, trade_rows


def _attach_overlay_allocations(observations: list[dict[str, object]]) -> None:
    by_day: dict[date, list[dict[str, object]]] = defaultdict(list)
    for row in observations:
        if row["availability"] == "evaluated" and row["trade_count"]:
            by_day[row["session_date"]].append(row)
    for day_rows in by_day.values():
        day_rows.sort(key=lambda item: (item["first_entry_timestamp"] or datetime.max.replace(tzinfo=UTC), str(item["symbol"])))
        for item in day_rows[:5]:
            item["normalized_allocation"] = FIXED_SLOT_NOTIONAL
            item["normalized_pnl"] = FIXED_SLOT_NOTIONAL * Decimal(str(item["strategy_return_pct"])) / Decimal("100")
        for item in day_rows[5:]:
            item["normalized_allocation"] = Decimal("0")
            item["normalized_pnl"] = Decimal("0")


def _trades_for_scope(trades: list[dict[str, object]], scope: str) -> list[dict[str, object]]:
    if scope == "combined":
        return trades
    if scope == "matched_control":
        return [row for row in trades if row["matched_control"]]
    return [row for row in trades if row["cohort"] == scope]


def _observations_for_scope(observations: list[dict[str, object]], scope: str) -> list[dict[str, object]]:
    if scope == "combined":
        return observations
    if scope == "matched_control":
        return [row for row in observations if row["matched_control"]]
    return [row for row in observations if row["cohort"] == scope]


def _dec_mean(values: list[Decimal]) -> Decimal | None:
    return sum(values, Decimal("0")) / Decimal(len(values)) if values else None


def _metric_row(scope: str, observations: list[dict[str, object]], trades: list[dict[str, object]]) -> dict[str, object]:
    evaluated = [row for row in observations if row["availability"] == "evaluated"]
    confirmed = [row for row in evaluated if row["leader_confirmed"]]
    returns = [Decimal(str(row["raw_return_pct"])) for row in trades]
    winners = [value for value in returns if value > 0]
    losers = [value for value in returns if value < 0]
    gross_pnl = sum((FIXED_SLOT_NOTIONAL * value / Decimal("100") for value in returns), Decimal("0"))
    gross_winner = sum((FIXED_SLOT_NOTIONAL * value / Decimal("100") for value in winners), Decimal("0"))
    gross_loser = sum((FIXED_SLOT_NOTIONAL * value / Decimal("100") for value in losers), Decimal("0"))
    leader_precision = None
    trade_precision = None
    if scope == "combined":
        leader_precision = Decimal(sum(row["cohort"] == "winner" for row in confirmed)) / Decimal(len(confirmed)) if confirmed else None
        trade_precision = Decimal(sum(row["cohort"] == "winner" for row in trades)) / Decimal(len(trades)) if trades else None
    return {
        "scope": scope,
        "expected_observations": len(observations),
        "evaluated_observations": len(evaluated),
        "missing_observations": len(observations) - len(evaluated),
        "evaluated_fraction": Decimal(len(evaluated)) / Decimal(len(observations)) if observations else Decimal("1"),
        "leader_confirmed_observations": len(confirmed),
        "p_leader_confirmed": Decimal(len(confirmed)) / Decimal(len(evaluated)) if evaluated else None,
        "trade_observations": sum(bool(row["trade_count"]) for row in evaluated),
        "trade_count": len(trades),
        "p_trade": Decimal(sum(bool(row["trade_count"]) for row in evaluated)) / Decimal(len(evaluated)) if evaluated else None,
        "leader_precision": leader_precision,
        "trade_precision": trade_precision,
        "wins": sum(value > 0 for value in returns),
        "losses": sum(value < 0 for value in returns),
        "win_rate": Decimal(sum(value > 0 for value in returns)) / Decimal(len(returns)) if returns else None,
        "mean_return_per_trade": _dec_mean(returns),
        "median_return_per_trade": Decimal(str(median(returns))) if returns else None,
        "expectancy": _dec_mean(returns),
        "average_winner": _dec_mean(winners),
        "average_loser": _dec_mean(losers),
        "median_winner": Decimal(str(median(winners))) if winners else None,
        "median_loser": Decimal(str(median(losers))) if losers else None,
        "profit_factor": (gross_winner / abs(gross_loser)) if gross_loser else None,
        "mean_mfe": _dec_mean([Decimal(str(row["mfe_pct"])) for row in trades]),
        "mean_mae": _dec_mean([Decimal(str(row["mae_pct"])) for row in trades]),
        "realized_mfe_capture_ratio": (
            _dec_mean([Decimal(str(row["raw_return_pct"])) / Decimal(str(row["mfe_pct"])) for row in trades if row["mfe_pct"] not in {None, ""} and Decimal(str(row["mfe_pct"])) > 0])
        ),
        "gross_pnl_at_20k_per_trade": gross_pnl,
        "normalized_overlay_pnl": sum((Decimal(str(row.get("normalized_pnl", 0))) for row in observations), Decimal("0")),
    }


def _pct_or_blank(value: object) -> object:
    return value


def _setup_mode_rows(trades: list[dict[str, object]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for scope in ("winner", "control", "matched_control", "combined"):
        for mode in ("controlled_pullback", "momentum_compression"):
            subset = [row for row in _trades_for_scope(trades, scope) if row["setup_mode"] == mode]
            returns = [Decimal(str(row["raw_return_pct"])) for row in subset]
            gross = sum((FIXED_SLOT_NOTIONAL * value / Decimal("100") for value in returns), Decimal("0"))
            friction = sum((FIXED_SLOT_NOTIONAL * (value - ASSUMED_SPREAD_BPS / Decimal("100")) / Decimal("100") for value in returns), Decimal("0"))
            rows.append(
                {
                    "scope": scope,
                    "setup_mode": mode,
                    "trade_count": len(subset),
                    "winner_trades": sum(row["cohort"] == "winner" for row in subset),
                    "control_trades": sum(row["cohort"] == "control" for row in subset),
                    "trade_precision": Decimal(sum(row["cohort"] == "winner" for row in subset)) / Decimal(len(subset)) if subset else None,
                    "win_rate": Decimal(sum(value > 0 for value in returns)) / Decimal(len(returns)) if returns else None,
                    "expectancy": _dec_mean(returns),
                    "median_return": Decimal(str(median(returns))) if returns else None,
                    "gross_pnl": gross,
                    "friction_bps": ASSUMED_SPREAD_BPS,
                    "friction_adjusted_pnl": friction,
                    "mean_mfe": _dec_mean([Decimal(str(row["mfe_pct"])) for row in subset]),
                    "mean_mae": _dec_mean([Decimal(str(row["mae_pct"])) for row in subset]),
                    "exit_distribution": json.dumps(
                        dict(
                            sorted(
                                {
                                    reason: sum(item["exit_reason"] == reason for item in subset)
                                    for reason in {str(item["exit_reason"]) for item in subset}
                                }.items()
                            )
                        )
                    ),
                }
            )
    return rows


def _exit_rows(trades: list[dict[str, object]]) -> list[dict[str, object]]:
    reasons = sorted({str(row["exit_reason"]) for row in trades})
    rows: list[dict[str, object]] = []
    for reason in reasons:
        subset = [row for row in trades if row["exit_reason"] == reason]
        returns = [Decimal(str(row["raw_return_pct"])) for row in subset]
        rows.append(
            {
                "exit_reason": reason,
                "count": len(subset),
                "winner_trades": sum(row["cohort"] == "winner" for row in subset),
                "control_trades": sum(row["cohort"] == "control" for row in subset),
                "mean_return": _dec_mean(returns),
                "median_return": Decimal(str(median(returns))) if returns else None,
                "mean_mfe": _dec_mean([Decimal(str(row["mfe_pct"])) for row in subset]),
                "mean_mae": _dec_mean([Decimal(str(row["mae_pct"])) for row in subset]),
                "substantial_positive_mfe_losing_trades": sum(value < 0 and Decimal(str(row["mfe_pct"])) >= Decimal("5") for value, row in zip(returns, subset)),
            }
        )
    return rows


def _friction_rows(trades: list[dict[str, object]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for bps in (Decimal("0"), Decimal("40"), Decimal("80"), Decimal("120"), Decimal("200")):
        for scope in ("winner", "control", "combined"):
            subset = _trades_for_scope(trades, scope)
            returns = [Decimal(str(row["raw_return_pct"])) - bps / Decimal("100") for row in subset]
            pnl = sum((FIXED_SLOT_NOTIONAL * value / Decimal("100") for value in returns), Decimal("0"))
            rows.append(
                {
                    "friction_bps_total_round_trip": bps,
                    "scope": scope,
                    "trade_count": len(subset),
                    "winner_trades": sum(row["cohort"] == "winner" for row in subset),
                    "control_trades": sum(row["cohort"] == "control" for row in subset),
                    "mean_return_after_friction": _dec_mean(returns),
                    "median_return_after_friction": Decimal(str(median(returns))) if returns else None,
                    "expectancy_after_friction": _dec_mean(returns),
                    "friction_adjusted_pnl": pnl,
                    "win_rate_after_friction": Decimal(sum(value > 0 for value in returns)) / Decimal(len(returns)) if returns else None,
                }
            )
    return rows


def _concentration_rows(trades: list[dict[str, object]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for scope in ("winner", "control", "combined"):
        subset = _trades_for_scope(trades, scope)
        ordered = sorted(subset, key=lambda row: Decimal(str(row["raw_return_pct"])), reverse=True)
        for label, count in (("all", 0), ("exclude_cphi", 0), ("exclude_top_1", 1), ("exclude_top_3", 3), ("exclude_top_5", 5)):
            if label == "all":
                kept = ordered
            elif label == "exclude_cphi":
                kept = [row for row in ordered if str(row["symbol"]).upper() != "CPHI"]
            else:
                kept = ordered[count:]
            values = [FIXED_SLOT_NOTIONAL * Decimal(str(row["raw_return_pct"])) / Decimal("100") for row in kept]
            total = sum(values, Decimal("0"))
            rows.append(
                {
                    "scope": scope,
                    "scenario": label,
                    "trade_count": len(kept),
                    "pnl": sum(values, Decimal("0")),
                    "return_on_reused_100k_pct": sum(values, Decimal("0")) / FIXED_DAILY_CAPITAL * Decimal("100"),
                    "largest_trade_contribution": values[0] if values else None,
                    "top_3_contribution": sum(values[:3], Decimal("0")),
                    "top_5_contribution": sum(values[:5], Decimal("0")),
                    "top_1_pct_of_total": values[0] / total * Decimal("100") if values and total else None,
                    "top_3_pct_of_total": sum(values[:3], Decimal("0")) / total * Decimal("100") if total else None,
                    "top_5_pct_of_total": sum(values[:5], Decimal("0")) / total * Decimal("100") if total else None,
                    "cphi_trade_count": sum(str(row["symbol"]).upper() == "CPHI" for row in kept),
                    "cphi_pnl": sum((FIXED_SLOT_NOTIONAL * Decimal(str(row["raw_return_pct"])) / Decimal("100") for row in kept if str(row["symbol"]).upper() == "CPHI"), Decimal("0")),
                }
            )
    return rows


def _monthly_rows(observations: list[dict[str, object]], trades: list[dict[str, object]]) -> list[dict[str, object]]:
    buckets: list[tuple[str, date, date]] = [
        ("June", date(2026, 6, 1), date(2026, 6, 30)),
        ("July", date(2026, 7, 1), date(2026, 7, 31)),
        ("August", date(2026, 8, 1), date(2026, 8, 31)),
        ("September", date(2026, 9, 1), date(2026, 9, 30)),
        ("June15-Aug12", date(2026, 6, 15), date(2026, 8, 12)),
        ("Aug13-Sep11", date(2026, 8, 13), date(2026, 9, 11)),
    ]
    rows: list[dict[str, object]] = []
    for scope in ("winner", "control", "combined"):
        for label, start, end in buckets:
            o = [row for row in _observations_for_scope(observations, scope) if start <= row["session_date"] <= end]
            t = [row for row in _trades_for_scope(trades, scope) if start <= row["session_date"] <= end]
            returns = [Decimal(str(row["raw_return_pct"])) for row in t]
            rows.append(
                {
                    "scope": scope,
                    "period": label,
                    "start_date": start,
                    "end_date": end,
                    "observations": len(o),
                    "evaluated_observations": sum(row["availability"] == "evaluated" for row in o),
                    "trade_count": len(t),
                    "wins": sum(value > 0 for value in returns),
                    "losses": sum(value < 0 for value in returns),
                    "expectancy": _dec_mean(returns),
                    "gross_pnl": sum((FIXED_SLOT_NOTIONAL * value / Decimal("100") for value in returns), Decimal("0")),
                }
            )
    return rows


def _portfolio_pressure(observations: list[dict[str, object]], trades: list[dict[str, object]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for scope in ("winner", "control", "combined"):
        subset_obs = _observations_for_scope(observations, scope)
        subset = sorted(_trades_for_scope(trades, scope), key=lambda row: (row["entry_timestamp"] or datetime.max.replace(tzinfo=UTC), str(row["symbol"])))
        by_day: dict[date, list[dict[str, object]]] = defaultdict(list)
        for trade in subset:
            by_day[trade["session_date"]].append(trade)
        simultaneous_max = 0
        over_five = 0
        first_five_pnl = Decimal("0")
        all_pnl = Decimal("0")
        days_over_five = 0
        for day, day_trades in by_day.items():
            day_trades.sort(key=lambda row: (row["entry_timestamp"] or datetime.max.replace(tzinfo=UTC), str(row["symbol"])))
            if len(day_trades) > 5:
                days_over_five += 1
                over_five += len(day_trades) - 5
            for index, trade in enumerate(day_trades):
                pnl = FIXED_SLOT_NOTIONAL * Decimal(str(trade["raw_return_pct"])) / Decimal("100")
                all_pnl += pnl
                if index < 5:
                    first_five_pnl += pnl
            for tick in day_trades:
                active = sum(other["entry_timestamp"] <= tick["entry_timestamp"] < other["exit_timestamp"] for other in day_trades)
                simultaneous_max = max(simultaneous_max, active)
        rows.append(
            {
                "scope": scope,
                "observation_count": len(subset_obs),
                "trade_signal_count": len(subset),
                "days_with_more_than_5_trade_signals": days_over_five,
                "maximum_simultaneous_open_trades": simultaneous_max,
                "trades_exceeding_5_available_slots": over_five,
                "first_5_entries_pnl": first_five_pnl,
                "all_signals_pnl": all_pnl,
                "first_5_entries_return_pct": first_five_pnl / FIXED_DAILY_CAPITAL * Decimal("100"),
                "all_signals_return_pct": all_pnl / FIXED_DAILY_CAPITAL * Decimal("100"),
                "admission_order": "causal entry_timestamp; symbol tie-break only",
            }
        )
    return rows


def _completeness(
    observations: list[dict[str, object]],
    *,
    expected_keys: set[tuple[date, str]],
) -> dict[str, object]:
    expected = tuple(
        ReplayExpectedObservation(session_date=day, instrument_id=f"equity:US:{symbol}")
        for day, symbol in sorted(expected_keys)
    )
    availability = tuple(
        replay_observation_from_result(
            arm="leader-momentum-continuation",
            session_date=row["session_date"],
            instrument_id=f"equity:US:{row['symbol']}",
            status=(
                "data_gap"
                if row["availability"] == "evaluated" and row.get("recovered_gap_count")
                else ("evaluated" if row["availability"] == "evaluated" else "data_unavailable")
            ),
            reason=("data_gap" if row.get("recovered_gap_count") else str(row.get("reason") or "")),
        )
        for row in observations
        if (row["session_date"], str(row["symbol"])) in expected_keys
    )
    report = assess_replay_completeness(
        availability,
        expected,
        arms=("leader-momentum-continuation",),
    )
    payload = report.model_dump(mode="json")
    payload["data_gap_observations"] = [
        {"session_date": row["session_date"], "symbol": row["symbol"], "recovered_gap_count": row["recovered_gap_count"]}
        for row in observations
        if row.get("recovered_gap_count")
    ]
    payload["provider_or_cache_failures"] = [
        {"session_date": row["session_date"], "symbol": row["symbol"], "reason": row["reason"]}
        for row in observations
        if row["availability"] != "evaluated"
    ]
    return payload


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run frozen Leader Momentum v1.2 on causal winner/control evidence.")
    parser.add_argument("--input", default="docs/trading/HISTORICAL_TOP5_WINNERS_2026-06-11_TO_2026-09-11.csv")
    parser.add_argument("--output-dir", default="artifacts/trading/leader-momentum-v1.2-prospective-control")
    parser.add_argument("--cache-dir", default=str(replay.CACHE_DIR))
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--skip-archives", action="store_true")
    # The credential-aware launcher appends this marker to every child batch.
    # It is provenance only; the value is never used as authority.
    parser.add_argument("--database-credential-injected", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    output_dir = Path(args.output_dir)
    strategy_before = _verify_frozen_policy()
    if strategy_before["git_sha"] != EXPECTED_COMMIT_SHA:
        raise RuntimeError(f"unexpected starting commit: {strategy_before['git_sha']}")
    input_path = Path(args.input)
    if not input_path.exists():
        raise RuntimeError(f"winner benchmark input is unavailable: {input_path}")
    winner_rows, sessions, grouped = _winner_rows(input_path)
    input_sha = _sha256(input_path)

    # Reproduce the existing winner benchmark before any control construction.
    replay.CACHE_DIR = Path(args.cache_dir)
    replay.ACTIVE_SOURCE = "alpaca-sip"
    replay.CACHE_ONLY = True
    replay.reset_cache_stats()
    winner_source_by_symbol: dict[str, dict[date, dict[str, object]]] = defaultdict(dict)
    for row in winner_rows:
        winner_source_by_symbol[str(row["symbol"])][row["session_date"]] = row
    winner_symbols = sorted(winner_source_by_symbol)
    winner_loaded = _load_symbols(winner_symbols, winner_source_by_symbol, sessions, workers=args.workers)
    winner_observations = replay._evaluate_overlay_arms(sessions, grouped, winner_loaded)
    replay._apply_normalized_allocations(winner_observations, sessions)
    winner_summary, _winner_daily = replay._summary_rows(winner_observations, sessions)
    benchmark = next(item for item in winner_summary if item["arm"] == "leader-momentum-continuation")
    expected_pnl = Decimal("125006")
    if abs(Decimal(str(benchmark["normalized_pnl"])) - expected_pnl) > Decimal("250") or int(benchmark["completed_trades"]) != 128:
        raise RuntimeError(f"winner benchmark materially failed reproduction: {benchmark}")
    benchmark_cache_stats = replay.cache_stats()
    print(f"Winner benchmark reproduced: {benchmark}", flush=True)

    archives, archive_metadata = ({}, {"available": False, "error": "archive lookup disabled", "sessions": []}) if args.skip_archives else _archive_snapshots(sessions)
    causal_rows, causal_metadata = _build_causal_universe(sessions, archives)
    _write_csv(output_dir / "causal-universe.csv", causal_rows, CAUSAL_FIELDS)
    causal_manifest_sha = _sha256(output_dir / "causal-universe.csv")

    winner_by_key = _winner_lookup(winner_rows)
    labelled_rows = _label_causal_rows(causal_rows, winner_by_key)
    matched_keys = _match_controls(labelled_rows, winner_rows)
    for row in labelled_rows:
        row["matched_control"] = (row["session_date"], str(row["symbol"])) in matched_keys
    _write_csv(output_dir / "causal-universe-labelled.csv", labelled_rows, [*CAUSAL_FIELDS, "cohort", "eventual_winner_rank", "eventual_winner_gain_pct", "matched_control"])

    # Winner benchmark observations are retained even when the causal scanner
    # did not select a name; controls are only non-winners from the frozen scan.
    control_rows = [row for row in labelled_rows if row["cohort"] == "control"]
    evaluation_rows: list[dict[str, object]] = []
    for source in winner_rows:
        key = (source["session_date"], str(source["symbol"]))
        if key in {(row["session_date"], str(row["symbol"])) for row in evaluation_rows}:
            continue
        evaluation_rows.append(
            {
                "session_date": source["session_date"],
                "symbol": str(source["symbol"]),
                "instrument_id": f"equity:US:{source['symbol']}",
                "causal_universe_source": "winner_benchmark_outcome_conditioned",
                "source_locator": source["source_url"],
                "universe_id": "winner-benchmark",
                "universe_source_fingerprint": input_sha,
                "discovery_candidate_timestamp": datetime.combine(source["session_date"], SCAN_TIME_ET, tzinfo=ET).astimezone(UTC),
                "discovery_timestamp": datetime.combine(source["session_date"], SCAN_TIME_ET, tzinfo=ET).astimezone(UTC),
                "causal_source_rank": source["rank"],
                "causal_source_status": "benchmark",
                "candidate_materialized": True,
                "premarket_price": None,
                "gap_pct": None,
                "premarket_volume": None,
                "premarket_dollar_volume": None,
                "tod_rvol": None,
                "spread_bps": ASSUMED_SPREAD_BPS,
                "market_data_complete": None,
                "data_quality_flags": "",
                "discovery_tier": None,
                "cohort": "winner",
                "matched_control": False,
            }
        )
    evaluation_rows.extend(control_rows)
    evaluation_rows.sort(key=lambda row: (row["session_date"], str(row["symbol"]), str(row["cohort"])))

    universe_symbols = sorted({str(row["symbol"]) for row in evaluation_rows})
    source_rows_by_symbol: dict[str, dict[date, dict[str, object]]] = defaultdict(dict)
    for row in evaluation_rows:
        source_rows_by_symbol[str(row["symbol"])][row["session_date"]] = {
            "session_date": row["session_date"],
            "rank": int(row["causal_source_rank"]),
            "symbol": row["symbol"],
            "gain_pct": Decimal("0"),
            "source_url": row["source_locator"],
        }
    cache_manifest = {
        "source": "alpaca-sip",
        "cache_dir": str(replay.CACHE_DIR),
        "symbols": universe_symbols,
        "requirements": [
            {
                "symbol": symbol,
                "timeframe": timeframe,
                "start": (
                    datetime.combine(min(sessions) - timedelta(days=30), time(0), tzinfo=ET).astimezone(UTC).isoformat()
                    if timeframe == "5m"
                    else datetime.combine(min(sessions), time(9, 30), tzinfo=ET).astimezone(UTC).isoformat()
                ),
                "end": (
                    datetime.combine(max(sessions) + timedelta(days=1), time(0), tzinfo=ET).astimezone(UTC).isoformat()
                    if timeframe == "5m"
                    else datetime.combine(max(sessions) + timedelta(days=1), time(16), tzinfo=ET).astimezone(UTC).isoformat()
                ),
                "query_profile": "extended_session" if timeframe == "5m" else "regular_session",
            }
            for symbol in universe_symbols
            for timeframe in ("5m", "1m")
        ],
    }
    _write_json(output_dir / "control-cache-manifest.json", cache_manifest)

    # Populate missing cache entries separately. The actual result is restarted
    # below in strict cache-only mode, as required by the experiment contract.
    replay.CACHE_ONLY = False
    replay.reset_cache_stats()
    _load_symbols(universe_symbols, source_rows_by_symbol, sessions, workers=args.workers)
    population_cache_stats = {
        name: replay.cache_stats().get(name, 0)
        for name in ("hits", "misses", "network_fetches", "writes")
    }

    replay.CACHE_ONLY = True
    replay.reset_cache_stats()
    loaded = _load_symbols(universe_symbols, source_rows_by_symbol, sessions, workers=args.workers)
    final_cache_stats = {
        name: replay.cache_stats().get(name, 0)
        for name in ("hits", "misses", "network_fetches", "writes")
    }

    by_key = {(row["session_date"], str(row["symbol"])): row for row in evaluation_rows}
    observations: list[dict[str, object]] = []
    trades: list[dict[str, object]] = []
    for index, row in enumerate(evaluation_rows, start=1):
        observation, trade_rows = _observation_and_trades(
            row=row,
            data=loaded[str(row["symbol"])],
            matched_keys=matched_keys,
        )
        observations.append(observation)
        trades.extend(trade_rows)
        if index % 100 == 0 or index == len(evaluation_rows):
            print(f"Evaluated {index}/{len(evaluation_rows)} observations", flush=True)
    _attach_overlay_allocations(observations)

    # Verify the strategy rows from the full run agree with the independently
    # reproduced benchmark on all 310 winner keys.
    winner_full = [row for row in observations if row["cohort"] == "winner"]
    full_winner_trades = [row for row in trades if row["cohort"] == "winner"]
    if len(winner_full) != 310 or sum(int(row["trade_count"]) for row in winner_full) != 128:
        raise RuntimeError("full-run winner rows drifted from the reproduced benchmark")

    completeness_payload = {
        "winner": _completeness(winner_full, expected_keys={(row["session_date"], str(row["symbol"])) for row in winner_full}),
        "control": _completeness([row for row in observations if row["cohort"] == "control"], expected_keys={(row["session_date"], str(row["symbol"])) for row in control_rows}),
        "matched_control": _completeness([row for row in observations if row["matched_control"]], expected_keys=matched_keys),
        "combined": _completeness(observations, expected_keys={(row["session_date"], str(row["symbol"])) for row in observations}),
    }
    validity = (
        not causal_metadata["missing_sessions"]
        and all(bool(payload["valid_for_strategy_inference"]) for payload in completeness_payload.values())
        and final_cache_stats["network_fetches"] == 0
        and final_cache_stats["misses"] == 0
    )

    _write_csv(output_dir / "observations.csv", observations)
    _write_csv(output_dir / "trades.csv", trades)
    summary_rows = []
    for scope in ("winner", "control", "matched_control", "combined"):
        summary_rows.append(_metric_row(scope, _observations_for_scope(observations, scope), _trades_for_scope(trades, scope)))
    _write_csv(output_dir / "cohort-summary.csv", summary_rows)
    _write_csv(output_dir / "setup-mode-summary.csv", _setup_mode_rows(trades))
    _write_csv(output_dir / "exit-summary.csv", _exit_rows(trades))
    _write_csv(output_dir / "friction-sensitivity.csv", _friction_rows(trades))
    _write_csv(output_dir / "concentration-summary.csv", _concentration_rows(trades))
    _write_csv(output_dir / "monthly-summary.csv", _monthly_rows(observations, trades))
    _write_csv(output_dir / "portfolio-pressure.csv", _portfolio_pressure(observations, trades))
    _write_json(output_dir / "completeness-report.json", {
        "valid_for_strategy_inference": validity,
        "strict_policy": True,
        "reports": completeness_payload,
        "causal_universe_missing_sessions": causal_metadata["missing_sessions"],
        "cache_population": population_cache_stats,
        "cache_final_evaluation": final_cache_stats,
    })

    strategy_after = _verify_frozen_policy()
    if strategy_after["strategy_sha256"] != strategy_before["strategy_sha256"] or strategy_after["policy_version"] != strategy_before["policy_version"] or strategy_after["parameters"] != strategy_before["parameters"]:
        raise RuntimeError("frozen Leader Momentum policy changed during experiment")

    run_config = {
        "git_sha": strategy_before["git_sha"],
        "expected_git_sha": EXPECTED_COMMIT_SHA,
        "strategy_file": "src/app/apps/trading/strategy_leader_momentum_continuation.py",
        "strategy_sha256_before": strategy_before["strategy_sha256"],
        "strategy_sha256_after": strategy_after["strategy_sha256"],
        "policy_version": strategy_before["policy_version"],
        "frozen_parameter_values": strategy_before["parameters"],
        "input": str(input_path),
        "input_sha256": input_sha,
        "benchmark_reproduction": {**benchmark, "cache_stats": benchmark_cache_stats},
        "market_data_source": "alpaca-sip",
        "market_data_cache_dir": str(replay.CACHE_DIR),
        "cache_population_stats": population_cache_stats,
        "cache_final_evaluation_stats": final_cache_stats,
        "cache_only_final_run": True,
        "network_fetches_final_run": final_cache_stats.get("network_fetches", 0),
        "archive_lookup": archive_metadata,
        "causal_universe": causal_metadata,
        "causal_manifest_sha256": causal_manifest_sha,
        "control_matching": {
            "method": "same-session greedy nearest-neighbor on causal premarket price, gap percentage, TOD RVOL, premarket dollar volume, and premarket volume; normalized absolute differences; symbol tie-break",
            "maximum_controls_per_winner": MATCHED_CONTROLS_PER_WINNER,
            "matched_control_count": len(matched_keys),
        },
        "label_join_order": "causal-universe.csv frozen and fingerprinted before EOD winner labels were joined",
        "observation_count": len(observations),
        "winner_observation_count": len(winner_full),
        "control_observation_count": len([row for row in observations if row["cohort"] == "control"]),
        "valid_for_strategy_inference": validity,
        "execution_authority": False,
        "llm_calls": 0,
        "orders_created": 0,
    }
    _write_json(output_dir / "run-config.json", run_config)

    # Generate a concise, auditable handoff report from the saved metric rows.
    combined = next(row for row in summary_rows if row["scope"] == "combined")
    winner = next(row for row in summary_rows if row["scope"] == "winner")
    control = next(row for row in summary_rows if row["scope"] == "control")
    friction = [row for row in _friction_rows(trades) if row["scope"] == "combined"]
    concentration = [row for row in _concentration_rows(trades) if row["scope"] == "combined"]
    lines = [
        "# Frozen Leader Momentum v1.2 prospective control evaluation",
        "",
        f"**Validity:** {'VALID' if validity else 'INVALID — strict observation-level completeness gate failed'}",
        "",
        f"Winner benchmark: {len(winner_full)} observations, {winner['trade_count']} trades, normalized overlay P/L ${sum((Decimal(str(row.get('normalized_pnl', 0))) for row in winner_full), Decimal('0')):.2f}. The independently reproduced expected result was {benchmark['completed_trades']} trades and ${Decimal(str(benchmark['normalized_pnl'])):.2f}.",
        f"Prospective controls: {control['expected_observations']} observations, {control['trade_count']} trades. Combined universe: {combined['expected_observations']} observations, {combined['trade_count']} trades, gross trade-level P/L ${combined['gross_pnl_at_20k_per_trade']:.2f} before friction.",
        "",
        "## Core comparison",
        "",
        f"- P(LEADER_CONFIRMED | winner): {winner['p_leader_confirmed']}",
        f"- P(LEADER_CONFIRMED | control): {control['p_leader_confirmed']}",
        f"- P(trade | winner): {winner['p_trade']}",
        f"- P(trade | control): {control['p_trade']}",
        f"- Combined trade precision: {combined['trade_precision']}",
        f"- Combined expectancy before friction: {combined['expectancy']}% per trade",
        "",
        "## Friction sensitivity",
        "",
        "| Round-trip friction | Combined expectancy | Combined P/L |",
        "|---:|---:|---:|",
    ]
    for row in friction:
        lines.append(f"| {row['friction_bps_total_round_trip']} bps | {row['expectancy_after_friction']}% | ${row['friction_adjusted_pnl']:.2f} |")
    lines.extend([
        "",
        "## Tail concentration",
        "",
        "| Scenario | P/L | Top-1 share | Top-3 share | Top-5 share | CPHI P/L |",
        "|---|---:|---:|---:|---:|---:|",
    ])
    for row in concentration:
        if row["scenario"] in {"all", "exclude_cphi", "exclude_top_1", "exclude_top_3", "exclude_top_5"}:
            lines.append(f"| {row['scenario']} | ${row['pnl']:.2f} | {row['top_1_pct_of_total']}% | {row['top_3_pct_of_total']}% | {row['top_5_pct_of_total']}% | ${row['cphi_pnl']:.2f} |")
    lines.extend([
        "",
        "## Interpretation boundary",
        "",
        "The winner cohort is outcome-conditioned and retained to reproduce the prior conditional winner-capture benchmark. Controls come only from the frozen causal scanner/reconstruction universe after that universe was fingerprinted. No control was selected or removed using intraday or EOD performance. This report is research evidence only; no execution authority, orders, or LLM calls were enabled.",
        "",
        f"Causal manifest SHA-256: `{causal_manifest_sha}`",
        f"Strategy SHA-256 before/after: `{strategy_before['strategy_sha256']}` / `{strategy_after['strategy_sha256']}`",
        f"Final cache stats: `{json.dumps(final_cache_stats, sort_keys=True)}`",
    ])
    (output_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print((output_dir / "summary.md").read_text(encoding="utf-8"))
    return 0 if validity else 2


if __name__ == "__main__":
    raise SystemExit(main())
