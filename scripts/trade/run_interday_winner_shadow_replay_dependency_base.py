from __future__ import annotations

"""Dependency-aware deterministic interday SHADOW replay.

This entry point layers decision-level data dependency semantics over the
recovery-aware acquisition adapter in
``run_interday_winner_shadow_replay_recovery_base``.

The central rule is intentionally narrower than either "any gap blocks the
session" or "old gaps can be ignored": an unresolved gap invalidates only the
strategy decisions whose inputs or open position state can still depend on it.
Factual partial tape is retained so a strategy can prove that a completed trade
or a later rolling calculation is independent of the missing interval.

Session-anchored strategies (session VWAP/opening structure/session return) may
use the causally complete prefix before the first unresolved gap. Rolling
requirements may reset and resume after their declared clean-bar warmup. No
OHLCV values are interpolated and no coarser candle is expanded into synthetic
1-minute bars.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from scripts.trade import run_interday_winner_shadow_replay_recovery_base as _base

from app.apps.trading.market_data_recovery import (
    DataEvaluability,
    RecoveryReport,
    StrategyDataRequirement,
    assess_data_requirement,
    detect_session_gaps,
    finalized_session_bars,
    latest_clean_bars,
)
from app.apps.trading.models import MarketBar
from app.apps.trading.strategy_leader_momentum_continuation import MAX_TRADES as LEADER_MAX_TRADES


_core = _base._core

# Preserve the public/testing surface of the acquisition adapter.
MarketDataCache = _base.MarketDataCache
RawBar = _base.RawBar
SymbolReplayData = _base.SymbolReplayData
cache_stats = _base.cache_stats
reset_cache_stats = _base.reset_cache_stats
_yahoo_1m_chunks = _base._yahoo_1m_chunks
_needs_one_minute_recovery = _base._needs_one_minute_recovery
_session_aware_cache_load = _base._session_aware_cache_load
_fetch_yahoo_1m = _base._fetch_yahoo_1m
_fetch_source_1m = _base._fetch_source_1m


_SESSION_OHLCV_1M = StrategyDataRequirement(
    interval="1m",
    continuity="session",
    minimum_clean_bars=1,
    required_fields=("ohlc", "volume"),
    reset_on_gap=False,
)

_STOCH_TREND_LAST_ENTRY_ET = time(11, 30)
_LEADER_LAST_ENTRY_ET = time(15, 30)
_LEADER_FORCE_FLAT_ET = time(15, 55)

# The acquisition layer keeps RawBar intentionally small.  Persist the richer
# recovery proof beside the loaded symbol/session so replay evaluators can ask
# whether the missing interval intersects their actual dependency window.
_ONE_MINUTE_RECOVERY_REPORTS: dict[tuple[str, date], RecoveryReport] = {}


@dataclass(frozen=True)
class DecisionDependencyView:
    """Causal data view for one strategy decision at one as-of time.

    ``bars`` are either the full dependency, the latest independently warmed
    rolling epoch, or the causally complete prefix before the first unresolved
    session-anchored gap.  ``decision_evaluable`` refers to the requested as-of
    decision.  A false value with non-empty ``bars`` means callers may still
    prove that an earlier completed outcome became final before ``blocked_after``.
    """

    bars: tuple[MarketBar, ...]
    decision_evaluable: bool
    reset_required: bool
    blocked_after: datetime | None
    status: str
    reason_codes: tuple[str, ...]


def _recover_one_minute_sessions(
    symbol: str,
    sessions: list[date],
    *,
    primary_raw: tuple[RawBar, ...],
    fallback_raw: tuple[RawBar, ...],
    primary_source: str,
    fallback_source: str | None,
) -> tuple[dict[date, tuple[RawBar, ...]], dict[date, str]]:
    """Retain factual partial tape and persist unresolved-gap evidence.

    The previous adapter intentionally replaced a partially recovered session
    with ``()``.  That was safe but too coarse: downstream strategies could no
    longer prove that a trade had already completed before the gap or that a
    rolling state had independently re-warmed after it.
    """

    instrument_id = f"equity:US:{symbol}"
    output: dict[date, tuple[RawBar, ...]] = {}
    unresolved: dict[date, str] = {}
    primary_provider = _base._provider_id(primary_source)
    fallback_provider = _base._provider_id(fallback_source) if fallback_source else None

    for session_date in sessions:
        primary_session = _core._session_bars(primary_raw, session_date, regular=True)
        fallback_session = _core._session_bars(fallback_raw, session_date, regular=True)
        recovered = _base.reconcile_recovery(
            instrument_id=instrument_id,
            interval="1m",
            session_date=session_date,
            as_of=_base._session_close(session_date),
            primary_bars=_base._raw_as_market_bars(
                primary_session,
                instrument_id=instrument_id,
                provider=primary_provider,
            ),
            fallback_bars=_base._raw_as_market_bars(
                fallback_session,
                instrument_id=instrument_id,
                provider=fallback_provider or primary_provider,
            ),
            primary_provider=primary_provider,
            fallback_provider=fallback_provider,
            fallback_attempted=bool(fallback_raw),
            partial_market_fallback=False,
        )
        _ONE_MINUTE_RECOVERY_REPORTS[(symbol.upper(), session_date)] = recovered.report

        if recovered.report.recovered_bar_count:
            _core._increment_cache_stat(
                "recovered_1m_bars", recovered.report.recovered_bar_count
            )
            if fallback_source:
                _base._RECOVERY_SOURCES.add(fallback_source)

        if recovered.report.unresolved_gaps:
            details = ",".join(
                f"{gap.start.isoformat()}..{gap.end.isoformat()}"
                for gap in recovered.report.unresolved_gaps
            )
            unresolved[session_date] = f"UNRESOLVED_1M_GAPS:{details}"

        rows = tuple(_base._market_as_raw(bar) for bar in recovered.bars)
        output[session_date] = rows
        for bar in recovered.bars:
            _base._BAR_PROVIDER_BY_KEY[(instrument_id, bar.start_time)] = bar.provider

    return output, unresolved


_BASE_LOAD_SYMBOL = _base._load_symbol


def _load_symbol(
    symbol: str,
    source_rows: dict[date, dict[str, object]],
    sessions: list[date],
) -> SymbolReplayData:
    """Use the acquisition adapter but retain unresolved diagnostics."""

    data = _BASE_LOAD_SYMBOL(symbol, source_rows, sessions)
    errors = dict(data.one_minute_errors or {})
    for session_date in sessions:
        report = _ONE_MINUTE_RECOVERY_REPORTS.get((symbol.upper(), session_date))
        if report is None or not report.unresolved_gaps:
            continue
        details = ",".join(
            f"{gap.start.isoformat()}..{gap.end.isoformat()}"
            for gap in report.unresolved_gaps
        )
        errors[f"unresolved:{session_date.isoformat()}"] = (
            f"UNRESOLVED_1M_GAPS:{details}"
        )
    return SymbolReplayData(
        symbol=data.symbol,
        candidates=data.candidates,
        bars_5m=data.bars_5m,
        bars_1m=data.bars_1m,
        bars_5m_history=data.bars_5m_history,
        five_minute_error=data.five_minute_error,
        one_minute_errors=errors,
    )


def _market_bars(
    raw_bars: tuple[RawBar, ...], instrument_id: str, interval: str
) -> list[MarketBar]:
    return _base._market_bars(raw_bars, instrument_id, interval)


def _data_source(interval: str) -> str:
    return _base._data_source(interval)


def _report_for(symbol: str, session_date: date) -> RecoveryReport | None:
    return _ONE_MINUTE_RECOVERY_REPORTS.get((symbol.upper(), session_date))


def _decision_dependency_view(
    symbol: str,
    session_date: date,
    raw_bars: tuple[RawBar, ...],
    *,
    requirement: StrategyDataRequirement,
    as_of: datetime | None = None,
) -> DecisionDependencyView:
    """Return exactly the factual bars that can support the requested decision."""

    instrument_id = f"equity:US:{symbol.upper()}"
    market = _market_bars(raw_bars, instrument_id, requirement.interval)
    decision_as_of = as_of or _base._session_close(session_date)
    report = _report_for(symbol, session_date)
    assessment = assess_data_requirement(
        market,
        session_date=session_date,
        as_of=decision_as_of,
        requirement=requirement,
        recovery_report=report,
    )

    if assessment.evaluable:
        if requirement.continuity == "rolling" and assessment.reset_required:
            selected = latest_clean_bars(
                market,
                session_date=session_date,
                interval=requirement.interval,
                as_of=decision_as_of,
            )
        else:
            selected = finalized_session_bars(
                market,
                session_date=session_date,
                interval=requirement.interval,
                as_of=decision_as_of,
            )
        return DecisionDependencyView(
            bars=tuple(selected),
            decision_evaluable=True,
            reset_required=assessment.reset_required,
            blocked_after=None,
            status=assessment.status,
            reason_codes=assessment.reason_codes,
        )

    if requirement.continuity == "rolling":
        selected = latest_clean_bars(
            market,
            session_date=session_date,
            interval=requirement.interval,
            as_of=decision_as_of,
        )
        return DecisionDependencyView(
            bars=tuple(selected),
            decision_evaluable=False,
            reset_required=assessment.reset_required,
            blocked_after=None,
            status=assessment.status,
            reason_codes=assessment.reason_codes,
        )

    gaps = detect_session_gaps(
        market,
        session_date=session_date,
        interval=requirement.interval,
        as_of=decision_as_of,
    )
    if not gaps:
        return DecisionDependencyView(
            bars=(),
            decision_evaluable=False,
            reset_required=False,
            blocked_after=None,
            status=assessment.status,
            reason_codes=assessment.reason_codes,
        )

    cutoff = gaps[0].start
    prefix_assessment = assess_data_requirement(
        market,
        session_date=session_date,
        as_of=cutoff,
        requirement=requirement,
        recovery_report=report,
    )
    prefix = finalized_session_bars(
        market,
        session_date=session_date,
        interval=requirement.interval,
        as_of=cutoff,
    )
    if not prefix_assessment.evaluable:
        prefix = []
    return DecisionDependencyView(
        bars=tuple(prefix),
        decision_evaluable=False,
        reset_required=False,
        blocked_after=cutoff,
        status=assessment.status,
        reason_codes=assessment.reason_codes,
    )


def _dependency_reason(prefix: str, view: DecisionDependencyView) -> str:
    codes = ",".join(view.reason_codes) or view.status
    boundary = (
        view.blocked_after.isoformat() if view.blocked_after is not None else "unknown"
    )
    return f"{prefix}:blocked_after={boundary}:reasons={codes}"


def _stoch_tolerable_single_minute_gap(symbol: str, session_date: date) -> bool:
    """Preserve the strategy's existing explicit one-minute omission policy."""

    report = _report_for(symbol, session_date)
    return bool(
        report is not None
        and len(report.unresolved_gaps) == 1
        and report.unresolved_gaps[0].missing_bar_count == 1
    )


