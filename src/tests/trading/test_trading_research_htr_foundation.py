from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.apps.trading.providers.errors import ProviderUnavailableError
from app.apps.trading.research.contracts import TradingEvidence, fingerprint
from app.apps.trading.research.adapters.base import AdapterExecutionResult
from app.apps.trading.research.coordinator import create_trading_research_request, run_trading_research
from app.apps.trading.research.facts.metrics import derive_supply_metrics
from app.apps.trading.research.facts.supply import extract_supply_facts
from app.apps.trading.research.issuer_identity import fallback_issuer_identity
from app.apps.trading.research.knowledge_time import latest_as_of


def _evidence(text: str, evidence_id: str = "e1") -> TradingEvidence:
    captured = datetime(2026, 8, 20, 13, 30, tzinfo=timezone.utc)
    return TradingEvidence(
        evidence_id=evidence_id, instrument_id="equity:NASDAQ:XYZ", evidence_type="sec_filing_content",
        source_type="sec", source_locator="https://sec.gov/test", source_authority_tier=1,
        source_published_at=captured, source_available_at=captured, captured_at=captured,
        omnix_known_at=captured, title="filing", content=text,
        content_hash="a" * 64, extraction_status="completed", metadata={},
        immutable_fingerprint=fingerprint({"id": evidence_id, "text": text}),
    )


def test_supply_parser_distinguishes_terminated_atm_from_active_atm():
    terminated = extract_supply_facts((_evidence("The previous at-the-market offering was terminated on August 10."),))
    active = extract_supply_facts((_evidence("The at-the-market offering remains available with $80 million remaining capacity."),))
    assert terminated[0].supply_type == "atm" and terminated[0].status == "terminated"
    assert active[0].status == "active" and active[0].remaining_capacity_usd == Decimal("80000000")


def test_supply_parser_distinguishes_exercised_and_exercisable_warrants():
    exercised = extract_supply_facts((_evidence("All outstanding warrants were exercised.", "e2"),))
    exercisable = extract_supply_facts((_evidence("10 million warrants are exercisable at an exercise price of $1.50.", "e3"),))
    assert exercised[0].status == "exhausted"
    assert exercisable[0].status == "exercisable"
    assert exercisable[0].shares == Decimal("10000000")
    assert exercisable[0].strike_price == Decimal("1.50")


@pytest.mark.parametrize(
    ("text", "supply_type", "status", "registration_status"),
    [
        ("The at-the-market offering is active and remains available.", "atm", "active", None),
        ("The at-the-market offering was terminated on August 10.", "atm", "terminated", None),
        ("The at-the-market facility is exhausted and no longer available.", "atm", "exhausted", None),
        ("All outstanding warrants were exercised.", "warrant", "exhausted", None),
        ("The warrants remain outstanding and may be exercised.", "warrant", "exercisable", None),
        # Future expiry language is deliberately not treated as already expired.
        ("The warrants expire above the current market price next year.", "warrant", "unknown", None),
        ("The resale registration became effective today.", "resale_registration", "active", "effective"),
        ("The shelf registration was withdrawn.", "shelf_registration", "withdrawn", "withdrawn"),
        ("The convertible notes were repaid in full.", "convertible", "exhausted", None),
        ("The convertible notes remain outstanding.", "convertible", "active", None),
    ],
)
def test_required_adversarial_supply_status_corpus(text, supply_type, status, registration_status):
    facts = extract_supply_facts((_evidence(text, f"fixture-{supply_type}-{status}"),))
    matching = [fact for fact in facts if fact.supply_type == supply_type]
    assert matching, text
    assert matching[0].status == status
    assert matching[0].registration_status == registration_status


def test_supply_metrics_are_continuous_not_keyword_vetoes():
    facts = extract_supply_facts((_evidence("10 million warrants are exercisable at an exercise price of $1.50."),))
    metrics = derive_supply_metrics(facts, float_shares=Decimal("8000000"), market_cap=Decimal("25000000"), market_price=Decimal("1.80"))
    assert metrics.in_the_money_warrant_pct_float == Decimal("125")
    assert metrics.immediate_supply_risk is True


def test_later_knowledge_is_not_visible_to_earlier_decision():
    first = _evidence("first", "early")
    later = _evidence("later", "late").model_copy(update={"omnix_known_at": first.omnix_known_at + timedelta(minutes=12)})
    assert latest_as_of([first, later], first.omnix_known_at + timedelta(minutes=6)).evidence_id == "early"
    assert latest_as_of([first, later], first.omnix_known_at + timedelta(minutes=13)).evidence_id == "late"


