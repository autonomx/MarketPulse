from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.apps.trading.research.contracts import TradingEvidence
from app.apps.trading.strategy_ai_shadow_v2 import (
    deterministic_evidence_quality as catalyst_deterministic_evidence_quality,
)

INSTRUMENT = "equity:NASDAQ:TEST"
NOW = datetime(2026, 9, 10, 13, 20, tzinfo=timezone.utc)


def _evidence(
    suffix: str,
    *,
    source_type: str,
    tier: int,
    form: str | None = None,
    age_hours: int = 1,
) -> TradingEvidence:
    known = NOW - timedelta(hours=age_hours)
    metadata = {"form": form} if form is not None else {}
    return TradingEvidence(
        evidence_id=f"ev-{suffix}",
        instrument_id=INSTRUMENT,
        evidence_type="catalyst",
        source_type=source_type,
        source_locator=f"https://example.test/{suffix}",
        source_authority_tier=tier,
        source_published_at=known,
        source_available_at=known,
        captured_at=known,
        omnix_known_at=known,
        title="Fixture",
        content="Fixture evidence",
        content_hash=("a" if suffix == "1" else "b") * 64,
        extraction_status="completed",
        metadata=metadata,
        immutable_fingerprint=("c" if suffix == "1" else "d") * 64,
    )


def test_supply_only_sec_document_does_not_primary_verify_gap_catalyst() -> None:
    quality, verified, score = catalyst_deterministic_evidence_quality(
        [
            _evidence("1", source_type="sec", tier=1, form="S-3"),
            _evidence("2", source_type="news", tier=2),
        ]
    )

    assert verified is False
    assert quality == "secondary_only"
    assert 0 < score < 100


def test_recent_non_supply_primary_source_can_verify_catalyst_bundle() -> None:
    quality, verified, score = catalyst_deterministic_evidence_quality(
        [
            _evidence("1", source_type="company_ir", tier=1),
            _evidence("2", source_type="news", tier=2),
        ]
    )

    assert verified is True
    assert quality == "primary_verified"
    assert score > 70


def test_stale_primary_document_does_not_upgrade_current_news() -> None:
    quality, verified, _ = catalyst_deterministic_evidence_quality(
        [
            _evidence("1", source_type="sec", tier=1, form="8-K", age_hours=120),
            _evidence("2", source_type="news", tier=2),
        ]
    )

    assert verified is False
    assert quality == "secondary_only"