def _stoch_trend_outcome_final_before_gap(snapshot: Any, cutoff: datetime) -> tuple[bool, str]:
    if snapshot.return_pct is not None:
        if snapshot.runner_exit_time is not None and snapshot.runner_exit_time <= cutoff:
            return True, "STOCH_TREND_TRADE_COMPLETED_BEFORE_GAP"
        return False, "STOCH_TREND_EXIT_DEPENDS_ON_GAP"
    if snapshot.entry_time is not None:
        return False, "STOCH_TREND_OPEN_POSITION_SPANS_GAP"
    if snapshot.state == "entry_armed":
        return False, "STOCH_TREND_PENDING_ENTRY_SPANS_GAP"
    if cutoff.astimezone(_core.ET).time() > _STOCH_TREND_LAST_ENTRY_ET:
        return True, "STOCH_TREND_GAP_AFTER_ENTRY_WINDOW_NO_OPEN_POSITION"
    return False, "STOCH_TREND_FUTURE_SETUP_DEPENDS_ON_GAP"


def _leader_outcome_final_before_gap(snapshot: Any, cutoff: datetime) -> tuple[bool, str]:
    for trade in snapshot.trades:
        if trade.exit_time > cutoff:
            return False, "LEADER_MOMENTUM_OPEN_POSITION_SPANS_GAP"
        if trade.exit_reason_code == "LEADER_MOMENTUM_FORCE_FLAT":
            # The evaluator treats the last supplied 3m bar as a force-flat when
            # replayed on a truncated prefix.  It is genuine only when that bar
            # actually reaches the configured 15:55 force-flat boundary.
            bar_end = trade.exit_time + timedelta(minutes=3)
            if bar_end.astimezone(_core.ET).time() < _LEADER_FORCE_FLAT_ET:
                return False, "LEADER_MOMENTUM_OPEN_POSITION_SPANS_GAP"
    if len(snapshot.trades) >= LEADER_MAX_TRADES:
        return True, "LEADER_MOMENTUM_MAX_TRADES_COMPLETED_BEFORE_GAP"
    if snapshot.state == "breakout_armed":
        return False, "LEADER_MOMENTUM_PENDING_ENTRY_SPANS_GAP"
    if cutoff.astimezone(_core.ET).time() > _LEADER_LAST_ENTRY_ET:
        return True, "LEADER_MOMENTUM_GAP_AFTER_ENTRY_WINDOW_NO_OPEN_POSITION"
    return False, "LEADER_MOMENTUM_FUTURE_SETUP_DEPENDS_ON_GAP"


