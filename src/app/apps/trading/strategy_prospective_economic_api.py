from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from .strategy_prospective_economic import (
    PROSPECTIVE_ECONOMIC_EVENT_TYPES,
    PROSPECTIVE_ECONOMIC_HOLDOUT_END,
    PROSPECTIVE_ECONOMIC_HOLDOUT_START,
    PROSPECTIVE_ECONOMIC_START,
    PROSPECTIVE_ECONOMIC_VERSION,
    ProspectiveEconomicStatus,
    evaluate_prospective_economic_status,
    holdout_verdict,
)
from .strategy_repository import (
    StrategyEvent,
    TradingStrategyRepository,
    default_strategy_repository,
)
from .strategy_v2_qualification import v2_profile_fingerprint
from typing import Any


_DIAGNOSTIC_EVENT_TYPES = ("prospective_economic_candidate",)
_ALL_EVIDENCE_EVENT_TYPES = (*PROSPECTIVE_ECONOMIC_EVENT_TYPES, *_DIAGNOSTIC_EVENT_TYPES)


class ProspectiveEconomicEvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    review_note: str = Field(min_length=10, max_length=2_000)


class ProspectiveEconomicHoldoutReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trade_count: int = Field(ge=0, le=10_000)
    win_rate: Decimal = Field(ge=0, le=1)
    expectancy_r: Decimal
    one_sided_90_lcb_r: Decimal | None = None
    max_drawdown_r: Decimal = Field(ge=0)
    artifact_ref: str = Field(min_length=8, max_length=500)
    review_note: str = Field(min_length=10, max_length=2_000)


class ProspectiveEconomicAutoPaperReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    review_note: str = Field(min_length=10, max_length=2_000)


class ProspectiveEconomicEventListResponse(BaseModel):
    events: list[StrategyEvent]


def _key(*parts: object) -> str:
    return hashlib.sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()


def _events(
    repository: TradingStrategyRepository,
    strategy_id: str,
    *,
    limit: int = 50_000,
) -> list[StrategyEvent]:
    start = datetime(
        PROSPECTIVE_ECONOMIC_START.year,
        PROSPECTIVE_ECONOMIC_START.month,
        PROSPECTIVE_ECONOMIC_START.day,
        tzinfo=timezone.utc,
    )
    end = datetime.now(timezone.utc) + timedelta(seconds=1)
    if hasattr(repository, "events_by_types_between"):
        return repository.events_by_types_between(
            strategy_id,
            event_types=_ALL_EVIDENCE_EVENT_TYPES,
            start_time=start,
            end_time=end,
            limit=limit,
        )
    return [
        event for event in repository.recent_events(strategy_id, limit)
        if event.event_type in _ALL_EVIDENCE_EVENT_TYPES
        and start <= event.observed_at.astimezone(timezone.utc) < end
    ]


def _append(
    repository: TradingStrategyRepository,
    *,
    strategy_id: str,
    instrument_id: str,
    event_type: str,
    state: str,
    reason_code: str,
    payload: dict[str, Any],
    identity: tuple[object, ...],
) -> StrategyEvent:
    observed_at = datetime.now(timezone.utc)
    idem = _key(strategy_id, PROSPECTIVE_ECONOMIC_VERSION, event_type, *identity)
    event = StrategyEvent(
        strategy_id=strategy_id,
        event_id=idem[:32],
        run_id=None,
        instrument_id=instrument_id,
        event_type=event_type,
        state=state,
        reason_code=reason_code,
        observed_at=observed_at,
        idempotency_key=idem,
        payload=payload,
    )
    repository.append_event(event)
    return event


