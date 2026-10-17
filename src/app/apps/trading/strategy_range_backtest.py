from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .gapper_dataset import GapperCandidate, GapperUniverseSnapshot
from .historical_gapper_reconstruction import (
    AlpacaHistoricalGapperReconstructor,
    HistoricalUniverseReconstruction,
    reconstruct_recent_alpaca_gapper_universe,
    reconstructed_strategy_config,
)
from .models import AdjustmentMode, MarketBar
from .paper import PaperExecutionPolicy
from .providers.errors import ProviderContractError, ProviderDataUnavailableError
from .providers.http_runtime import ProviderHttpRuntime
from .research.policy import ResearchPolicyDecision
from .research.runtime_policy import forbid_external_web_search
from .strategy_backtest import GapPullbackBacktestResult, freeze_backtest_session, run_gap_pullback_backtest
from .strategy_historical_bars import alpaca_historical_session_bars
from .strategy_repository import TradingStrategyConfigDocument
from .us_equity_calendar import regular_holidays
from app.apps.trading.us_equity_calendar import EASTERN as _ET
from app.apps.trading.us_equity_calendar import regular_close_time


_YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
HistoricalUniverseMode = Literal["captured_only", "captured_or_reconstructed", "reconstructed_only"]
HistoricalUniverseOrigin = Literal["captured", "reconstructed"]
BacktestResultQuality = Literal["exact", "mixed", "approximate", "unavailable"]
Reconstructor = Callable[..., HistoricalUniverseReconstruction]
ProgressCallback = Callable[[int, int, date], None]


class StrategyRangeBacktestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_date: date
    end_date: date
    initial_cash: Decimal = Field(default=Decimal("100000"), gt=0)
    assumed_spread_bps: Decimal = Field(default=Decimal("40"), ge=0, le=10_000)
    max_hold_minutes: int = Field(
        default=390,
        ge=1,
        le=390,
        description="Legacy compatibility field; indicator-based exits do not use elapsed time.",
    )
    universe_scan_time_et: time | None = None
    universe_cutoff_et: time | None = None
    universe_mode: HistoricalUniverseMode = "captured_or_reconstructed"
    reconstruction_max_age_days: int = Field(default=30, ge=1, le=3650)
    max_sessions: int = Field(default=60, ge=1, le=252)

    @model_validator(mode="after")
    def validate_scan_time_aliases(self):
        if (
            self.universe_scan_time_et is not None
            and self.universe_cutoff_et is not None
            and self.universe_scan_time_et != self.universe_cutoff_et
        ):
            raise ValueError("universe_scan_time_et conflicts with legacy universe_cutoff_et")
        return self


