from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.apps.trading.research.contracts import TradingEvidence, TradingResearchReport, fingerprint
from app.apps.trading.research.facts.extraction import build_fact_set
from app.apps.trading.research.market_brief import generate_trading_market_brief


def _evidence() -> TradingEvidence:
    observed_at = datetime(2026, 8, 25, 21, 0, tzinfo=timezone.utc)
    return TradingEvidence(
        evidence_id="evidence-nvda-1",
        instrument_id="equity:NASDAQ:NVDA",
        evidence_type="web_result",
        source_type="web",
        source_locator="https://example.test/nvda",
        source_authority_tier=3,
        source_published_at=observed_at,
        source_available_at=observed_at,
        captured_at=observed_at,
        omnix_known_at=observed_at,
        title="NVIDIA reports demand update",
        content="NVIDIA reported a demand update in its company release.",
        content_hash="a" * 64,
        immutable_fingerprint=fingerprint({"fixture": "evidence-nvda-1"}),
    )


def _report(evidence: TradingEvidence) -> TradingResearchReport:
    observed_at = evidence.captured_at
    return TradingResearchReport(
        report_id="report-nvda-1",
        report_version=1,
        instrument_id=evidence.instrument_id,
        research_started_at=observed_at,
        research_completed_at=observed_at,
        evidence_cutoff_at=observed_at,
        omnix_known_at=observed_at,
        catalyst_status="confirmed",
        supply_status="unresolved",
        research_status="partial",
        source_evidence_ids=(evidence.evidence_id,),
        immutable_fingerprint=fingerprint({"fixture": "report-nvda-1"}),
    )


class _Provider:
    provider_name = "configured-test-provider"
    model = "configured-test-model"

    def __init__(self) -> None:
        self.messages = []

    def chat_completion(self, messages, model=None, stream=False, **kwargs):
        self.messages = messages
        return {
            "content": """{
              \"headline\": \"Demand update is the current catalyst\",
              \"summary\": \"The supplied source reports a demand update; financing coverage remains unresolved.\",
              \"key_points\": [{
                \"text\": \"The company update identifies demand as the current catalyst.\",
                \"source_evidence_ids\": [\"evidence-nvda-1\"]
              }],
              \"risks\": [{
                \"text\": \"Supply and financing coverage are still unresolved.\",
                \"source_evidence_ids\": [\"evidence-nvda-1\"]
              }],
              \"watch_items\": [],
              \"confidence\": \"medium\",
              \"source_evidence_ids\": [\"evidence-nvda-1\"]
            }"""
        }


def test_market_brief_uses_configured_provider_and_keeps_only_captured_citations():
    evidence = _evidence()
    report = _report(evidence)
    fact_set = build_fact_set(
        instrument_id=evidence.instrument_id,
        evidence=(evidence,),
        decision_at=evidence.captured_at,
        report_id=report.report_id,
    )
    provider = _Provider()

    brief = generate_trading_market_brief(
        report,
        fact_set,
        (evidence,),
        provider_factory=lambda: provider,
    )

    assert brief.provider == "configured-test-provider"
    assert brief.model == "configured-test-model"
    assert brief.headline == "Demand update is the current catalyst"
    assert brief.source_evidence_ids == (evidence.evidence_id,)
    assert brief.key_points[0].source_evidence_ids == (evidence.evidence_id,)
    assert "untrusted reference material" in provider.messages[0].content
    assert '"evidence_id":"evidence-nvda-1"' in provider.messages[1].content


class _InvalidCitationProvider(_Provider):
    def chat_completion(self, messages, model=None, stream=False, **kwargs):
        self.messages = messages
        return {
            "content": """{
              "headline": "Unsupported claim",
              "summary": "This claim cites evidence that was never captured.",
              "key_points": [{
                "text": "Unsupported current claim.",
                "source_evidence_ids": ["not-a-source"]
              }],
              "risks": [],
              "watch_items": [],
              "confidence": "medium",
              "source_evidence_ids": ["evidence-nvda-1"]
            }"""
        }


def test_market_brief_rejects_claims_with_unknown_citations():
    evidence = _evidence()
    report = _report(evidence)
    fact_set = build_fact_set(
        instrument_id=evidence.instrument_id,
        evidence=(evidence,),
        decision_at=evidence.captured_at,
        report_id=report.report_id,
    )

    with pytest.raises(ValueError, match="market_brief_source_evidence_ids_invalid"):
        generate_trading_market_brief(
            report,
            fact_set,
            (evidence,),
            provider_factory=_InvalidCitationProvider,
        )
