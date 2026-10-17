from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from app.security.tenant_context import RequestTenant, TenantContext
from app.persistence.unit_of_work import unit_of_work


class PaperTradeJournalEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_id: str
    run_id: str | None = None
    event_type: str
    state: str
    reason_code: str | None = None
    observed_at: datetime


class PaperTradeJournalEntry(BaseModel):
    """Read-only journal projection of the canonical AUTO PAPER trade record."""

    model_config = ConfigDict(frozen=True)

    trade_id: str
    account_id: str
    epoch_id: str
    strategy_id: str
    strategy_version: str | None = None
    strategy_revision: int | None = None
    strategy_run_id: str | None = None
    profile_fingerprint: str | None = None
    universe_id: str | None = None
    instrument_id: str
    session_date: date
    entry_time: datetime
    exit_time: datetime
    holding_seconds: int
    entry_signal_event_id: str | None = None
    entry_order_id: str
    exit_order_id: str
    entry_fill_ids: list[str] = Field(default_factory=list)
    exit_fill_ids: list[str] = Field(default_factory=list)
    session_id: str | None = None
    setup_id: str | None = None
    trade_intent_id: str | None = None
    risk_decision_id: str | None = None
    protection_id: str | None = None
    lifecycle_state: str
    review_state: str
    average_entry_price: Decimal
    average_exit_price: Decimal
    quantity: Decimal
    initial_risk_dollars: Decimal | None = None
    initial_stop: Decimal | None = None
    initial_target: Decimal | None = None
    realized_pnl: Decimal
    r_result: Decimal | None = None
    mae_r: Decimal | None = None
    mfe_r: Decimal | None = None
    signal_to_executable_bps: Decimal | None = None
    fill_slippage_bps: Decimal | None = None
    implementation_shortfall_bps: Decimal | None = None
    exit_reason: str | None = None
    setup_features: dict[str, Any] = Field(default_factory=dict)
    execution_features: dict[str, Any] = Field(default_factory=dict)
    outcome: Literal["win", "loss", "flat"]
    automatic_observations: list[str] = Field(default_factory=list)
    events: list[PaperTradeJournalEvent] = Field(default_factory=list)


class PaperTradeJournalResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    account_id: str
    strategy_id: str | None = None
    epoch_id: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    entries: list[PaperTradeJournalEntry] = Field(default_factory=list)


def _decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def _json_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None]


