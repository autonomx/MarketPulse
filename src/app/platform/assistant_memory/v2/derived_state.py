from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, cast

from app.persistence.database import PostgresDatabase, default_database

from .contracts import (
    DerivedItemType,
    DerivedMemoryRevision,
    DerivedPolicyEnvelope,
    MemorySpaceKey,
    RetrievalSourceRevision,
    VisibilityScope,
)


@dataclass(frozen=True, slots=True)
class DerivedStateSnapshot:
    derived_revision: int
    source_observation_watermark: int
    source_governance_revision: int
    decision_set_id: str | None
    policy_digest: str


def _space_values(space: MemorySpaceKey) -> tuple[str, str, str]:
    return space.principal_id, space.owner_type, space.owner_id


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _policy_from_row(row: Any, *, offset: int = 1) -> DerivedPolicyEnvelope:
    return DerivedPolicyEnvelope(
        sensitivity=cast(Any, str(row[offset])),
        effective_visibility=tuple(
            VisibilityScope.model_validate(item) for item in row[offset + 1]
        ),
        trust_class=cast(Any, str(row[offset + 2])),
        source_observation_ids=tuple(str(item) for item in row[offset + 3]),
        source_assertion_ids=tuple(str(item) for item in row[offset + 4]),
        source_governance_revision=int(row[offset + 5]),
        policy_version=str(row[offset + 6]),
        policy_digest=str(row[offset + 7]),
    )


def federation_revision_digest(revisions: tuple[RetrievalSourceRevision, ...]) -> str:
    material = [
        item.model_dump(mode="json")
        for item in sorted(
            revisions,
            key=lambda value: (
                value.source_space.principal_id,
                value.source_space.owner_type,
                value.source_space.owner_id,
                value.grant_revision,
            ),
        )
    ]
    return hashlib.sha256(_json(material).encode("utf-8")).hexdigest()


class PostgresMemoryV2DerivedStateStore:
    def __init__(self, database: PostgresDatabase | None = None) -> None:
        self.database = database or default_database()

    def governance_revision(self, space: MemorySpaceKey) -> int:
        with self.database.transaction() as connection:
            row = connection.execute(
                """
                SELECT governance_revision
                  FROM omnix_memory_v2_authority_streams
                 WHERE principal_id = %s AND owner_type = %s AND owner_id = %s
                """,
                _space_values(space),
            ).fetchone()
        return int(row[0]) if row is not None else 0

    def state(self, space: MemorySpaceKey) -> DerivedStateSnapshot:
        with self.database.transaction() as connection:
            row = connection.execute(
                """
                SELECT derived_revision, source_observation_watermark,
                       source_governance_revision, decision_set_id, policy_digest
                  FROM omnix_memory_v2_derived_state
                 WHERE principal_id = %s AND owner_type = %s AND owner_id = %s
                """,
                _space_values(space),
            ).fetchone()
        if row is None:
            return DerivedStateSnapshot(0, 0, 0, None, "")
        return DerivedStateSnapshot(
            derived_revision=int(row[0]),
            source_observation_watermark=int(row[1]),
            source_governance_revision=int(row[2]),
            decision_set_id=str(row[3]) if row[3] is not None else None,
            policy_digest=str(row[4]),
        )

    def latest_revision(self, space: MemorySpaceKey) -> DerivedMemoryRevision | None:
        with self.database.transaction() as connection:
            row = connection.execute(
                """
                SELECT revision_id, derived_revision, source_observation_watermark,
                       source_governance_revision, decision_set_id, policy_digest, created_at
                  FROM omnix_memory_v2_derived_revisions
                 WHERE principal_id = %s AND owner_type = %s AND owner_id = %s
                 ORDER BY derived_revision DESC
                 LIMIT 1
                """,
                _space_values(space),
            ).fetchone()
        if row is None:
            return None
        return DerivedMemoryRevision(
            revision_id=str(row[0]),
            space=space,
            derived_revision=int(row[1]),
            source_observation_watermark=int(row[2]),
            source_governance_revision=int(row[3]),
            decision_set_id=str(row[4]),
            policy_digest=str(row[5]),
            created_at=row[6],
        )

    def policies(
        self,
        space: MemorySpaceKey,
        item_type: DerivedItemType,
        ref_ids: tuple[str, ...],
    ) -> dict[str, DerivedPolicyEnvelope]:
        ids = tuple(dict.fromkeys(ref_ids))
        if not ids:
            return {}
        with self.database.transaction() as connection:
            rows = connection.execute(
                """
                SELECT ref_id, sensitivity, effective_visibility, trust_class,
                       source_observation_ids, source_assertion_ids,
                       source_governance_revision, policy_version, policy_digest
                  FROM omnix_memory_v2_derived_policy_envelopes
                 WHERE principal_id = %s AND owner_type = %s AND owner_id = %s
                   AND item_type = %s AND ref_id = ANY(%s)
                """,
                (*_space_values(space), item_type, list(ids)),
            ).fetchall()
        return {str(row[0]): _policy_from_row(row) for row in rows}

    def policy(
        self,
        space: MemorySpaceKey,
        item_type: DerivedItemType,
        ref_id: str,
    ) -> DerivedPolicyEnvelope | None:
        return self.policies(space, item_type, (ref_id,)).get(ref_id)

    @staticmethod
    def write_policy(
        connection: Any,
        *,
        space: MemorySpaceKey,
        item_type: DerivedItemType,
        ref_id: str,
        policy: DerivedPolicyEnvelope,
        derived_revision: int,
    ) -> None:
        connection.execute(
            """
            INSERT INTO omnix_memory_v2_derived_policy_envelopes (
                principal_id, owner_type, owner_id, item_type, ref_id,
                sensitivity, effective_visibility, trust_class,
                source_observation_ids, source_assertion_ids,
                source_governance_revision, policy_version, policy_digest,
                derived_revision
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s::jsonb, %s,
                %s::jsonb, %s::jsonb, %s, %s, %s, %s
            )
            ON CONFLICT (principal_id, owner_type, owner_id, item_type, ref_id)
            DO UPDATE SET
                sensitivity = EXCLUDED.sensitivity,
                effective_visibility = EXCLUDED.effective_visibility,
                trust_class = EXCLUDED.trust_class,
                source_observation_ids = EXCLUDED.source_observation_ids,
                source_assertion_ids = EXCLUDED.source_assertion_ids,
                source_governance_revision = EXCLUDED.source_governance_revision,
                policy_version = EXCLUDED.policy_version,
                policy_digest = EXCLUDED.policy_digest,
                derived_revision = EXCLUDED.derived_revision,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                *_space_values(space),
                item_type,
                ref_id,
                policy.sensitivity,
                _json([item.model_dump(mode="json") for item in policy.effective_visibility]),
                policy.trust_class,
                _json(policy.source_observation_ids),
                _json(policy.source_assertion_ids),
                policy.source_governance_revision,
                policy.policy_version,
                policy.policy_digest,
                derived_revision,
            ),
        )

    def source_revision(
        self,
        space: MemorySpaceKey,
        *,
        observation_watermark: int,
        index_revision: int,
        grant_revision: int = 0,
    ) -> RetrievalSourceRevision:
        state = self.state(space)
        return RetrievalSourceRevision(
            source_space=space,
            observation_watermark=observation_watermark,
            governance_revision=self.governance_revision(space),
            derived_revision=state.derived_revision,
            index_revision=int(index_revision),
            grant_revision=int(grant_revision),
        )
