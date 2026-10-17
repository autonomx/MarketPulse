from __future__ import annotations

from app.config.env import env_str, environment

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

from pydantic import ValidationError

from app.apps.trading.trade_logging import trade_log
from app.apps.trading.providers.request_budget import in_provider_lane

from .adapters.company_ir import CompanyIrAdapter
from .adapters.generic_web import GenericWebAdapter
from .adapters.sec_edgar import SecEdgarAdapter
from .contracts import IssuerIdentity, ResearchActionProposal, ResearchActionRecord, TradingResearchRequest, fingerprint
from .hermes_contract import TradingHermesContext, TradingHermesNextActionDecision, evidence_summary, trading_next_action_payload
from .repository import TradingResearchRepository


class NextActionPlanner(Protocol):
    backend: str
    def next_action(self, request: TradingResearchRequest, context: TradingHermesContext) -> Any: ...


class HermesPlanner:
    backend = "hermes"
    def __init__(self, client=None) -> None:
        if client is None:
            from app.providers.hermes_client import HermesSidecarClient
            from app.providers.hermes_status import hermes_runtime_config
            config = hermes_runtime_config()
            client = HermesSidecarClient(base_url=config.base_url, api_key=environment().get("HERMES_API_KEY") or None, timeout=config.timeout_seconds)
        self.client = client

    def next_action(self, request: TradingResearchRequest, context: TradingHermesContext) -> Any:
        """Return exactly one proposal-only semantic trading research action."""
        from app.providers import ChatMessage

        validated_request = TradingResearchRequest.model_validate(request)
        validated_context = TradingHermesContext.model_validate(context)
        return self.client.structured(
            [
                ChatMessage(
                    role="system",
                    content=(
                        "You are a non-executing trading research next-action planner. Return exactly one JSON action matching the supplied schema. "
                        "Never execute anything. Never propose orders, position sizing, broker actions, strategy mutation, shell, files, GitHub, or unlisted operations. "
                        "Use the evidence summary to decide the single highest-value unresolved follow-up, or stop."
                    ),
                ),
                ChatMessage(
                    role="user",
                    content=json.dumps(trading_next_action_payload(validated_request, validated_context), sort_keys=True, default=str),
                ),
            ],
            output_model=TradingHermesNextActionDecision,
            contract_id="hermes.trading_next_action",
            json_mode=True,
            timeout=self.client.timeout,
            error="Hermes did not return a valid trading next-action proposal",
        )


class SafeStopPlanner:
    backend = "local_safe_stop"
    def __init__(self, reason: str = "hermes_unavailable") -> None: self.reason = reason
    def next_action(self, request: TradingResearchRequest, context: TradingHermesContext) -> Any:
        return {"action": {"operation": "stop", "args": {}, "reason": self.reason}, "rationale": self.reason}


def hermes_trading_research_enabled() -> bool:
    def flag(name: str) -> bool:
        return environment().get(name, "0").strip().lower() in {"1", "true", "yes", "on"}

    if not flag("HERMES_ENABLED"):
        return False
    # Trading research is read-only. Reuse the same Hermes switch as chat when
    # no trading-specific override was supplied; an explicit false still
    # provides a safe opt-out for deployments that want Hermes chat only.
    if "OMNIX_TRADING_HERMES_RESEARCH_ENABLED" in environment():
        return flag("OMNIX_TRADING_HERMES_RESEARCH_ENABLED")
    return True


@dataclass(frozen=True)
class ResearchLoopResult:
    trace_id: str
    planner_backend: str
    stop_reason: str
    action_count: int
    evidence_ids: tuple[str, ...]
    warnings: tuple[str, ...] = ()


def _action_id(trace_id: str, step: int, operation: str) -> str:
    return "tra-" + hashlib.sha256(f"{trace_id}|{step}|{operation}".encode()).hexdigest()[:24]


def _evidence_for_ids(
    repository: TradingResearchRepository,
    request: TradingResearchRequest,
    evidence_ids: list[str] | tuple[str, ...],
    known_at: datetime,
):
    ids = tuple(dict.fromkeys(evidence_ids))
    if not ids:
        return []
    loader = getattr(repository, "evidence_by_ids_as_of", None)
    if callable(loader):
        return loader(request.instrument_id, ids, known_at)
    available = repository.list_evidence_as_of(
        request.instrument_id,
        known_at,
        max(200, request.max_sources * 4),
    )
    by_id = {item.evidence_id: item for item in available}
    return [by_id[evidence_id] for evidence_id in ids if evidence_id in by_id]


