from __future__ import annotations

import logging

import math
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.security.tenant_context import RequestTenant, TenantContext
from app.persistence.unit_of_work import unit_of_work

from .strategy_repository import StrategyEvent, TradingStrategyRepository
from .strategy_v2_qualification import (
    V2_PROSPECTIVE_START,
    V2_QUALIFICATION_EVENT_TYPES,
    V2ProspectiveQualification,
    evaluate_v2_prospective_qualification,
)
from app.apps.trading.us_equity_calendar import EASTERN as _ET

logger = logging.getLogger(__name__)

_ONE_SIDED_90_Z = Decimal("1.2815515655446004")


class PaperSimulationEpoch(BaseModel):
    model_config = ConfigDict(frozen=True)

    account_id: str
    epoch_id: str
    ordinal: int
    initial_cash: Decimal
    started_at: datetime
    ended_at: datetime | None = None
    is_current: bool
    end_reason: str | None = None


class PaperEquityPoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    epoch_id: str
    observed_at: datetime
    cash: Decimal
    equity: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    gross_exposure: Decimal
    risk_at_stop: Decimal


class PaperAnalyticsTrade(BaseModel):
    model_config = ConfigDict(frozen=True)

    trade_id: str
    source: Literal["auto_paper", "shadow_replay"]
    strategy_id: str
    strategy_version: str | None = None
    profile_fingerprint: str | None = None
    epoch_id: str | None = None
    universe_id: str | None = None
    instrument_id: str
    session_date: date
    entry_time: datetime
    exit_time: datetime
    exit_reason: str | None = None
    quantity: Decimal | None = None
    realized_pnl: Decimal | None = None
    r_result: Decimal
    mae_r: Decimal | None = None
    mfe_r: Decimal | None = None
    signal_to_executable_bps: Decimal | None = None
    fill_slippage_bps: Decimal | None = None
    implementation_shortfall_bps: Decimal | None = None
    initial_stop: Decimal | None = None
    initial_target: Decimal | None = None
    setup_features: dict[str, Any] = Field(default_factory=dict)


class PaperPerformanceSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    trade_count: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: Decimal | None = None
    expectancy_r: Decimal | None = None
    total_r: Decimal = Decimal("0")
    profit_factor: Decimal | None = None
    average_mae_r: Decimal | None = None
    average_mfe_r: Decimal | None = None
    max_drawdown_r: Decimal | None = None


class PaperModeComparison(BaseModel):
    model_config = ConfigDict(frozen=True)

    shadow: PaperPerformanceSummary = Field(default_factory=PaperPerformanceSummary)
    auto_paper: PaperPerformanceSummary = Field(default_factory=PaperPerformanceSummary)
    expectancy_delta_r: Decimal | None = None


class PaperDailyR(BaseModel):
    model_config = ConfigDict(frozen=True)

    session_date: date
    r_result: Decimal
    trade_count: int


class PaperDrawdownPoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    observed_at: datetime
    drawdown: Decimal
    unit: Literal["R", "percent"]


class PaperRollingExpectancyPoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    observed_at: datetime
    sample_size: int
    expectancy_r: Decimal
    one_sided_90_lcb_r: Decimal | None = None


class PaperRDistributionBucket(BaseModel):
    model_config = ConfigDict(frozen=True)

    label: str
    minimum_r: Decimal | None = None
    maximum_r: Decimal | None = None
    count: int


class PaperMaeMfePoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    trade_id: str
    instrument_id: str
    session_date: date
    r_result: Decimal
    mae_r: Decimal
    mfe_r: Decimal
    risk_dollars: Decimal | None = None
    exit_reason: str | None = None


class PaperFunnelStage(BaseModel):
    model_config = ConfigDict(frozen=True)

    stage: str
    count: int
    conversion_from_previous: Decimal | None = None
    dominant_drop_reason: str | None = None
    dominant_drop_count: int = 0


class PaperExecutionSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    trade_count: int = 0
    average_signal_to_executable_bps: Decimal | None = None
    average_fill_slippage_bps: Decimal | None = None
    average_implementation_shortfall_bps: Decimal | None = None


class PaperFactorBucket(BaseModel):
    model_config = ConfigDict(frozen=True)

    label: str
    count: int
    expectancy_r: Decimal
    win_rate: Decimal


