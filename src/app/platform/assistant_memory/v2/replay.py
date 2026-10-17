from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from app.persistence.database import PostgresDatabase, default_database

from .affect_store import PostgresMemoryV2AffectStore
from .contracts import DerivedPolicyEnvelope, MemorySpaceKey
from .convergence import PostgresMemoryV2DerivedCoordinator, RedactedDecisionSetError
from .episode_store import PostgresMemoryV2EpisodeStore
from .graph_store import PostgresMemoryV2GraphStore
from .observation_store import _space_values
from .relationship_store import PostgresMemoryV2RelationshipStore


@dataclass(frozen=True, slots=True)
class DerivedReplayReport:
    matches: bool
    persisted_digest: str
    replay_digest: str
    derived_revision: int
    decision_set_count: int
    reason: str | None = None


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _without_policy(item: Any) -> dict[str, Any]:
    data = item.model_dump(mode="json")
    data.pop("policy", None)
    return data


def _policy_record(item_type: str, ref_id: str, policy: DerivedPolicyEnvelope) -> dict[str, Any]:
    return {
        "item_type": item_type,
        "ref_id": ref_id,
        **policy.model_dump(mode="json"),
    }


class PostgresMemoryV2DerivedReplayValidator:
    """Exact replay validation using committed decision sets, never re-inference.

    Governance-driven rebuilds are replacement checkpoints. Exact replay starts from the
    most recent such checkpoint instead of accumulating superseded pre-governance state.
    This also means an older redacted decision set does not make a later complete rebuild
    unreplayable.
    """

    def __init__(
        self,
        database: PostgresDatabase | None = None,
        *,
        coordinator: PostgresMemoryV2DerivedCoordinator | None = None,
    ) -> None:
        self.database = database or default_database()
        self.coordinator = coordinator or PostgresMemoryV2DerivedCoordinator(self.database)
        self.graph_store = PostgresMemoryV2GraphStore(self.database)
        self.episode_store = PostgresMemoryV2EpisodeStore(self.database)
        self.relationship_store = PostgresMemoryV2RelationshipStore(self.database)
        self.affect_store = PostgresMemoryV2AffectStore(self.database)

    def _revision_rows(self, space: MemorySpaceKey) -> list[Any]:
        with self.database.transaction() as connection:
            return connection.execute(
                """
                SELECT r.derived_revision, r.decision_set_id,
                       d.input_observation_from, d.previous_derived_revision
                  FROM omnix_memory_v2_derived_revisions r
                  JOIN omnix_memory_v2_consolidation_decision_sets d
                    ON d.decision_set_id = r.decision_set_id
                 WHERE r.principal_id = %s AND r.owner_type = %s AND r.owner_id = %s
                 ORDER BY r.derived_revision
                """,
                _space_values(space),
            ).fetchall()

    @staticmethod
    def _replay_rows(rows: list[Any]) -> list[Any]:
        start = 0
        for index, row in enumerate(rows):
            if int(row[2]) == 1 and int(row[3]) > 0:
                start = index
        return rows[start:]

    def _persisted_policy_records(self, space: MemorySpaceKey) -> list[dict[str, Any]]:
        with self.database.transaction() as connection:
            rows = connection.execute(
                """
                SELECT item_type, ref_id, sensitivity, effective_visibility,
                       trust_class, source_observation_ids, source_assertion_ids,
                       source_governance_revision, policy_version, policy_digest
                  FROM omnix_memory_v2_derived_policy_envelopes
                 WHERE principal_id = %s AND owner_type = %s AND owner_id = %s
                 ORDER BY item_type, ref_id
                """,
                _space_values(space),
            ).fetchall()
        return [
            {
                "item_type": str(row[0]),
                "ref_id": str(row[1]),
                "sensitivity": str(row[2]),
                "effective_visibility": list(row[3]),
                "trust_class": str(row[4]),
                "source_observation_ids": list(row[5]),
                "source_assertion_ids": list(row[6]),
                "source_governance_revision": int(row[7]),
                "policy_version": str(row[8]),
                "policy_digest": str(row[9]),
            }
            for row in rows
        ]

    def validate(self, space: MemorySpaceKey) -> DerivedReplayReport:
        all_rows = self._revision_rows(space)
        if not all_rows:
            return DerivedReplayReport(
                matches=False,
                persisted_digest="",
                replay_digest="",
                derived_revision=0,
                decision_set_count=0,
                reason="no_derived_revisions",
            )
        rows = self._replay_rows(all_rows)

        assertions: dict[str, Any] = {}
        episodes: dict[str, Any] = {}
        relationships: dict[str, Any] = {}
        affect: dict[str, Any] = {}
        policies: dict[tuple[str, str], DerivedPolicyEnvelope] = {}
        try:
            for row in rows:
                payload = self.coordinator.exact_replay_payload(str(row[1]))
                for item in payload.assertions:
                    assertions[item.assertion_id] = item
                    if item.policy is not None:
                        policies[("assertion", item.assertion_id)] = item.policy
                for episode in payload.episodes:
                    episodes[episode.episode_id] = episode
                    if episode.policy is not None:
                        policies[("episode", episode.episode_id)] = episode.policy
                for relationship in payload.relationships:
                    relationships[relationship.relationship_id] = relationship
                    if relationship.policy is not None:
                        policies[("relationship", relationship.relationship_id)] = relationship.policy
                for affect_item in payload.affect:
                    affect[affect_item.affect_id] = affect_item
                    if affect_item.policy is not None:
                        policies[("affect", affect_item.affect_id)] = affect_item.policy
        except RedactedDecisionSetError as exc:
            return DerivedReplayReport(
                matches=False,
                persisted_digest="",
                replay_digest="",
                derived_revision=int(all_rows[-1][0]),
                decision_set_count=len(rows),
                reason=str(exc),
            )

        replay_state = {
            "assertions": sorted(
                (_without_policy(item) for item in assertions.values()),
                key=lambda item: item["assertion_id"],
            ),
            "episodes": sorted(
                (_without_policy(item) for item in episodes.values()),
                key=lambda item: item["episode_id"],
            ),
            "relationships": sorted(
                (_without_policy(item) for item in relationships.values()),
                key=lambda item: item["relationship_id"],
            ),
            "affect": sorted(
                (_without_policy(item) for item in affect.values()),
                key=lambda item: item["affect_id"],
            ),
            "policies": [
                _policy_record(item_type, ref_id, policy)
                for (item_type, ref_id), policy in sorted(policies.items())
            ],
        }
        persisted_state = {
            "assertions": sorted(
                (_without_policy(item) for item in self.graph_store.list_assertions(space)),
                key=lambda item: item["assertion_id"],
            ),
            "episodes": sorted(
                (_without_policy(item) for item in self.episode_store.list(space, limit=100_000)),
                key=lambda item: item["episode_id"],
            ),
            "relationships": sorted(
                (
                    _without_policy(item)
                    for item in self.relationship_store.list(
                        space,
                        status=None,
                        limit=100_000,
                    )
                ),
                key=lambda item: item["relationship_id"],
            ),
            "affect": sorted(
                (_without_policy(item) for item in self.affect_store.history(space, limit=100_000)),
                key=lambda item: item["affect_id"],
            ),
            "policies": self._persisted_policy_records(space),
        }
        replay_digest = _digest(replay_state)
        persisted_digest = _digest(persisted_state)
        return DerivedReplayReport(
            matches=replay_digest == persisted_digest,
            persisted_digest=persisted_digest,
            replay_digest=replay_digest,
            derived_revision=int(all_rows[-1][0]),
            decision_set_count=len(rows),
            reason=None if replay_digest == persisted_digest else "derived_state_diverged",
        )
