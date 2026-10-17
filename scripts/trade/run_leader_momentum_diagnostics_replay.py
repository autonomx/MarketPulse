from __future__ import annotations

"""Run the frozen leader-momentum v1.2 winner-cohort diagnostics replay.

This is research/reporting plumbing only.  It reuses the Alpaca SIP loader from
``run_interday_winner_shadow_replay`` and never imports broker execution code.
The outcome-labelled rank and gain are attached only after both causal
evaluators have returned.
"""

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time, timezone
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
from app.apps.trading.strategy_leader_momentum_diagnostics import (
    LeaderMomentumDiagnosticTrace,
    LeaderScoreBreakdown,
    SetupCandidateDiagnostic,
    diagnose_leader_momentum_continuation,
)
from scripts.trade import run_interday_winner_shadow_replay as replay


ET = ZoneInfo("America/New_York")
UTC = timezone.utc
EXPECTED_POLICY_VERSION = "leader-momentum-continuation-v1.2"
EXPECTED_STRATEGY_SHA256 = (
    "8cdf2f858b19003feb6176608dcd45318500c0772255e3db64e6b0a3367c428a"
)
EXPECTED_THRESHOLDS = {
    "MIN_LEADER_SCORE": Decimal("70"),
    "MIN_SESSION_RETURN_PCT": Decimal("5"),
    "MIN_IMPULSE_PCT": Decimal("4"),
    "MIN_RUNAWAY_IMPULSE_PCT": Decimal("5"),
    "MIN_BREAKOUT_VOLUME_RATIO": Decimal("1.25"),
    "MIN_COMPRESSION_VOLUME_RATIO": Decimal("1"),
    "MAX_PULLBACK_RETRACE": Decimal("0.80"),
    "MIN_PULLBACK_RETRACE": Decimal("0.10"),
    "MAX_PULLBACK_VOLUME_RATIO": Decimal("0.70"),
    "MAX_ENTRY_RISK_PCT": Decimal("12"),
    "MAX_EMA9_EXTENSION_PCT": Decimal("12"),
    "MAX_ATR_EXTENSION": Decimal("3"),
    "MIN_BREAKOUT_CLOSE_LOCATION": Decimal("0.40"),
    "MAX_COMPRESSION_WIDTH_RATIO": Decimal("0.75"),
    "REQUIRE_PULLBACK_NO_NEW_HIGH": True,
    "REQUIRE_COMPRESSION_ABOVE_EMA20": False,
    "REQUIRE_COMPRESSION_HOD_BREAK": False,
    "PARTIAL_TRIGGER_R": Decimal("3"),
    "PARTIAL_FRACTION": Decimal("0.20"),
    "STRUCTURAL_BUFFER_ATR": Decimal("1"),
    "INITIAL_STOP_BUFFER_ATR": Decimal("0.25"),
    "BELOW_TREND_EXIT_BARS": 2,
    "DISTRIBUTION_RANGE_ATR": Decimal("1.5"),
    "DISTRIBUTION_VOLUME_RATIO": Decimal("1.5"),
    "ENABLE_STRUCTURAL_EXIT": True,
    "ENABLE_TREND_EXIT": True,
    "ENABLE_DISTRIBUTION_EXIT": True,
    "LEADER_LATCH_TTL": "2:00:00",
    "REENTRY_COOLDOWN": "0:15:00",
    "MAX_TRADES": 2,
}

SCORE_FIELDS = (
    "observed_at",
    "total_score",
    "market_leadership_points",
    "price_strength_points",
    "trend_points",
    "hod_points",
    "volume_points",
    "context_execution_points",
    "base_context_points",
    "tod_rvol_points",
    "relative_strength_points",
    "spread_penalty_points",
    "dollar_volume_penalty_points",
    "volume_acceleration_points",
    "context_hod_points",
    "session_return_pct",
    "volume_ratio",
    "new_high_count",
)

CANDIDATE_FIELDS = (
    "observed_at",
    "window_start",
    "window_end",
    "candidate",
    "setup_valid",
    "execution_valid",
    "passed_gate_count",
    "failed_gate_count",
    "normalized_shortfall",
    "impulse_pct",
    "retrace_pct",
    "pullback_volume_ratio",
    "compression_width_ratio",
    "breakout_volume_ratio",
    "close_location",
    "ema9_extension_pct",
    "ema9_extension_atr",
    "proposed_risk_pct",
)


def _strategy_path() -> Path:
    return Path("src/app/apps/trading/strategy_leader_momentum_continuation.py")


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _verify_frozen_policy() -> str:
    if leader.POLICY_VERSION != EXPECTED_POLICY_VERSION:
        raise AssertionError(
            f"policy version changed: {leader.POLICY_VERSION!r}"
        )
    for name, expected in EXPECTED_THRESHOLDS.items():
        actual = getattr(leader, name)
        comparable = str(actual) if isinstance(expected, str) else actual
        if comparable != expected:
            raise AssertionError(
                f"frozen threshold changed: {name}={actual!r}, expected {expected!r}"
            )
    digest = _file_sha256(_strategy_path())
    if digest != EXPECTED_STRATEGY_SHA256:
        raise AssertionError(
            f"frozen strategy digest changed: {digest}, expected {EXPECTED_STRATEGY_SHA256}"
        )
    return digest


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


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for field in row:
            if field not in seen:
                seen.add(field)
                fields.append(field)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {field: _csv_value(row.get(field)) for field in fields}
            )