class PaperFactorStudy(BaseModel):
    model_config = ConfigDict(frozen=True)

    factor: str
    buckets: list[PaperFactorBucket]


class PaperAnalyticsOverview(BaseModel):
    model_config = ConfigDict(frozen=True)

    account_id: str
    strategy_id: str | None = None
    epoch_id: str | None = None
    mode: Literal["all", "shadow", "auto_paper"] = "shadow"
    start_date: date | None = None
    end_date: date | None = None
    rolling_window: int = 20
    epochs: list[PaperSimulationEpoch] = Field(default_factory=list)
    qualification: V2ProspectiveQualification | None = None
    summary: PaperPerformanceSummary = Field(default_factory=PaperPerformanceSummary)
    mode_comparison: PaperModeComparison = Field(default_factory=PaperModeComparison)
    equity: list[PaperEquityPoint] = Field(default_factory=list)
    drawdown: list[PaperDrawdownPoint] = Field(default_factory=list)
    daily_r: list[PaperDailyR] = Field(default_factory=list)
    rolling_expectancy: list[PaperRollingExpectancyPoint] = Field(default_factory=list)
    r_distribution: list[PaperRDistributionBucket] = Field(default_factory=list)
    mae_mfe: list[PaperMaeMfePoint] = Field(default_factory=list)
    funnel: list[PaperFunnelStage] = Field(default_factory=list)
    execution: PaperExecutionSummary = Field(default_factory=PaperExecutionSummary)
    factors: list[PaperFactorStudy] = Field(default_factory=list)
    recent_trades: list[PaperAnalyticsTrade] = Field(default_factory=list)
    archived_strategy_count: int = 0


def _decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        logger.debug("suppressed error in %s", "_decimal", exc_info=True)
        return None


def _aware_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _mean(values: list[Decimal]) -> Decimal | None:
    return sum(values, Decimal("0")) / Decimal(len(values)) if values else None


def _sample_stdev(values: list[Decimal]) -> Decimal | None:
    if len(values) < 2:
        return None
    mean = _mean(values)
    assert mean is not None
    variance = sum(((value - mean) ** 2 for value in values), Decimal("0")) / Decimal(len(values) - 1)
    return Decimal(str(math.sqrt(float(variance))))


def _one_sided_90_lcb(values: list[Decimal]) -> Decimal | None:
    if len(values) < 2:
        return None
    mean = _mean(values)
    stdev = _sample_stdev(values)
    if mean is None or stdev is None:
        return None
    return mean - _ONE_SIDED_90_Z * stdev / Decimal(str(math.sqrt(len(values))))


def performance_summary(trades: list[PaperAnalyticsTrade]) -> PaperPerformanceSummary:
    values = [trade.r_result for trade in trades]
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value < 0]
    equity = Decimal("0")
    peak = Decimal("0")
    max_drawdown = Decimal("0")
    for value in values:
        equity += value
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    gross_profit = sum(wins, Decimal("0"))
    gross_loss = abs(sum(losses, Decimal("0")))
    mae = [trade.mae_r for trade in trades if trade.mae_r is not None]
    mfe = [trade.mfe_r for trade in trades if trade.mfe_r is not None]
    return PaperPerformanceSummary(
        trade_count=len(trades),
        wins=len(wins),
        losses=len(losses),
        win_rate=Decimal(len(wins)) / Decimal(len(trades)) if trades else None,
        expectancy_r=_mean(values),
        total_r=sum(values, Decimal("0")),
        profit_factor=(gross_profit / gross_loss if gross_loss > 0 else None),
        average_mae_r=_mean(mae),
        average_mfe_r=_mean(mfe),
        max_drawdown_r=max_drawdown if trades else None,
    )


def rolling_expectancy(
    trades: list[PaperAnalyticsTrade],
    window: int,
) -> list[PaperRollingExpectancyPoint]:
    ordered = sorted(trades, key=lambda item: (item.exit_time, item.trade_id))
    points: list[PaperRollingExpectancyPoint] = []
    for index in range(len(ordered)):
        start = max(0, index + 1 - window)
        sample = ordered[start : index + 1]
        values = [item.r_result for item in sample]
        expectation = _mean(values)
        if expectation is None:
            continue
        points.append(
            PaperRollingExpectancyPoint(
                observed_at=ordered[index].exit_time,
                sample_size=len(sample),
                expectancy_r=expectation,
                one_sided_90_lcb_r=_one_sided_90_lcb(values),
            )
        )
    return points