class StrategyRangeBacktestDay(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_date: date
    status: Literal["backtested", "no_candidates", "missing_universe", "data_unavailable", "error"]
    universe_id: str | None = None
    universe_evaluation_time: datetime | None = None
    universe_origin: HistoricalUniverseOrigin | None = None
    fidelity: str | None = None
    fidelity_warnings: tuple[str, ...] = ()
    strategy_fidelity_adjustments: tuple[str, ...] = ()
    candidate_count: int = 0
    starting_cash: Decimal
    ending_cash: Decimal
    pnl: Decimal = Decimal("0")
    trigger_count: int = 0
    trade_count: int = 0
    detail: str | None = None
    result: GapPullbackBacktestResult | None = None


class StrategyRangeBacktestResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy_id: str
    strategy_kind: str
    strategy_version: str
    start_date: date
    end_date: date
    universe_scan_time_et: time
    universe_cutoff_et: time
    universe_mode: HistoricalUniverseMode
    initial_cash: Decimal
    ending_cash: Decimal
    pnl: Decimal | None
    return_pct: Decimal | None
    requested_trading_sessions: int
    covered_sessions: int
    exact_sessions: int
    reconstructed_sessions: int
    no_candidate_sessions: int
    missing_universe_sessions: int
    data_unavailable_sessions: int
    error_sessions: int
    candidate_count: int
    trigger_count: int
    trade_count: int
    win_count: int
    loss_count: int
    expectancy_r: Decimal | None
    result_quality: BacktestResultQuality
    days: tuple[StrategyRangeBacktestDay, ...]
    point_in_time_universes_required: Literal[True] = True
    reconstruction_is_approximate: Literal[True] = True


def _trading_dates(start_date: date, end_date: date) -> list[date]:
    if end_date < start_date:
        raise ValueError("backtest_end_date_precedes_start_date")
    output: list[date] = []
    cursor = start_date
    while cursor <= end_date:
        if cursor.weekday() < 5 and cursor not in regular_holidays(cursor.year):
            output.append(cursor)
        cursor += timedelta(days=1)
    return output


def _cutoff(session_date: date, cutoff_time: time, grace_minutes: int = 0) -> datetime:
    return (
        datetime.combine(session_date, cutoff_time, tzinfo=_ET)
        + timedelta(minutes=max(0, grace_minutes))
    ).astimezone(timezone.utc)


def choose_causal_universe(
    universes: list[GapperUniverseSnapshot] | tuple[GapperUniverseSnapshot, ...],
    *,
    session_date: date,
    cutoff_time: time,
    grace_minutes: int = 0,
) -> GapperUniverseSnapshot | None:
    """Choose the freshest raw/research snapshot captured in the allowed scan window."""

    cutoff = _cutoff(session_date, cutoff_time, grace_minutes)
    eligible = [
        snapshot
        for snapshot in universes
        if snapshot.session_date == session_date
        and snapshot.evaluation_time.astimezone(timezone.utc) <= cutoff
        and "-selected-" not in snapshot.universe_id
        and not snapshot.universe_id.startswith("reconstructed-")
    ]
    if not eligible:
        return None
    eligible.sort(key=lambda snapshot: (snapshot.evaluation_time, snapshot.universe_id))
    return eligible[-1]


def _session_bounds(session_date: date) -> tuple[datetime, datetime]:
    close = regular_close_time(session_date)
    start = datetime.combine(session_date, time(9, 30), tzinfo=_ET)
    end = datetime.combine(session_date, close, tzinfo=_ET)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def yahoo_historical_session_bars(
    candidate: GapperCandidate,
    session_date: date,
    *,
    runtime: ProviderHttpRuntime | None = None,
) -> list[MarketBar]:
    """Fetch regular-session 1m Yahoo bars for an already-captured candidate."""

    active_runtime = runtime or ProviderHttpRuntime("yahoo_strategy_backtest", max_concurrency=2)
    symbol = candidate.instrument_id.split(":")[-1]
    session_start, session_end = _session_bounds(session_date)
    response = active_runtime.get(
        _YAHOO_CHART_URL.format(symbol=symbol),
        params={
            "period1": int(session_start.timestamp()),
            "period2": int((session_end + timedelta(minutes=1)).timestamp()),
            "interval": "1m",
            "includePrePost": "false",
            "events": "",
        },
        headers={"User-Agent": "Mozilla/5.0 Omnix local research"},
        timeout=20,
    )
    try:
        payload = response.json()
    except ValueError as exc:
        raise ProviderContractError("Yahoo returned invalid historical strategy JSON") from exc
    chart = payload.get("chart") if isinstance(payload, dict) else None
    error = chart.get("error") if isinstance(chart, dict) else None
    if error:
        description = error.get("description") if isinstance(error, dict) else str(error)
        raise ProviderDataUnavailableError(f"Yahoo historical intraday data unavailable for {symbol}: {description}")
    result = ((chart or {}).get("result") or [None])[0] if isinstance(chart, dict) else None
    if not isinstance(result, dict):
        raise ProviderDataUnavailableError(f"Yahoo returned no historical strategy bars for {symbol}")
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    timestamps = result.get("timestamp") or []
    if not isinstance(quote, dict) or not isinstance(timestamps, list):
        raise ProviderContractError("Yahoo historical strategy payload is malformed")
    values = {field: quote.get(field) or [] for field in ("open", "high", "low", "close", "volume")}
    received_at = datetime.now(timezone.utc)
    bars: list[MarketBar] = []
    for index, raw_timestamp in enumerate(timestamps):
        if any(not isinstance(series, list) or index >= len(series) for series in values.values()):
            continue
        open_value = values["open"][index]
        high = values["high"][index]
        low = values["low"][index]
        close = values["close"][index]
        volume = values["volume"][index]
        if None in (open_value, high, low, close):
            continue
        start = datetime.fromtimestamp(int(raw_timestamp), tz=timezone.utc)
        local = start.astimezone(_ET)
        if local.date() != session_date or local.timetz().replace(tzinfo=None) < time(9, 30):
            continue
        if start >= session_end:
            continue
        bars.append(
            MarketBar(
                instrument_id=candidate.instrument_id,
                interval="1m",
                start_time=start,
                end_time=start + timedelta(minutes=1),
                open=Decimal(str(open_value)),
                high=Decimal(str(high)),
                low=Decimal(str(low)),
                close=Decimal(str(close)),
                volume=Decimal(str(volume or 0)),
                is_final=True,
                adjustment_mode=AdjustmentMode.RAW,
                session="regular",
                provider="yahoo",
                provider_event_id=str(raw_timestamp),
                received_at=received_at,
            )
        )
    if not bars:
        raise ProviderDataUnavailableError(f"Yahoo returned no regular-session 1m bars for {symbol} on {session_date}")
    return bars


def _result_quality(exact_sessions: int, reconstructed_sessions: int, covered_sessions: int) -> BacktestResultQuality:
    if covered_sessions == 0:
        return "unavailable"
    if exact_sessions and reconstructed_sessions:
        return "mixed"
    if reconstructed_sessions:
        return "approximate"
    return "exact"


@forbid_external_web_search("trading_backtest")
def run_strategy_range_backtest(
    strategy: TradingStrategyConfigDocument,
    universes: list[GapperUniverseSnapshot] | tuple[GapperUniverseSnapshot, ...],
    request: StrategyRangeBacktestRequest,
    *,
    reconstructor: Reconstructor = reconstruct_recent_alpaca_gapper_universe,
    research_policy_resolver: Callable[[str, datetime], ResearchPolicyDecision] | None = None,
    progress_callback: ProgressCallback | None = None,
) -> StrategyRangeBacktestResult:
    if strategy.strategy_kind != "gap_pullback_v1":
        raise ValueError("strategy_backtest_not_supported")
    sessions = _trading_dates(request.start_date, request.end_date)
    if len(sessions) > request.max_sessions:
        raise ValueError(f"backtest_session_limit_exceeded:{len(sessions)}>{request.max_sessions}")

    scan_time = request.universe_scan_time_et or request.universe_cutoff_et or strategy.config.universe_scan_time_et
    current_cash = request.initial_cash
    days: list[StrategyRangeBacktestDay] = []
    yahoo_runtime = ProviderHttpRuntime("yahoo_strategy_range_backtest", max_concurrency=2)
    alpaca_runtime = ProviderHttpRuntime("alpaca_strategy_range_backtest", max_concurrency=4)

    grouped: dict[date, list[GapperUniverseSnapshot]] = defaultdict(list)
    for archived in universes:
        grouped[archived.session_date].append(archived)

    active_reconstructor: Reconstructor = reconstructor
    if reconstructor is reconstruct_recent_alpaca_gapper_universe and request.universe_mode != "captured_only":
        active_reconstructor = AlpacaHistoricalGapperReconstructor(
            start_date=request.start_date,
            end_date=request.end_date,
            config=strategy.config,
            assumed_spread_bps=request.assumed_spread_bps,
            max_age_days=request.reconstruction_max_age_days,
        )

    completed_sessions = 0

    def report_progress(session_date: date) -> None:
        nonlocal completed_sessions
        completed_sessions += 1
        if progress_callback:
            progress_callback(completed_sessions, len(sessions), session_date)

    for session_date in sessions:
        starting_cash = current_cash
        captured: GapperUniverseSnapshot | None = None
        if request.universe_mode != "reconstructed_only":
            captured = choose_causal_universe(
                grouped.get(session_date, []),
                session_date=session_date,
                cutoff_time=scan_time,
                grace_minutes=strategy.config.universe_archive_grace_minutes,
            )

        universe: GapperUniverseSnapshot | None = captured
        origin: HistoricalUniverseOrigin | None = "captured" if captured is not None else None
        fidelity = "captured_point_in_time" if captured is not None else None
        fidelity_warnings: tuple[str, ...] = ()
        fidelity_adjustments: tuple[str, ...] = ()
        active_config = strategy.config

        if universe is None and request.universe_mode != "captured_only":
            try:
                reconstruction = active_reconstructor(
                    session_date=session_date,
                    scan_time=scan_time,
                    config=strategy.config,
                    assumed_spread_bps=request.assumed_spread_bps,
                    max_age_days=request.reconstruction_max_age_days,
                )
            except (ProviderContractError, ProviderDataUnavailableError, OSError, ValueError) as exc:
                days.append(
                    StrategyRangeBacktestDay(
                        session_date=session_date,
                        status="data_unavailable",
                        starting_cash=starting_cash,
                        ending_cash=current_cash,
                        universe_origin="reconstructed",
                        fidelity="reconstruction_failed",
                        detail=f"Historical universe reconstruction failed: {exc}",
                    )
                )
                report_progress(session_date)
                continue
            universe = reconstruction.snapshot
            origin = "reconstructed"
            fidelity = reconstruction.fidelity
            fidelity_warnings = reconstruction.warnings
            active_config, fidelity_adjustments = reconstructed_strategy_config(strategy.config)
            if universe is None:
                if reconstruction.fidelity == "reconstructed_current_listings_iex":
                    days.append(
                        StrategyRangeBacktestDay(
                            session_date=session_date,
                            status="no_candidates",
                            starting_cash=starting_cash,
                            ending_cash=current_cash,
                            universe_origin="reconstructed",
                            fidelity=reconstruction.fidelity,
                            fidelity_warnings=reconstruction.warnings,
                            strategy_fidelity_adjustments=fidelity_adjustments,
                            detail=reconstruction.detail or "Historical scan found no qualifying candidates.",
                        )
                    )
                else:
                    days.append(
                        StrategyRangeBacktestDay(
                            session_date=session_date,
                            status="data_unavailable",
                            starting_cash=starting_cash,
                            ending_cash=current_cash,
                            universe_origin="reconstructed",
                            fidelity=reconstruction.fidelity,
                            fidelity_warnings=reconstruction.warnings,
                            strategy_fidelity_adjustments=fidelity_adjustments,
                            detail=reconstruction.detail or "Historical universe reconstruction is unavailable.",
                        )
                    )
                report_progress(session_date)
                continue

        if universe is None:
            days.append(
                StrategyRangeBacktestDay(
                    session_date=session_date,
                    status="missing_universe",
                    starting_cash=starting_cash,
                    ending_cash=current_cash,
                    detail="No frozen point-in-time gapper/research universe existed in the configured scan/grace window. Choose reconstructed mode for an explicitly approximate recent-history scan.",
                )
            )
            report_progress(session_date)
            continue

        bars_by_instrument: dict[str, list[MarketBar]] = {}
        try:
            if not universe.candidates:
                bars_by_instrument = {}
            elif origin == "reconstructed":
                bars_by_instrument = alpaca_historical_session_bars(
                    universe.candidates,
                    session_date,
                    runtime=alpaca_runtime,
                )
            else:
                for candidate in universe.candidates:
                    bars_by_instrument[candidate.instrument_id] = yahoo_historical_session_bars(
                        candidate,
                        session_date,
                        runtime=yahoo_runtime,
                    )
        except (ProviderContractError, ProviderDataUnavailableError, OSError) as exc:
            days.append(
                StrategyRangeBacktestDay(
                    session_date=session_date,
                    status="data_unavailable",
                    universe_id=universe.universe_id,
                    universe_evaluation_time=universe.evaluation_time,
                    universe_origin=origin,
                    fidelity=fidelity,
                    fidelity_warnings=fidelity_warnings,
                    strategy_fidelity_adjustments=fidelity_adjustments,
                    candidate_count=len(universe.candidates),
                    starting_cash=starting_cash,
                    ending_cash=current_cash,
                    detail=str(exc),
                )
            )
            report_progress(session_date)
            continue

        try:
            dataset = freeze_backtest_session(
                session_date=session_date,
                universe=universe,
                bars_by_instrument=bars_by_instrument,
            )
            result = run_gap_pullback_backtest(
                dataset,
                active_config,
                PaperExecutionPolicy(max_volume_participation_pct=Decimal("1")),
                assumed_spread_bps=request.assumed_spread_bps,
                max_hold_minutes=request.max_hold_minutes,
                max_concurrent_positions=strategy.risk.max_positions,
                risk_profile=strategy.risk,
                initial_cash=current_cash,
                research_policy_resolver=research_policy_resolver,
            )
            pnl = sum(
                (trade.pnl_per_share * trade.entry_fill_quantity for trade in result.trades),
                Decimal("0"),
            )
            current_cash += pnl
            days.append(
                StrategyRangeBacktestDay(
                    session_date=session_date,
                    status="backtested",
                    universe_id=universe.universe_id,
                    universe_evaluation_time=universe.evaluation_time,
                    universe_origin=origin,
                    fidelity=fidelity,
                    fidelity_warnings=fidelity_warnings,
                    strategy_fidelity_adjustments=fidelity_adjustments,
                    candidate_count=result.summary.candidate_count,
                    starting_cash=starting_cash,
                    ending_cash=current_cash,
                    pnl=pnl,
                    trigger_count=result.summary.trigger_count,
                    trade_count=result.summary.trade_count,
                    result=result,
                )
            )

        except ValueError as exc:
            days.append(
                StrategyRangeBacktestDay(
                    session_date=session_date,
                    status="error",
                    universe_id=universe.universe_id,
                    universe_evaluation_time=universe.evaluation_time,
                    universe_origin=origin,
                    fidelity=fidelity,
                    fidelity_warnings=fidelity_warnings,
                    strategy_fidelity_adjustments=fidelity_adjustments,
                    candidate_count=len(universe.candidates),
                    starting_cash=starting_cash,
                    ending_cash=current_cash,
                    detail=str(exc),
                )
            )

        report_progress(session_date)

    trades = [trade for day in days if day.result is not None for trade in day.result.trades]
    covered_days = [day for day in days if day.status in {"backtested", "no_candidates"}]
    exact_sessions = sum(day.status == "backtested" and day.universe_origin == "captured" for day in days)
    reconstructed_sessions = sum(
        day.status in {"backtested", "no_candidates"} and day.universe_origin == "reconstructed"
        for day in days
    )
    covered_sessions = len(covered_days)
    total_pnl = current_cash - request.initial_cash if covered_sessions else None
    return_pct = (total_pnl / request.initial_cash) * Decimal("100") if total_pnl is not None else None
    expectancy = (
        sum((trade.r_multiple for trade in trades), Decimal("0")) / Decimal(len(trades))
        if trades
        else None
    )
    return StrategyRangeBacktestResult(
        strategy_id=strategy.strategy_id,
        strategy_kind=strategy.strategy_kind,
        strategy_version=strategy.strategy_version,
        start_date=request.start_date,
        end_date=request.end_date,
        universe_scan_time_et=scan_time,
        universe_cutoff_et=scan_time,
        universe_mode=request.universe_mode,
        initial_cash=request.initial_cash,
        ending_cash=current_cash,
        pnl=total_pnl,
        return_pct=return_pct,
        requested_trading_sessions=len(sessions),
        covered_sessions=covered_sessions,
        exact_sessions=exact_sessions,
        reconstructed_sessions=reconstructed_sessions,
        no_candidate_sessions=sum(day.status == "no_candidates" for day in days),
        missing_universe_sessions=sum(day.status == "missing_universe" for day in days),
        data_unavailable_sessions=sum(day.status == "data_unavailable" for day in days),
        error_sessions=sum(day.status == "error" for day in days),
        candidate_count=sum(day.candidate_count for day in days if day.status == "backtested"),
        trigger_count=sum(day.trigger_count for day in days),
        trade_count=len(trades),
        win_count=sum(trade.r_multiple > 0 for trade in trades),
        loss_count=sum(trade.r_multiple < 0 for trade in trades),
        expectancy_r=expectancy,
        result_quality=_result_quality(exact_sessions, reconstructed_sessions, covered_sessions),
        days=tuple(days),
    )