def _record(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _number_text(value: Decimal | None, *, places: int = 3, signed: bool = False) -> str:
    if value is None:
        return "—"
    rendered = f"{value:.{places}f}"
    if signed and value > 0:
        return f"+{rendered}"
    return rendered


def automatic_trade_observations(
    *,
    realized_pnl: Decimal,
    r_result: Decimal | None,
    mae_r: Decimal | None,
    mfe_r: Decimal | None,
    signal_to_executable_bps: Decimal | None,
    fill_slippage_bps: Decimal | None,
    implementation_shortfall_bps: Decimal | None,
    initial_stop: Decimal | None,
    initial_target: Decimal | None,
    initial_risk_dollars: Decimal | None,
    holding_seconds: int,
    exit_reason: str | None,
    setup_features: dict[str, Any],
) -> list[str]:
    """Create deterministic journal observations from persisted trade evidence.

    These are factual summaries only. They do not infer causality, quality, or a
    future recommendation and therefore remain safe to regenerate at any time.
    """

    outcome = "win" if realized_pnl > 0 else "loss" if realized_pnl < 0 else "flat"
    observations = [
        f"Outcome: {outcome}; realized P&L {_number_text(realized_pnl, places=2, signed=True)}"
        + (f"; {_number_text(r_result, places=3, signed=True)}R" if r_result is not None else ""),
        f"Holding time: {holding_seconds} seconds"
        + (f"; exit reason: {exit_reason}" if exit_reason else ""),
    ]
    if mae_r is not None or mfe_r is not None:
        observations.append(
            f"Excursion: MAE {_number_text(mae_r, places=3, signed=True)}R; "
            f"MFE {_number_text(mfe_r, places=3, signed=True)}R"
        )
    if any(value is not None for value in (
        signal_to_executable_bps,
        fill_slippage_bps,
        implementation_shortfall_bps,
    )):
        observations.append(
            "Execution: signal→executable "
            f"{_number_text(signal_to_executable_bps, places=2, signed=True)} bps; "
            f"fill slippage {_number_text(fill_slippage_bps, places=2, signed=True)} bps; "
            "implementation shortfall "
            f"{_number_text(implementation_shortfall_bps, places=2, signed=True)} bps"
        )
    if initial_stop is not None or initial_target is not None or initial_risk_dollars is not None:
        observations.append(
            f"Initial plan: stop {_number_text(initial_stop, places=4)}; "
            f"target {_number_text(initial_target, places=4)}; "
            f"risk ${_number_text(initial_risk_dollars, places=2)}"
        )
    quality = setup_features.get("quality_score")
    gap = setup_features.get("gap_pct")
    l1 = setup_features.get("l1")
    b1 = setup_features.get("b1")
    l2 = setup_features.get("l2")
    structure_parts: list[str] = []
    if quality is not None:
        structure_parts.append(f"quality {quality}")
    if gap is not None:
        structure_parts.append(f"gap {gap}%")
    if l1 is not None:
        structure_parts.append(f"L1 {l1}")
    if b1 is not None:
        structure_parts.append(f"B1 {b1}")
    if l2 is not None:
        structure_parts.append(f"L2 {l2}")
    if structure_parts:
        observations.append("Setup snapshot: " + "; ".join(structure_parts))
    return observations


class TradingPaperJournal:
    context = RequestTenant()
    def __init__(
        self,
        *,
        context: TenantContext | None = None,
        uow_factory=unit_of_work,
    ) -> None:
        self.context = context
        self.uow_factory = uow_factory

    def list_entries(
        self,
        account_id: str,
        *,
        strategy_id: str | None = None,
        epoch_id: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int = 100,
    ) -> PaperTradeJournalResponse:
        clauses = ["trade.workspace_id = %s", "trade.account_id = %s"]
        params: list[Any] = [self.context.workspace_id, account_id]
        if strategy_id:
            clauses.append("trade.strategy_id = %s")
            params.append(strategy_id)
        if epoch_id:
            clauses.append("trade.epoch_id = %s")
            params.append(epoch_id)
        if start_date:
            clauses.append("trade.session_date >= %s")
            params.append(start_date)
        if end_date:
            clauses.append("trade.session_date <= %s")
            params.append(end_date)
        params.append(max(1, min(int(limit), 200)))

        with self.uow_factory() as uow:
            rows = uow.connection.execute(
                f"""
                SELECT
                    trade.trade_id,
                    trade.account_id,
                    trade.epoch_id,
                    COALESCE(trade.strategy_id, 'manual'),
                    trade.strategy_version,
                    trade.strategy_revision,
                    trade.strategy_run_id,
                    trade.profile_fingerprint,
                    trade.universe_id,
                    trade.instrument_id,
                    trade.session_date,
                    trade.entry_time,
                    trade.exit_time,
                    trade.holding_seconds,
                    trade.entry_signal_event_id,
                    trade.entry_order_id,
                    trade.exit_order_id,
                    trade.entry_fill_ids,
                    trade.exit_fill_ids,
                    trade.session_id,
                    trade.setup_id,
                    trade.trade_intent_id,
                    trade.risk_decision_id,
                    trade.protection_id,
                    trade.lifecycle_state,
                    trade.review_state,
                    trade.average_entry_price,
                    trade.average_exit_price,
                    trade.quantity,
                    trade.initial_risk_dollars,
                    trade.initial_stop,
                    trade.initial_target,
                    trade.realized_pnl,
                    trade.realized_r,
                    trade.mae_r,
                    trade.mfe_r,
                    trade.signal_to_executable_bps,
                    trade.fill_slippage_bps,
                    trade.implementation_shortfall_bps,
                    trade.exit_reason,
                    trade.setup_features,
                    trade.execution_features,
                    COALESCE((
                        SELECT JSONB_AGG(
                            JSONB_BUILD_OBJECT(
                                'event_id', event.event_id,
                                'run_id', event.run_id,
                                'event_type', event.event_type,
                                'state', event.state,
                                'reason_code', event.reason_code,
                                'observed_at', event.observed_at
                            ) ORDER BY event.observed_at, event.event_id
                        )
                          FROM omnix_trading_strategy_events AS event
                         WHERE event.workspace_id = trade.workspace_id
                           AND event.strategy_id = trade.strategy_id
                           AND (
                               (trade.setup_id IS NOT NULL AND event.setup_id = trade.setup_id)
                               OR (
                                   trade.trade_intent_id IS NOT NULL
                                   AND event.trade_intent_id = trade.trade_intent_id
                               )
                           )
                    ), '[]'::JSONB) AS lifecycle_events
                  FROM omnix_trading_paper_trade_records AS trade
                 WHERE {' AND '.join(clauses)}
                 ORDER BY trade.exit_time DESC, trade.trade_id DESC
                 LIMIT %s
                """,
                tuple(params),
            ).fetchall()

        entries: list[PaperTradeJournalEntry] = []
        for row in rows:
            realized_pnl = Decimal(row[32])
            r_result = _decimal(row[33])
            mae_r = _decimal(row[34])
            mfe_r = _decimal(row[35])
            signal_to_executable_bps = _decimal(row[36])
            fill_slippage_bps = _decimal(row[37])
            implementation_shortfall_bps = _decimal(row[38])
            initial_risk_dollars = _decimal(row[29])
            initial_stop = _decimal(row[30])
            initial_target = _decimal(row[31])
            setup_features = _record(row[40])
            raw_events = row[42] if isinstance(row[42], list) else []
            events = [
                PaperTradeJournalEvent(
                    event_id=str(item.get("event_id")),
                    run_id=str(item.get("run_id")) if item.get("run_id") is not None else None,
                    event_type=str(item.get("event_type")),
                    state=str(item.get("state")),
                    reason_code=(
                        str(item.get("reason_code"))
                        if item.get("reason_code") is not None
                        else None
                    ),
                    observed_at=cast(Any, item.get("observed_at")),
                )
                for item in raw_events
                if isinstance(item, dict) and item.get("event_id") and item.get("observed_at")
            ]
            outcome: Literal["win", "loss", "flat"] = (
                "win" if realized_pnl > 0 else "loss" if realized_pnl < 0 else "flat"
            )
            entries.append(
                PaperTradeJournalEntry(
                    trade_id=str(row[0]),
                    account_id=str(row[1]),
                    epoch_id=str(row[2]),
                    strategy_id=str(row[3]),
                    strategy_version=str(row[4]) if row[4] is not None else None,
                    strategy_revision=int(row[5]) if row[5] is not None else None,
                    strategy_run_id=str(row[6]) if row[6] is not None else None,
                    profile_fingerprint=str(row[7]) if row[7] is not None else None,
                    universe_id=str(row[8]) if row[8] is not None else None,
                    instrument_id=str(row[9]),
                    session_date=row[10],
                    entry_time=row[11],
                    exit_time=row[12],
                    holding_seconds=int(row[13]),
                    entry_signal_event_id=str(row[14]) if row[14] is not None else None,
                    entry_order_id=str(row[15]),
                    exit_order_id=str(row[16]),
                    entry_fill_ids=_json_list(row[17]),
                    exit_fill_ids=_json_list(row[18]),
                    session_id=str(row[19]) if row[19] is not None else None,
                    setup_id=str(row[20]) if row[20] is not None else None,
                    trade_intent_id=str(row[21]) if row[21] is not None else None,
                    risk_decision_id=str(row[22]) if row[22] is not None else None,
                    protection_id=str(row[23]) if row[23] is not None else None,
                    lifecycle_state=str(row[24]),
                    review_state=str(row[25]),
                    average_entry_price=Decimal(row[26]),
                    average_exit_price=Decimal(row[27]),
                    quantity=Decimal(row[28]),
                    initial_risk_dollars=initial_risk_dollars,
                    initial_stop=initial_stop,
                    initial_target=initial_target,
                    realized_pnl=realized_pnl,
                    r_result=r_result,
                    mae_r=mae_r,
                    mfe_r=mfe_r,
                    signal_to_executable_bps=signal_to_executable_bps,
                    fill_slippage_bps=fill_slippage_bps,
                    implementation_shortfall_bps=implementation_shortfall_bps,
                    exit_reason=str(row[39]) if row[39] is not None else None,
                    setup_features=setup_features,
                    execution_features=_record(row[41]),
                    outcome=outcome,
                    automatic_observations=automatic_trade_observations(
                        realized_pnl=realized_pnl,
                        r_result=r_result,
                        mae_r=mae_r,
                        mfe_r=mfe_r,
                        signal_to_executable_bps=signal_to_executable_bps,
                        fill_slippage_bps=fill_slippage_bps,
                        implementation_shortfall_bps=implementation_shortfall_bps,
                        initial_stop=initial_stop,
                        initial_target=initial_target,
                        initial_risk_dollars=initial_risk_dollars,
                        holding_seconds=int(row[13]),
                        exit_reason=str(row[39]) if row[39] is not None else None,
                        setup_features=setup_features,
                    ),
                    events=events,
                )
            )

        return PaperTradeJournalResponse(
            account_id=account_id,
            strategy_id=strategy_id,
            epoch_id=epoch_id,
            start_date=start_date,
            end_date=end_date,
            entries=entries,
        )


__all__ = [
    "PaperTradeJournalEntry",
    "PaperTradeJournalEvent",
    "PaperTradeJournalResponse",
    "TradingPaperJournal",
    "automatic_trade_observations",
]
