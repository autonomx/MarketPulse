from __future__ import annotations

"""Second-pass correctness layer for dependency-aware interday replay.

The first dependency-aware adapter correctly retained partial factual tape and
proved whether individual outcomes were terminal before an unresolved gap.  This
layer tightens the portfolio semantics: deterministic V2 / Gap Pullback are
portfolio strategies, so one symbol cannot be salvaged by replaying it in
isolation.  Instead the whole cohort is replayed only through the earliest
unresolved dependency boundary, preserving cross-symbol risk/capacity ordering.

A partial session is authoritative only when every candidate's outcome is
already terminal before that boundary.  Otherwise individually terminal
pre-gap outcomes may still be reported for research, but the compounded account
chain is explicitly no longer authoritative.
"""

import json
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any

from scripts.trade import run_interday_winner_shadow_replay_dependency_base as _dep

from app.apps.trading.market_data_recovery import finalized_session_bars


_core = _dep._core
_base = _dep._base

# Public compatibility surface.
MarketDataCache = _dep.MarketDataCache
RawBar = _dep.RawBar
SymbolReplayData = _dep.SymbolReplayData
cache_stats = _dep.cache_stats
reset_cache_stats = _dep.reset_cache_stats
_yahoo_1m_chunks = _dep._yahoo_1m_chunks
_needs_one_minute_recovery = _dep._needs_one_minute_recovery
_recover_one_minute_sessions = _dep._recover_one_minute_sessions
_decision_dependency_view = _dep._decision_dependency_view
_build_5m_history = _dep._core._build_5m_history
_stoch_tolerable_single_minute_gap = _dep._stoch_tolerable_single_minute_gap
_stoch_trend_outcome_final_before_gap = _dep._stoch_trend_outcome_final_before_gap
_leader_outcome_final_before_gap = _dep._leader_outcome_final_before_gap


_TERMINAL_GAP_PULLBACK_STATES = {
    "rejected",
    "expired",
    "risk_rejected",
    "research_rejected",
    "execution_rejected",
}

_LAST_COVERAGE_METADATA: dict[str, object] = {}
_ORIGINAL_SUMMARY_ROWS = _core._summary_rows
_ORIGINAL_WRITE_SUMMARY = _core._write_summary


def _gap_pullback_outcome_final_before_gap(
    trade: Any | None,
    decision: Any | None,
    cutoff: datetime,
    *,
    config: Any,
) -> tuple[bool, str]:
    """Prove whether a V2 candidate can no longer depend on bars after cutoff."""

    if trade is not None:
        if trade.exit_time > cutoff:
            return False, "GAP_PULLBACK_OPEN_POSITION_SPANS_GAP"
        # The backtester uses the final supplied bar as an EOD liquidation when
        # a position is still open. On a causal prefix that is an artificial exit
        # unless the prefix really reaches the regular-session close.
        if trade.exit_reason == "eod" and cutoff < _base._session_close(
            cutoff.astimezone(_core.ET).date()
        ):
            return False, "GAP_PULLBACK_OPEN_POSITION_SPANS_GAP"
        return True, "GAP_PULLBACK_TRADE_COMPLETED_BEFORE_GAP"

    if decision is not None and decision.state in _TERMINAL_GAP_PULLBACK_STATES:
        return True, "GAP_PULLBACK_TERMINAL_DECISION_BEFORE_GAP"
    if decision is not None and (
        decision.rejection_reason == "no_next_bar" or decision.state == "entry_ready"
    ):
        return False, "GAP_PULLBACK_PENDING_ENTRY_SPANS_GAP"
    if cutoff.astimezone(_core.ET).time() > config.last_entry_et:
        return True, "GAP_PULLBACK_GAP_AFTER_ENTRY_WINDOW_NO_OPEN_POSITION"
    return False, "GAP_PULLBACK_FUTURE_SETUP_DEPENDS_ON_GAP"


def _portfolio_dependency_cutoff(
    views: dict[str, _dep.DecisionDependencyView],
) -> datetime | None:
    """Earliest point after which the cohort can no longer be replayed exactly."""

    incomplete = [view for view in views.values() if not view.decision_evaluable]
    if not incomplete:
        return None
    if any(view.blocked_after is None for view in incomplete):
        return None
    return min(view.blocked_after for view in incomplete if view.blocked_after is not None)


