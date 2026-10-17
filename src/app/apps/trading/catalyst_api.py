from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from .catalyst_evidence import (
    CatalystEvidence,
    CatalystShadowClassification,
    capture_catalyst_evidence,
)
from .catalyst_repository import TradingCatalystRepository, default_catalyst_repository
from .catalyst_shadow import generate_catalyst_shadow_classification


class CatalystEvidenceCaptureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(min_length=1, max_length=200)
    instrument_id: str = Field(min_length=3, max_length=200)
    source_type: Literal["sec", "company", "news", "manual"]
    source_locator: str = Field(min_length=1, max_length=2000)
    published_at: datetime
    headline: str | None = Field(default=None, max_length=2000)
    raw_text: str = Field(min_length=1, max_length=100_000)
    facts: dict[str, Any] = Field(default_factory=dict)


class CatalystEvidenceListResponse(BaseModel):
    evidence: list[CatalystEvidence]


class CatalystClassificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instrument_id: str = Field(min_length=3, max_length=200)
    evidence_ids: list[str] = Field(default_factory=list, min_length=1, max_length=50)
    model: str | None = Field(default=None, max_length=200)


RepositoryFactory = Callable[[], TradingCatalystRepository]
Classifier = Callable[..., CatalystShadowClassification]


def create_trading_catalyst_router(
    repository_factory: RepositoryFactory = default_catalyst_repository,
    classifier: Callable[..., CatalystShadowClassification] = generate_catalyst_shadow_classification,
) -> APIRouter:
    router = APIRouter(prefix="/api/trading/catalysts", tags=["trading-catalysts"])

    @router.post("/evidence", response_model=CatalystEvidence, status_code=201)
    async def capture(request: CatalystEvidenceCaptureRequest):
        try:
            evidence = capture_catalyst_evidence(
                evidence_id=request.evidence_id,
                instrument_id=request.instrument_id,
                source_type=request.source_type,
                source_locator=request.source_locator,
                published_at=request.published_at,
                raw_text=request.raw_text,
                headline=request.headline,
                facts=request.facts,
            )
            await asyncio.to_thread(repository_factory().save_evidence, evidence)
            return evidence
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get("/evidence/{instrument_id:path}", response_model=CatalystEvidenceListResponse)
    async def list_evidence(
        instrument_id: str,
        limit: int = Query(default=100, ge=1, le=500),
    ):
        return CatalystEvidenceListResponse(
            evidence=await asyncio.to_thread(
                repository_factory().list_evidence,
                instrument_id,
                limit,
            )
        )

    @router.post("/classify-shadow", response_model=CatalystShadowClassification)
    async def classify_shadow(request: CatalystClassificationRequest):
        try:
            available = await asyncio.to_thread(
                repository_factory().list_evidence,
                request.instrument_id,
                500,
            )
            requested = set(request.evidence_ids)
            evidence = [item for item in available if item.evidence_id in requested]
            found = {item.evidence_id for item in evidence}
            missing = sorted(requested - found)
            if missing:
                raise ValueError(f"catalyst_evidence_not_found: {','.join(missing)}")
            return await asyncio.to_thread(
                classifier,
                evidence,
                model=request.model,
            )
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return router