def _append_stoch_trend_observation(
    observations: list[dict[str, object]],
    *,
    session_date: date,
    source_row: dict[str, object],
    symbol: str,
    snapshot: Any,
    reason_override: str | None = None,
) -> None:
    completed = snapshot.return_pct is not None
    observations.append(
        _core._base_observation(
            "stoch-trend-capture",
            session_date,
            source_row,
            symbol=symbol,
            status="completed" if completed else snapshot.state,
            reason=reason_override or snapshot.reason_code,
            entry_time=snapshot.entry_time,
            exit_time=snapshot.runner_exit_time,
            entry_price=snapshot.entry_price,
            exit_price=snapshot.combined_exit_price,
            return_pct=snapshot.return_pct,
            trade_count=1 if completed else 0,
            win_count=1 if completed and snapshot.return_pct > 0 else 0,
            loss_count=1 if completed and snapshot.return_pct < 0 else 0,
            data_source=_data_source("1m"),
        )
    )


def _append_leader_observation(
    observations: list[dict[str, object]],
    *,
    session_date: date,
    source_row: dict[str, object],
    symbol: str,
    snapshot: Any,
    reason_override: str | None = None,
) -> None:
    trades = tuple(snapshot.trades)
    factor = Decimal("1")
    for trade in trades:
        factor *= Decimal("1") + trade.return_pct / Decimal("100")
    total_return = (factor - Decimal("1")) * Decimal("100") if trades else None
    observations.append(
        _core._base_observation(
            "leader-momentum-continuation",
            session_date,
            source_row,
            symbol=symbol,
            status="completed" if trades else snapshot.state,
            reason=reason_override or snapshot.reason_code,
            entry_time=trades[0].entry_time if trades else snapshot.entry_time,
            exit_time=trades[-1].exit_time if trades else None,
            entry_price=trades[0].entry_price if trades else snapshot.entry_price,
            exit_price=trades[-1].exit_price if trades else None,
            return_pct=total_return,
            trade_count=len(trades),
            win_count=sum(trade.return_pct > 0 for trade in trades),
            loss_count=sum(trade.return_pct < 0 for trade in trades),
            data_source=_data_source("1m"),
        )
    )


