from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.persistence.config import DatabaseSettings
from app.persistence.database import PostgresDatabase
from app.persistence.identity_service import ensure_local_identity
from app.persistence.unit_of_work import unit_of_work
from app.apps.trading.catalyst_evidence import capture_catalyst_evidence
from app.apps.trading.catalyst_repository import TradingCatalystRepository


pytestmark = pytest.mark.skipif(
    not os.environ.get("OMNIX_TEST_DATABASE_URL"),
    reason="OMNIX_TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


def _database() -> PostgresDatabase:
    return PostgresDatabase(
        DatabaseSettings(
            url=os.environ["OMNIX_TEST_DATABASE_URL"],
            pool_min=1,
            pool_max=3,
            connect_timeout_seconds=10,
            statement_timeout_ms=30_000,
            application_name="omnix-catalyst-idempotency-tests",
        )
    )


def test_catalyst_evidence_is_idempotent_across_both_unique_identities() -> None:
    database = _database()
    try:
        context = ensure_local_identity(database)
        repository = TradingCatalystRepository(
            context=context,
            uow_factory=lambda: unit_of_work(database),
        )
        suffix = uuid.uuid4().hex[:12]
        instrument_id = f"equity:NASDAQ:C{suffix[:4].upper()}"
        evidence_id = f"evidence-{suffix}"
        published_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        captured_at = datetime.now(timezone.utc)

        original = capture_catalyst_evidence(
            evidence_id=evidence_id,
            instrument_id=instrument_id,
            source_type="news",
            source_locator=f"https://example.test/{suffix}",
            published_at=published_at,
            captured_at=captured_at,
            headline="Original catalyst",
            raw_text="Company reports a material operating update.",
        )
        assert repository.save_evidence(original) is True

        # Reusing the provider's durable evidence_id with a later capture changes
        # immutable_fingerprint. This is the production collision that previously
        # aborted the morning universe archive.
        same_id_different_fingerprint = capture_catalyst_evidence(
            evidence_id=evidence_id,
            instrument_id=instrument_id,
            source_type="news",
            source_locator=f"https://example.test/{suffix}",
            published_at=published_at,
            captured_at=captured_at + timedelta(seconds=30),
            headline="Original catalyst",
            raw_text="Company reports a material operating update.",
        )
        assert (
            same_id_different_fingerprint.immutable_fingerprint
            != original.immutable_fingerprint
        )
        assert repository.save_evidence(same_id_different_fingerprint) is False

        # The independent immutable_fingerprint uniqueness constraint must also
        # converge safely even if a caller presents a different evidence_id.
        different_id_same_fingerprint = original.model_copy(
            update={"evidence_id": f"{evidence_id}-alias"}
        )
        assert repository.save_evidence(different_id_same_fingerprint) is False

        stored = repository.list_evidence(instrument_id)
        assert len(stored) == 1
        assert stored[0].evidence_id == original.evidence_id
        assert stored[0].immutable_fingerprint == original.immutable_fingerprint
    finally:
        database.close()