def _minutes(left: datetime | None, right: datetime | None) -> Decimal | None:
    if left is None or right is None:
        return None
    return Decimal(str((right - left).total_seconds() / 60))


def _flatten_score(
    row: dict[str, object],
    prefix: str,
    score: LeaderScoreBreakdown | None,
) -> None:
    for field in SCORE_FIELDS:
        row[f"{prefix}_{field}"] = getattr(score, field) if score else None


def _flatten_candidate(
    row: dict[str, object],
    prefix: str,
    candidate: SetupCandidateDiagnostic | None,
) -> None:
    for field in CANDIDATE_FIELDS:
        row[f"{prefix}_{field}"] = (
            getattr(candidate, field) if candidate else None
        )
    gates = candidate.gates if candidate else ()
    row[f"{prefix}_gates_json"] = json.dumps(
        [gate.model_dump(mode="json") for gate in gates],
        separators=(",", ":"),
    )
    for gate in gates:
        gate_prefix = f"{prefix}_gate_{gate.gate}"
        row[f"{gate_prefix}_passed"] = gate.passed
        row[f"{gate_prefix}_actual"] = gate.actual
        row[f"{gate_prefix}_minimum"] = gate.minimum
        row[f"{gate_prefix}_maximum"] = gate.maximum
        row[f"{gate_prefix}_distance_to_pass"] = gate.distance_to_pass


def _transition_count(trace: LeaderMomentumDiagnosticTrace, kind: str) -> int:
    return sum(item.kind == kind for item in trace.transitions)


def _context_for(
    candidate: replay.GapperCandidate | None,
) -> leader.LeaderMomentumContext | None:
    if candidate is None:
        return None
    return leader.LeaderMomentumContext(
        tod_rvol=candidate.tod_rvol,
        spread_bps=candidate.spread_bps,
        dollar_volume=candidate.premarket_dollar_volume,
    )


def _strategy_values(snapshot: leader.LeaderMomentumSnapshot) -> dict[str, object]:
    trades = tuple(snapshot.trades)
    factor = Decimal("1")
    for trade in trades:
        factor *= Decimal("1") + trade.return_pct / Decimal("100")
    total_return = (
        (factor - Decimal("1")) * Decimal("100") if trades else None
    )
    return {
        "final_strategy_state": snapshot.state,
        "final_strategy_reason": snapshot.reason_code,
        "setup_mode": (
            trades[0].mode if trades else snapshot.setup_mode
        ),
        "signal_time": trades[0].signal_time if trades else snapshot.signal_time,
        "entry_time": trades[0].entry_time if trades else snapshot.entry_time,
        "entry_price": trades[0].entry_price if trades else snapshot.entry_price,
        "exit_time": trades[-1].exit_time if trades else None,
        "exit_price": trades[-1].exit_price if trades else None,
        "return_pct": total_return,
        "mfe_pct": max((trade.mfe_pct for trade in trades), default=None),
        "mae_pct": min((trade.mae_pct for trade in trades), default=None),
        "trade_count": len(trades),
        "win_count": sum(trade.return_pct > 0 for trade in trades),
        "loss_count": sum(trade.return_pct < 0 for trade in trades),
    }


def _diagnostic_row(
    *,
    source_row: dict[str, object],
    symbol: str,
    raw_bar_count: int,
    candidate: replay.GapperCandidate | None,
    trace: LeaderMomentumDiagnosticTrace,
) -> dict[str, object]:
    snapshot = trace.strategy_snapshot
    row: dict[str, object] = {
        "session_date": source_row["session_date"],
        "symbol": symbol,
        "benchmark_rank": int(source_row["rank"]),
        "eventual_benchmark_gain_pct": source_row["gain_pct"],
        "policy_version": trace.policy_version,
        "market_data_source": "alpaca-sip-1m",
        "raw_regular_1m_bar_count": raw_bar_count,
        "candidate_context_available": candidate is not None,
        "strategy_snapshot_verified": True,
        "diagnostic_execution_authority": trace.execution_authority,
        **_strategy_values(snapshot),
        "first_3m_score_timestamp": (
            trace.first_score.observed_at if trace.first_score else None
        ),
        "first_3m_leader_confirmation": trace.first_leader_confirmed_at,
        "last_3m_leader_confirmation": trace.last_leader_confirmed_at,
        "max_3m_leader_score": (
            trace.max_score.total_score if trace.max_score else None
        ),
        "max_3m_leader_score_timestamp": (
            trace.max_score.observed_at if trace.max_score else None
        ),
        "bars_in_leader_confirmed_state": trace.bars_in_confirmed_state,
        "confirmation_count": _transition_count(trace, "confirmed"),
        "confirmation_refresh_count": _transition_count(trace, "refreshed"),
        "expiry_transition_count": _transition_count(trace, "expired"),
        "structural_invalidation_observation_count": _transition_count(
            trace, "structural_invalidation_observed"
        ),
        "first_1m_score_timestamp": (
            trace.research_1m_first_score.observed_at
            if trace.research_1m_first_score
            else None
        ),
        "first_1m_leader_confirmation": (
            trace.research_1m_first_confirmed_score.observed_at
            if trace.research_1m_first_confirmed_score
            else None
        ),
        "max_1m_leader_score": (
            trace.research_1m_max_score.total_score
            if trace.research_1m_max_score
            else None
        ),
        "max_1m_leader_score_timestamp": (
            trace.research_1m_max_score.observed_at
            if trace.research_1m_max_score
            else None
        ),
        "one_minute_vs_3m_first_score_delay_minutes": _minutes(
            (
                trace.research_1m_first_score.observed_at
                if trace.research_1m_first_score
                else None
            ),
            trace.first_score.observed_at if trace.first_score else None,
        ),
        "one_minute_vs_3m_first_confirmation_delay_minutes": _minutes(
            (
                trace.research_1m_first_confirmed_score.observed_at
                if trace.research_1m_first_confirmed_score
                else None
            ),
            trace.first_leader_confirmed_at,
        ),
        "first_setup_candidate_time": trace.first_setup_candidate_at,
        "first_valid_setup_time": trace.first_setup_valid_at,
        "first_execution_valid_time": trace.first_execution_valid_at,
        "leader_to_first_setup_candidate_minutes": (
            trace.leader_to_first_candidate_minutes
        ),
        "leader_to_first_valid_setup_minutes": trace.leader_to_first_valid_minutes,
    }
    _flatten_score(row, "max_score", trace.max_score)
    _flatten_score(row, "first_confirmation_score", trace.first_confirmed_score)
    _flatten_candidate(row, "first_setup_candidate", trace.first_setup_candidate)
    _flatten_candidate(row, "mode_a_best", trace.best_mode_a)
    _flatten_candidate(row, "mode_b_best", trace.best_mode_b)
    return row