def test_identity_fallback_preserves_symbol_without_claiming_sec_authority():
    identity = fallback_issuer_identity("equity:NASDAQ:XYZ")
    replay = fallback_issuer_identity("equity:NASDAQ:XYZ")

    assert identity.symbol == "XYZ"
    assert identity.exchange == "NASDAQ"
    assert identity.cik is None
    assert identity.source == "instrument_id_fallback"
    assert identity.confidence == Decimal("0")
    assert identity.immutable_fingerprint == replay.immutable_fingerprint


def test_coordinator_returns_failed_report_when_identity_provider_fails():
    class Repository:
        def __init__(self):
            self.identity = None
            self.evidence = []
            self.actions = []

        def identity_as_of(self, instrument_id, known_at_lte):
            return self.identity

        def save_identity(self, item):
            self.identity = item.model_copy(update={"omnix_known_at": item.captured_at})
            return self.identity

        def list_evidence_as_of(self, instrument_id, known_at_lte, limit=200):
            return [item for item in self.evidence if item.instrument_id == instrument_id][:limit]

        def save_evidence(self, item):
            saved = item.model_copy(update={"omnix_known_at": item.captured_at})
            self.evidence.append(saved)
            return saved

        def save_action(self, item):
            saved = item.model_copy(update={"omnix_known_at": item.completed_at or item.requested_at})
            self.actions.append(saved)
            return saved

        def action_trace(self, trace_id):
            return [item for item in self.actions if item.trace_id == trace_id]

        def next_report_version(self, instrument_id):
            return 1

        def save_report(self, item):
            return item.model_copy(update={"omnix_known_at": item.research_completed_at})

    class FactRepository:
        def save_supply_fact(self, item):
            return item.model_copy(update={"omnix_known_at": item.generated_at})

        def save_fact_set(self, item):
            return item.model_copy(update={"omnix_known_at": item.generated_at})

        def save_features(self, item):
            return item.model_copy(update={"omnix_known_at": item.decision_at})

    class EmptyAdapter:
        def find(self, identity, *, query=None, limit=10):
            return AdapterExecutionResult()

        def extract(self, identity, *, locator):
            return AdapterExecutionResult()

    class BrokenIdentityResolver:
        def resolve(self, instrument_id):
            raise ProviderUnavailableError("SEC unavailable")

    repository = Repository()
    repository.evidence.append(_evidence("Historical evidence must not satisfy a new run.", "historical"))

    result = run_trading_research(
        create_trading_research_request(instrument_id="equity:NASDAQ:XYZ"),
        repository=repository,
        fact_repository=FactRepository(),
        shadow_repository=object(),
        identity_resolver=BrokenIdentityResolver(),
        sec=EmptyAdapter(),
        company=EmptyAdapter(),
        web=EmptyAdapter(),
        run_shadow_ai=False,
    )

    assert result.report.research_status == "failed"
    assert result.report.source_evidence_ids == ()
    assert result.fact_set.evidence_ids == ()
    assert "issuer_identity_unavailable:ProviderUnavailableError" in result.warnings

    class ProgrammingErrorResolver:
        def resolve(self, instrument_id):
            raise RuntimeError("resolver bug")

    with pytest.raises(RuntimeError, match="resolver bug"):
        run_trading_research(
            create_trading_research_request(instrument_id="equity:NASDAQ:XYZ"),
            repository=Repository(),
            fact_repository=FactRepository(),
            shadow_repository=object(),
            identity_resolver=ProgrammingErrorResolver(),
            sec=EmptyAdapter(),
            company=EmptyAdapter(),
            web=EmptyAdapter(),
            run_shadow_ai=False,
        )


def test_research_package_has_no_order_execution_imports():
    root = Path(__file__).resolve().parents[2] / "app" / "apps" / "trading" / "research"
    forbidden_imports = (
        r"(?:from|import)\s+app\.trading\.paper_api\b",
        r"(?:from|import)\s+app\.trading\.execution_api\b",
        r"(?:from|import)\s+app\.trading\.paper_repository\b",
        r"(?:from|import)\s+app\.trading\.strategy_monitor\b",
    )
    forbidden_calls = (r"\.place_order\s*\(", r"\.cancel_order\s*\(")
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not any(re.search(pattern, text) for pattern in forbidden_imports), f"forbidden execution import in {path}"
        assert not any(re.search(pattern, text) for pattern in forbidden_calls), f"forbidden order call in {path}"
