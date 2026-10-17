from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import date
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from .paper_analytics import PaperAnalyticsOverview, PaperSimulationEpoch, TradingPaperAnalytics
from .paper_journal import PaperTradeJournalResponse, TradingPaperJournal


class PaperEpochListResponse(BaseModel):
    epochs: list[PaperSimulationEpoch]


AnalyticsFactory = Callable[[], TradingPaperAnalytics]
JournalFactory = Callable[[], TradingPaperJournal]


def default_paper_analytics() -> TradingPaperAnalytics:
    return TradingPaperAnalytics()


def default_paper_journal() -> TradingPaperJournal:
    return TradingPaperJournal()


def create_trading_paper_analytics_router(
    analytics_factory: AnalyticsFactory = default_paper_analytics,
    journal_factory: JournalFactory = default_paper_journal,
) -> APIRouter:
    router = APIRouter(prefix="/api/trading/paper-analytics", tags=["trading-paper-analytics"])

    @router.get("/epochs", response_model=PaperEpochListResponse)
    async def list_epochs(account_id: str = Query(min_length=1)) -> PaperEpochListResponse:
        try:
            epochs = await asyncio.to_thread(analytics_factory().list_epochs, account_id)
            return PaperEpochListResponse(epochs=epochs)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/overview", response_model=PaperAnalyticsOverview)
    async def overview(
        account_id: str = Query(min_length=1),
        strategy_id: str | None = Query(default=None),
        epoch_id: str | None = Query(default=None),
        mode: Literal["all", "shadow", "auto_paper"] = Query(default="shadow"),
        start_date: date | None = Query(default=None),
        end_date: date | None = Query(default=None),
        rolling_window: int = Query(default=20, ge=5, le=200),
    ) -> PaperAnalyticsOverview:
        if start_date and end_date and end_date < start_date:
            raise HTTPException(status_code=422, detail="analytics_end_date_precedes_start_date")
        try:
            return await asyncio.to_thread(
                analytics_factory().overview,
                account_id,
                strategy_id=strategy_id,
                epoch_id=epoch_id,
                mode=mode,
                start_date=start_date,
                end_date=end_date,
                rolling_window=rolling_window,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get("/journal", response_model=PaperTradeJournalResponse)
    async def journal(
        account_id: str = Query(min_length=1),
        strategy_id: str | None = Query(default=None),
        epoch_id: str | None = Query(default=None),
        start_date: date | None = Query(default=None),
        end_date: date | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=200),
    ) -> PaperTradeJournalResponse:
        if start_date and end_date and end_date < start_date:
            raise HTTPException(status_code=422, detail="journal_end_date_precedes_start_date")
        try:
            return await asyncio.to_thread(
                journal_factory().list_entries,
                account_id,
                strategy_id=strategy_id,
                epoch_id=epoch_id,
                start_date=start_date,
                end_date=end_date,
                limit=limit,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return router


__all__ = [
    "PaperEpochListResponse",
    "create_trading_paper_analytics_router",
]
