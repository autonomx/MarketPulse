from __future__ import annotations

import json

from app.security.tenant_context import RequestTenant, TenantContext
from app.persistence.unit_of_work import unit_of_work

from .bounce_training import BounceModelArtifact


class TradingBounceModelRepository:
    context = RequestTenant()
    def __init__(self, *, context: TenantContext | None = None, uow_factory=unit_of_work) -> None:
        self.context = context
        self.uow_factory = uow_factory

    def save(self, artifact: BounceModelArtifact) -> BounceModelArtifact:
        with self.uow_factory() as uow:
            existing = uow.connection.execute(
                """
                SELECT artifact, fingerprint
                  FROM omnix_trading_model_artifacts
                 WHERE workspace_id = %s AND model_id = %s AND model_version = %s
                 FOR UPDATE
                """,
                (self.context.workspace_id, artifact.model_id, artifact.model_version),
            ).fetchone()
            if existing is not None:
                if str(existing[1]) != artifact.fingerprint:
                    raise ValueError("bounce_model_version_payload_mismatch")
                return BounceModelArtifact.model_validate(existing[0])
            uow.connection.execute(
                """
                INSERT INTO omnix_trading_model_artifacts (
                    workspace_id, model_id, model_version, label_definition,
                    trained_at, training_examples, positive_examples, shadow_only,
                    artifact, fingerprint
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE, %s::jsonb, %s)
                """,
                (
                    self.context.workspace_id,
                    artifact.model_id,
                    artifact.model_version,
                    artifact.label_definition,
                    artifact.trained_at,
                    artifact.training_examples,
                    artifact.positive_examples,
                    json.dumps(artifact.model_dump(mode="json"), separators=(",", ":")),
                    artifact.fingerprint,
                ),
            )
            uow.commit()
        return artifact

    def get(
        self,
        model_version: str,
        *,
        model_id: str = "gap_pullback_logistic",
    ) -> BounceModelArtifact:
        with self.uow_factory() as uow:
            row = uow.connection.execute(
                """
                SELECT artifact
                  FROM omnix_trading_model_artifacts
                 WHERE workspace_id = %s AND model_id = %s AND model_version = %s
                """,
                (self.context.workspace_id, model_id, model_version),
            ).fetchone()
        if row is None:
            raise ValueError(f"bounce_model_not_found: {model_version}")
        return BounceModelArtifact.model_validate(row[0])

    def latest(self, *, model_id: str = "gap_pullback_logistic") -> BounceModelArtifact:
        with self.uow_factory() as uow:
            row = uow.connection.execute(
                """
                SELECT artifact
                  FROM omnix_trading_model_artifacts
                 WHERE workspace_id = %s AND model_id = %s
                 ORDER BY trained_at DESC, model_version DESC
                 LIMIT 1
                """,
                (self.context.workspace_id, model_id),
            ).fetchone()
        if row is None:
            raise ValueError("bounce_model_not_found")
        return BounceModelArtifact.model_validate(row[0])

    def list(self, limit: int = 50) -> list[BounceModelArtifact]:
        with self.uow_factory() as uow:
            rows = uow.connection.execute(
                """
                SELECT artifact
                  FROM omnix_trading_model_artifacts
                 WHERE workspace_id = %s AND model_id = 'gap_pullback_logistic'
                 ORDER BY trained_at DESC, model_version DESC
                 LIMIT %s
                """,
                (self.context.workspace_id, limit),
            ).fetchall()
        return [BounceModelArtifact.model_validate(row[0]) for row in rows]


def default_bounce_model_repository() -> TradingBounceModelRepository:
    return TradingBounceModelRepository()
