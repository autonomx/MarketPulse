from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import AbstractContextManager
from decimal import Decimal
from typing import Any, Protocol, cast

from app.security.tenant_context import RequestTenant, TenantContext
from app.persistence.unit_of_work import PostgresUnitOfWork, unit_of_work

from .backtest import (
    BACKTEST_MARK_TO_MARKET_POLICY,
    BacktestArtifactReference,
    BacktestEquityPoint,
    BacktestLogEntry,
    BacktestRunResult,
    BacktestTrade,
    backtest_economic_breakdown,
    backtest_economic_result_fingerprint,
)
from .replay import FrozenDatasetSnapshot


class UnitOfWorkFactory(Protocol):
    def __call__(self) -> AbstractContextManager[PostgresUnitOfWork]: ...


def _dataset(row) -> FrozenDatasetSnapshot:
    return FrozenDatasetSnapshot(
        dataset_id=str(row[0]),
        instrument_id=str(row[1]),
        requested_binding_id=str(row[2]) if row[2] is not None else None,
        resolved_binding_id=str(row[3]),
        provider=str(row[4]),
        interval=str(row[5]),
        adjustment_mode=str(row[6]),
        session_calendar=str(row[7]),
        exchange_timezone=str(row[8]),
        gap_policy=cast(Any, str(row[9])),
        dataset_fingerprint=str(row[10]),
        source_as_of=row[11],
        bars=tuple(row[12] or []),
        created_at=row[13],
    )


_DATASET_COLUMNS = """
    dataset_id, instrument_id, requested_binding_id, resolved_binding_id,
    provider, interval, adjustment_mode, session_calendar, exchange_timezone,
    gap_policy, dataset_fingerprint, source_as_of, bars, created_at
"""


def _artifact_from_values(values) -> BacktestArtifactReference | None:
    if not values or values[0] is None:
        return None
    return BacktestArtifactReference(
        storage_provider=str(values[0]),
        storage_key=str(values[1]),
        checksum_sha256=str(values[2]),
        byte_size=int(values[3]),
    )