def _evaluate_overlay_arms(
    sessions: list[date],
    grouped: dict[date, list[dict[str, object]]],
    loaded: dict[str, SymbolReplayData],
) -> list[dict[str, object]]:
    """Evaluate overlay arms without letting an irrelevant late gap poison them."""

    observations: list[dict[str, object]] = []
    stoch_config = _core.StochRsi5mConfig()
    stoch_rsi_variants = (
        ("stoch-rsi-5min", _core.evaluate_stoch_rsi_5m),
        ("stoch-rsi-5min-early-single", _core.evaluate_stoch_rsi_5m_early_single),
        ("stoch-rsi-5min-late-stage", _core.evaluate_stoch_rsi_5m_late_stage),
    )

    for symbol, data in loaded.items():
        source_by_date = {
            session_date: row
            for session_date, rows in grouped.items()
            for row in rows
            if row["symbol"] == symbol
        }
        history_1m: list[MarketBar] = []

        for session_date in sessions:
            source_row = source_by_date.get(session_date)
            five_raw = data.bars_5m.get(session_date, ())
            one_raw = data.bars_1m.get(session_date, ())
            instrument_id = f"equity:US:{symbol}"

            # Both Stoch RSI arms own their rolling post-gap reset semantics;
            # the late-stage variant only changes the entry-start cutoff.
            if source_row is not None:
                for arm, evaluator in stoch_rsi_variants:
                    if data.five_minute_error:
                        observations.append(
                            _core._base_observation(
                                arm, session_date, source_row,
                                symbol=symbol, status="data_unavailable",
                                reason=data.five_minute_error, data_source=_data_source("5m")
                            )
                        )
                    elif not five_raw:
                        observations.append(
                            _core._base_observation(
                                arm, session_date, source_row,
                                symbol=symbol, status="data_unavailable",
                                reason="STOCH_RSI_5M_REGULAR_BARS_UNAVAILABLE",
                                data_source=_data_source("5m")
                            )
                        )
                    else:
                        five_history_raw = data.bars_5m_history.get(session_date) or five_raw
                        snapshot = evaluator(
                            _market_bars(five_history_raw, instrument_id, "5m"), stoch_config
                        )
                        trades = tuple(snapshot.trades)
                        factor = Decimal("1")
                        for trade in trades:
                            factor *= Decimal("1") + trade.return_pct / Decimal("100")
                        total_return = (factor - Decimal("1")) * Decimal("100") if trades else None
                        observations.append(
                            _core._base_observation(
                                arm, session_date, source_row,
                                symbol=symbol,
                                status="completed" if trades else snapshot.state,
                                reason=snapshot.reason_code,
                                entry_time=trades[0].entry_time if trades else snapshot.entry_time,
                                exit_time=trades[-1].exit_time if trades else snapshot.exit_time,
                                entry_price=trades[0].entry_price if trades else snapshot.entry_price,
                                exit_price=trades[-1].exit_price if trades else snapshot.exit_price,
                                return_pct=total_return,
                                trade_count=len(trades),
                                win_count=sum(trade.return_pct > 0 for trade in trades),
                                loss_count=sum(trade.return_pct < 0 for trade in trades),
                                data_source=_data_source("5m"),
                            )
                        )

            day_market = _market_bars(one_raw, instrument_id, "1m") if one_raw else []
            report = _report_for(symbol, session_date)
            unresolved = bool(report and report.unresolved_gaps)

            # Stoch Trend Capture is single-trade, but uses session VWAP. A
            # larger unresolved gap therefore blocks future setups; a completed
            # pre-gap trade, or a no-position state after the entry window, is
            # nevertheless final and remains evaluable. Its existing explicit
            # one-minute omission policy is preserved.
            if source_row is not None:
                if not one_raw:
                    reason = "STOCH_TREND_1M_BARS_UNAVAILABLE"
                    if data.one_minute_errors:
                        reason = "; ".join(data.one_minute_errors.values())
                    observations.append(
                        _core._base_observation(
                            "stoch-trend-capture", session_date, source_row,
                            symbol=symbol, status="data_unavailable", reason=reason,
                            data_source=_data_source("1m")
                        )
                    )
                elif _stoch_tolerable_single_minute_gap(symbol, session_date):
                    snapshot = _core.evaluate_stoch_trend_capture([*history_1m, *day_market])
                    if snapshot.state == "data_gap":
                        observations.append(
                            _core._base_observation(
                                "stoch-trend-capture", session_date, source_row,
                                symbol=symbol, status="data_unavailable",
                                reason=snapshot.reason_code, data_source=_data_source("1m")
                            )
                        )
                    else:
                        _append_stoch_trend_observation(
                            observations, session_date=session_date,
                            source_row=source_row, symbol=symbol, snapshot=snapshot
                        )
                else:
                    view = _decision_dependency_view(
                        symbol, session_date, one_raw, requirement=_SESSION_OHLCV_1M
                    )
                    if view.decision_evaluable:
                        snapshot = _core.evaluate_stoch_trend_capture(
                            [*history_1m, *view.bars]
                        )
                        _append_stoch_trend_observation(
                            observations, session_date=session_date,
                            source_row=source_row, symbol=symbol, snapshot=snapshot
                        )
                    elif view.bars and view.blocked_after is not None:
                        snapshot = _core.evaluate_stoch_trend_capture(
                            [*history_1m, *view.bars]
                        )
                        final, reason = _stoch_trend_outcome_final_before_gap(
                            snapshot, view.blocked_after
                        )
                        if final:
                            _append_stoch_trend_observation(
                                observations, session_date=session_date,
                                source_row=source_row, symbol=symbol, snapshot=snapshot,
                                reason_override=reason,
                            )
                        else:
                            observations.append(
                                _core._base_observation(
                                    "stoch-trend-capture", session_date, source_row,
                                    symbol=symbol, status="data_unavailable",
                                    reason=f"{reason};{_dependency_reason('STOCH_TREND_UNRESOLVED_DEPENDENCY', view)}",
                                    data_source=_data_source("1m")
                                )
                            )
                    else:
                        observations.append(
                            _core._base_observation(
                                "stoch-trend-capture", session_date, source_row,
                                symbol=symbol, status="data_unavailable",
                                reason=_dependency_reason(
                                    "STOCH_TREND_UNRESOLVED_DEPENDENCY", view
                                ),
                                data_source=_data_source("1m")
                            )
                        )

            # Leader Momentum also uses session VWAP/session return, so it is
            # session-anchored. Its local setup contiguity checks are not enough
            # to make a post-gap leader score independent of the missing volume.
            if source_row is not None:
                candidate = data.candidates.get(session_date)
                if data.five_minute_error:
                    observations.append(
                        _core._base_observation(
                            "leader-momentum-continuation", session_date, source_row,
                            symbol=symbol, status="data_unavailable",
                            reason=data.five_minute_error, data_source=_data_source("1m")
                        )
                    )
                elif candidate is None:
                    observations.append(
                        _core._base_observation(
                            "leader-momentum-continuation", session_date, source_row,
                            symbol=symbol, status="data_unavailable",
                            reason="LEADER_MOMENTUM_CANDIDATE_METADATA_UNAVAILABLE",
                            data_source=_data_source("1m")
                        )
                    )
                elif not one_raw:
                    observations.append(
                        _core._base_observation(
                            "leader-momentum-continuation", session_date, source_row,
                            symbol=symbol, status="data_unavailable",
                            reason="LEADER_MOMENTUM_1M_REGULAR_BARS_UNAVAILABLE",
                            data_source=_data_source("1m")
                        )
                    )
                else:
                    context = _core.LeaderMomentumContext(
                        tod_rvol=candidate.tod_rvol,
                        spread_bps=candidate.spread_bps,
                        dollar_volume=candidate.premarket_dollar_volume,
                    )
                    view = _decision_dependency_view(
                        symbol, session_date, one_raw, requirement=_SESSION_OHLCV_1M
                    )
                    if view.decision_evaluable:
                        snapshot = _core.evaluate_leader_momentum_continuation(
                            list(view.bars), context=context
                        )
                        _append_leader_observation(
                            observations, session_date=session_date,
                            source_row=source_row, symbol=symbol, snapshot=snapshot
                        )
                    elif view.bars and view.blocked_after is not None:
                        snapshot = _core.evaluate_leader_momentum_continuation(
                            list(view.bars), context=context
                        )
                        final, reason = _leader_outcome_final_before_gap(
                            snapshot, view.blocked_after
                        )
                        if final:
                            _append_leader_observation(
                                observations, session_date=session_date,
                                source_row=source_row, symbol=symbol, snapshot=snapshot,
                                reason_override=reason,
                            )
                        else:
                            observations.append(
                                _core._base_observation(
                                    "leader-momentum-continuation", session_date, source_row,
                                    symbol=symbol, status="data_unavailable",
                                    reason=f"{reason};{_dependency_reason('LEADER_MOMENTUM_UNRESOLVED_DEPENDENCY', view)}",
                                    data_source=_data_source("1m")
                                )
                            )
                    else:
                        observations.append(
                            _core._base_observation(
                                "leader-momentum-continuation", session_date, source_row,
                                symbol=symbol, status="data_unavailable",
                                reason=_dependency_reason(
                                    "LEADER_MOMENTUM_UNRESOLVED_DEPENDENCY", view
                                ),
                                data_source=_data_source("1m")
                            )
                        )

            # Only a fully continuous day may warm the next session. A gappy
            # prior day is evidence, not a safe oscillator warmup source.
            if day_market and not unresolved:
                history_1m.extend(day_market)

    return observations