def _context(
    repository: TradingResearchRepository,
    request: TradingResearchRequest,
    *,
    step: int,
    queries: int,
    extracts: int,
    prior_actions: list[str],
    evidence_ids: list[str],
) -> TradingHermesContext:
    now = datetime.now(timezone.utc)
    evidence = _evidence_for_ids(repository, request, evidence_ids, now)
    unresolved: list[str] = []
    text = " ".join(item.content.lower() for item in evidence)
    if not any(item.source_authority_tier == 1 for item in evidence): unresolved.append("primary_source_confirmation")
    if "warrant" in text and not any(token in text for token in ("exercisable", "expired", "redeemed", "exercised")): unresolved.append("warrant_status")
    if any(token in text for token in ("at-the-market", "atm offering", "sales agreement")) and not any(token in text for token in ("terminated", "exhausted", "remaining")): unresolved.append("atm_status")
    return TradingHermesContext(
        step=step,
        remaining_steps=max(0, request.max_steps-step),
        remaining_queries=max(0, request.max_queries-queries),
        remaining_sources=max(0, request.max_sources-len(evidence)),
        remaining_extracts=max(0, request.max_extracts-extracts),
        evidence=tuple(evidence_summary(item) for item in evidence[-20:]),
        unresolved_facts=tuple(unresolved),
        prior_actions=tuple(prior_actions[-20:]),
    )


def _execute(proposal: ResearchActionProposal, identity: IssuerIdentity, *, sec: SecEdgarAdapter, company: CompanyIrAdapter, web: GenericWebAdapter):
    op = proposal.operation; args = proposal.args
    if op == "sec_find_filings": return sec.find(identity, query=str(args.get("forms") or args.get("query") or ""), limit=int(args.get("limit") or 10))
    if op == "sec_extract_filing": return sec.extract(identity, locator=str(args.get("locator") or ""))
    if op == "company_find_releases": return company.find(identity, query=str(args.get("query") or "") or None, limit=int(args.get("limit") or 8))
    if op == "company_extract_release": return company.extract(identity, locator=str(args.get("locator") or ""))
    if op == "web_search": return web.find(identity, query=str(args.get("query") or "") or None, limit=int(args.get("limit") or 8))
    if op == "web_extract": return web.extract(identity, locator=str(args.get("locator") or ""))
    if op in {"evaluate", "stop"}: return None
    raise ValueError("trading_research_operation_not_allowlisted")