class TradingReplayRepository:
    context = RequestTenant()
    def __init__(
        self,
        *,
        context: TenantContext | None = None,
        uow_factory: UnitOfWorkFactory = unit_of_work,
    ) -> None:
        self.context = context
        self.uow_factory = uow_factory

    def create_dataset(self, snapshot: FrozenDatasetSnapshot) -> FrozenDatasetSnapshot:
        with self.uow_factory() as uow:
            row = uow.connection.execute(
                f"""
                INSERT INTO omnix_trading_datasets (
                    workspace_id, dataset_id, owner_user_id, instrument_id,
                    requested_binding_id, resolved_binding_id, provider, interval,
                    adjustment_mode, session_calendar, exchange_timezone, gap_policy,
                    dataset_fingerprint, source_as_of, bars, bar_count
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s::jsonb, %s
                )
                ON CONFLICT (workspace_id, dataset_fingerprint) DO UPDATE
                    SET dataset_id = omnix_trading_datasets.dataset_id
                RETURNING {_DATASET_COLUMNS}
                """,
                (
                    self.context.workspace_id,
                    snapshot.dataset_id,
                    self.context.user_id,
                    snapshot.instrument_id,
                    snapshot.requested_binding_id,
                    snapshot.resolved_binding_id,
                    snapshot.provider,
                    snapshot.interval,
                    snapshot.adjustment_mode,
                    snapshot.session_calendar,
                    snapshot.exchange_timezone,
                    snapshot.gap_policy,
                    snapshot.dataset_fingerprint,
                    snapshot.source_as_of,
                    json.dumps([bar.model_dump(mode="json") for bar in snapshot.bars]),
                    len(snapshot.bars),
                ),
            ).fetchone()
            uow.commit()
            return _dataset(row)

    def get_dataset(self, dataset_id: str) -> FrozenDatasetSnapshot | None:
        with self.uow_factory() as uow:
            row = uow.connection.execute(
                f"SELECT {_DATASET_COLUMNS} FROM omnix_trading_datasets WHERE workspace_id = %s AND dataset_id = %s",
                (self.context.workspace_id, dataset_id),
            ).fetchone()
            return _dataset(row) if row else None

    def list_datasets(self, limit: int = 100) -> list[FrozenDatasetSnapshot]:
        with self.uow_factory() as uow:
            rows = uow.connection.execute(
                f"SELECT {_DATASET_COLUMNS} FROM omnix_trading_datasets WHERE workspace_id = %s ORDER BY created_at DESC LIMIT %s",
                (self.context.workspace_id, limit),
            ).fetchall()
            return [_dataset(row) for row in rows]

    def save_backtest(self, result: BacktestRunResult) -> BacktestRunResult:
        artifact = result.artifact
        with self.uow_factory() as uow:
            uow.connection.execute(
                """
                INSERT INTO omnix_trading_backtest_runs (
                    workspace_id, run_id, dataset_id, strategy_id, strategy_parameters,
                    execution_policy, formula_version, status, initial_cash,
                    ending_cash, ending_position, ending_mark_price,
                    realized_pnl, unrealized_pnl, final_equity,
                    total_return_percent, max_drawdown_percent,
                    win_rate_percent, exposure_percent, trade_count,
                    mark_to_market_policy, economic_result_fingerprint,
                    artifact_storage_provider, artifact_storage_key,
                    artifact_checksum_sha256, artifact_byte_size,
                    error_message, started_at, finished_at
                ) VALUES (
                    %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    self.context.workspace_id,
                    result.run_id,
                    result.dataset_id,
                    result.strategy_id,
                    json.dumps(result.strategy_parameters),
                    json.dumps(result.execution_policy),
                    result.formula_version,
                    result.status,
                    result.initial_cash,
                    result.ending_cash,
                    result.ending_position,
                    result.ending_mark_price,
                    result.realized_pnl,
                    result.unrealized_pnl,
                    result.final_equity,
                    result.total_return_percent,
                    result.max_drawdown_percent,
                    result.win_rate_percent,
                    result.exposure_percent,
                    result.trade_count,
                    result.mark_to_market_policy,
                    result.economic_result_fingerprint,
                    artifact.storage_provider if artifact else None,
                    artifact.storage_key if artifact else None,
                    artifact.checksum_sha256 if artifact else None,
                    artifact.byte_size if artifact else None,
                    result.error_message,
                    result.started_at,
                    result.finished_at,
                ),
            )
            for trade in result.trades:
                uow.connection.execute(
                    """
                    INSERT INTO omnix_trading_backtest_trades (
                        workspace_id, run_id, trade_index, side,
                        signal_bar_index, fill_bar_index, signal_time,
                        fill_time, quantity, fill_price, commission, cash_after,
                        position_after
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        self.context.workspace_id,
                        result.run_id,
                        trade.trade_index,
                        trade.side,
                        trade.signal_bar_index,
                        trade.fill_bar_index,
                        trade.signal_time,
                        trade.fill_time,
                        trade.quantity,
                        trade.fill_price,
                        trade.commission,
                        trade.cash_after,
                        trade.position_after,
                    ),
                )
            for point in result.equity_curve:
                uow.connection.execute(
                    """
                    INSERT INTO omnix_trading_backtest_equity (
                        workspace_id, run_id, point_index, bar_time, cash,
                        position, equity, drawdown_percent
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        self.context.workspace_id,
                        result.run_id,
                        point.point_index,
                        point.bar_time,
                        point.cash,
                        point.position,
                        point.equity,
                        point.drawdown_percent,
                    ),
                )
            for entry in result.logs:
                uow.connection.execute(
                    """
                    INSERT INTO omnix_trading_backtest_logs (
                        workspace_id, run_id, log_index, bar_time, level,
                        message, payload
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                    """,
                    (
                        self.context.workspace_id,
                        result.run_id,
                        entry.log_index,
                        entry.bar_time,
                        entry.level,
                        entry.message,
                        json.dumps(entry.payload),
                    ),
                )
            uow.commit()
        return result

    def list_backtests(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.uow_factory() as uow:
            rows = uow.connection.execute(
                """
                SELECT run_id, dataset_id, strategy_id, status, initial_cash,
                       ending_cash, ending_position, ending_mark_price,
                       realized_pnl, unrealized_pnl, final_equity,
                       total_return_percent, max_drawdown_percent,
                       win_rate_percent, exposure_percent, trade_count,
                       formula_version, mark_to_market_policy,
                       economic_result_fingerprint, started_at, finished_at,
                       error_message
                  FROM omnix_trading_backtest_runs
                 WHERE workspace_id = %s
                 ORDER BY created_at DESC
                 LIMIT %s
                """,
                (self.context.workspace_id, limit),
            ).fetchall()
            return [
                {
                    "run_id": str(row[0]),
                    "dataset_id": str(row[1]),
                    "strategy_id": str(row[2]),
                    "status": str(row[3]),
                    "initial_cash": str(row[4]),
                    "ending_cash": str(row[5]) if row[5] is not None else None,
                    "ending_position": str(row[6]) if row[6] is not None else None,
                    "ending_mark_price": str(row[7]) if row[7] is not None else None,
                    "realized_pnl": str(row[8]) if row[8] is not None else None,
                    "unrealized_pnl": str(row[9]) if row[9] is not None else None,
                    "final_equity": str(row[10]),
                    "total_return_percent": str(row[11]),
                    "max_drawdown_percent": str(row[12]),
                    "win_rate_percent": str(row[13]),
                    "exposure_percent": str(row[14]),
                    "trade_count": int(row[15]),
                    "formula_version": str(row[16]),
                    "mark_to_market_policy": str(row[17]) if row[17] else None,
                    "economic_result_fingerprint": str(row[18]) if row[18] else None,
                    "started_at": row[19].isoformat(),
                    "finished_at": row[20].isoformat(),
                    "error_message": str(row[21]) if row[21] is not None else None,
                }
                for row in rows
            ]

    def get_backtest(self, run_id: str) -> BacktestRunResult | None:
        with self.uow_factory() as uow:
            run = uow.connection.execute(
                """
                SELECT dataset_id, strategy_id, strategy_parameters, execution_policy,
                       formula_version, status, initial_cash,
                       ending_cash, ending_position, ending_mark_price,
                       realized_pnl, unrealized_pnl,
                       final_equity, total_return_percent, max_drawdown_percent,
                       win_rate_percent, exposure_percent, trade_count,
                       mark_to_market_policy, economic_result_fingerprint,
                       error_message, started_at, finished_at,
                       artifact_storage_provider, artifact_storage_key,
                       artifact_checksum_sha256, artifact_byte_size
                  FROM omnix_trading_backtest_runs
                 WHERE workspace_id = %s AND run_id = %s
                """,
                (self.context.workspace_id, run_id),
            ).fetchone()
            if run is None:
                return None
            dataset = self.get_dataset(str(run[0]))
            if dataset is None:
                return None
            trade_rows = uow.connection.execute(
                """
                SELECT trade_index, side, signal_bar_index, fill_bar_index,
                       signal_time, fill_time, quantity, fill_price,
                       commission, cash_after, position_after
                  FROM omnix_trading_backtest_trades
                 WHERE workspace_id = %s AND run_id = %s ORDER BY trade_index
                """,
                (self.context.workspace_id, run_id),
            ).fetchall()
            equity_rows = uow.connection.execute(
                """
                SELECT point_index, bar_time, cash, position, equity, drawdown_percent
                  FROM omnix_trading_backtest_equity
                 WHERE workspace_id = %s AND run_id = %s ORDER BY point_index
                """,
                (self.context.workspace_id, run_id),
            ).fetchall()
            log_rows = uow.connection.execute(
                """
                SELECT log_index, bar_time, level, message, payload
                  FROM omnix_trading_backtest_logs
                 WHERE workspace_id = %s AND run_id = %s ORDER BY log_index
                """,
                (self.context.workspace_id, run_id),
            ).fetchall()

        trades = tuple(
            BacktestTrade(
                trade_index=int(row[0]),
                side=cast(Any, str(row[1])),
                signal_bar_index=int(row[2]),
                fill_bar_index=int(row[3]),
                signal_time=row[4],
                fill_time=row[5],
                quantity=Decimal(row[6]),
                fill_price=Decimal(row[7]),
                commission=Decimal(row[8]),
                cash_after=Decimal(row[9]),
                position_after=Decimal(row[10]),
            )
            for row in trade_rows
        )
        equity_curve = tuple(
            BacktestEquityPoint(
                point_index=int(row[0]),
                bar_time=row[1],
                cash=Decimal(row[2]),
                position=Decimal(row[3]),
                equity=Decimal(row[4]),
                drawdown_percent=Decimal(row[5]),
            )
            for row in equity_rows
        )
        ending_cash = Decimal(run[7]) if run[7] is not None else (
            equity_curve[-1].cash if equity_curve else Decimal(run[6])
        )
        ending_position = Decimal(run[8]) if run[8] is not None else (
            equity_curve[-1].position if equity_curve else Decimal("0")
        )
        ending_mark_price = Decimal(run[9]) if run[9] is not None else (
            dataset.bars[-1].close if dataset.bars else None
        )
        economics = backtest_economic_breakdown(
            initial_cash=Decimal(run[6]),
            ending_cash=ending_cash,
            ending_position=ending_position,
            ending_mark_price=ending_mark_price,
            trades=trades,
        )
        realized_pnl = Decimal(run[10]) if run[10] is not None else economics.realized_pnl
        unrealized_pnl = Decimal(run[11]) if run[11] is not None else economics.unrealized_pnl
        policy = str(run[18]) if run[18] else BACKTEST_MARK_TO_MARKET_POLICY
        fingerprint = str(run[19]) if run[19] else backtest_economic_result_fingerprint(
            dataset_fingerprint=dataset.dataset_fingerprint,
            strategy_id=str(run[1]),
            strategy_parameters=dict(run[2] or {}),
            execution_policy=dict(run[3] or {}),
            formula_version=str(run[4]),
            status=str(run[5]),
            initial_cash=Decimal(run[6]),
            economics=economics.model_copy(
                update={
                    "realized_pnl": realized_pnl,
                    "unrealized_pnl": unrealized_pnl,
                }
            ),
            final_equity=Decimal(run[12]),
            total_return_percent=Decimal(run[13]),
            max_drawdown_percent=Decimal(run[14]),
            win_rate_percent=Decimal(run[15]),
            exposure_percent=Decimal(run[16]),
            trades=trades,
            equity_curve=equity_curve,
            error_message=str(run[20]) if run[20] is not None else None,
        )
        return BacktestRunResult(
            run_id=run_id,
            dataset_id=str(run[0]),
            dataset_fingerprint=dataset.dataset_fingerprint,
            strategy_id=str(run[1]),
            strategy_parameters=dict(run[2] or {}),
            execution_policy=dict(run[3] or {}),
            formula_version=str(run[4]),
            status=cast(Any, str(run[5])),
            initial_cash=Decimal(run[6]),
            ending_cash=ending_cash,
            ending_position=ending_position,
            ending_mark_price=ending_mark_price,
            realized_pnl=realized_pnl,
            unrealized_pnl=unrealized_pnl,
            final_equity=Decimal(run[12]),
            total_return_percent=Decimal(run[13]),
            max_drawdown_percent=Decimal(run[14]),
            win_rate_percent=Decimal(run[15]),
            exposure_percent=Decimal(run[16]),
            trade_count=int(run[17]),
            mark_to_market_policy=cast(Any, policy),
            economic_result_fingerprint=fingerprint,
            error_message=str(run[20]) if run[20] is not None else None,
            started_at=run[21],
            finished_at=run[22],
            trades=trades,
            equity_curve=equity_curve,
            logs=tuple(
                BacktestLogEntry(
                    log_index=int(row[0]),
                    bar_time=row[1],
                    level=cast(Any, str(row[2])),
                    message=str(row[3]),
                    payload=dict(row[4] or {}),
                )
                for row in log_rows
            ),
            artifact=_artifact_from_values(run[23:27]),
        )


ReplayRepositoryFactory = Callable[[], TradingReplayRepository]


def default_replay_repository() -> TradingReplayRepository:
    return TradingReplayRepository()
