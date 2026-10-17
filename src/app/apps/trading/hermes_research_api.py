from __future__ import annotations

import logging

import asyncio
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from .research.contracts import (
    IssuerIdentity, NoveltyShadowAnnotation, ResearchActionRecord, ResearchValidationReport,
    StrategyResearchFeatures, TradingEvidence, TradingFactSet, TradingResearchReport,
)
from .research.coordinator import TradingResearchCoordinatorResult, create_trading_research_request, run_trading_research
from .research.fact_repository import TradingFactRepository, default_fact_repository
from .research.market_brief import generate_trading_market_brief
from .research.outcome_dataset import attribution_summary
from .research.policy import ResearchPolicyDecision, evaluate_research_policy
from .research.repository import TradingResearchRepository, default_research_repository
from .research.review import Recommendation, create_reviewed_validation_report
from .research.shadow_repository import TradingShadowResearchRepository, default_shadow_repository
from .research.validation import build_validation_report
from app.errors import error_code

logger = logging.getLogger(__name__)


class StartTradingResearchInput(BaseModel):
    model_config=ConfigDict(extra="forbid")
    instrument_id: str=Field(min_length=3,max_length=200)
    strategy_id: str | None=None
    decision_context_at: datetime | None=None
    deadline_seconds: int=Field(default=45,ge=5,le=180)
    max_steps: int=Field(default=8,ge=1,le=20)
    max_queries: int=Field(default=5,ge=0,le=20)
    max_sources: int=Field(default=20,ge=1,le=100)
    max_extracts: int=Field(default=8,ge=0,le=30)
    run_shadow_ai: bool=True