def r_distribution(trades: list[PaperAnalyticsTrade]) -> list[PaperRDistributionBucket]:
    definitions: list[tuple[str, Decimal | None, Decimal | None]] = [
        ("< -1R", None, Decimal("-1")),
        ("-1R to -0.5R", Decimal("-1"), Decimal("-0.5")),
        ("-0.5R to 0R", Decimal("-0.5"), Decimal("0")),
        ("0R to +0.5R", Decimal("0"), Decimal("0.5")),
        ("+0.5R to +1R", Decimal("0.5"), Decimal("1")),
        ("+1R to +1.5R", Decimal("1"), Decimal("1.5")),
        ("+1.5R to +2R", Decimal("1.5"), Decimal("2")),
        (">= +2R", Decimal("2"), None),
    ]
    buckets: list[PaperRDistributionBucket] = []
    for label, minimum, maximum in definitions:
        count = sum(
            1
            for trade in trades
            if (minimum is None or trade.r_result >= minimum)
            and (maximum is None or trade.r_result < maximum)
        )
        buckets.append(
            PaperRDistributionBucket(
                label=label,
                minimum_r=minimum,
                maximum_r=maximum,
                count=count,
            )
        )
    return buckets


def _strategy_drawdown(trades: list[PaperAnalyticsTrade]) -> list[PaperDrawdownPoint]:
    equity = Decimal("0")
    peak = Decimal("0")
    points: list[PaperDrawdownPoint] = []
    for trade in sorted(trades, key=lambda item: (item.exit_time, item.trade_id)):
        equity += trade.r_result
        peak = max(peak, equity)
        points.append(
            PaperDrawdownPoint(
                observed_at=trade.exit_time,
                drawdown=peak - equity,
                unit="R",
            )
        )
    return points


def _account_drawdown(points: list[PaperEquityPoint]) -> list[PaperDrawdownPoint]:
    peak: Decimal | None = None
    output: list[PaperDrawdownPoint] = []
    for point in points:
        peak = point.equity if peak is None else max(peak, point.equity)
        drawdown = Decimal("0") if not peak else (peak - point.equity) / peak * Decimal("100")
        output.append(
            PaperDrawdownPoint(
                observed_at=point.observed_at,
                drawdown=drawdown,
                unit="percent",
            )
        )
    return output


_FUNNEL_STAGES = (
    "DISCOVERED",
    "BASIC MARKET FILTER PASS",
    "RESEARCH / SUPPLY PASS",
    "STRUCTURE FORMED",
    "ENTRY READY",
    "EXECUTION ELIGIBLE",
    "RISK ELIGIBLE",
    "ORDER SUBMITTED",
    "FILLED",
)


def _event_stage(event: StrategyEvent) -> int:
    payload = event.payload or {}
    execution = raw_execution if isinstance(raw_execution := payload.get("execution"), dict) else {}
    explicit = payload.get("lifecycle_stage")
    if isinstance(explicit, int) and 0 <= explicit <= 8:
        return explicit
    if event.event_type == "protection" and event.state == "active":
        return 8
    if event.event_type == "entry_order_submitted":
        return 7
    if event.event_type in {"risk_decision", "risk_approved"} or (
        event.reason_code is not None and "RISK_APPROVED" in event.reason_code
    ):
        return 6
    if event.event_type == "shadow_execution" and bool(execution.get("execution_eligible")):
        return 5
    if event.state == "entry_ready":
        return 4

    transitions = {str(item) for item in payload.get("transitions", []) if isinstance(item, str)}
    if "higher_low_confirmed" in transitions or event.state in {
        "higher_low_confirmed", "vwap_reclaim", "lower_high_break", "breakout_hold",
    }:
        return 3

    basic_rejections = {
        "GAP_BELOW_MINIMUM", "PRICE_OUT_OF_RANGE", "PREMARKET_DOLLAR_VOLUME_LOW",
        "TOD_RVOL_MISSING", "TOD_RVOL_LOW", "SPREAD_MISSING", "SPREAD_TOO_WIDE",
    }
    research_rejections = {
        "CATALYST_EVIDENCE_REQUIRED", "DILUTION_SUPPLY_RISK", "FLOAT_OUTSIDE_REQUIRED_RANGE",
    }
    if event.reason_code in basic_rejections:
        return 0
    if event.reason_code in research_rejections:
        return 1
    if "qualified_gap" in transitions or event.state in {
        "qualified_gap", "opening_impulse", "first_pullback", "first_low_confirmed",
        "bounce_high_confirmed", "second_pullback",
    }:
        return 2
    if event.state == "research_reviewed" or event.event_type in {"research_llm", "research_fact"}:
        return 2
    if event.state != "rejected" and event.event_type != "rejection":
        return 1
    return 0