def _gap_pullback_outcome_final_before_gap(
    trade: Any | None,
    decision: Any | None,
    cutoff: datetime,
    *,
    config: Any,
) -> tuple[bool, str]:
    if trade is not None:
        if trade.exit_time > cutoff:
            return False, "GAP_PULLBACK_OPEN_POSITION_SPANS_GAP"
        if trade.exit_reason == "eod" and cutoff < _base._session_close(
            cutoff.astimezone(_core.ET).date()
        ):
            return False, "GAP_PULLBACK_OPEN_POSITION_SPANS_GAP"
        return True, "GAP_PULLBACK_TRADE_COMPLETED_BEFORE_GAP"

    if decision is not None and decision.state in {"rejected", "expired"}:
        return True, "GAP_PULLBACK_TERMINAL_DECISION_BEFORE_GAP"
    if decision is not None and (
        decision.rejection_reason == "no_next_bar" or decision.state == "entry_ready"
    ):
        return False, "GAP_PULLBACK_PENDING_ENTRY_SPANS_GAP"
    if cutoff.astimezone(_core.ET).time() > config.last_entry_et:
        return True, "GAP_PULLBACK_GAP_AFTER_ENTRY_WINDOW_NO_OPEN_POSITION"
    return False, "GAP_PULLBACK_FUTURE_SETUP_DEPENDS_ON_GAP"


