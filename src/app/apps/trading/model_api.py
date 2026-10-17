from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from .bounce_model import BounceFeatureVector, BounceModelScore, score_bounce_probability
from .bounce_model_repository import TradingBounceModelRepository, default_bounce_model_repository
from .bounce_training import (
    BounceModelArtifact,
    BounceTrainingExample,
    fit_bounce_logistic,
    score_fitted_bounce_model,
)
from .bounce_validation import (
    BounceValidationExample,
    BounceValidationMetrics,
    validate_bounce_artifact,
)
from .catalyst_repository import TradingCatalystRepository, default_catalyst_repository


class BounceModelTrainRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_version: str = Field(min_length=1, max_length=200)
    examples: list[BounceTrainingExample] = Field(min_length=20, max_length=20_000)
    trained_at: datetime | None = None
    iterations: int = Field(default=800, ge=1, le=20_000)
    learning_rate: Decimal = Field(default=Decimal("0.05"), gt=0, le=2)
    l2_penalty: Decimal = Field(default=Decimal("0.001"), ge=0, le=10)


class BounceModelListResponse(BaseModel):
    models: list[BounceModelArtifact]


class BounceModelScoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score_id: str = Field(min_length=1, max_length=200)
    instrument_id: str = Field(min_length=3, max_length=200)
    strategy_id: str | None = Field(default=None, max_length=200)
    features: BounceFeatureVector
    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    model_version: str | None = Field(default=None, max_length=200)


class BounceModelValidateRequest(BaseModel):
    """Out-of-sample validation request; validation remains shadow-only."""

    model_config = ConfigDict(extra="forbid")

    model_version: str = Field(min_length=1, max_length=200)
    examples: list[BounceValidationExample] = Field(min_length=2, max_length=50_000)
    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    minimum_examples: int = Field(default=100, ge=1, le=50_000)
    minimum_sessions: int = Field(default=20, ge=1, le=10_000)


ModelRepositoryFactory = Callable[[], TradingBounceModelRepository]
ScoreRepositoryFactory = Callable[[], TradingCatalystRepository]


def create_trading_model_router(
    model_repository_factory: ModelRepositoryFactory = default_bounce_model_repository,
    score_repository_factory: ScoreRepositoryFactory = default_catalyst_repository,
) -> APIRouter:
    router = APIRouter(prefix="/api/trading/models", tags=["trading-models"])

    @router.get("/bounce", response_model=BounceModelListResponse)
    async def list_bounce_models(limit: int = Query(default=50, ge=1, le=500)):
        return BounceModelListResponse(
            models=await asyncio.to_thread(model_repository_factory().list, limit)
        )

    @router.get("/bounce/{model_version}", response_model=BounceModelArtifact)
    async def get_bounce_model(model_version: str):
        try:
            return await asyncio.to_thread(model_repository_factory().get, model_version)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/bounce/train", response_model=BounceModelArtifact, status_code=201)
    async def train_bounce_model(request: BounceModelTrainRequest):
        """Fit and persist a transparent logistic artifact; never change execution."""
        try:
            artifact = await asyncio.to_thread(
                fit_bounce_logistic,
                request.examples,
                model_version=request.model_version,
                trained_at=request.trained_at,
                iterations=request.iterations,
                learning_rate=request.learning_rate,
                l2_penalty=request.l2_penalty,
            )
            return await asyncio.to_thread(model_repository_factory().save, artifact)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post("/bounce/validate-shadow", response_model=BounceValidationMetrics)
    async def validate_bounce_shadow(request: BounceModelValidateRequest):
        """Evaluate locked artifacts on dated OOS examples; never gate paper orders."""
        try:
            artifact = await asyncio.to_thread(
                model_repository_factory().get,
                request.model_version,
            )
            return await asyncio.to_thread(
                validate_bounce_artifact,
                artifact,
                request.examples,
                observed_at=request.observed_at,
                minimum_examples=request.minimum_examples,
                minimum_sessions=request.minimum_sessions,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post("/bounce/score-shadow", response_model=BounceModelScore)
    async def score_bounce_shadow(request: BounceModelScoreRequest):
        """Score only. The result is persisted for evaluation but cannot authorize an order."""
        try:
            if request.model_version:
                artifact = await asyncio.to_thread(
                    model_repository_factory().get,
                    request.model_version,
                )
                score = await asyncio.to_thread(
                    score_fitted_bounce_model,
                    artifact,
                    request.features,
                    observed_at=request.observed_at,
                )
            else:
                score = await asyncio.to_thread(
                    score_bounce_probability,
                    request.features,
                    observed_at=request.observed_at,
                )
            await asyncio.to_thread(
                score_repository_factory().save_model_score,
                score_id=request.score_id,
                strategy_id=request.strategy_id,
                instrument_id=request.instrument_id,
                score=score,
            )
            return score
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return router