def _timing_row(row: dict[str, object]) -> dict[str, object]:
    fields = (
        "session_date",
        "symbol",
        "benchmark_rank",
        "eventual_benchmark_gain_pct",
        "raw_regular_1m_bar_count",
        "first_1m_score_timestamp",
        "first_3m_score_timestamp",
        "one_minute_vs_3m_first_score_delay_minutes",
        "first_1m_leader_confirmation",
        "first_3m_leader_confirmation",
        "one_minute_vs_3m_first_confirmation_delay_minutes",
        "last_3m_leader_confirmation",
        "max_1m_leader_score",
        "max_1m_leader_score_timestamp",
        "max_3m_leader_score",
        "max_3m_leader_score_timestamp",
        "bars_in_leader_confirmed_state",
        "confirmation_refresh_count",
        "expiry_transition_count",
        "structural_invalidation_observation_count",
        "first_setup_candidate_time",
        "first_valid_setup_time",
        "first_execution_valid_time",
        "leader_to_first_setup_candidate_minutes",
        "leader_to_first_valid_setup_minutes",
        "signal_time",
        "entry_time",
        "exit_time",
    )
    return {field: row.get(field) for field in fields}


def _aggregate_gate_counts(
    records: Iterable[dict[str, Any]],
) -> list[dict[str, object]]:
    totals: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    observation_sets: dict[tuple[str, str], set[tuple[object, object]]] = defaultdict(set)
    for record in records:
        trace: LeaderMomentumDiagnosticTrace = record["trace"]
        observation_key = (record["source_row"]["session_date"], record["symbol"])
        for gate in trace.gate_counts:
            key = (gate.mode, gate.gate)
            totals[key]["passed"] += gate.passed
            totals[key]["failed"] += gate.failed
            observation_sets[key].add(observation_key)
    rows: list[dict[str, object]] = []
    for (mode, gate), count in sorted(totals.items()):
        evaluated = count["passed"] + count["failed"]
        rows.append(
            {
                "mode": mode,
                "gate": gate,
                "scope": "all_evaluated_candidate_windows",
                "observations_with_gate": len(observation_sets[(mode, gate)]),
                "passed": count["passed"],
                "failed": count["failed"],
                "evaluated": evaluated,
                "pass_rate_pct": (
                    Decimal(count["passed"]) / Decimal(evaluated) * Decimal("100")
                    if evaluated
                    else None
                ),
                "fail_rate_pct": (
                    Decimal(count["failed"]) / Decimal(evaluated) * Decimal("100")
                    if evaluated
                    else None
                ),
            }
        )
    return rows


