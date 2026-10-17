from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

from app.apps.trading.research.contracts import TradingEvidence, fingerprint
from app.apps.trading.research.novelty_shadow import generate_novelty_shadow


class FakeProvider:
    def __init__(self):
        self.prompt = ""

    def generate(self, prompt: str):
        self.prompt = prompt
        return json.dumps({
            "novelty": "recycled",
            "relevance": "medium",
            "catalyst_class": "contract_partnership",
            "conflict_summary": "The new release substantially repeats the prior partnership announcement.",
            "confidence": 0.91,
            "rationale": "Both visible announcements describe the same core partnership.",
        })


def _evidence(evidence_id: str, content: str, published: datetime, known: datetime) -> TradingEvidence:
    return TradingEvidence(
        evidence_id=evidence_id,
        instrument_id="equity:NASDAQ:XYZ",
        evidence_type="company_ir_release",
        source_type="company_ir",
        source_locator=f"https://ir.example.test/{evidence_id}",
        source_authority_tier=1,
        source_published_at=published,
        source_available_at=published,
        captured_at=known,
        omnix_known_at=known,
        title=content,
        content=content,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        extraction_status="completed",
        metadata={},
        immutable_fingerprint=fingerprint({"id": evidence_id, "content": content}),
    )


def test_similar_prior_announcement_can_be_shadow_classified_recycled_without_future_evidence():
    decision = datetime(2026, 8, 20, 13, 34, tzinfo=timezone.utc)
    prior = _evidence(
        "prior",
        "XYZ announces strategic partnership with Acme.",
        decision - timedelta(days=25),
        decision - timedelta(minutes=8),
    )
    current = _evidence(
        "current",
        "XYZ expands strategic partnership with Acme.",
        decision - timedelta(minutes=20),
        decision - timedelta(minutes=3),
    )
    future = _evidence(
        "future",
        "FUTURE LEAK: later filing says the partnership was terminated.",
        decision - timedelta(minutes=10),
        decision + timedelta(minutes=8),
    )
    provider = FakeProvider()
    annotation = generate_novelty_shadow(
        "equity:NASDAQ:XYZ",
        (prior, current, future),
        observed_at=decision,
        provider_factory=lambda: provider,
    )
    assert annotation.novelty == "recycled"
    assert annotation.shadow_only is True
    assert set(annotation.evidence_ids) == {"prior", "current"}
    assert "FUTURE LEAK" not in provider.prompt
    assert "strategic partnership" in provider.prompt