def _run_single_symbol_gap_backtest(
    *,
    session_date: date,
    row: dict[str, object],
    candidate: Any,
    bars: tuple[MarketBar, ...],
    input_path: Path,
    config: Any,
    risk: Any,
) -> Any:
    universe = _core.freeze_gapper_universe(
        universe_id=f"winner-benchmark-{session_date.isoformat()}-{candidate.instrument_id}",
        session_date=session_date,
        evaluation_time=datetime.combine(
            session_date, time(9, 15), tzinfo=_core.ET
        ).astimezone(_core.UTC),
        discovery_source="import",
        candidates=[candidate],
        source_locator=f"{input_path.as_posix()}#{session_date.isoformat()}#{row['symbol']}",
        source_candidate_symbols=(str(row["symbol"]),),
    )
    dataset = _core.freeze_backtest_session(
        session_date=session_date,
        universe=universe,
        bars_by_instrument={candidate.instrument_id: list(bars)},
    )
    return _core.run_gap_pullback_backtest(
        dataset,
        config,
        _core.PaperExecutionPolicy(max_volume_participation_pct=Decimal("1")),
        assumed_spread_bps=_core.ASSUMED_SPREAD_BPS,
        max_hold_minutes=config.v2_max_hold_minutes,
        max_concurrent_positions=risk.max_positions,
        risk_profile=risk,
        initial_cash=_core.FIXED_DAILY_CAPITAL,
    )