def lifecycle_funnel(events: list[StrategyEvent]) -> list[PaperFunnelStage]:
    lifecycles: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for event in sorted(events, key=lambda item: (item.observed_at, item.event_id)):
        session = str(event.payload.get("session_date") or event.observed_at.astimezone(_ET).date())
        universe = str(event.payload.get("universe_id") or event.payload.get("universe_source") or "unscoped")
        profile = str(event.payload.get("profile_fingerprint") or event.payload.get("strategy_version") or "legacy")
        key = (profile, session, universe, event.instrument_id)
        lifecycle = lifecycles.setdefault(key, {"rank": 0, "reasons": []})
        lifecycle["rank"] = max(int(lifecycle["rank"]), _event_stage(event))
        if event.reason_code and (
            event.event_type == "rejection"
            or event.state == "rejected"
            or "REJECT" in event.reason_code
            or "BLOCK" in event.reason_code
            or "LOW" in event.reason_code
            or "MISSING" in event.reason_code
        ):
            reasons = lifecycle["reasons"]
            assert isinstance(reasons, list)
            reasons.append(event.reason_code)

    output: list[PaperFunnelStage] = []
    previous: int | None = None
    for rank, name in enumerate(_FUNNEL_STAGES):
        count = sum(1 for lifecycle in lifecycles.values() if int(lifecycle["rank"]) >= rank)
        stopped = [lifecycle for lifecycle in lifecycles.values() if int(lifecycle["rank"]) == rank]
        reason_counts = Counter(
            reason
            for lifecycle in stopped
            for reason in lifecycle["reasons"]
            if isinstance(reason, str)
        )
        dominant = reason_counts.most_common(1)[0] if reason_counts else None
        output.append(
            PaperFunnelStage(
                stage=name,
                count=count,
                conversion_from_previous=(
                    Decimal(count) / Decimal(previous) if previous not in (None, 0) else None
                ),
                dominant_drop_reason=dominant[0] if dominant else None,
                dominant_drop_count=dominant[1] if dominant else 0,
            )
        )
        previous = count
    return output


_FACTOR_DEFINITIONS: dict[str, tuple[str, tuple[Decimal, ...]]] = {
    "gap_pct": ("Gap %", (Decimal("30"), Decimal("50"), Decimal("100"))),
    "tod_rvol": ("TOD RVOL", (Decimal("5"), Decimal("10"), Decimal("20"), Decimal("50"))),
    "float_shares": ("Float", (Decimal("2000000"), Decimal("10000000"), Decimal("30000000"))),
    "spread_bps": ("Spread bps", (Decimal("50"), Decimal("100"), Decimal("150"), Decimal("250"))),
    "pullback_pct": ("Pullback %", (Decimal("8"), Decimal("15"), Decimal("25"), Decimal("55"))),
    "quality_score": ("Quality score", (Decimal("3"), Decimal("5"), Decimal("7"), Decimal("9"))),
}


def _feature_value(features: dict[str, Any], key: str) -> Decimal | None:
    direct = _decimal(features.get(key))
    if direct is not None:
        return direct
    aliases = {
        "pullback_pct": ("pullback_depth_pct", "pullback_depth"),
        "spread_bps": ("entry_spread_bps",),
        "tod_rvol": ("rvol",),
    }
    for alias in aliases.get(key, ()):
        value = _decimal(features.get(alias))
        if value is not None:
            return value
    return None


