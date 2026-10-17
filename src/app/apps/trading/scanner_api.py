from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field

from app.persistence.errors import RevisionConflict

from .scanner_repository import ScannerRunActive
from .scanner import TradingScannerDefinition, TradingScannerResult, TradingScannerRun
from .scanner_manager import TradingScannerManager, default_scanner_manager
from .scanner_repository import TradingScannerRepository, default_scanner_repository
from .screener_words import ScreenProposal, propose_screen

logger = logging.getLogger(__name__)


class ScreenFromWordsRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)


class ScannerDefinitionListResponse(BaseModel):
    scanners: list[TradingScannerDefinition]


class ScannerRunListResponse(BaseModel):
    runs: list[TradingScannerRun]


class ScannerResultListResponse(BaseModel):
    results: list[TradingScannerResult]


class ScannerRunCancelResponse(BaseModel):
    ok: Literal[True]
    run_id: str
    status: Literal["cancellation_requested"]


RepositoryFactory = Callable[[], TradingScannerRepository]
ManagerFactory = Callable[[], TradingScannerManager]


def create_trading_scanner_router(
    repository_factory: RepositoryFactory = default_scanner_repository,
    manager_factory: ManagerFactory = default_scanner_manager,
    propose: Callable[[str], ScreenProposal] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/trading/scanners", tags=["trading-scanners"])

    @router.post("/propose", response_model=ScreenProposal)
    async def propose_from_words(request: ScreenFromWordsRequest) -> Any:
        """Screener rules for a description (TVP-9.4): a proposal for the user to edit and run, never run here."""
        try:
            return await asyncio.to_thread(propose or propose_screen, request.text)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            logger.warning("screen_from_words_failed", exc_info=True)
            raise HTTPException(status_code=502, detail="the research model could not propose a screen; try again or write the rules") from exc

    @router.get("", response_model=ScannerDefinitionListResponse)
    def list_scanners(limit: int = Query(default=100, ge=1, le=200)):
        return ScannerDefinitionListResponse(
            scanners=repository_factory().list_definitions(limit)
        )

    @router.post("", response_model=TradingScannerDefinition, status_code=201)
    def create_scanner(definition: TradingScannerDefinition):
        try:
            return repository_factory().create_definition(definition)
        except Exception as exc:
            if "duplicate" in str(exc).lower() or "unique" in str(exc).lower():
                # Not the database's message: it names constraints and values (WP-10.5).
                raise HTTPException(status_code=409, detail="scanner_definition_exists") from exc
            raise

    @router.put("/{scanner_id}", response_model=TradingScannerDefinition)
    def update_scanner(
        scanner_id: str,
        definition: TradingScannerDefinition,
        if_match: int = Header(alias="If-Match", ge=1),
    ):
        if definition.scanner_id != scanner_id:
            raise HTTPException(status_code=422, detail="scanner_id_mismatch")
        try:
            return repository_factory().update_definition(
                scanner_id,
                definition,
                expected_revision=if_match,
            )
        except RevisionConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/{scanner_id}/runs", response_model=TradingScannerRun, status_code=202)
    async def start_run(scanner_id: str):
        try:
            return await manager_factory().start_run(scanner_id)
        except ScannerRunActive as exc:
            # One run at a time per scanner (TVP-9.2): the caller waits for the working one.
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/runs", response_model=ScannerRunListResponse)
    def list_runs(
        scanner_id: str | None = Query(default=None, max_length=200),
        limit: int = Query(default=100, ge=1, le=500),
    ):
        return ScannerRunListResponse(
            runs=repository_factory().list_runs(scanner_id=scanner_id, limit=limit)
        )

    @router.post(
        "/runs/{run_id}/cancel",
        response_model=ScannerRunCancelResponse,
        status_code=202,
    )
    async def cancel_run(run_id: str) -> ScannerRunCancelResponse:
        await manager_factory().cancel_run(run_id)
        return ScannerRunCancelResponse(
            ok=True, run_id=run_id, status="cancellation_requested"
        )

    @router.get("/runs/{run_id}/results", response_model=ScannerResultListResponse)
    def list_results(
        run_id: str,
        limit: int = Query(default=500, ge=1, le=500),
    ):
        return ScannerResultListResponse(
            results=repository_factory().list_results(run_id, limit)
        )

    return router