def _strategy_replay_rows(
    diagnostics: list[dict[str, object]],
) -> tuple[list[dict[str, object]], list[dict[str, object]], dict[str, object]]:
    fields = (
        "session_date",
        "symbol",
        "benchmark_rank",
        "eventual_benchmark_gain_pct",
        "raw_regular_1m_bar_count",
        "final_strategy_state",
        "final_strategy_reason",
        "setup_mode",
        "signal_time",
        "entry_time",
        "entry_price",
        "exit_time",
        "exit_price",
        "return_pct",
        "mfe_pct",
        "mae_pct",
        "trade_count",
        "win_count",
        "loss_count",
    )
    rows: list[dict[str, object]] = []
    daily: dict[date, dict[str, object]] = {}
    for diagnostic in diagnostics:
        row = {field: diagnostic.get(field) for field in fields}
        allocation = (
            replay.FIXED_SLOT_NOTIONAL if int(row["trade_count"] or 0) else Decimal("0")
        )
        normalized_pnl = (
            allocation * Decimal(str(row["return_pct"])) / Decimal("100")
            if row["return_pct"] is not None
            else Decimal("0")
        )
        row["normalized_allocation"] = allocation
        row["normalized_pnl"] = normalized_pnl
        rows.append(row)
        session_date = row["session_date"]
        assert isinstance(session_date, date)
        item = daily.setdefault(
            session_date,
            {
                "session_date": session_date,
                "completed_trades": 0,
                "wins": 0,
                "losses": 0,
                "normalized_pnl": Decimal("0"),
            },
        )
        item["completed_trades"] = int(item["completed_trades"]) + int(
            row["trade_count"] or 0
        )
        item["wins"] = int(item["wins"]) + int(row["win_count"] or 0)
        item["losses"] = int(item["losses"]) + int(row["loss_count"] or 0)
        item["normalized_pnl"] = Decimal(str(item["normalized_pnl"])) + normalized_pnl
    daily_rows = []
    for item in daily.values():
        item["normalized_return_pct"] = (
            Decimal(str(item["normalized_pnl"]))
            / replay.FIXED_DAILY_CAPITAL
            * Decimal("100")
        )
        daily_rows.append(item)
    total_pnl = sum(
        (Decimal(str(row["normalized_pnl"])) for row in rows), Decimal("0")
    )
    summary: dict[str, object] = {
        "policy_version": EXPECTED_POLICY_VERSION,
        "benchmark_observations": len(rows),
        "evaluable_sessions": len(
            {row["session_date"] for row in rows if int(row["raw_regular_1m_bar_count"] or 0)}
        ),
        "completed_trades": sum(int(row["trade_count"] or 0) for row in rows),
        "wins": sum(int(row["win_count"] or 0) for row in rows),
        "losses": sum(int(row["loss_count"] or 0) for row in rows),
        "normalized_pnl": total_pnl,
        "normalized_return_pct": (
            total_pnl / replay.FIXED_DAILY_CAPITAL * Decimal("100")
        ),
    }
    return rows, sorted(daily_rows, key=lambda item: item["session_date"]), summary


def _verify_previous_result(summary: dict[str, object]) -> None:
    actual_return = Decimal(str(summary["normalized_return_pct"]))
    if (
        summary["completed_trades"] != 43
        or summary["wins"] != 19
        or summary["losses"] != 24
        or abs(actual_return - Decimal("27.6552114695")) > Decimal("0.02")
    ):
        raise RuntimeError(
            "frozen v1.2 replay materially changed before interpretation: "
            + json.dumps(summary, default=str, sort_keys=True)
        )


def _fmt_dt(value: datetime | None) -> str:
    if value is None:
        return "not observed"
    return value.astimezone(ET).strftime("%Y-%m-%d %H:%M ET")