def factor_studies(trades: list[PaperAnalyticsTrade]) -> list[PaperFactorStudy]:
    studies: list[PaperFactorStudy] = []
    for key, (label, boundaries) in _FACTOR_DEFINITIONS.items():
        grouped: dict[str, list[Decimal]] = {}
        for trade in trades:
            value = _feature_value(trade.setup_features, key)
            if value is None:
                continue
            lower: Decimal | None = None
            upper: Decimal | None = None
            for boundary in boundaries:
                if value < boundary:
                    upper = boundary
                    break
                lower = boundary
            if lower is None and upper is not None:
                bucket_label = f"< {upper}"
            elif upper is None and lower is not None:
                bucket_label = f">= {lower}"
            else:
                bucket_label = f"{lower}–{upper}"
            grouped.setdefault(bucket_label, []).append(trade.r_result)
        if not grouped:
            continue
        buckets = []
        for bucket_label, values in grouped.items():
            expectancy = _mean(values) or Decimal("0")
            wins = sum(1 for value in values if value > 0)
            buckets.append(
                PaperFactorBucket(
                    label=bucket_label,
                    count=len(values),
                    expectancy_r=expectancy,
                    win_rate=Decimal(wins) / Decimal(len(values)),
                )
            )
        studies.append(PaperFactorStudy(factor=label, buckets=buckets))
    return studies