@in_provider_lane("research")
def run_iterative_research(
    request: TradingResearchRequest,
    identity: IssuerIdentity,
    repository: TradingResearchRepository,
    *,
    planner: NextActionPlanner | None = None,
    sec: SecEdgarAdapter | None = None,
    company: CompanyIrAdapter | None = None,
    web: GenericWebAdapter | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    seed_evidence_ids: tuple[str, ...] = (),
) -> ResearchLoopResult:
    active_planner = planner or (HermesPlanner() if hermes_trading_research_enabled() else SafeStopPlanner("hermes_disabled"))
    sec = sec or SecEdgarAdapter(); web = web or GenericWebAdapter(); company = company or CompanyIrAdapter(web)
    trace_id = "htr-" + hashlib.sha256(f"{request.request_id}|{request.instrument_id}|{request.requested_at.isoformat()}".encode()).hexdigest()[:24]
    queries = extracts = 0; prior: list[str] = []; warnings: list[str] = []; stop_reason = "step_budget_exhausted"
    run_evidence_ids = list(dict.fromkeys(seed_evidence_ids))[:request.max_sources]
    for step in range(request.max_steps):
        now = clock().astimezone(timezone.utc)
        if now >= request.deadline_at:
            stop_reason = "deadline_exhausted"; break
        context = _context(
            repository,
            request,
            step=step,
            queries=queries,
            extracts=extracts,
            prior_actions=prior,
            evidence_ids=run_evidence_ids,
        )
        try:
            decision = TradingHermesNextActionDecision.model_validate(active_planner.next_action(request, context))
        except (ValidationError, ValueError, TypeError) as exc:
            warnings.append(f"planner_invalid:{type(exc).__name__}"); stop_reason = "planner_invalid"; break
        except Exception as exc:
            # Keep sidecar transport/HTTP failures inside the research
            # boundary. The deterministic harvest remains valid and the API
            # can return a partial report with an actionable warning.
            warnings.append(f"planner_unavailable:{type(exc).__name__}")
            stop_reason = "planner_unavailable"
            break
        proposal = decision.action
        if proposal.operation not in request.allowed_operations:
            stop_reason = "operation_blocked"; warnings.append(f"blocked:{proposal.operation}"); break
        if proposal.operation == "stop":
            stop_reason = proposal.reason or "planner_stop"; prior.append("stop"); break
        if proposal.operation in {"web_search", "sec_find_filings", "company_find_releases"}:
            if queries >= request.max_queries: stop_reason = "query_budget_exhausted"; break
            queries += 1
        if proposal.operation in {"web_extract", "sec_extract_filing", "company_extract_release"}:
            if extracts >= request.max_extracts: stop_reason = "extract_budget_exhausted"; break
            extracts += 1
        requested_at = now
        action_id = _action_id(trace_id, step, proposal.operation)
        try:
            result = _execute(proposal, identity, sec=sec, company=company, web=web)
            saved_ids: list[str] = []
            detail = "evaluation_checkpoint" if result is None else result.detail
            if result is not None:
                remaining = max(0, request.max_sources - len(run_evidence_ids))
                for item in result.evidence[:remaining]:
                    saved = repository.save_evidence(item)
                    saved_ids.append(saved.evidence_id)
                    if saved.evidence_id not in run_evidence_ids:
                        run_evidence_ids.append(saved.evidence_id)
                warnings.extend(result.warnings)
            completed = clock().astimezone(timezone.utc)
            record = ResearchActionRecord(
                action_id=action_id, trace_id=trace_id, strategy_id=request.strategy_id,
                instrument_id=request.instrument_id, step=step, operation=proposal.operation,
                args=proposal.args, reason=proposal.reason, status="completed",
                result_summary={"detail": detail, "evidence_count": len(saved_ids)}, evidence_ids=tuple(saved_ids),
                requested_at=requested_at, completed_at=completed,
                immutable_fingerprint=fingerprint({"trace":trace_id,"step":step,"operation":proposal.operation,"args":proposal.args,"evidence_ids":saved_ids}),
            )
            repository.save_action(record)
            prior.append(f"{proposal.operation}:{detail or ''}")
            trade_log("auto_trading", "trading_research_action", trace_id=trace_id, strategy_id=request.strategy_id,
                      instrument_id=request.instrument_id, step=step, operation=proposal.operation,
                      evidence_ids=saved_ids, planner_backend=active_planner.backend, deadline_at=request.deadline_at)
            if proposal.operation == "evaluate" and not context.unresolved_facts:
                stop_reason = "evidence_complete"; break
        except Exception as exc:
            completed = clock().astimezone(timezone.utc)
            record = ResearchActionRecord(
                action_id=action_id, trace_id=trace_id, strategy_id=request.strategy_id,
                instrument_id=request.instrument_id, step=step, operation=proposal.operation,
                args=proposal.args, reason=proposal.reason, status="failed", result_summary={}, evidence_ids=(),
                requested_at=requested_at, completed_at=completed, error_code=type(exc).__name__,
                immutable_fingerprint=fingerprint({"trace":trace_id,"step":step,"operation":proposal.operation,"error":type(exc).__name__}),
            )
            repository.save_action(record); warnings.append(f"action_failed:{proposal.operation}:{type(exc).__name__}")
            prior.append(f"{proposal.operation}:failed")
    return ResearchLoopResult(trace_id=trace_id, planner_backend=active_planner.backend, stop_reason=stop_reason,
                              action_count=len(repository.action_trace(trace_id)), evidence_ids=tuple(run_evidence_ids),
                              warnings=tuple(dict.fromkeys(warnings)))