def _fmt_num(value: object, places: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"{Decimal(str(value)):.{places}f}"


def _delay_stats(values: list[Decimal]) -> str:
    if not values:
        return "No comparable observations."
    earlier = sum(value > 0 for value in values)
    equal = sum(value == 0 for value in values)
    later = sum(value < 0 for value in values)
    return (
        f"n={len(values)}; median 3m lag {_fmt_num(median(values))} minutes, "
        f"mean {_fmt_num(sum(values, Decimal('0')) / Decimal(len(values)))} minutes, "
        f"range {_fmt_num(min(values))} to {_fmt_num(max(values))}; "
        f"1m earlier/equal/later in {earlier}/{equal}/{later}."
    )


def _gate_totals_for(
    records: Iterable[dict[str, Any]], mode: str | None = None
) -> list[tuple[str, str, int, int, Decimal]]:
    counts: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    for record in records:
        trace: LeaderMomentumDiagnosticTrace = record["trace"]
        for gate in trace.gate_counts:
            if mode is not None and gate.mode != mode:
                continue
            counts[(gate.mode, gate.gate)]["passed"] += gate.passed
            counts[(gate.mode, gate.gate)]["failed"] += gate.failed
    output = []
    for (gate_mode, gate), count in counts.items():
        total = count["passed"] + count["failed"]
        rate = Decimal(count["failed"]) / Decimal(total) * Decimal("100") if total else Decimal("0")
        output.append((gate_mode, gate, count["failed"], total, rate))
    return sorted(output, key=lambda item: (-item[4], -item[2], item[1]))


def _candidate_summary(candidate: SetupCandidateDiagnostic | None) -> str:
    if candidate is None:
        return "no candidate window was evaluated"
    failed = [gate.gate for gate in candidate.gates if not gate.passed]
    failed_text = ", ".join(failed) if failed else "none"
    return (
        f"{candidate.mode} at {_fmt_dt(candidate.observed_at)}; "
        f"{candidate.passed_gate_count} passed / {candidate.failed_gate_count} failed, "
        f"normalized shortfall {_fmt_num(candidate.normalized_shortfall, 3)}; "
        f"failed gates: {failed_text}"
    )


def _focus_lines(records: list[dict[str, Any]], symbols: tuple[str, ...]) -> list[str]:
    lines: list[str] = []
    for symbol in symbols:
        selected = [item for item in records if item["symbol"] == symbol]
        if not selected:
            lines.append(f"- **{symbol}:** not present in the benchmark.")
            continue
        for item in selected:
            trace: LeaderMomentumDiagnosticTrace = item["trace"]
            snapshot = trace.strategy_snapshot
            lines.append(
                f"- **{symbol} ({item['source_row']['session_date']}):** "
                f"1m first score {_fmt_dt(trace.research_1m_first_score.observed_at if trace.research_1m_first_score else None)}; "
                f"1m confirmation {_fmt_dt(trace.research_1m_first_confirmed_score.observed_at if trace.research_1m_first_confirmed_score else None)}; "
                f"3m first score {_fmt_dt(trace.first_score.observed_at if trace.first_score else None)}; "
                f"3m confirmation {_fmt_dt(trace.first_leader_confirmed_at)}; "
                f"max 3m score {_fmt_num(trace.max_score.total_score if trace.max_score else None)}; "
                f"first candidate {_fmt_dt(trace.first_setup_candidate_at)}; "
                f"first valid {_fmt_dt(trace.first_setup_valid_at)}; final `{snapshot.state}` / `{snapshot.reason_code}`. "
                f"Best A: {_candidate_summary(trace.best_mode_a)}. "
                f"Best B: {_candidate_summary(trace.best_mode_b)}."
            )
    return lines


def _write_summary(
    path: Path,
    *,
    input_path: Path,
    records: list[dict[str, Any]],
    strategy_summary: dict[str, object],
    strategy_sha: str,
) -> None:
    confirmed = [item for item in records if item["trace"].first_leader_confirmed_at]
    never_confirmed = [item for item in records if not item["trace"].first_leader_confirmed_at]
    confirmed_no_trade = [
        item
        for item in confirmed
        if not item["trace"].strategy_snapshot.trades
    ]
    bucket_counts: list[tuple[str, int]] = []
    for label, cutoff in (
        ("09:45", time(9, 45)),
        ("10:00", time(10, 0)),
        ("10:30", time(10, 30)),
        ("11:00", time(11, 0)),
    ):
        bucket_counts.append(
            (
                label,
                sum(
                    item["trace"].first_leader_confirmed_at.astimezone(ET).time()
                    <= cutoff
                    for item in confirmed
                ),
            )
        )

    first_score_delays = [
        value
        for item in records
        if (
            value := _minutes(
                item["trace"].research_1m_first_score.observed_at
                if item["trace"].research_1m_first_score
                else None,
                item["trace"].first_score.observed_at
                if item["trace"].first_score
                else None,
            )
        )
        is not None
    ]
    confirmation_delays = [
        value
        for item in records
        if (
            value := _minutes(
                item["trace"].research_1m_first_confirmed_score.observed_at
                if item["trace"].research_1m_first_confirmed_score
                else None,
                item["trace"].first_leader_confirmed_at,
            )
        )
        is not None
    ]
    one_minute_only = sum(
        item["trace"].research_1m_first_confirmed_score is not None
        and item["trace"].first_leader_confirmed_at is None
        for item in records
    )

    component_caps = {
        "price_strength_points": Decimal("25"),
        "trend_points": Decimal("20"),
        "hod_points": Decimal("15"),
        "volume_points": Decimal("20"),
    }
    component_rows = []
    scored_never = [item for item in never_confirmed if item["trace"].max_score]
    for field, cap in component_caps.items():
        values = [getattr(item["trace"].max_score, field) for item in scored_never]
        average = sum(values, Decimal("0")) / Decimal(len(values)) if values else Decimal("0")
        component_rows.append((field, average, cap - average))
    component_rows.sort(key=lambda item: item[2], reverse=True)

    penalty_cases = []
    for item in records:
        score = item["trace"].max_score
        if score is None or score.total_score >= leader.MIN_LEADER_SCORE:
            continue
        without_penalties = (
            score.total_score
            - score.spread_penalty_points
            - score.dollar_volume_penalty_points
        )
        if without_penalties >= leader.MIN_LEADER_SCORE:
            penalty_cases.append((item, score, without_penalties))
    spread_cases = sum(score.spread_penalty_points < 0 for _, score, _ in penalty_cases)
    dollar_cases = sum(score.dollar_volume_penalty_points < 0 for _, score, _ in penalty_cases)

    gate_failures = _gate_totals_for(confirmed_no_trade)
    mode_a = _gate_totals_for(confirmed_no_trade, "controlled_pullback")
    mode_b = _gate_totals_for(confirmed_no_trade, "momentum_compression")

    closeness = Counter()
    for item in confirmed_no_trade:
        candidates = [
            candidate
            for candidate in (item["trace"].best_mode_a, item["trace"].best_mode_b)
            if candidate is not None
        ]
        if not candidates:
            closeness["nowhere_near"] += 1
            continue
        best = min(
            candidates,
            key=lambda candidate: (
                candidate.failed_gate_count,
                candidate.normalized_shortfall,
            ),
        )
        if best.failed_gate_count <= 2 and best.normalized_shortfall <= Decimal("0.5"):
            closeness["close"] += 1
        elif best.failed_gate_count >= 5 or best.normalized_shortfall >= Decimal("2"):
            closeness["nowhere_near"] += 1
        else:
            closeness["intermediate"] += 1

    waiting = [
        item
        for item in records
        if item["trace"].strategy_snapshot.state == "waiting_setup"
    ]
    waiting_names = sorted({item["symbol"] for item in waiting})
    emphasized = {"VIOT", "SSM", "RDIB", "INDP", "LGPS", "USDE"}
    waiting_focus = [item for item in records if item["symbol"] in emphasized]
    waiting_focus_gates = _gate_totals_for(waiting_focus)[:6]

    data_unavailable = sum(not item["raw_bar_count"] for item in records)
    data_gap_states = sum(
        item["trace"].strategy_snapshot.state == "data_gap" for item in records
    )
    risk_failures = sum(
        any(
            gate.gate == "proposed_risk_pct" and not gate.passed
            for candidate in (item["trace"].best_mode_a, item["trace"].best_mode_b)
            if candidate
            for gate in candidate.gates
        )
        for item in confirmed_no_trade
    )

    celu_records = [item for item in records if item["symbol"] == "CELU"]
    celu_lines = _focus_lines(records, ("CELU",))
    if celu_records and celu_records[0]["trace"].strategy_snapshot.trades:
        celu_trace = celu_records[0]["trace"]
        trade = celu_trace.strategy_snapshot.trades[0]
        celu_trade = (
            f"The trade signalled {_fmt_dt(trade.signal_time)}, entered {_fmt_dt(trade.entry_time)} "
            f"at ${_fmt_num(trade.entry_price, 4)}, and exited {_fmt_dt(trade.exit_time)} "
            f"at ${_fmt_num(trade.exit_price, 4)} for {_fmt_num(trade.return_pct, 4)}% "
            f"(`{trade.exit_reason_code}`). The earliest qualifying setup candidate was "
            f"{_candidate_summary(celu_trace.first_setup_candidate)}. Those failed gates prevented an earlier "
            f"entry; no 1m diagnostic state was allowed to authorize one."
        )
    else:
        celu_trade = "CELU did not produce the expected frozen-policy trade."

    def gate_table(items: list[tuple[str, str, int, int, Decimal]], limit: int = 8) -> list[str]:
        lines = ["| Mode | Gate | Failures / evaluations | Failure rate |", "|---|---|---:|---:|"]
        for mode, gate, failed, total, rate in items[:limit]:
            lines.append(f"| `{mode}` | `{gate}` | {failed}/{total} | {rate:.2f}% |")
        if not items:
            lines.append("| n/a | n/a | 0/0 | n/a |")
        return lines

    lines = [
        "# Frozen leader-momentum-continuation v1.2 diagnostics",
        "",
        f"- Dataset: `{input_path.as_posix()}` ({len(records)} observations)",
        "- Market data: Alpaca SIP historical 1-minute bars; causal premarket context frozen at 09:15 ET",
        f"- Frozen policy: `{EXPECTED_POLICY_VERSION}`; strategy SHA-256 `{strategy_sha}`",
        "- Authority: diagnostics and strategy snapshots both report `execution_authority=false`; no orders were created",
        "- Lookahead control: benchmark rank and eventual gain were attached only after evaluation and were never passed in strategy context",
        "",
        "## Frozen replay check",
        "",
        f"The replay produced **{strategy_summary['completed_trades']} trades, {strategy_summary['wins']} wins / {strategy_summary['losses']} losses**, normalized P/L **${Decimal(str(strategy_summary['normalized_pnl'])):.2f}** and reused-$100k return **{Decimal(str(strategy_summary['normalized_return_pct'])):.4f}%**. This matches the independently reproduced v1.2 Alpaca SIP result within the predeclared 0.02 percentage-point materiality tolerance.",
        "",
        "## Required findings",
        "",
        f"1. **Leader confirmation:** {len(confirmed)}/{len(records)} winners were ever `LEADER_CONFIRMED`; {len(never_confirmed)} never confirmed.",
        "",
        "2. **Confirmation time (cumulative):** " + "; ".join(f"{count} by {label} ET" for label, count in bucket_counts) + ".",
        "",
        "3. **Research 1m versus decision 3m:** First-score timing: " + _delay_stats(first_score_delays) + " Confirmation timing: " + _delay_stats(confirmation_delays) + f" The 1m diagnostic crossed the confirmation condition for {one_minute_only} additional observations that never confirmed in the 3m decision path; this remained research-only.",
        "",
        "4. **Never-confirmed score composition:** At each never-confirmed stock's maximum 3m score, average component points and average unused capacity were:",
        "",
        "| Component | Average points | Average shortfall to component cap |",
        "|---|---:|---:|",
    ]
    for field, average, shortfall in component_rows:
        lines.append(f"| `{field}` | {average:.2f} | {shortfall:.2f} |")
    lines.extend(
        [
            "",
            "The largest average component shortfalls are the strongest descriptive bottlenecks. They are not a recommendation to lower the total-score threshold.",
            "",
            f"5. **Spread/liquidity suppression:** {len(penalty_cases)} observations had a maximum 3m total below 70 but a counterfactual total of at least 70 after adding back only spread and dollar-volume penalties. Spread contributed in {spread_cases}; dollar volume contributed in {dollar_cases}. This isolates score suppression and does not claim the session-return gate would necessarily pass.",
            "",
            f"6. **Confirmed but never traded:** {len(confirmed_no_trade)} observations confirmed and produced no trade. Their dominant all-window setup failures were:",
            "",
            *gate_table(gate_failures),
            "",
            "7. **Mode A (`controlled_pullback`) dominant failures:**",
            "",
            *gate_table(mode_a),
            "",
            "8. **Mode B (`momentum_compression`) dominant failures:**",
            "",
            *gate_table(mode_b),
            "",
            f"9. **Setup closeness:** among confirmed non-traders, {closeness['close']} were close, {closeness['intermediate']} intermediate, and {closeness['nowhere_near']} nowhere near. `Close` means the better of the two best mode windows missed at most two gates with normalized shortfall <=0.5; `nowhere near` means no evaluated window, at least five failed gates, or normalized shortfall >=2; the rest are intermediate.",
            "",
            f"10. **Previously observed waiting setups:** The frozen final state was `waiting_setup` for {len(waiting)} observations ({', '.join(waiting_names) if waiting_names else 'none'}). For VIOT, SSM, RDIB, INDP, LGPS, and USDE, the most frequent all-window failures were:",
            "",
            *gate_table(waiting_focus_gates, limit=6),
            "",
            "11. **CELU:**",
            "",
            *celu_lines,
            "",
            celu_trade,
            "",
            "12. **Focused timing cases:**",
            "",
            *_focus_lines(records, ("VIOT", "SUNE", "DAIC", "PFSA", "WETO", "ZSTK")),
            "",
            "13. **Bottleneck classification:**",
            "",
            f"- **Score composition:** {len(never_confirmed)}/{len(records)} observations failed before 3m leader confirmation.",
            f"- **Setup geometry:** {len(confirmed_no_trade)}/{len(confirmed)} confirmed observations did not trade; the gate tables identify the repeated geometric blockers.",
            f"- **Execution/risk gating:** proposed-risk failure appeared in the best mode window of {risk_failures}/{len(confirmed_no_trade)} confirmed non-traders.",
            f"- **Leader-detection latency:** the 1m/3m distributions above quantify latency, including {one_minute_only} 1m-only threshold crossings; this is observational, not execution authority.",
            f"- **Data availability/gaps:** {data_unavailable} observations had no regular 1m bars and {data_gap_states} finished in `data_gap`.",
            "",
            f"By stage attrition, the principal bottleneck is **{'score composition' if len(never_confirmed) >= len(confirmed_no_trade) else 'setup geometry'}**, with **{'setup geometry' if len(never_confirmed) >= len(confirmed_no_trade) else 'score composition'}** secondary. Execution/risk, latency, and data availability are separately quantified above rather than inferred from aggregate return alone.",
            "",
            "## Interpretation boundary",
            "",
            "This is a hindsight-selected winner cohort, not a prospective tradable universe. All decisions used only bars and causal context available at the evaluation time. No parameter recommendations are made here.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay frozen leader momentum v1.2 diagnostics on winners."
    )
    parser.add_argument(
        "--input",
        default="docs/trading/HISTORICAL_TOP5_WINNERS_2026-08-13_TO_2026-09-11.csv",
    )
    parser.add_argument(
        "--output-dir",
        default="docs/trading/leader-momentum-diagnostics-2026-08-13_to_2026-09-11-alpaca-sip-v1-2",
    )
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument(
        "--cache-dir",
        default=str(replay.CACHE_DIR),
        help="Persistent raw market-data cache shared with the normal replay.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    replay.CACHE_DIR = Path(args.cache_dir)
    replay.reset_cache_stats()
    strategy_sha_before = _verify_frozen_policy()
    replay.ACTIVE_SOURCE = "alpaca-sip"

    rows, sessions, _grouped = replay._parse_source(input_path)
    if len(rows) != 105 or len(sessions) != 21:
        raise AssertionError(
            f"unexpected benchmark shape: {len(rows)} observations / {len(sessions)} sessions"
        )
    symbols = sorted({str(row["symbol"]) for row in rows})
    source_by_symbol: dict[str, dict[date, dict[str, object]]] = defaultdict(dict)
    for row in rows:
        source_by_symbol[str(row["symbol"])][row["session_date"]] = row

    loaded: dict[str, replay.SymbolReplayData] = {}
    print(
        f"Fetching Alpaca SIP 5m context and 1m decision history for {len(symbols)} symbols...",
        flush=True,
    )
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {
            executor.submit(
                replay._load_symbol,
                symbol,
                source_by_symbol[symbol],
                sessions,
            ): symbol
            for symbol in symbols
        }
        for index, future in enumerate(as_completed(futures), start=1):
            symbol = futures[future]
            try:
                loaded[symbol] = future.result()
            except Exception as exc:
                print(
                    f"[{index}/{len(symbols)}] {symbol}: FAILED: {type(exc).__name__}: {exc}",
                    flush=True,
                )
                loaded[symbol] = replay.SymbolReplayData(
                    symbol=symbol,
                    candidates={},
                    bars_5m={},
                    bars_1m={},
                    five_minute_error=f"{type(exc).__name__}: {exc}",
                    one_minute_errors={},
                )
            else:
                data = loaded[symbol]
                target_dates = set(source_by_symbol[symbol])
                covered = sum(bool(data.bars_1m.get(day)) for day in target_dates)
                print(
                    f"[{index}/{len(symbols)}] {symbol}: target 1m={covered}/{len(target_dates)}",
                    flush=True,
                )

    records: list[dict[str, Any]] = []
    diagnostics: list[dict[str, object]] = []
    for index, source_row in enumerate(rows, start=1):
        session_date = source_row["session_date"]
        assert isinstance(session_date, date)
        symbol = str(source_row["symbol"])
        data = loaded[symbol]
        raw_bars = data.bars_1m.get(session_date, ())
        candidate = data.candidates.get(session_date)
        context = _context_for(candidate)
        market_bars = replay._market_bars(
            raw_bars,
            f"equity:US:{symbol}",
            "1m",
        )

        # Rank and eventual gain are deliberately not present in either call.
        strategy_snapshot = leader.evaluate_leader_momentum_continuation(
            market_bars,
            context=context,
        )
        trace = diagnose_leader_momentum_continuation(
            market_bars,
            context=context,
        )
        if trace.strategy_snapshot != strategy_snapshot:
            raise AssertionError(
                f"diagnostic strategy snapshot drift: {session_date} {symbol}"
            )
        if strategy_snapshot.execution_authority or trace.execution_authority:
            raise AssertionError(
                f"unexpected execution authority: {session_date} {symbol}"
            )

        diagnostic = _diagnostic_row(
            source_row=source_row,
            symbol=symbol,
            raw_bar_count=len(raw_bars),
            candidate=candidate,
            trace=trace,
        )
        diagnostics.append(diagnostic)
        records.append(
            {
                "source_row": source_row,
                "symbol": symbol,
                "raw_bar_count": len(raw_bars),
                "candidate": candidate,
                "context": context,
                "trace": trace,
                "diagnostic": diagnostic,
                "loader_errors": {
                    "five_minute": data.five_minute_error,
                    "one_minute": data.one_minute_errors,
                },
            }
        )
        if index % 20 == 0 or index == len(rows):
            print(f"Diagnosed {index}/{len(rows)} observations", flush=True)

    strategy_rows, daily_rows, strategy_summary = _strategy_replay_rows(diagnostics)
    _verify_previous_result(strategy_summary)
    strategy_sha_after = _verify_frozen_policy()
    if strategy_sha_after != strategy_sha_before:
        raise AssertionError("strategy file changed during replay")

    gate_rows = _aggregate_gate_counts(records)
    timing_rows = [_timing_row(row) for row in diagnostics]
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(output_dir / "leader-momentum-diagnostics.csv", diagnostics)
    _write_csv(output_dir / "leader-momentum-gate-summary.csv", gate_rows)
    _write_csv(output_dir / "leader-momentum-timing-summary.csv", timing_rows)
    _write_csv(output_dir / "leader-momentum-strategy-replay.csv", strategy_rows)
    _write_csv(output_dir / "leader-momentum-strategy-daily-pnl.csv", daily_rows)
    _write_csv(output_dir / "leader-momentum-strategy-summary.csv", [strategy_summary])

    with (output_dir / "leader-momentum-diagnostic-traces.jsonl").open(
        "w", encoding="utf-8"
    ) as handle:
        for record in records:
            source_row = record["source_row"]
            payload = {
                "session_date": source_row["session_date"],
                "symbol": record["symbol"],
                "benchmark_rank": source_row["rank"],
                "eventual_benchmark_gain_pct": source_row["gain_pct"],
                "market_data_source": "alpaca-sip-1m",
                "raw_regular_1m_bar_count": record["raw_bar_count"],
                "causal_context": (
                    record["context"].model_dump(mode="json")
                    if record["context"]
                    else None
                ),
                "loader_errors": record["loader_errors"],
                "independent_strategy_snapshot_verified": True,
                "diagnostic_trace": record["trace"].model_dump(mode="json"),
            }
            handle.write(json.dumps(payload, default=str, sort_keys=True) + "\n")

    _write_summary(
        output_dir / "leader-momentum-summary.md",
        input_path=input_path,
        records=records,
        strategy_summary=strategy_summary,
        strategy_sha=strategy_sha_after,
    )
    metadata = {
        "input": input_path.as_posix(),
        "output_dir": output_dir.as_posix(),
        "observations": len(records),
        "sessions": len(sessions),
        "unique_symbols": len(symbols),
        "market_data_source": "alpaca-sip",
        "market_data_cache_dir": replay.CACHE_DIR.as_posix(),
        "market_data_cache_stats": replay.cache_stats(),
        "policy_version": EXPECTED_POLICY_VERSION,
        "strategy_sha256_before": strategy_sha_before,
        "strategy_sha256_after": strategy_sha_after,
        "frozen_thresholds": EXPECTED_THRESHOLDS,
        "strategy_snapshot_matches": len(records),
        "execution_authority": False,
        "lookahead_control": (
            "rank and eventual gain attached only after evaluator calls"
        ),
    }
    (output_dir / "run-metadata.json").write_text(
        json.dumps(metadata, default=str, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print((output_dir / "leader-momentum-summary.md").read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