class TradingPaperAnalytics:
    context = RequestTenant()
    def __init__(
        self,
        *,
        context: TenantContext | None = None,
        uow_factory=unit_of_work,
    ) -> None:
        self.context = context
        self.uow_factory = uow_factory

    def list_epochs(self, account_id: str) -> list[PaperSimulationEpoch]:
        with self.uow_factory() as uow:
            rows = uow.connection.execute(
                """
                SELECT account_id, epoch_id, ordinal, initial_cash, started_at,
                       ended_at, is_current, end_reason
                  FROM omnix_trading_paper_simulation_epochs
                 WHERE workspace_id = %s AND account_id = %s
                 ORDER BY ordinal DESC
                """,
                (self.context.workspace_id, account_id),
            ).fetchall()
        return [
            PaperSimulationEpoch(
                account_id=str(row[0]),
                epoch_id=str(row[1]),
                ordinal=int(row[2]),
                initial_cash=Decimal(row[3]),
                started_at=row[4],
                ended_at=row[5],
                is_current=bool(row[6]),
                end_reason=str(row[7]) if row[7] is not None else None,
            )
            for row in rows
        ]

    def _equity(
        self,
        account_id: str,
        *,
        epoch_id: str | None,
        start_date: date | None,
        end_date: date | None,
    ) -> list[PaperEquityPoint]:
        clauses = ["workspace_id = %s", "account_id = %s"]
        params: list[Any] = [self.context.workspace_id, account_id]
        if epoch_id:
            clauses.append("epoch_id = %s")
            params.append(epoch_id)
        if start_date:
            clauses.append("observed_at >= %s")
            params.append(datetime.combine(start_date, time.min, tzinfo=_ET).astimezone(timezone.utc))
        if end_date:
            clauses.append("observed_at < %s")
            params.append(datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=_ET).astimezone(timezone.utc))
        where = " AND ".join(clauses)
        with self.uow_factory() as uow:
            rows = uow.connection.execute(
                f"""
                SELECT DISTINCT ON (epoch_id, date_trunc('minute', observed_at))
                       epoch_id, observed_at, cash, equity, realized_pnl,
                       unrealized_pnl, gross_exposure, risk_at_stop
                  FROM omnix_trading_paper_equity_snapshots
                 WHERE {where}
                 ORDER BY epoch_id, date_trunc('minute', observed_at), observed_at DESC
                """,
                tuple(params),
            ).fetchall()
        points = [
            PaperEquityPoint(
                epoch_id=str(row[0]),
                observed_at=row[1],
                cash=Decimal(row[2]),
                equity=Decimal(row[3]),
                realized_pnl=Decimal(row[4]),
                unrealized_pnl=Decimal(row[5]),
                gross_exposure=Decimal(row[6]),
                risk_at_stop=Decimal(row[7]),
            )
            for row in rows
        ]
        return sorted(points, key=lambda item: item.observed_at)

    def _auto_paper_trades(
        self,
        account_id: str,
        *,
        strategy_id: str | None,
        epoch_id: str | None,
        start_date: date | None,
        end_date: date | None,
    ) -> list[PaperAnalyticsTrade]:
        clauses = ["workspace_id = %s", "account_id = %s"]
        params: list[Any] = [self.context.workspace_id, account_id]
        if strategy_id:
            clauses.append("strategy_id = %s")
            params.append(strategy_id)
        if epoch_id:
            clauses.append("epoch_id = %s")
            params.append(epoch_id)
        if start_date:
            clauses.append("session_date >= %s")
            params.append(start_date)
        if end_date:
            clauses.append("session_date <= %s")
            params.append(end_date)
        with self.uow_factory() as uow:
            rows = uow.connection.execute(
                f"""
                SELECT trade_id, strategy_id, strategy_version, profile_fingerprint,
                       epoch_id, universe_id, instrument_id, session_date,
                       entry_time, exit_time, exit_reason, quantity, realized_pnl,
                       realized_r, mae_r, mfe_r, signal_to_executable_bps,
                       fill_slippage_bps, implementation_shortfall_bps,
                       initial_stop, initial_target, setup_features
                  FROM omnix_trading_paper_trade_records
                 WHERE {' AND '.join(clauses)}
                 ORDER BY entry_time, trade_id
                """,
                tuple(params),
            ).fetchall()
        return [
            PaperAnalyticsTrade(
                trade_id=str(row[0]),
                source="auto_paper",
                strategy_id=str(row[1] or "manual"),
                strategy_version=str(row[2]) if row[2] is not None else None,
                profile_fingerprint=str(row[3]) if row[3] is not None else None,
                epoch_id=str(row[4]),
                universe_id=str(row[5]) if row[5] is not None else None,
                instrument_id=str(row[6]),
                session_date=row[7],
                entry_time=row[8],
                exit_time=row[9],
                exit_reason=str(row[10]) if row[10] is not None else None,
                quantity=Decimal(row[11]) if row[11] is not None else None,
                realized_pnl=Decimal(row[12]) if row[12] is not None else None,
                r_result=Decimal(row[13]) if row[13] is not None else Decimal("0"),
                mae_r=Decimal(row[14]) if row[14] is not None else None,
                mfe_r=Decimal(row[15]) if row[15] is not None else None,
                signal_to_executable_bps=Decimal(row[16]) if row[16] is not None else None,
                fill_slippage_bps=Decimal(row[17]) if row[17] is not None else None,
                implementation_shortfall_bps=Decimal(row[18]) if row[18] is not None else None,
                initial_stop=Decimal(row[19]) if row[19] is not None else None,
                initial_target=Decimal(row[20]) if row[20] is not None else None,
                setup_features=row[21] if isinstance(row[21], dict) else {},
            )
            for row in rows
        ]

    def _shadow_trades(
        self,
        account_id: str,
        strategy_id: str,
        *,
        start_date: date | None,
        end_date: date | None,
    ) -> list[PaperAnalyticsTrade]:
        with self.uow_factory() as uow:
            config = uow.connection.execute(
                """
                SELECT account_id, strategy_version
                  FROM omnix_trading_strategy_configs
                 WHERE workspace_id = %s AND strategy_id = %s
                """,
                (self.context.workspace_id, strategy_id),
            ).fetchone()
            if config is None or str(config[0]) != account_id:
                return []
            clauses = [
                "workspace_id = %s",
                "strategy_id = %s",
                "event_type = 'v2_shadow_replay_trade'",
            ]
            params: list[Any] = [self.context.workspace_id, strategy_id]
            if start_date:
                clauses.append("(payload ->> 'session_date')::date >= %s")
                params.append(start_date)
            if end_date:
                clauses.append("(payload ->> 'session_date')::date <= %s")
                params.append(end_date)
            rows = uow.connection.execute(
                f"""
                SELECT event_id, instrument_id, payload
                  FROM omnix_trading_strategy_events
                 WHERE {' AND '.join(clauses)}
                 ORDER BY (payload ->> 'entry_time')::timestamptz, event_id
                """,
                tuple(params),
            ).fetchall()
        trades: list[PaperAnalyticsTrade] = []
        for row in rows:
            payload = row[2] if isinstance(row[2], dict) else {}
            entry_time = _aware_datetime(payload.get("entry_time"))
            exit_time = _aware_datetime(payload.get("exit_time"))
            r_result = _decimal(payload.get("r_result"))
            raw_session = payload.get("session_date")
            if entry_time is None or exit_time is None or r_result is None or raw_session is None:
                continue
            try:
                session_date = date.fromisoformat(str(raw_session))
            except ValueError:
                continue
            trades.append(
                PaperAnalyticsTrade(
                    trade_id=str(row[0]),
                    source="shadow_replay",
                    strategy_id=strategy_id,
                    strategy_version=str(config[1]),
                    profile_fingerprint=str(payload.get("profile_fingerprint") or "") or None,
                    universe_id=str(payload.get("universe_id") or "") or None,
                    instrument_id=str(row[1]),
                    session_date=session_date,
                    entry_time=entry_time,
                    exit_time=exit_time,
                    exit_reason=str(payload.get("exit_reason") or "") or None,
                    r_result=r_result,
                    mae_r=_decimal(payload.get("mae_r")),
                    mfe_r=_decimal(payload.get("mfe_r")),
                )
            )
        return trades

    def _events(
        self,
        strategy_id: str,
        *,
        start_date: date | None,
        end_date: date | None,
    ) -> list[StrategyEvent]:
        start = datetime.combine(start_date or date(2000, 1, 1), time.min, tzinfo=_ET).astimezone(timezone.utc)
        end = datetime.combine((end_date or date.today()) + timedelta(days=1), time.min, tzinfo=_ET).astimezone(timezone.utc)
        repository = TradingStrategyRepository(context=self.context, uow_factory=self.uow_factory)
        with self.uow_factory() as uow:
            rows = uow.connection.execute(
                """
                SELECT strategy_id, event_id, run_id, instrument_id, event_type,
                       state, reason_code, observed_at, idempotency_key, payload
                  FROM omnix_trading_strategy_events
                 WHERE workspace_id = %s AND strategy_id = %s
                   AND observed_at >= %s AND observed_at < %s
                 ORDER BY observed_at, event_id
                 LIMIT 50000
                """,
                (self.context.workspace_id, strategy_id, start, end),
            ).fetchall()
        del repository
        return [
            StrategyEvent(
                strategy_id=str(row[0]),
                event_id=str(row[1]),
                run_id=str(row[2]) if row[2] is not None else None,
                instrument_id=str(row[3]),
                event_type=str(row[4]),
                state=str(row[5]),
                reason_code=str(row[6]) if row[6] is not None else None,
                observed_at=row[7],
                idempotency_key=str(row[8]),
                payload=row[9] if isinstance(row[9], dict) else {},
            )
            for row in rows
        ]

    def _qualification(self, strategy_id: str | None) -> V2ProspectiveQualification | None:
        if not strategy_id:
            return None
        repository = TradingStrategyRepository(context=self.context, uow_factory=self.uow_factory)
        try:
            strategy = repository.get_config(strategy_id)
        except ValueError:
            return None
        if strategy.config.strategy_version != "2.0.0":
            return None
        start = datetime.combine(V2_PROSPECTIVE_START, time.min, tzinfo=timezone.utc)
        events = repository.events_by_types_between(
            strategy_id,
            event_types=V2_QUALIFICATION_EVENT_TYPES,
            start_time=start,
            end_time=datetime.now(timezone.utc) + timedelta(seconds=1),
            limit=20_000,
        )
        return evaluate_v2_prospective_qualification(strategy, events)

    def _archived_strategy_count(self, account_id: str) -> int:
        with self.uow_factory() as uow:
            row = uow.connection.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM omnix_trading_strategy_configs
                      WHERE workspace_id = %s AND account_id = %s AND archived_at IS NOT NULL)
                  + (SELECT COUNT(*) FROM omnix_trading_strategy_archives
                      WHERE workspace_id = %s AND account_id = %s)
                """,
                (
                    self.context.workspace_id, account_id,
                    self.context.workspace_id, account_id,
                ),
            ).fetchone()
        return int(row[0]) if row is not None else 0

    def overview(
        self,
        account_id: str,
        *,
        strategy_id: str | None = None,
        epoch_id: str | None = None,
        mode: Literal["all", "shadow", "auto_paper"] = "shadow",
        start_date: date | None = None,
        end_date: date | None = None,
        rolling_window: int = 20,
    ) -> PaperAnalyticsOverview:
        epochs = self.list_epochs(account_id)
        equity = self._equity(
            account_id,
            epoch_id=epoch_id,
            start_date=start_date,
            end_date=end_date,
        )
        auto_trades = self._auto_paper_trades(
            account_id,
            strategy_id=strategy_id,
            epoch_id=epoch_id,
            start_date=start_date,
            end_date=end_date,
        )
        shadow_trades = (
            self._shadow_trades(
                account_id,
                strategy_id,
                start_date=start_date,
                end_date=end_date,
            )
            if strategy_id
            else []
        )
        if mode == "auto_paper":
            trades = list(auto_trades)
        elif mode == "shadow":
            trades = list(shadow_trades)
        else:
            # Retained for API compatibility only. The UI deliberately exposes
            # the two regimes separately and displays this comparison instead.
            trades = [*shadow_trades, *auto_trades]
        trades.sort(key=lambda item: (item.exit_time, item.trade_id))

        shadow_summary = performance_summary(shadow_trades)
        auto_summary = performance_summary(auto_trades)
        delta = None
        if shadow_summary.expectancy_r is not None and auto_summary.expectancy_r is not None:
            delta = auto_summary.expectancy_r - shadow_summary.expectancy_r

        daily: dict[date, list[Decimal]] = {}
        for trade in trades:
            daily.setdefault(trade.session_date, []).append(trade.r_result)
        daily_r = [
            PaperDailyR(
                session_date=session,
                r_result=sum(values, Decimal("0")),
                trade_count=len(values),
            )
            for session, values in sorted(daily.items())
        ]

        mae_mfe = [
            PaperMaeMfePoint(
                trade_id=trade.trade_id,
                instrument_id=trade.instrument_id,
                session_date=trade.session_date,
                r_result=trade.r_result,
                mae_r=trade.mae_r,
                mfe_r=trade.mfe_r,
                exit_reason=trade.exit_reason,
            )
            for trade in trades
            if trade.mae_r is not None and trade.mfe_r is not None
        ]
        signal_exec = [trade.signal_to_executable_bps for trade in auto_trades if trade.signal_to_executable_bps is not None]
        fill_slip = [trade.fill_slippage_bps for trade in auto_trades if trade.fill_slippage_bps is not None]
        shortfall = [trade.implementation_shortfall_bps for trade in auto_trades if trade.implementation_shortfall_bps is not None]
        events = self._events(strategy_id, start_date=start_date, end_date=end_date) if strategy_id else []

        return PaperAnalyticsOverview(
            account_id=account_id,
            strategy_id=strategy_id,
            epoch_id=epoch_id,
            mode=mode,
            start_date=start_date,
            end_date=end_date,
            rolling_window=rolling_window,
            epochs=epochs,
            qualification=self._qualification(strategy_id),
            summary=performance_summary(trades),
            mode_comparison=PaperModeComparison(
                shadow=shadow_summary,
                auto_paper=auto_summary,
                expectancy_delta_r=delta,
            ),
            equity=equity,
            drawdown=_strategy_drawdown(trades) if trades else _account_drawdown(equity),
            daily_r=daily_r,
            rolling_expectancy=rolling_expectancy(trades, rolling_window),
            r_distribution=r_distribution(trades),
            mae_mfe=mae_mfe,
            funnel=lifecycle_funnel(events) if events else [],
            execution=PaperExecutionSummary(
                trade_count=len(auto_trades),
                average_signal_to_executable_bps=_mean(signal_exec),
                average_fill_slippage_bps=_mean(fill_slip),
                average_implementation_shortfall_bps=_mean(shortfall),
            ),
            factors=factor_studies(auto_trades),
            recent_trades=list(reversed(trades[-50:])),
            archived_strategy_count=self._archived_strategy_count(account_id),
        )


__all__ = [
    "PaperAnalyticsOverview",
    "PaperAnalyticsTrade",
    "PaperModeComparison",
    "PaperSimulationEpoch",
    "TradingPaperAnalytics",
    "factor_studies",
    "lifecycle_funnel",
    "performance_summary",
    "r_distribution",
    "rolling_expectancy",
]
