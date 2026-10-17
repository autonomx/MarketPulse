from __future__ import annotations

from datetime import datetime, timezone

from app.apps.trading.strategy_ai_shadow_v2 import (
    CatalystIntelligenceSnapshot,
    normalize_catalyst_provenance,
)

AT = datetime(2026, 9, 10, 13, 20, tzinfo=timezone.utc)
INSTRUMENT = "equity:NASDAQ:TEST"


def _snapshot(*, verified: bool, quality: str, evidence_ids: tuple[str, ...]):
    return CatalystIntelligenceSnapshot(
        snapshot_id="snapshot",
        instrument_id=INSTRUMENT,
        as_of=AT,
        provider="fixture",
        evidence_fingerprint="a" * 64,
        evidence_ids=evidence_ids,
        evidence_quality=quality,
        primary_source_verified=verified,
    )


def test_final_snapshot_cannot_claim_primary_verified_when_binding_failed() -> None:
    result = normalize_catalyst_provenance(_snapshot(
        verified=False,
        quality="primary_verified",
        evidence_ids=("ev-1", "ev-2"),
    ))

    assert result.primary_source_verified is False
    assert result.evidence_quality == "mixed"


def test_empty_unverified_snapshot_is_unresolved() -> None:
    result = normalize_catalyst_provenance(
        _snapshot(verified=False, quality="primary_verified", evidence_ids=())
    )

    assert result.evidence_quality == "unresolved"