def _run_portfolio_backtest(
    *,
    session_date: date,
    rows: list[dict[str, object]],
    candidate_pairs: list[tuple[dict[str, object], Any]],
    bars_by_instrument: dict[str, list[Any]],
    input_path: Path,
    config: Any,
    risk: Any,
    initial_cash: Decimal,
) -> Any:
    candidates = [candidate for _, candidate in candidate_pairs]
    universe = _core.freeze_gapper_universe(
        universe_id=f"winner-benchmark-{session_date.isoformat()}",
        session_date=session_date,
        evaluation_time=datetime.combine(
            session_date, time(9, 15), tzinfo=_core.ET
        ).astimezone(_core.UTC),
        discovery_source="import",
        candidates=candidates,
        source_locator=f"{input_path.as_posix()}#{session_date.isoformat()}",
        source_candidate_symbols=tuple(str(row["symbol"]) for row in rows),
    )
    dataset = _core.freeze_backtest_session(
        session_date=session_date,
        universe=universe,
        bars_by_instrument=bars_by_instrument,
    )
    return _core.run_gap_pullback_backtest(
        dataset,
        config,
        _core.PaperExecutionPolicy(max_volume_participation_pct=Decimal("1")),
        assumed_spread_bps=_core.ASSUMED_SPREAD_BPS,
        max_hold_minutes=config.v2_max_hold_minutes,
        max_concurrent_positions=risk.max_positions,
        risk_profile=risk,
        initial_cash=initial_cash,
    )


