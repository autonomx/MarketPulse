from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from app.apps.trading.research.adapters.base import AdapterExecutionResult
from app.apps.trading.research.contracts import (
    IssuerIdentity,
    TradingEvidence,
    TradingResearchRequest,
    fingerprint,
)
from app.apps.trading.research.hermes_loop import hermes_trading_research_enabled, run_iterative_research


class MemoryRepository:
    def __init__(self):
        self.evidence: list[TradingEvidence] = []
        self.actions = []

    def list_evidence_as_of(self, instrument_id, known_at_lte, limit=200):
        return [item for item in self.evidence if item.instrument_id == instrument_id and item.omnix_known_at <= known_at_lte][:limit]

    def save_evidence(self, item):
        known = item.omnix_known_at or datetime.now(timezone.utc)
        saved = item.model_copy(update={"omnix_known_at": known})
        self.evidence.append(saved)
        return saved

    def save_action(self, item):
        saved = item.model_copy(update={"omnix_known_at": item.completed_at or item.requested_at})
        self.actions.append(saved)
        return saved

    def action_trace(self, trace_id):
        return [item for item in self.actions if item.trace_id == trace_id]


class EmptyAdapter:
    def find(self, identity, *, query=None, limit=10):
        return AdapterExecutionResult()

    def extract(self, identity, *, locator):
        return AdapterExecutionResult()


class FinancingWeb(EmptyAdapter):
    def find(self, identity, *, query=None, limit=10):
        now = datetime.now(timezone.utc)
        content = "The company announced financing; warrant status remains unclear."
        value = TradingEvidence(
            evidence_id="web-financing",
            instrument_id=identity.instrument_id,
            issuer_identity_id=identity.identity_id,
            evidence_type="web_search_result",
            source_type="web",
            source_locator="https://example.test/news",
            source_authority_tier=3,
            captured_at=now,
            omnix_known_at=now,
            title="Financing announcement",
            content=content,
            content_hash=hashlib.sha256(content.encode()).hexdigest(),
            extraction_status="snippet",
            metadata={"query": query},
            immutable_fingerprint=fingerprint({"content": content}),
        )
        return AdapterExecutionResult(evidence=(value,), detail="financing clue")


class AdaptivePlanner:
    backend = "fake-hermes"

    def __init__(self):
        self.operations: list[str] = []

    def next_action(self, request, context):
        if not context.evidence:
            operation = "web_search"
            args = {"query": "XYZ catalyst"}
        elif any("financing" in item.clue.lower() for item in context.evidence):
            operation = "sec_find_filings"
            args = {"forms": "S-3,424B5,8-K"}
        else:
            operation = "stop"
            args = {}
        self.operations.append(operation)
        return {"action": {"operation": operation, "args": args, "reason": "adaptive follow-up"}, "rationale": "test"}


class UnknownOperationPlanner:
    backend = "bad"
    def next_action(self, request, context):
        return {"action": {"operation": "place_order", "args": {}, "reason": "bad"}, "rationale": "bad"}


class OfflinePlanner:
    backend = "hermes"

    def next_action(self, request, context):
        raise Exception("Hermes sidecar unavailable")


def _identity():
    now = datetime.now(timezone.utc)
    return IssuerIdentity(
        identity_id="issuer-xyz", instrument_id="equity:NASDAQ:XYZ", symbol="XYZ", exchange="NASDAQ",
        legal_name="XYZ Corp", cik="0000000001", source="fixture", source_available_at=now,
        captured_at=now, omnix_known_at=now, confidence=1, immutable_fingerprint="a" * 64,
    )


def _request(**updates):
    now = datetime.now(timezone.utc)
    values = dict(
        request_id="request-xyz", instrument_id="equity:NASDAQ:XYZ", requested_at=now,
        decision_context_at=now, evidence_cutoff_at=now, deadline_at=now + timedelta(seconds=60),
        max_steps=4, max_queries=4, max_sources=10, max_extracts=4,
    )
    values.update(updates)
    return TradingResearchRequest(**values)


def test_search_evidence_changes_the_next_hermes_action_to_sec():
    repository = MemoryRepository(); planner = AdaptivePlanner(); empty = EmptyAdapter()
    result = run_iterative_research(
        _request(), _identity(), repository, planner=planner,
        sec=empty, company=empty, web=FinancingWeb(),
    )
    assert planner.operations[:2] == ["web_search", "sec_find_filings"]
    assert result.planner_backend == "fake-hermes"
    assert any(action.operation == "sec_find_filings" for action in repository.actions)


def test_historical_evidence_does_not_steer_a_new_iterative_run():
    repository = MemoryRepository()
    historical = FinancingWeb().find(_identity(), query="old", limit=10).evidence[0]
    repository.save_evidence(historical)
    planner = AdaptivePlanner()
    empty = EmptyAdapter()

    result = run_iterative_research(
        _request(), _identity(), repository, planner=planner,
        sec=empty, company=empty, web=FinancingWeb(),
    )

    assert planner.operations[:2] == ["web_search", "sec_find_filings"]
    assert result.evidence_ids == ("web-financing",)


def test_query_budget_stops_before_an_extra_search():
    class SearchForever:
        backend = "fake-hermes"
        def next_action(self, request, context):
            return {"action": {"operation": "web_search", "args": {"query": "again"}, "reason": "again"}, "rationale": "again"}

    repository = MemoryRepository(); empty = EmptyAdapter()
    result = run_iterative_research(
        _request(max_queries=1), _identity(), repository, planner=SearchForever(),
        sec=empty, company=empty, web=FinancingWeb(),
    )
    assert result.stop_reason == "query_budget_exhausted"
    assert len(repository.actions) == 1


def test_past_deadline_executes_no_action():
    repository = MemoryRepository(); empty = EmptyAdapter()
    result = run_iterative_research(
        _request(deadline_at=datetime.now(timezone.utc) - timedelta(seconds=1)),
        _identity(), repository, planner=AdaptivePlanner(), sec=empty, company=empty, web=empty,
    )
    assert result.stop_reason == "deadline_exhausted"
    assert repository.actions == []


def test_unknown_order_operation_is_rejected_by_schema_and_not_executed():
    repository = MemoryRepository(); empty = EmptyAdapter()
    result = run_iterative_research(
        _request(), _identity(), repository, planner=UnknownOperationPlanner(), sec=empty, company=empty, web=empty,
    )
    assert result.stop_reason == "planner_invalid"
    assert repository.actions == []


def test_planner_transport_failure_returns_partial_loop_result():
    repository = MemoryRepository()
    empty = EmptyAdapter()
    result = run_iterative_research(
        _request(), _identity(), repository, planner=OfflinePlanner(), sec=empty, company=empty, web=empty,
    )
    assert result.stop_reason == "planner_unavailable"
    assert result.warnings == ("planner_unavailable:Exception",)


def test_trading_research_reuses_chat_hermes_switch_unless_explicitly_disabled(monkeypatch):
    monkeypatch.setenv("HERMES_ENABLED", "1")
    monkeypatch.delenv("OMNIX_TRADING_HERMES_RESEARCH_ENABLED", raising=False)
    assert hermes_trading_research_enabled() is True

    monkeypatch.setenv("OMNIX_TRADING_HERMES_RESEARCH_ENABLED", "0")
    assert hermes_trading_research_enabled() is False