def _append_gap_result_observations(
    observations: list[dict[str, object]],
    *,
    session_date: date,
    row: dict[str, object],
    candidate: Any,
    result: Any,
    risk_pnl_authoritative: bool,
    reason_override: str | None = None,
) -> None:
    trade_by_symbol = {trade.instrument_id: trade for trade in result.trades}
    decision_by_symbol = {
        decision.instrument_id: decision for decision in result.candidate_decisions
    }
    trade = trade_by_symbol.get(candidate.instrument_id)
    decision = decision_by_symbol.get(candidate.instrument_id)

    for arm in ("deterministic-v2", "gap-pullback-v2-prospective-20260825"):
        if trade is not None:
            return_pct = (
                trade.exit_price / trade.entry_price - Decimal("1")
            ) * Decimal("100")
            observations.append(
                _core._base_observation(
                    arm, session_date, row, symbol=str(row["symbol"]),
                    status="completed",
                    reason=reason_override or trade.exit_reason,
                    entry_time=trade.entry_time,
                    exit_time=trade.exit_time,
                    entry_price=trade.entry_price,
                    exit_price=trade.exit_price,
                    return_pct=return_pct,
                    trade_count=1,
                    win_count=1 if return_pct > 0 else 0,
                    loss_count=1 if return_pct < 0 else 0,
                    risk_pnl=(
                        trade.pnl_per_share * trade.entry_fill_quantity
                        if risk_pnl_authoritative else None
                    ),
                    data_source=_data_source("1m"),
                )
            )
        else:
            reason = (
                reason_override
                or (decision.rejection_reason if decision else None)
                or (decision.state if decision else "NO_DECISION")
            )
            observations.append(
                _core._base_observation(
                    arm, session_date, row, symbol=str(row["symbol"]),
                    status=decision.state if decision else "data_unavailable",
                    reason=str(reason), data_source=_data_source("1m")
                )
            )