def _result_maps(result: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    return (
        {trade.instrument_id: trade for trade in result.trades},
        {decision.instrument_id: decision for decision in result.candidate_decisions},
    )


def _append_unavailable_gap_rows(
    observations: list[dict[str, object]],
    *,
    session_date: date,
    row: dict[str, object],
    reason: str,
) -> None:
    symbol = str(row["symbol"])
    for arm in ("deterministic-v2", "gap-pullback-v2-prospective-20260825"):
        observations.append(
            _core._base_observation(
                arm,
                session_date,
                row,
                symbol=symbol,
                status="data_unavailable",
                reason=reason,
                data_source=_dep._data_source("1m"),
            )
        )


def _gap_observations(
    sessions: list[date],
    grouped: dict[date, list[dict[str, object]]],
    loaded: dict[str, SymbolReplayData],
    observations: list[dict[str, object]],
    input_path: Path,
) -> tuple[dict[date, Any], dict[date, Decimal], dict[date, str]]:
    """Evaluate V2 at the earliest cohort-safe dependency boundary.

    Full sessions use the ordinary multi-symbol portfolio replay.  For a gappy
    session, every candidate is truncated to the *same earliest unresolved
    boundary* and the entire portfolio is replayed together.  This preserves
    max-position, risk, cash, ranking, and concurrent-position dependencies.
    """

    config = _core.managed_finviz_v2_config()
    risk = _core.StrategyRiskProfile()
    current_cash = _core.FIXED_DAILY_CAPITAL
    account_authoritative = True
    gap_results: dict[date, Any] = {}
    gap_risk_pnl: dict[date, Decimal] = {}
    gap_status: dict[date, str] = {}

    for session_date in sessions:
        rows = sorted(grouped[session_date], key=lambda row: int(row["rank"]))
        candidate_pairs: list[tuple[dict[str, object], Any]] = []
        metadata_missing = False
        for row in rows:
            candidate = loaded[str(row["symbol"])].candidates.get(session_date)
            if candidate is None:
                metadata_missing = True
            else:
                candidate_pairs.append((row, candidate))

        if metadata_missing:
            gap_status[session_date] = "5m candidate metadata unavailable"
            for row in rows:
                _append_unavailable_gap_rows(
                    observations,
                    session_date=session_date,
                    row=row,
                    reason="5m candidate metadata unavailable",
                )
            account_authoritative = False
            continue

        views: dict[str, _dep.DecisionDependencyView] = {}
        for row, candidate in candidate_pairs:
            symbol = str(row["symbol"])
            raw = loaded[symbol].bars_1m.get(session_date, ())
            views[candidate.instrument_id] = _dep._decision_dependency_view(
                symbol,
                session_date,
                raw,
                requirement=_dep._SESSION_OHLCV_1M,
            )

        all_full = all(
            view.decision_evaluable and bool(view.bars) for view in views.values()
        )
        initial_cash = (
            current_cash if account_authoritative else _core.FIXED_DAILY_CAPITAL
        )

        if all_full:
            bars_by_instrument = {
                candidate.instrument_id: list(views[candidate.instrument_id].bars)
                for _, candidate in candidate_pairs
            }
            result = _run_portfolio_backtest(
                session_date=session_date,
                rows=rows,
                candidate_pairs=candidate_pairs,
                bars_by_instrument=bars_by_instrument,
                input_path=input_path,
                config=config,
                risk=risk,
                initial_cash=initial_cash,
            )
            if account_authoritative:
                pnl = sum(
                    (
                        trade.pnl_per_share * trade.entry_fill_quantity
                        for trade in result.trades
                    ),
                    Decimal("0"),
                )
                current_cash += pnl
                gap_results[session_date] = result
                gap_risk_pnl[session_date] = pnl
                gap_status[session_date] = "backtested"
            else:
                gap_status[session_date] = (
                    "strategy backtested with daily reset; canonical account chain unavailable"
                )
            for row, candidate in candidate_pairs:
                _dep._append_gap_result_observations(
                    observations,
                    session_date=session_date,
                    row=row,
                    candidate=candidate,
                    result=result,
                    risk_pnl_authoritative=account_authoritative,
                )
            continue

        cutoff = _portfolio_dependency_cutoff(views)
        if cutoff is None:
            gap_status[session_date] = (
                "unresolved dependency without a provable cohort-safe boundary"
            )
            for row in rows:
                _append_unavailable_gap_rows(
                    observations,
                    session_date=session_date,
                    row=row,
                    reason="GAP_PULLBACK_UNRESOLVED_COHORT_DEPENDENCY",
                )
            account_authoritative = False
            continue

        # Every symbol is cut to the same causal boundary. A symbol with complete
        # data may therefore lose later bars because another candidate's missing
        # interval can still change portfolio capacity/selection after cutoff.
        bars_by_instrument: dict[str, list[Any]] = {}
        for row, candidate in candidate_pairs:
            symbol = str(row["symbol"])
            raw = loaded[symbol].bars_1m.get(session_date, ())
            market = _dep._market_bars(raw, candidate.instrument_id, "1m")
            bars_by_instrument[candidate.instrument_id] = finalized_session_bars(
                market,
                session_date=session_date,
                interval="1m",
                as_of=cutoff,
            )

        result = _run_portfolio_backtest(
            session_date=session_date,
            rows=rows,
            candidate_pairs=candidate_pairs,
            bars_by_instrument=bars_by_instrument,
            input_path=input_path,
            config=config,
            risk=risk,
            initial_cash=initial_cash,
        )
        trades, decisions = _result_maps(result)

        finality: dict[str, tuple[bool, str]] = {}
        for _, candidate in candidate_pairs:
            finality[candidate.instrument_id] = _gap_pullback_outcome_final_before_gap(
                trades.get(candidate.instrument_id),
                decisions.get(candidate.instrument_id),
                cutoff,
                config=config,
            )

        all_final = all(final for final, _ in finality.values())
        account_was_authoritative = account_authoritative
        if all_final and account_was_authoritative:
            pnl = sum(
                (
                    trade.pnl_per_share * trade.entry_fill_quantity
                    for trade in result.trades
                ),
                Decimal("0"),
            )
            current_cash += pnl
            gap_results[session_date] = result
            gap_risk_pnl[session_date] = pnl
            # Keep the legacy value so the core summary counts this as an
            # authoritative session; the wrapped summary explains that the proof
            # may be a terminal prefix rather than a physically complete tape.
            gap_status[session_date] = "backtested"
        elif all_final:
            gap_status[session_date] = (
                "dependency-complete prefix; canonical account chain already unavailable"
            )
        else:
            gap_status[session_date] = (
                "dependency-aware cohort prefix; canonical portfolio account unresolved"
            )
            account_authoritative = False

        for row, candidate in candidate_pairs:
            final, reason = finality[candidate.instrument_id]
            if final:
                _dep._append_gap_result_observations(
                    observations,
                    session_date=session_date,
                    row=row,
                    candidate=candidate,
                    result=result,
                    risk_pnl_authoritative=(
                        all_final and account_was_authoritative
                    ),
                    reason_override=reason,
                )
            else:
                view = views[candidate.instrument_id]
                _append_unavailable_gap_rows(
                    observations,
                    session_date=session_date,
                    row=row,
                    reason=(
                        f"{reason};"
                        f"{_dep._dependency_reason('GAP_PULLBACK_UNRESOLVED_DEPENDENCY', view)};"
                        f"cohort_blocked_after={cutoff.isoformat()}"
                    ),
                )

    gap_risk_pnl[date.min] = current_cash
    return gap_results, gap_risk_pnl, gap_status


def _summary_rows(
    observations: list[dict[str, object]], sessions: list[date]
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Count an evaluable session only when every cohort observation is evaluable."""

    arm_summary, daily = _ORIGINAL_SUMMARY_ROWS(observations, sessions)
    for summary in arm_summary:
        arm = str(summary["arm"])
        fully_evaluable = 0
        for session_date in sessions:
            rows = [
                row
                for row in observations
                if row["arm"] == arm and row["session_date"] == session_date
            ]
            if rows and all(row["status"] != "data_unavailable" for row in rows):
                fully_evaluable += 1
        summary["evaluable_sessions"] = fully_evaluable
    return arm_summary, daily


def _coverage_metadata(
    sessions: list[date], loaded: dict[str, SymbolReplayData]
) -> dict[str, object]:
    complete: list[str] = []
    all_factual: list[str] = []
    partial: list[str] = []

    for session_date in sessions:
        relevant = [
            data for data in loaded.values() if session_date in data.candidates
        ]
        if not relevant:
            continue
        factual_flags = [bool(data.bars_1m.get(session_date)) for data in relevant]
        complete_flags: list[bool] = []
        for data in relevant:
            report = _dep._report_for(data.symbol, session_date)
            complete_flags.append(
                bool(data.bars_1m.get(session_date))
                and report is not None
                and not report.unresolved_gaps
            )
        if all(factual_flags):
            all_factual.append(session_date.isoformat())
        if all(complete_flags):
            complete.append(session_date.isoformat())
        elif any(factual_flags):
            partial.append(session_date.isoformat())

    return {
        "one_minute_all_cohort_symbols_present_sessions": all_factual,
        "one_minute_dependency_complete_sessions": complete,
        "one_minute_partial_or_unresolved_sessions": partial,
    }


def _write_summary(path: Path, **kwargs: Any) -> None:
    _ORIGINAL_WRITE_SUMMARY(path, **kwargs)
    sessions = kwargs["sessions"]
    loaded = kwargs["loaded"]
    metadata = _coverage_metadata(sessions, loaded)
    _LAST_COVERAGE_METADATA.clear()
    _LAST_COVERAGE_METADATA.update(metadata)

    source_name = (
        "Alpaca SIP consolidated" if _core.ACTIVE_SOURCE == "alpaca-sip" else "Yahoo"
    )
    old_one_covered = sum(
        any(data.bars_1m.get(session_date) for data in loaded.values())
        for session_date in sessions
    )
    complete_count = len(metadata["one_minute_dependency_complete_sessions"])
    factual_count = len(metadata["one_minute_all_cohort_symbols_present_sessions"])

    text = path.read_text(encoding="utf-8")
    text = text.replace(
        f"{source_name} historical 1-minute bars covered {old_one_covered}/{len(sessions)} sessions.",
        (
            f"{source_name} historical 1-minute bars were present for every cohort "
            f"symbol in {factual_count}/{len(sessions)} sessions; dependency-complete "
            f"1-minute coverage was {complete_count}/{len(sessions)} sessions."
        ),
    )
    text = text.replace(
        "sessions with complete 1-minute tapes.",
        (
            "sessions whose V2 portfolio outcome remained dependency-complete "
            "(full tape or a provably terminal causal prefix)."
        ),
    )
    partial_count = len(metadata["one_minute_partial_or_unresolved_sessions"])
    text += (
        "\nDependency-aware coverage: partial factual 1-minute tape was retained in "
        f"{partial_count}/{len(sessions)} sessions so already-terminal decisions could "
        "remain evaluable without authorizing decisions that still depended on a gap.\n"
    )
    path.write_text(text, encoding="utf-8")


# Override only the pieces tightened by this second-pass review. The dependency
# base continues to own acquisition, per-symbol overlay dependencies, provenance,
# and factual partial-tape retention.
_core._gap_observations = _gap_observations
_core._summary_rows = _summary_rows
_core._write_summary = _write_summary


def main() -> int:
    _LAST_COVERAGE_METADATA.clear()
    args = _core.parse_args()
    result = _dep.main()

    # Preserve the legacy field for compatibility, but add explicit coverage
    # semantics to the machine-readable run configuration.
    run_config_path = Path(args.output_dir) / "run-config.json"
    if run_config_path.exists() and _LAST_COVERAGE_METADATA:
        payload = json.loads(run_config_path.read_text(encoding="utf-8"))
        payload["dependency_aware_gap_evaluation"] = True
        payload.update(_LAST_COVERAGE_METADATA)
        run_config_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return result


def __getattr__(name: str) -> Any:
    return getattr(_dep, name)


if __name__ == "__main__":
    raise SystemExit(main())