def create_trading_strategy_prospective_economic_router() -> APIRouter:
    router = APIRouter(
        prefix="/api/trading/strategies",
        tags=["trading-strategy-prospective-economic"],
    )

    @router.get("/{strategy_id}/prospective-economic", response_model=ProspectiveEconomicStatus)
    async def status(strategy_id: str) -> ProspectiveEconomicStatus:
        try:
            repository = default_strategy_repository()
            strategy = await asyncio.to_thread(repository.get_config, strategy_id)
            if strategy.config.strategy_version != "2.0.0":
                raise ValueError("prospective_economic_requires_strategy_version_2_0_0")
            events = await asyncio.to_thread(_events, repository, strategy_id)
            return await asyncio.to_thread(evaluate_prospective_economic_status, strategy, events)
        except ValueError as exc:
            code = 404 if str(exc) == "strategy_config_not_found" else 422
            raise HTTPException(status_code=code, detail=str(exc)) from exc

    @router.get(
        "/{strategy_id}/prospective-economic/events",
        response_model=ProspectiveEconomicEventListResponse,
    )
    async def evidence_events(
        strategy_id: str,
        limit: int = Query(default=500, ge=1, le=5_000),
    ) -> ProspectiveEconomicEventListResponse:
        repository = default_strategy_repository()
        try:
            await asyncio.to_thread(repository.get_config, strategy_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        rows = await asyncio.to_thread(_events, repository, strategy_id, limit=limit)
        return ProspectiveEconomicEventListResponse(events=list(reversed(rows[-limit:])))

    @router.post("/{strategy_id}/prospective-economic/evaluate", response_model=ProspectiveEconomicStatus)
    async def evaluate_once(
        strategy_id: str,
        request: ProspectiveEconomicEvaluationRequest,
    ) -> ProspectiveEconomicStatus:
        repository = default_strategy_repository()
        try:
            strategy = await asyncio.to_thread(repository.get_config, strategy_id)
            if strategy.config.strategy_version != "2.0.0":
                raise ValueError("prospective_economic_requires_strategy_version_2_0_0")
            events = await asyncio.to_thread(_events, repository, strategy_id)
            current = await asyncio.to_thread(evaluate_prospective_economic_status, strategy, events)
            if current.evaluation_recorded:
                return current
            if not current.sample_ready:
                raise ValueError("prospective_economic_sample_not_ready_for_one_shot_evaluation")
            note = " ".join(request.review_note.split()).strip()
            if len(note) < 10:
                raise ValueError("prospective_economic_evaluation_note_too_short")
            evaluation = _append(
                repository,
                strategy_id=strategy_id,
                instrument_id=f"strategy:{strategy_id}",
                event_type="prospective_economic_evaluation",
                state="passed" if current.quantitative_pass else "failed",
                reason_code=(
                    "PROSPECTIVE_ECONOMIC_ONE_SHOT_PASS"
                    if current.quantitative_pass
                    else "PROSPECTIVE_ECONOMIC_ONE_SHOT_FAIL"
                ),
                payload={
                    "policy_version": PROSPECTIVE_ECONOMIC_VERSION,
                    "profile_fingerprint": current.profile_fingerprint,
                    "evidence_fingerprint": current.evidence_fingerprint,
                    "metrics": current.metrics.model_dump(mode="json"),
                    "thresholds": current.thresholds.model_dump(mode="json"),
                    "passed": current.quantitative_pass,
                    "immutable_one_shot": True,
                    "review_note": note,
                    "execution_authority": False,
                },
                identity=(current.profile_fingerprint,),
            )
            return await asyncio.to_thread(
                evaluate_prospective_economic_status,
                strategy,
                [*events, evaluation],
            )
        except ValueError as exc:
            code = 404 if str(exc) == "strategy_config_not_found" else 422
            raise HTTPException(status_code=code, detail=str(exc)) from exc

    @router.post(
        "/{strategy_id}/prospective-economic/holdout-review",
        response_model=ProspectiveEconomicStatus,
    )
    async def review_sealed_holdout(
        strategy_id: str,
        request: ProspectiveEconomicHoldoutReviewRequest,
    ) -> ProspectiveEconomicStatus:
        repository = default_strategy_repository()
        try:
            strategy = await asyncio.to_thread(repository.get_config, strategy_id)
            events = await asyncio.to_thread(_events, repository, strategy_id)
            current = await asyncio.to_thread(evaluate_prospective_economic_status, strategy, events)
            if not current.evaluation_passed or current.evaluation_event_id is None:
                raise ValueError("prospective_economic_holdout_remains_sealed")
            if current.holdout_reviewed:
                return current
            verdict = holdout_verdict(
                trade_count=request.trade_count,
                win_rate=request.win_rate,
                expectancy_r=request.expectancy_r,
                max_drawdown_r=request.max_drawdown_r,
            )
            approved = verdict in {"ROBUST", "GOLD"}
            note = " ".join(request.review_note.split()).strip()
            event = _append(
                repository,
                strategy_id=strategy_id,
                instrument_id=f"strategy:{strategy_id}",
                event_type="prospective_economic_holdout_review",
                state="passed" if approved else "failed",
                reason_code=f"PROSPECTIVE_ECONOMIC_HOLDOUT_{verdict}",
                payload={
                    "policy_version": PROSPECTIVE_ECONOMIC_VERSION,
                    "profile_fingerprint": current.profile_fingerprint,
                    "evaluation_event_id": current.evaluation_event_id,
                    "evaluation_evidence_fingerprint": current.evidence_fingerprint,
                    "holdout_start": PROSPECTIVE_ECONOMIC_HOLDOUT_START.isoformat(),
                    "holdout_end": PROSPECTIVE_ECONOMIC_HOLDOUT_END.isoformat(),
                    "trade_count": request.trade_count,
                    "win_rate": str(request.win_rate),
                    "expectancy_r": str(request.expectancy_r),
                    "one_sided_90_lcb_r": (
                        str(request.one_sided_90_lcb_r)
                        if request.one_sided_90_lcb_r is not None
                        else None
                    ),
                    "max_drawdown_r": str(request.max_drawdown_r),
                    "holdout_verdict": verdict,
                    "approved": approved,
                    "artifact_ref": request.artifact_ref.strip(),
                    "review_note": note,
                    "execution_authority": False,
                },
                identity=(current.evaluation_event_id,),
            )
            return await asyncio.to_thread(
                evaluate_prospective_economic_status,
                strategy,
                [*events, event],
            )
        except ValueError as exc:
            code = 404 if str(exc) == "strategy_config_not_found" else 422
            raise HTTPException(status_code=code, detail=str(exc)) from exc

    @router.post(
        "/{strategy_id}/prospective-economic/auto-paper-review",
        response_model=ProspectiveEconomicStatus,
    )
    async def approve_auto_paper_research_gate(
        strategy_id: str,
        request: ProspectiveEconomicAutoPaperReviewRequest,
    ) -> ProspectiveEconomicStatus:
        repository = default_strategy_repository()
        try:
            strategy = await asyncio.to_thread(repository.get_config, strategy_id)
            events = await asyncio.to_thread(_events, repository, strategy_id)
            current = await asyncio.to_thread(evaluate_prospective_economic_status, strategy, events)
            if current.auto_paper_reviewed:
                return current
            if not current.soak_passed:
                raise ValueError("prospective_economic_shadow_soak_not_complete")
            note = " ".join(request.review_note.split()).strip()
            if len(note) < 10:
                raise ValueError("prospective_economic_auto_paper_review_note_too_short")
            event = _append(
                repository,
                strategy_id=strategy_id,
                instrument_id=f"strategy:{strategy_id}",
                event_type="prospective_economic_auto_paper_review",
                state="approved",
                reason_code="PROSPECTIVE_ECONOMIC_AUTO_PAPER_REVIEW_APPROVED",
                payload={
                    "policy_version": PROSPECTIVE_ECONOMIC_VERSION,
                    "profile_fingerprint": current.profile_fingerprint,
                    "v2_profile_fingerprint": v2_profile_fingerprint(strategy.config),
                    "pipeline_evidence_fingerprint": current.pipeline_evidence_fingerprint,
                    "approved": True,
                    "review_note": note,
                    "execution_authority": False,
                },
                identity=(current.pipeline_evidence_fingerprint,),
            )
            return await asyncio.to_thread(
                evaluate_prospective_economic_status,
                strategy,
                [*events, event],
            )
        except ValueError as exc:
            code = 404 if str(exc) == "strategy_config_not_found" else 422
            raise HTTPException(status_code=code, detail=str(exc)) from exc

    return router


__all__ = [
    "ProspectiveEconomicAutoPaperReviewRequest",
    "ProspectiveEconomicEvaluationRequest",
    "ProspectiveEconomicHoldoutReviewRequest",
    "create_trading_strategy_prospective_economic_router",
]
