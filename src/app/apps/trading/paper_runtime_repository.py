from __future__ import annotations

from decimal import Decimal

from .paper import PaperFill, PaperMarketObservation, paper_unrealized_pnl
from .paper_repository import TradingPaperRepository


class TradingPaperRuntimeRepository(TradingPaperRepository):
    """Production paper repository with mark refresh for non-filling observations.

    The base repository owns the atomic fill transaction. When an observation does
    not fill an order, this subclass still refreshes any existing position mark so
    unrealized P&L remains reproducible from persisted positions and observations.
    """

    def process_observation(
        self,
        account_id: str,
        observation: PaperMarketObservation,
    ) -> list[PaperFill]:
        fills = super().process_observation(account_id, observation)
        if fills:
            return fills
        with self.uow_factory() as uow:
            position = uow.connection.execute(
                """
                SELECT quantity, average_cost
                  FROM omnix_trading_paper_positions
                 WHERE workspace_id = %s AND account_id = %s AND instrument_id = %s
                 FOR UPDATE
                """,
                (
                    self.context.workspace_id,
                    account_id,
                    observation.instrument_id,
                ),
            ).fetchone()
            if position is not None:
                quantity = Decimal(position[0])
                average_cost = Decimal(position[1])
                unrealized = paper_unrealized_pnl(
                    quantity,
                    average_cost,
                    observation.price,
                )
                uow.connection.execute(
                    """
                    UPDATE omnix_trading_paper_positions
                       SET last_price = %s, unrealized_pnl = %s,
                           updated_at = CURRENT_TIMESTAMP
                     WHERE workspace_id = %s AND account_id = %s AND instrument_id = %s
                    """,
                    (
                        observation.price,
                        unrealized,
                        self.context.workspace_id,
                        account_id,
                        observation.instrument_id,
                    ),
                )
            uow.commit()
        return fills


def default_runtime_paper_repository() -> TradingPaperRuntimeRepository:
    return TradingPaperRuntimeRepository()
