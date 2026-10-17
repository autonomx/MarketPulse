from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, cast

from app.persistence.database import PostgresDatabase, default_database

from .affect_store import PostgresMemoryV2AffectStore
from .contracts import (
    AffectObservation,
    ConsolidationDecisionSet,
    DerivedMemoryRevision,
    DerivedPolicyEnvelope,
    Episode,
    GraphAssertion,
    MemorySpaceKey,
    Observation,
    RelationshipState,
)
from .derived_state import PostgresMemoryV2DerivedStateStore
from .episode_store import PostgresMemoryV2EpisodeStore
from .graph_store import PostgresMemoryV2GraphStore
from .observation_store import (
    PostgresMemoryV2ObservationStore,
    _observation_from_row,
    _qualified_observation_columns,
    _space_values,
)
from .policy import derive_policy_envelope, policy_digest
from .relationship_store import PostgresMemoryV2RelationshipStore


class DerivedConvergenceError(RuntimeError):
    pass


class StaleDerivedPlanError(DerivedConvergenceError):
    pass


class RedactedDecisionSetError(DerivedConvergenceError):
    pass


@dataclass(frozen=True, slots=True)
class DerivedPlanPayload:
    assertions: tuple[GraphAssertion, ...] = ()
    episodes: tuple[Episode, ...] = ()
    relationships: tuple[RelationshipState, ...] = ()
    affect: tuple[AffectObservation, ...] = ()
    normalized_proposals: tuple[dict[str, Any], ...] = ()
    deterministic_decisions: dict[str, Any] | None = None
    consolidator_version: str = "memory-v2-derived@1"
    schema_version: str = "memory-v2-derived-plan@1"
    provider_id: str | None = None
    model_id: str | None = None


@dataclass(frozen=True, slots=True)
class PreparedDerivedCommit:
    space: MemorySpaceKey
    input_observation_from: int
    input_observation_through: int
    expected_governance_revision: int
    expected_previous_derived_revision: int
    rebuild: bool
    payload: DerivedPlanPayload
    decision_set_id: str
    decision_digest: str
    deterministic_decisions: dict[str, Any]
    started_at: datetime


DerivedPlanner = Callable[
    [MemorySpaceKey, tuple[Observation, ...], tuple[GraphAssertion, ...]],
    DerivedPlanPayload,
]


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _plan_document(payload: DerivedPlanPayload) -> dict[str, Any]:
    return {
        "assertions": [item.model_dump(mode="json") for item in payload.assertions],
        "episodes": [item.model_dump(mode="json") for item in payload.episodes],
        "relationships": [item.model_dump(mode="json") for item in payload.relationships],
        "affect": [item.model_dump(mode="json") for item in payload.affect],
    }