def _gap_observations(
    sessions: list[date],
    grouped: dict[date, list[dict[str, object]]],
    loaded: dict[str, SymbolReplayData],
    observations: list[dict[str, object]],
    input_path: Path,
) -> tuple[dict[date, Any], dict[date, Decimal], dict[date, str]]:
    """Keep canonical portfolio accounting strict; salvage independent symbol outcomes."""

    config = _core.managed_finviz_v2_config()
    risk = _core.StrategyRiskProfile()
    current_cash = _core.FIXED_DAILY_CAPITAL
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
            for arm in ("deterministic-v2", "gap-pullback-v2-prospective-20260825"):
                for row in rows:
                    observations.append(
                        _core._base_observation(
                            arm, session_date, row, symbol=str(row["symbol"]),
                            status="data_unavailable",
                            reason="5m candidate metadata unavailable",
                            data_source=_data_source("1m"),
                        )
                    )
            continue

        views: dict[str, DecisionDependencyView] = {}
        all_full = True
        for row, candidate in candidate_pairs:
            symbol = str(row["symbol"])
            raw = loaded[symbol].bars_1m.get(session_date, ())
            view = _decision_dependency_view(
                symbol, session_date, raw, requirement=_SESSION_OHLCV_1M
            )
            views[candidate.instrument_id] = view
            all_full = all_full and view.decision_evaluable and bool(view.bars)

        if all_full:
            candidate_list = [candidate for _, candidate in candidate_pairs]
            bars_by_instrument = {
                candidate.instrument_id: list(views[candidate.instrument_id].bars)
                for _, candidate in candidate_pairs
            }
            universe = _core.freeze_gapper_universe(
                universe_id=f"winner-benchmark-{session_date.isoformat()}",
                session_date=session_date,
                evaluation_time=datetime.combine(
                    session_date, time(9, 15), tzinfo=_core.ET
                ).astimezone(_core.UTC),
                discovery_source="import",
                candidates=candidate_list,
                source_locator=f"{input_path.as_posix()}#{session_date.isoformat()}",
                source_candidate_symbols=tuple(str(row["symbol"]) for row in rows),
            )
            dataset = _core.freeze_backtest_session(
                session_date=session_date,
                universe=universe,
                bars_by_instrument=bars_by_instrument,
            )
            result = _core.run_gap_pullback_backtest(
                dataset,
                config,
                _core.PaperExecutionPolicy(max_volume_participation_pct=Decimal("1")),
                assumed_spread_bps=_core.ASSUMED_SPREAD_BPS,
                max_hold_minutes=config.v2_max_hold_minutes,
                max_concurrent_positions=risk.max_positions,
                risk_profile=risk,
                initial_cash=current_cash,
            )
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
            for row, candidate in candidate_pairs:
                _append_gap_result_observations(
                    observations,
                    session_date=session_date,
                    row=row,
                    candidate=candidate,
                    result=result,
                    risk_pnl_authoritative=True,
                )
            continue

        gap_status[session_date] = (
            "dependency-aware symbol replay; canonical portfolio account skipped"
        )
        for row, candidate in candidate_pairs:
            symbol = str(row["symbol"])
            view = views[candidate.instrument_id]
            if not view.bars:
                for arm in ("deterministic-v2", "gap-pullback-v2-prospective-20260825"):
                    observations.append(
                        _core._base_observation(
                            arm, session_date, row, symbol=symbol,
                            status="data_unavailable",
                            reason=_dependency_reason(
                                "GAP_PULLBACK_UNRESOLVED_DEPENDENCY", view
                            ),
                            data_source=_data_source("1m"),
                        )
                    )
                continue

            result = _run_single_symbol_gap_backtest(
                session_date=session_date,
                row=row,
                candidate=candidate,
                bars=view.bars,
                input_path=input_path,
                config=config,
                risk=risk,
            )
            trade = next(
                (item for item in result.trades if item.instrument_id == candidate.instrument_id),
                None,
            )
            decision = next(
                (
                    item for item in result.candidate_decisions
                    if item.instrument_id == candidate.instrument_id
                ),
                None,
            )

            if view.decision_evaluable:
                _append_gap_result_observations(
                    observations,
                    session_date=session_date,
                    row=row,
                    candidate=candidate,
                    result=result,
                    risk_pnl_authoritative=False,
                )
                continue

            if view.blocked_after is None:
                final, reason = False, "GAP_PULLBACK_UNRESOLVED_DEPENDENCY"
            else:
                final, reason = _gap_pullback_outcome_final_before_gap(
                    trade, decision, view.blocked_after, config=config
                )
            if final:
                _append_gap_result_observations(
                    observations,
                    session_date=session_date,
                    row=row,
                    candidate=candidate,
                    result=result,
                    risk_pnl_authoritative=False,
                    reason_override=reason,
                )
            else:
                for arm in ("deterministic-v2", "gap-pullback-v2-prospective-20260825"):
                    observations.append(
                        _core._base_observation(
                            arm, session_date, row, symbol=symbol,
                            status="data_unavailable",
                            reason=f"{reason};{_dependency_reason('GAP_PULLBACK_UNRESOLVED_DEPENDENCY', view)}",
                            data_source=_data_source("1m"),
                        )
                    )

    gap_risk_pnl[date.min] = current_cash
    return gap_results, gap_risk_pnl, gap_status


# Install the dependency-aware boundary after the acquisition adapter has
# installed its cache/provider patches.
_base._recover_one_minute_sessions = _recover_one_minute_sessions
_core._load_symbol = _load_symbol
_core._market_bars = _market_bars
_core._data_source = _data_source
_core._evaluate_overlay_arms = _evaluate_overlay_arms
_core._gap_observations = _gap_observations


def main() -> int:
    _ONE_MINUTE_RECOVERY_REPORTS.clear()
    return _base.main()


def __getattr__(name: str) -> Any:
    return getattr(_base, name)


if __name__ == "__main__":
    raise SystemExit(main())
