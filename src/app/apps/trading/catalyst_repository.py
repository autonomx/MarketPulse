from __future__ import annotations

import json
from decimal import Decimal

from app.security.tenant_context import RequestTenant, TenantContext
from app.persistence.unit_of_work import unit_of_work

from .bounce_model import BounceModelScore
from .catalyst_evidence import CatalystEvidence


class TradingCatalystRepository:
    context = RequestTenant()
    def __init__(self, *, context: TenantContext | None = None, uow_factory=unit_of_work) -> None:
        self.context = context
        self.uow_factory = uow_factory

    def save_evidence(self, evidence: CatalystEvidence) -> bool:
        with self.uow_factory() as uow:
            inserted = uow.connection.execute(
                """
                INSERT INTO omnix_trading_catalyst_evidence (
                    workspace_id, evidence_id, instrument_id, source_type,
                    source_locator, published_at, captured_at, headline, content,
                    text_hash, facts, dilution_flags, immutable_fingerprint
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)
                -- Treat either durable identity as idempotent. A repeated provider
                -- capture can reuse evidence_id while producing a different capture-time
                -- fingerprint, and either unique constraint must converge instead of
                -- aborting the entire morning archive transaction.
                ON CONFLICT DO NOTHING
                RETURNING evidence_id
                """,
                (
                    self.context.workspace_id,
                    evidence.evidence_id,
                    evidence.instrument_id,
                    evidence.source_type,
                    evidence.source_locator,
                    evidence.published_at,
                    evidence.captured_at,
                    evidence.headline,
                    evidence.content,
                    evidence.text_hash,
                    json.dumps(evidence.facts, default=str),
                    json.dumps(list(evidence.dilution_flags)),
                    evidence.immutable_fingerprint,
                ),
            ).fetchone()
            uow.commit()
        return inserted is not None

    def list_evidence(self, instrument_id: str, limit: int = 100) -> list[CatalystEvidence]:
        with self.uow_factory() as uow:
            rows = uow.connection.execute(
                """
                SELECT evidence_id, instrument_id, source_type, source_locator,
                       published_at, captured_at, headline, content, text_hash,
                       facts, dilution_flags, immutable_fingerprint
                  FROM omnix_trading_catalyst_evidence
                 WHERE workspace_id = %s AND instrument_id = %s
                 ORDER BY published_at DESC, captured_at DESC LIMIT %s
                """,
                (self.context.workspace_id, instrument_id, limit),
            ).fetchall()
        return [
            CatalystEvidence(
                evidence_id=row[0], instrument_id=row[1], source_type=row[2], source_locator=row[3],
                published_at=row[4], captured_at=row[5], headline=row[6], content=row[7],
                text_hash=row[8], facts=row[9], dilution_flags=tuple(row[10]),
                immutable_fingerprint=row[11],
            )
            for row in rows
        ]

    def evidence_by_ids(
        self,
        instrument_id: str,
        evidence_ids: tuple[str, ...] | list[str],
    ) -> tuple[CatalystEvidence, ...]:
        requested = tuple(dict.fromkeys(str(value) for value in evidence_ids if str(value)))
        if not requested:
            return ()
        # Universes are bounded to 2,000 candidates and catalyst lists are tiny;
        # use the existing workspace/instrument-scoped read path rather than a
        # second SQL contract so provenance checks remain tenant-isolated.
        available = {item.evidence_id: item for item in self.list_evidence(instrument_id, 1000)}
        missing = [evidence_id for evidence_id in requested if evidence_id not in available]
        if missing:
            raise ValueError(
                "catalyst_evidence_not_found:" + ",".join(sorted(missing))
            )
        return tuple(available[evidence_id] for evidence_id in requested)

    def save_model_score(
        self,
        *,
        score_id: str,
        strategy_id: str | None,
        instrument_id: str,
        score: BounceModelScore,
    ) -> bool:
        with self.uow_factory() as uow:
            inserted = uow.connection.execute(
                """
                INSERT INTO omnix_trading_model_scores (
                    workspace_id, score_id, strategy_id, instrument_id, model_id,
                    model_version, observed_at, probability, features,
                    label_definition, shadow_only, fingerprint
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, TRUE, %s)
                -- score_id and fingerprint are both durable identities; duplicate
                -- retries on either constraint are harmless and must remain idempotent.
                ON CONFLICT DO NOTHING
                RETURNING score_id
                """,
                (
                    self.context.workspace_id,
                    score_id,
                    strategy_id,
                    instrument_id,
                    score.model_id,
                    score.model_version,
                    score.observed_at,
                    Decimal(score.probability),
                    json.dumps(score.features.model_dump(mode="json")),
                    score.label_definition,
                    score.fingerprint,
                ),
            ).fetchone()
            uow.commit()
        return inserted is not None


def default_catalyst_repository() -> TradingCatalystRepository:
    return TradingCatalystRepository()