class TradingResearchAuditView(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    instrument_id: str
    as_of: datetime
    identity: IssuerIdentity | None=None
    latest_report: TradingResearchReport | None=None
    report_timeline: tuple[TradingResearchReport,...]=()
    evidence: tuple[TradingEvidence,...]=()
    fact_set: TradingFactSet | None=None
    features: StrategyResearchFeatures | None=None
    shadow: NoveltyShadowAnnotation | None=None
    hermes_actions: tuple[ResearchActionRecord,...]=()


class ValidationInput(BaseModel):
    model_config=ConfigDict(extra="forbid")
    strategy_id: str | None=None
    policy_version: str="trading-research-1"
    minimum_sample: int=Field(default=100,ge=20,le=100000)
    minimum_exact_sample: int=Field(default=50,ge=10,le=100000)


class ReviewValidationInput(BaseModel):
    model_config=ConfigDict(extra="forbid")
    source_validation_id: str=Field(min_length=3,max_length=200)
    policy_version: str="trading-research-1"
    approved_recommendations: dict[str, Recommendation]=Field(min_length=1,max_length=20)
    review_note: str=Field(min_length=10,max_length=2000)
    confirm_execution_authority: Literal[True]


class ResearchPolicyStatus(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    strategy_version: str
    decision_at: datetime
    features: StrategyResearchFeatures | None=None
    validation: ResearchValidationReport | None=None
    decision: ResearchPolicyDecision


def create_trading_hermes_research_router(
    *, repository_factory=default_research_repository,
    fact_repository_factory=default_fact_repository,
    shadow_repository_factory=default_shadow_repository,
) -> APIRouter:
    router=APIRouter(prefix="/api/trading/hermes-research",tags=["trading-hermes-research"])

    @router.post("/start",response_model=TradingResearchCoordinatorResult)
    async def start(request: StartTradingResearchInput):
        try:
            research_request=create_trading_research_request(instrument_id=request.instrument_id,strategy_id=request.strategy_id,
                decision_context_at=request.decision_context_at,deadline_seconds=request.deadline_seconds,max_steps=request.max_steps,
                max_queries=request.max_queries,max_sources=request.max_sources,max_extracts=request.max_extracts)
            repository=repository_factory()
            result=await asyncio.to_thread(run_trading_research,research_request,repository=repository,
                fact_repository=fact_repository_factory(),shadow_repository=shadow_repository_factory(),run_shadow_ai=request.run_shadow_ai)
            completed_at=result.report.research_completed_at or datetime.now(timezone.utc)
            evidence_loader=getattr(repository,"evidence_by_ids_as_of",None)
            if callable(evidence_loader):
                evidence=await asyncio.to_thread(
                    evidence_loader,
                    request.instrument_id,
                    result.report.source_evidence_ids,
                    completed_at,
                )
            else:
                available=await asyncio.to_thread(
                    repository.list_evidence_as_of,
                    request.instrument_id,
                    completed_at,
                    max(200,request.max_sources*4),
                )
                current_ids=set(result.report.source_evidence_ids)
                evidence=[item for item in available if item.evidence_id in current_ids]
            if not evidence:
                return result.model_copy(update={"brief_warning":"No source evidence was available for an AI market brief."})
            try:
                brief=await asyncio.to_thread(generate_trading_market_brief,result.report,result.fact_set,evidence)
            except Exception as exc:
                return result.model_copy(update={
                    "brief_warning":"AI market brief was unavailable; the captured sources remain available.",
                    "warnings":tuple(dict.fromkeys((*result.warnings,f"market_brief_unavailable:{type(exc).__name__}"))),
                })
            return result.model_copy(update={"brief":brief})
        except ValueError as exc: raise HTTPException(status_code=422,detail=str(exc)) from exc
        except RuntimeError as exc:
            logger.warning("hermes_research_unavailable", exc_info=True)
            raise HTTPException(status_code=503, detail=error_code(exc, "hermes_research_unavailable")) from exc

    @router.get("/audit",response_model=TradingResearchAuditView)
    async def audit(instrument_id: str=Query(min_length=3,max_length=200),as_of: datetime | None=None):
        cutoff=(as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
        repo:TradingResearchRepository=repository_factory(); facts:TradingFactRepository=fact_repository_factory(); shadow:TradingShadowResearchRepository=shadow_repository_factory()
        identity=await asyncio.to_thread(repo.identity_as_of,instrument_id,cutoff)
        evidence=await asyncio.to_thread(repo.list_evidence_as_of,instrument_id,cutoff,200)
        latest=await asyncio.to_thread(repo.latest_report_as_of,instrument_id,cutoff)
        evidence_loader=getattr(repo,"evidence_by_ids_as_of",None)
        if latest is not None and latest.source_evidence_ids and callable(evidence_loader):
            current_evidence=await asyncio.to_thread(
                evidence_loader,
                instrument_id,
                latest.source_evidence_ids,
                cutoff,
            )
            merged={item.evidence_id:item for item in current_evidence}
            for item in evidence:
                merged.setdefault(item.evidence_id,item)
            evidence=list(merged.values())[:200]
        timeline=await asyncio.to_thread(repo.report_timeline,instrument_id,100)
        timeline=[item for item in timeline if item.omnix_known_at is not None and item.omnix_known_at<=cutoff]
        fact_set=await asyncio.to_thread(facts.latest_fact_set_as_of,instrument_id,cutoff)
        features=await asyncio.to_thread(facts.research_features_as_of,instrument_id,cutoff)
        shadow_item=await asyncio.to_thread(shadow.latest_as_of,instrument_id,cutoff)
        actions=await asyncio.to_thread(repo.action_trace,latest.hermes_trace_id) if latest and latest.hermes_trace_id else []
        return TradingResearchAuditView(instrument_id=instrument_id,as_of=cutoff,identity=identity,latest_report=latest,
            report_timeline=tuple(timeline),evidence=tuple(evidence),fact_set=fact_set,features=features,shadow=shadow_item,hermes_actions=tuple(actions))

    @router.get("/traces/{trace_id}",response_model=list[ResearchActionRecord])
    async def trace(trace_id: str): return await asyncio.to_thread(repository_factory().action_trace,trace_id)

    @router.get("/attribution")
    async def attribution(strategy_id: str | None=None,limit: int=Query(default=10000,ge=1,le=100000)) -> dict[str,Any]:
        values=await asyncio.to_thread(fact_repository_factory().outcomes,strategy_id,limit)
        return attribution_summary(values)

    @router.post("/validate",response_model=ResearchValidationReport)
    async def validate(request: ValidationInput):
        repo=fact_repository_factory()
        promoted=await asyncio.to_thread(repo.promoted_validation_report,request.policy_version)
        if promoted is not None:
            raise HTTPException(status_code=409,detail="research_policy_version_already_promoted_use_new_version")
        values=await asyncio.to_thread(repo.outcomes,request.strategy_id,100000)
        report=await asyncio.to_thread(build_validation_report,values,policy_version=request.policy_version,
            minimum_sample=request.minimum_sample,minimum_exact_sample=request.minimum_exact_sample)
        await asyncio.to_thread(repo.save_validation_report,report); return report

    @router.post("/validation/review",response_model=ResearchValidationReport)
    async def review_validation(request: ReviewValidationInput):
        repo=fact_repository_factory()
        promoted=await asyncio.to_thread(repo.promoted_validation_report,request.policy_version)
        if promoted is not None:
            raise HTTPException(status_code=409,detail="research_policy_version_already_promoted_use_new_version")
        source=await asyncio.to_thread(repo.latest_validation_report,request.policy_version)
        if source is None:
            raise HTTPException(status_code=404,detail="research_validation_not_found")
        if source.validation_id != request.source_validation_id:
            raise HTTPException(status_code=409,detail="research_validation_not_latest")
        if source.promotion_allowed:
            raise HTTPException(status_code=409,detail="research_validation_already_promoted")
        try:
            reviewed=await asyncio.to_thread(
                create_reviewed_validation_report,
                source,
                approved_recommendations=request.approved_recommendations,
                review_note=request.review_note,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422,detail=str(exc)) from exc
        await asyncio.to_thread(repo.save_validation_report,reviewed)
        return reviewed

    @router.get("/validation/{policy_version}",response_model=ResearchValidationReport | None)
    async def validation(policy_version: str): return await asyncio.to_thread(fact_repository_factory().latest_validation_report,policy_version)

    @router.get("/policy-status",response_model=ResearchPolicyStatus)
    async def policy_status(instrument_id: str,strategy_version: str="1.1.0",decision_at: datetime | None=None,policy_version: str="trading-research-1"):
        at=(decision_at or datetime.now(timezone.utc)).astimezone(timezone.utc); repo=fact_repository_factory()
        features=await asyncio.to_thread(repo.research_features_as_of,instrument_id,at)
        validation=await asyncio.to_thread(
            repo.promoted_validation_report if strategy_version == "1.2.0" else repo.latest_validation_report,
            policy_version,
        )
        decision=evaluate_research_policy(strategy_version=strategy_version,features=features,validation=validation,policy_version=policy_version)
        return ResearchPolicyStatus(strategy_version=strategy_version,decision_at=at,features=features,validation=validation,decision=decision)

    return router