class PostgresMemoryV2DerivedCoordinator:
    """Optimistic Observation -> atomic derived-memory convergence.

    `prepare()` deliberately completes all database reads before invoking `planner`, so a
    semantic/LLM extractor cannot run while authority rows are locked. `commit()` then
    revalidates observation watermark, governance revision, and previous derived revision
    under one short transaction before persisting graph, episodes, relationship, affect,
    policies, receipt, and DerivedMemoryRevision together.
    """

    def __init__(
        self,
        database: PostgresDatabase | None = None,
        *,
        observation_store: PostgresMemoryV2ObservationStore | None = None,
        graph_store: PostgresMemoryV2GraphStore | None = None,
        derived_store: PostgresMemoryV2DerivedStateStore | None = None,
    ) -> None:
        self.database = database or default_database()
        self.observation_store = observation_store or PostgresMemoryV2ObservationStore(self.database)
        self.graph_store = graph_store or PostgresMemoryV2GraphStore(self.database)
        self.derived_store = derived_store or PostgresMemoryV2DerivedStateStore(self.database)

    def prepare(
        self,
        space: MemorySpaceKey,
        planner: DerivedPlanner,
    ) -> PreparedDerivedCommit | None:
        state = self.derived_store.state(space)
        observation_watermark = self.observation_store.watermark(space)
        governance_revision = self.observation_store.governance_revision(space)
        if (
            observation_watermark == state.source_observation_watermark
            and governance_revision == state.source_governance_revision
        ):
            return None

        rebuild = governance_revision != state.source_governance_revision
        after_sequence = 0 if rebuild else state.source_observation_watermark
        observations = tuple(
            self.observation_store.list(
                space,
                after_sequence=after_sequence,
                through_sequence=observation_watermark,
                include_inactive=False,
                limit=100_000,
            )
        )
        existing = tuple(self.graph_store.list_assertions(space))

        # No transaction is open here. Provider/LLM inference may safely take seconds.
        payload = planner(space, observations, existing)
        self._validate_payload_space(space, payload)
        start = 1 if rebuild else state.source_observation_watermark + 1
        if observation_watermark < 1:
            return None
        document = _plan_document(payload)
        deterministic = dict(payload.deterministic_decisions or {})
        deterministic["plan"] = document
        material = {
            "space": space.model_dump(mode="json"),
            "input_observation_from": start,
            "input_observation_through": observation_watermark,
            "previous_derived_revision": state.derived_revision,
            "source_governance_revision": governance_revision,
            "normalized_proposals": payload.normalized_proposals,
            "deterministic_decisions": deterministic,
            "consolidator_version": payload.consolidator_version,
            "schema_version": payload.schema_version,
            "provider_id": payload.provider_id,
            "model_id": payload.model_id,
        }
        decision_digest = _digest(material)
        return PreparedDerivedCommit(
            space=space,
            input_observation_from=start,
            input_observation_through=observation_watermark,
            expected_governance_revision=governance_revision,
            expected_previous_derived_revision=state.derived_revision,
            rebuild=rebuild,
            payload=payload,
            decision_set_id=f"decision:{decision_digest}",
            decision_digest=decision_digest,
            deterministic_decisions=deterministic,
            started_at=datetime.now(timezone.utc),
        )

    @staticmethod
    def _validate_payload_space(space: MemorySpaceKey, payload: DerivedPlanPayload) -> None:
        items: tuple[GraphAssertion | Episode | RelationshipState | AffectObservation, ...] = (
            *payload.assertions, *payload.episodes, *payload.relationships, *payload.affect
        )
        if any(item.space != space for item in items):
            raise DerivedConvergenceError("derived plan crosses MemorySpaceKey authority")

    @staticmethod
    def _ensure_derived_state(connection: Any, space: MemorySpaceKey) -> tuple[int, int, int]:
        values = _space_values(space)
        connection.execute(
            """
            INSERT INTO omnix_memory_v2_derived_state (
                principal_id, owner_type, owner_id,
                derived_revision, source_observation_watermark,
                source_governance_revision, policy_digest
            ) VALUES (%s, %s, %s, 0, 0, 0, '')
            ON CONFLICT (principal_id, owner_type, owner_id) DO NOTHING
            """,
            values,
        )
        row = connection.execute(
            """
            SELECT derived_revision, source_observation_watermark,
                   source_governance_revision
              FROM omnix_memory_v2_derived_state
             WHERE principal_id = %s AND owner_type = %s AND owner_id = %s
             FOR UPDATE
            """,
            values,
        ).fetchone()
        if row is None:  # pragma: no cover
            raise DerivedConvergenceError("failed to establish derived state")
        return int(row[0]), int(row[1]), int(row[2])

    @staticmethod
    def _source_observation_ids_for_assertions(
        connection: Any,
        assertion_ids: tuple[str, ...],
    ) -> tuple[str, ...]:
        if not assertion_ids:
            return ()
        rows = connection.execute(
            """
            WITH RECURSIVE refs(assertion_id) AS (
                SELECT unnest(%s::text[])
                UNION
                SELECT e.evidence_assertion_id
                  FROM omnix_memory_v2_assertion_assertion_evidence e
                  JOIN refs r ON r.assertion_id = e.assertion_id
            )
            SELECT DISTINCT oe.observation_id
              FROM refs r
              JOIN omnix_memory_v2_assertion_observation_evidence oe
                ON oe.assertion_id = r.assertion_id
             ORDER BY oe.observation_id
            """,
            (list(assertion_ids),),
        ).fetchall()
        return tuple(str(row[0]) for row in rows)

    @staticmethod
    def _load_active_observations(
        connection: Any,
        space: MemorySpaceKey,
        observation_ids: tuple[str, ...],
    ) -> tuple[Observation, ...]:
        ids = tuple(dict.fromkeys(observation_ids))
        if not ids:
            return ()
        columns = _qualified_observation_columns("o")
        rows = connection.execute(
            f"""
            SELECT {columns}
              FROM omnix_memory_v2_observations o
              LEFT JOIN omnix_memory_v2_observation_dispositions d
                ON d.observation_id = o.observation_id
             WHERE o.principal_id = %s AND o.owner_type = %s AND o.owner_id = %s
               AND o.observation_id = ANY(%s)
               AND COALESCE(d.state, 'active') = 'active'
             ORDER BY o.authority_sequence
            """,
            (*_space_values(space), list(ids)),
        ).fetchall()
        observations = tuple(_observation_from_row(row) for row in rows)
        found = {item.observation_id for item in observations}
        missing = set(ids) - found
        if missing:
            raise StaleDerivedPlanError(
                f"derived plan evidence became inactive or disappeared: {sorted(missing)}"
            )
        return observations

    def _policy_for(
        self,
        connection: Any,
        *,
        space: MemorySpaceKey,
        observation_ids: tuple[str, ...],
        assertion_ids: tuple[str, ...],
        declared_visibility: tuple[Any, ...],
        governance_revision: int,
    ) -> DerivedPolicyEnvelope:
        inherited_observation_ids = self._source_observation_ids_for_assertions(
            connection,
            assertion_ids,
        )
        all_observation_ids = tuple(
            dict.fromkeys((*observation_ids, *inherited_observation_ids))
        )
        observations = self._load_active_observations(
            connection,
            space,
            all_observation_ids,
        )
        return derive_policy_envelope(
            observations,
            source_assertion_ids=assertion_ids,
            declared_visibility=declared_visibility,
            governance_revision=governance_revision,
        )

    @staticmethod
    def _write_episode(connection: Any, episode: Episode) -> None:
        PostgresMemoryV2EpisodeStore._validate_observations(connection, episode)
        PostgresMemoryV2EpisodeStore._validate_assertions(connection, episode)
        values = _space_values(episode.space)
        connection.execute(
            """
            INSERT INTO omnix_memory_v2_episodes (
                episode_id, principal_id, owner_type, owner_id, visibility_scopes,
                title, summary, started_at, ended_at, participant_entity_ids,
                importance, derivation_version, revision
            ) VALUES (
                %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s::jsonb, %s, %s, %s
            )
            ON CONFLICT (episode_id) DO UPDATE SET
                visibility_scopes = EXCLUDED.visibility_scopes,
                title = EXCLUDED.title,
                summary = EXCLUDED.summary,
                started_at = EXCLUDED.started_at,
                ended_at = EXCLUDED.ended_at,
                participant_entity_ids = EXCLUDED.participant_entity_ids,
                importance = EXCLUDED.importance,
                derivation_version = EXCLUDED.derivation_version,
                revision = EXCLUDED.revision,
                updated_at = CURRENT_TIMESTAMP
            WHERE omnix_memory_v2_episodes.principal_id = EXCLUDED.principal_id
              AND omnix_memory_v2_episodes.owner_type = EXCLUDED.owner_type
              AND omnix_memory_v2_episodes.owner_id = EXCLUDED.owner_id
            """,
            (
                episode.episode_id,
                *values,
                _json([scope.model_dump(mode="json") for scope in episode.visibility_scopes]),
                episode.title,
                episode.summary,
                episode.started_at,
                episode.ended_at,
                _json(episode.participant_entity_ids),
                episode.importance,
                episode.derivation_version,
                episode.revision,
            ),
        )
        connection.execute(
            "DELETE FROM omnix_memory_v2_episode_observations WHERE episode_id = %s",
            (episode.episode_id,),
        )
        connection.execute(
            "DELETE FROM omnix_memory_v2_episode_assertions WHERE episode_id = %s",
            (episode.episode_id,),
        )
        for observation_id in tuple(dict.fromkeys(episode.observation_ids)):
            connection.execute(
                """
                INSERT INTO omnix_memory_v2_episode_observations (
                    episode_id, observation_id, principal_id, owner_type, owner_id
                ) VALUES (%s, %s, %s, %s, %s)
                """,
                (episode.episode_id, observation_id, *values),
            )
        for assertion_id in tuple(dict.fromkeys(episode.generated_assertion_ids)):
            connection.execute(
                """
                INSERT INTO omnix_memory_v2_episode_assertions (
                    episode_id, assertion_id, principal_id, owner_type, owner_id
                ) VALUES (%s, %s, %s, %s, %s)
                """,
                (episode.episode_id, assertion_id, *values),
            )

    @staticmethod
    def _write_relationship(connection: Any, state: RelationshipState) -> None:
        PostgresMemoryV2RelationshipStore._validate_active_evidence(connection, state)
        values = _space_values(state.space)
        connection.execute(
            """
            INSERT INTO omnix_memory_v2_relationships (
                relationship_id, principal_id, owner_type, owner_id,
                subject_entity_id, subject_entity_type,
                counterpart_entity_id, counterpart_entity_type,
                prompt_interpretation, derivation_version, status, revision
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (relationship_id) DO UPDATE SET
                subject_entity_id = EXCLUDED.subject_entity_id,
                subject_entity_type = EXCLUDED.subject_entity_type,
                counterpart_entity_id = EXCLUDED.counterpart_entity_id,
                counterpart_entity_type = EXCLUDED.counterpart_entity_type,
                prompt_interpretation = EXCLUDED.prompt_interpretation,
                derivation_version = EXCLUDED.derivation_version,
                status = EXCLUDED.status,
                revision = EXCLUDED.revision,
                updated_at = CURRENT_TIMESTAMP
            WHERE omnix_memory_v2_relationships.principal_id = EXCLUDED.principal_id
              AND omnix_memory_v2_relationships.owner_type = EXCLUDED.owner_type
              AND omnix_memory_v2_relationships.owner_id = EXCLUDED.owner_id
            """,
            (
                state.relationship_id,
                *values,
                state.subject.entity_id,
                state.subject.entity_type,
                state.counterpart.entity_id,
                state.counterpart.entity_type,
                state.prompt_interpretation,
                state.derivation_version,
                state.status,
                state.revision,
            ),
        )
        connection.execute(
            "DELETE FROM omnix_memory_v2_relationship_metric_evidence WHERE relationship_id = %s",
            (state.relationship_id,),
        )
        connection.execute(
            "DELETE FROM omnix_memory_v2_relationship_evidence WHERE relationship_id = %s",
            (state.relationship_id,),
        )
        connection.execute(
            "DELETE FROM omnix_memory_v2_relationship_metrics WHERE relationship_id = %s",
            (state.relationship_id,),
        )
        for metric in state.metrics:
            connection.execute(
                """
                INSERT INTO omnix_memory_v2_relationship_metrics (
                    relationship_id, metric_name, principal_id, owner_type, owner_id,
                    metric_value, confidence
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    state.relationship_id,
                    metric.name,
                    *values,
                    metric.value,
                    metric.confidence,
                ),
            )
            for observation_id in tuple(dict.fromkeys(metric.evidence_observation_ids)):
                connection.execute(
                    """
                    INSERT INTO omnix_memory_v2_relationship_metric_evidence (
                        relationship_id, metric_name, observation_id,
                        principal_id, owner_type, owner_id
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (state.relationship_id, metric.name, observation_id, *values),
                )
        for observation_id in tuple(dict.fromkeys(state.evidence_observation_ids)):
            connection.execute(
                """
                INSERT INTO omnix_memory_v2_relationship_evidence (
                    relationship_id, observation_id, principal_id, owner_type, owner_id
                ) VALUES (%s, %s, %s, %s, %s)
                """,
                (state.relationship_id, observation_id, *values),
            )

    @staticmethod
    def _write_affect(connection: Any, observation: AffectObservation) -> None:
        PostgresMemoryV2AffectStore._validate_source(connection, observation)
        connection.execute(
            """
            INSERT INTO omnix_memory_v2_affect_observations (
                affect_id, principal_id, owner_type, owner_id, source_observation_id,
                source, observed_at, valence, arousal, emotion_distribution,
                confidence, model_version
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
            ON CONFLICT (affect_id) DO UPDATE SET
                source_observation_id = EXCLUDED.source_observation_id,
                source = EXCLUDED.source,
                observed_at = EXCLUDED.observed_at,
                valence = EXCLUDED.valence,
                arousal = EXCLUDED.arousal,
                emotion_distribution = EXCLUDED.emotion_distribution,
                confidence = EXCLUDED.confidence,
                model_version = EXCLUDED.model_version
            WHERE omnix_memory_v2_affect_observations.principal_id = EXCLUDED.principal_id
              AND omnix_memory_v2_affect_observations.owner_type = EXCLUDED.owner_type
              AND omnix_memory_v2_affect_observations.owner_id = EXCLUDED.owner_id
            """,
            (
                observation.affect_id,
                *_space_values(observation.space),
                observation.source_observation_id,
                observation.source,
                observation.observed_at,
                observation.valence,
                observation.arousal,
                _json(observation.emotion_distribution),
                observation.confidence,
                observation.model_version,
            ),
        )

    def commit(self, prepared: PreparedDerivedCommit) -> DerivedMemoryRevision:
        space = prepared.space
        values = _space_values(space)
        completed_at = datetime.now(timezone.utc)
        with self.database.transaction() as connection:
            stream = connection.execute(
                """
                SELECT observation_watermark, governance_revision
                  FROM omnix_memory_v2_authority_streams
                 WHERE principal_id = %s AND owner_type = %s AND owner_id = %s
                 FOR UPDATE
                """,
                values,
            ).fetchone()
            if stream is None:
                raise StaleDerivedPlanError("authority stream disappeared before derived commit")
            current_revision, _, _ = self._ensure_derived_state(connection, space)
            if int(stream[0]) != prepared.input_observation_through:
                raise StaleDerivedPlanError("observation watermark changed during inference")
            if int(stream[1]) != prepared.expected_governance_revision:
                raise StaleDerivedPlanError("governance revision changed during inference")
            if current_revision != prepared.expected_previous_derived_revision:
                raise StaleDerivedPlanError("derived revision changed during inference")

            next_revision = current_revision + 1
            graph_state = self.graph_store._ensure_and_lock_state(connection, space)
            graph_revision = graph_state.graph_revision + 1
            previous_assertion_ids = {
                str(row[0])
                for row in connection.execute(
                    """
                    SELECT assertion_id FROM omnix_memory_v2_graph_assertions
                     WHERE principal_id = %s AND owner_type = %s AND owner_id = %s
                    """,
                    values,
                ).fetchall()
            }

            policies: list[DerivedPolicyEnvelope] = []
            normalized_assertions: list[GraphAssertion] = []
            for assertion in prepared.payload.assertions:
                policy = self._policy_for(
                    connection,
                    space=space,
                    observation_ids=assertion.evidence_observation_ids,
                    assertion_ids=assertion.evidence_assertion_ids,
                    declared_visibility=assertion.visibility_scopes,
                    governance_revision=prepared.expected_governance_revision,
                )
                normalized = assertion.model_copy(
                    update={"visibility_scopes": policy.effective_visibility, "policy": policy}
                )
                self.graph_store._write_assertion(connection, normalized, graph_revision)
                PostgresMemoryV2DerivedStateStore.write_policy(
                    connection,
                    space=space,
                    item_type="assertion",
                    ref_id=normalized.assertion_id,
                    policy=policy,
                    derived_revision=next_revision,
                )
                policies.append(policy)
                normalized_assertions.append(normalized)

            normalized_episodes: list[Episode] = []
            for episode in prepared.payload.episodes:
                policy = self._policy_for(
                    connection,
                    space=space,
                    observation_ids=episode.observation_ids,
                    assertion_ids=(),
                    declared_visibility=episode.visibility_scopes,
                    governance_revision=prepared.expected_governance_revision,
                )
                normalized_episode = episode.model_copy(
                    update={"visibility_scopes": policy.effective_visibility, "policy": policy}
                )
                self._write_episode(connection, normalized_episode)
                PostgresMemoryV2DerivedStateStore.write_policy(
                    connection,
                    space=space,
                    item_type="episode",
                    ref_id=normalized_episode.episode_id,
                    policy=policy,
                    derived_revision=next_revision,
                )
                policies.append(policy)
                normalized_episodes.append(normalized_episode)

            normalized_relationships: list[RelationshipState] = []
            for relationship in prepared.payload.relationships:
                evidence_ids = tuple(
                    dict.fromkeys(
                        (
                            *relationship.evidence_observation_ids,
                            *(
                                observation_id
                                for metric in relationship.metrics
                                for observation_id in metric.evidence_observation_ids
                            ),
                        )
                    )
                )
                policy = self._policy_for(
                    connection,
                    space=space,
                    observation_ids=evidence_ids,
                    assertion_ids=(),
                    declared_visibility=(),
                    governance_revision=prepared.expected_governance_revision,
                )
                normalized_relationship = relationship.model_copy(update={"policy": policy})
                self._write_relationship(connection, normalized_relationship)
                PostgresMemoryV2DerivedStateStore.write_policy(
                    connection,
                    space=space,
                    item_type="relationship",
                    ref_id=normalized_relationship.relationship_id,
                    policy=policy,
                    derived_revision=next_revision,
                )
                policies.append(policy)
                normalized_relationships.append(normalized_relationship)

            normalized_affect: list[AffectObservation] = []
            for affect in prepared.payload.affect:
                policy = self._policy_for(
                    connection,
                    space=space,
                    observation_ids=(affect.source_observation_id,),
                    assertion_ids=(),
                    declared_visibility=(),
                    governance_revision=prepared.expected_governance_revision,
                )
                normalized_affect_item = affect.model_copy(update={"policy": policy})
                self._write_affect(connection, normalized_affect_item)
                PostgresMemoryV2DerivedStateStore.write_policy(
                    connection,
                    space=space,
                    item_type="affect",
                    ref_id=normalized_affect_item.affect_id,
                    policy=policy,
                    derived_revision=next_revision,
                )
                policies.append(policy)
                normalized_affect.append(normalized_affect_item)

            computed_plan = {
                "assertions": [item.model_dump(mode="json") for item in normalized_assertions],
                "episodes": [item.model_dump(mode="json") for item in normalized_episodes],
                "relationships": [item.model_dump(mode="json") for item in normalized_relationships],
                "affect": [item.model_dump(mode="json") for item in normalized_affect],
            }
            deterministic = dict(prepared.deterministic_decisions)
            deterministic["plan"] = computed_plan
            decision_material = {
                "space": space.model_dump(mode="json"),
                "input_observation_from": prepared.input_observation_from,
                "input_observation_through": prepared.input_observation_through,
                "previous_derived_revision": prepared.expected_previous_derived_revision,
                "source_governance_revision": prepared.expected_governance_revision,
                "normalized_proposals": prepared.payload.normalized_proposals,
                "deterministic_decisions": deterministic,
                "consolidator_version": prepared.payload.consolidator_version,
                "schema_version": prepared.payload.schema_version,
                "provider_id": prepared.payload.provider_id,
                "model_id": prepared.payload.model_id,
            }
            final_decision_digest = _digest(decision_material)
            decision_set_id = f"decision:{final_decision_digest}"
            connection.execute(
                """
                INSERT INTO omnix_memory_v2_consolidation_decision_sets (
                    decision_set_id, principal_id, owner_type, owner_id,
                    input_observation_from, input_observation_through,
                    previous_derived_revision, source_governance_revision,
                    normalized_proposals, deterministic_decisions,
                    consolidator_version, schema_version, provider_id, model_id,
                    decision_digest
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s,
                    %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s
                )
                ON CONFLICT (principal_id, owner_type, owner_id, decision_digest)
                DO NOTHING
                """,
                (
                    decision_set_id,
                    *values,
                    prepared.input_observation_from,
                    prepared.input_observation_through,
                    prepared.expected_previous_derived_revision,
                    prepared.expected_governance_revision,
                    _json(prepared.payload.normalized_proposals),
                    _json(deterministic),
                    prepared.payload.consolidator_version,
                    prepared.payload.schema_version,
                    prepared.payload.provider_id,
                    prepared.payload.model_id,
                    final_decision_digest,
                ),
            )

            connection.execute(
                """
                UPDATE omnix_memory_v2_graph_state
                   SET graph_revision = %s,
                       source_observation_watermark = %s,
                       updated_at = CURRENT_TIMESTAMP
                 WHERE principal_id = %s AND owner_type = %s AND owner_id = %s
                """,
                (graph_revision, prepared.input_observation_through, *values),
            )

            policy_state_digest = policy_digest(policies)
            revision_id = (
                f"derived:{space.principal_id}:{space.owner_type}:{space.owner_id}:"
                f"{next_revision}"
            )
            connection.execute(
                """
                INSERT INTO omnix_memory_v2_derived_revisions (
                    revision_id, principal_id, owner_type, owner_id,
                    derived_revision, source_observation_watermark,
                    source_governance_revision, decision_set_id, policy_digest
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    revision_id,
                    *values,
                    next_revision,
                    prepared.input_observation_through,
                    prepared.expected_governance_revision,
                    decision_set_id,
                    policy_state_digest,
                ),
            )
            connection.execute(
                """
                UPDATE omnix_memory_v2_derived_state
                   SET derived_revision = %s,
                       source_observation_watermark = %s,
                       source_governance_revision = %s,
                       decision_set_id = %s,
                       policy_digest = %s,
                       updated_at = CURRENT_TIMESTAMP
                 WHERE principal_id = %s AND owner_type = %s AND owner_id = %s
                """,
                (
                    next_revision,
                    prepared.input_observation_through,
                    prepared.expected_governance_revision,
                    decision_set_id,
                    policy_state_digest,
                    *values,
                ),
            )

            created_assertions = tuple(
                item.assertion_id
                for item in normalized_assertions
                if item.assertion_id not in previous_assertion_ids
            )
            reinforced = tuple(
                item.assertion_id
                for item in normalized_assertions
                if item.assertion_id in previous_assertion_ids and item.status == "active"
            )
            superseded = tuple(
                item.assertion_id for item in normalized_assertions if item.status == "superseded"
            )
            retracted = tuple(
                item.assertion_id for item in normalized_assertions if item.status == "retracted"
            )
            conflicted = tuple(
                item.assertion_id for item in normalized_assertions if item.status == "disputed"
            )
            receipt_key = _digest(
                {
                    "decision_set_id": decision_set_id,
                    "derived_revision": next_revision,
                    "space": space.model_dump(mode="json"),
                }
            )
            receipt_id = f"consolidation:{receipt_key}"
            connection.execute(
                """
                INSERT INTO omnix_memory_v2_consolidation_state (
                    principal_id, owner_type, owner_id, consolidation_watermark,
                    last_receipt_id
                ) VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (principal_id, owner_type, owner_id) DO UPDATE SET
                    consolidation_watermark = EXCLUDED.consolidation_watermark,
                    last_receipt_id = EXCLUDED.last_receipt_id,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (*values, prepared.input_observation_through, receipt_id),
            )
            connection.execute(
                """
                INSERT INTO omnix_memory_v2_consolidation_receipts (
                    receipt_id, principal_id, owner_type, owner_id,
                    input_observation_from, input_observation_through,
                    consolidator_version, schema_version, provider_id, model_id,
                    created_assertion_ids, reinforced_assertion_ids,
                    superseded_assertion_ids, retracted_assertion_ids,
                    conflicted_assertion_ids, created_episode_ids,
                    relationship_update_ids, affect_update_ids,
                    resulting_graph_revision, idempotency_key,
                    started_at, completed_at, decision_set_id,
                    resulting_derived_revision
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb,
                    %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb,
                    %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (receipt_id) DO NOTHING
                """,
                (
                    receipt_id,
                    *values,
                    prepared.input_observation_from,
                    prepared.input_observation_through,
                    prepared.payload.consolidator_version,
                    prepared.payload.schema_version,
                    prepared.payload.provider_id,
                    prepared.payload.model_id,
                    _json(created_assertions),
                    _json(reinforced),
                    _json(superseded),
                    _json(retracted),
                    _json(conflicted),
                    _json([item.episode_id for item in normalized_episodes]),
                    _json([item.relationship_id for item in normalized_relationships]),
                    _json([item.affect_id for item in normalized_affect]),
                    graph_revision,
                    receipt_key,
                    prepared.started_at,
                    completed_at,
                    decision_set_id,
                    next_revision,
                ),
            )
            connection.execute(
                """
                DELETE FROM omnix_memory_v2_derive_jobs
                 WHERE principal_id = %s AND owner_type = %s AND owner_id = %s
                   AND target_observation_watermark <= %s
                   AND target_governance_revision <= %s
                """,
                (
                    *values,
                    prepared.input_observation_through,
                    prepared.expected_governance_revision,
                ),
            )
            connection.execute(
                """
                INSERT INTO omnix_memory_v2_projection_jobs (
                    principal_id, owner_type, owner_id, target_derived_revision,
                    status, attempts, available_at
                ) VALUES (%s, %s, %s, %s, 'pending', 0, CURRENT_TIMESTAMP)
                ON CONFLICT (principal_id, owner_type, owner_id) DO UPDATE SET
                    target_derived_revision = GREATEST(
                        omnix_memory_v2_projection_jobs.target_derived_revision,
                        EXCLUDED.target_derived_revision
                    ),
                    status = 'pending', attempts = 0, last_error = NULL,
                    available_at = CURRENT_TIMESTAMP, claimed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (*values, next_revision),
            )

        return DerivedMemoryRevision(
            revision_id=revision_id,
            space=space,
            derived_revision=next_revision,
            source_observation_watermark=prepared.input_observation_through,
            source_governance_revision=prepared.expected_governance_revision,
            decision_set_id=decision_set_id,
            policy_digest=policy_state_digest,
            created_at=completed_at,
        )

    def decision_set(self, decision_set_id: str) -> ConsolidationDecisionSet | None:
        with self.database.transaction() as connection:
            row = connection.execute(
                """
                SELECT principal_id, owner_type, owner_id,
                       input_observation_from, input_observation_through,
                       previous_derived_revision, source_governance_revision,
                       normalized_proposals, deterministic_decisions,
                       consolidator_version, schema_version, provider_id, model_id,
                       decision_digest, invalidated_at, redacted_at, created_at
                  FROM omnix_memory_v2_consolidation_decision_sets
                 WHERE decision_set_id = %s
                """,
                (decision_set_id,),
            ).fetchone()
        if row is None:
            return None
        return ConsolidationDecisionSet(
            decision_set_id=decision_set_id,
            space=MemorySpaceKey(
                principal_id=str(row[0]), owner_type=cast(Any, str(row[1])), owner_id=str(row[2])
            ),
            input_observation_from=int(row[3]),
            input_observation_through=int(row[4]),
            previous_derived_revision=int(row[5]),
            source_governance_revision=int(row[6]),
            normalized_proposals=tuple(dict(item) for item in row[7]),
            deterministic_decisions=dict(row[8]),
            consolidator_version=str(row[9]),
            schema_version=str(row[10]),
            provider_id=str(row[11]) if row[11] is not None else None,
            model_id=str(row[12]) if row[12] is not None else None,
            decision_digest=str(row[13]),
            invalidated_at=row[14],
            redacted_at=row[15],
            created_at=row[16],
        )

    def exact_replay_payload(self, decision_set_id: str) -> DerivedPlanPayload:
        decision = self.decision_set(decision_set_id)
        if decision is None:
            raise DerivedConvergenceError(f"decision set not found: {decision_set_id}")
        if decision.redacted_at is not None or decision.invalidated_at is not None:
            raise RedactedDecisionSetError(
                "decision set is redacted/invalidated and cannot be used for exact replay"
            )
        plan = dict(decision.deterministic_decisions).get("plan")
        if not isinstance(plan, dict):
            raise DerivedConvergenceError("decision set does not contain an exact replay plan")
        return DerivedPlanPayload(
            assertions=tuple(GraphAssertion.model_validate(item) for item in plan.get("assertions", ())),
            episodes=tuple(Episode.model_validate(item) for item in plan.get("episodes", ())),
            relationships=tuple(
                RelationshipState.model_validate(item) for item in plan.get("relationships", ())
            ),
            affect=tuple(AffectObservation.model_validate(item) for item in plan.get("affect", ())),
            normalized_proposals=decision.normalized_proposals,
            deterministic_decisions={
                key: value
                for key, value in decision.deterministic_decisions.items()
                if key != "plan"
            },
            consolidator_version=decision.consolidator_version,
            schema_version=decision.schema_version,
            provider_id=decision.provider_id,
            model_id=decision.model_id,
        )
