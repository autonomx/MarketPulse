from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from app.persistence.database import PostgresDatabase, default_database

from .contracts import (
    MemorySpaceKey,
    Observation,
    ObservationProvenance,
    VisibilityScope,
)
from .observation_store import (
    ObservationAppendRequest,
    PostgresMemoryV2ObservationStore,
)


class AssistantOutputLifecycleError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class AssistantOutputState:
    space: MemorySpaceKey
    correlation_id: str
    visibility_scope: VisibilityScope
    generated_text: str
    delivered_text: str | None
    experienced_prefix: str
    generated_observation_id: str
    delivered_observation_id: str | None
    experienced_observation_id: str | None
    finalized: bool


def _space_values(space: MemorySpaceKey) -> tuple[str, str, str]:
    return space.principal_id, space.owner_type, space.owner_id


def _state_from_row(row: Any) -> AssistantOutputState:
    return AssistantOutputState(
        space=MemorySpaceKey(
            principal_id=str(row[0]),
            owner_type=cast(Any, str(row[1])),
            owner_id=str(row[2]),
        ),
        correlation_id=str(row[3]),
        visibility_scope=VisibilityScope(kind=cast(Any, str(row[4])), scope_id=str(row[5])),
        generated_text=str(row[6]),
        delivered_text=str(row[7]) if row[7] is not None else None,
        experienced_prefix=str(row[8]),
        generated_observation_id=str(row[9]),
        delivered_observation_id=str(row[10]) if row[10] is not None else None,
        experienced_observation_id=str(row[11]) if row[11] is not None else None,
        finalized=bool(row[12]),
    )


_STATE_COLUMNS = """
principal_id, owner_type, owner_id, correlation_id,
visibility_kind, visibility_scope_id, generated_text, delivered_text,
experienced_prefix, generated_observation_id, delivered_observation_id,
experienced_observation_id, finalized
"""


class PostgresMemoryV2AssistantOutputLifecycle:
    """Durable generated -> delivered -> experienced assistant-output coordinator.

    Only the finalized experienced prefix becomes `assistant_experienced` evidence. Playback
    progress is durable coordination state so interruption/restart recovery can finish the
    Observation Log correctly without pretending the full generated answer was heard.
    """

    def __init__(
        self,
        database: PostgresDatabase | None = None,
        *,
        observation_store: PostgresMemoryV2ObservationStore | None = None,
    ) -> None:
        self.database = database or default_database()
        self.observation_store = observation_store or PostgresMemoryV2ObservationStore(self.database)

    def get(self, space: MemorySpaceKey, correlation_id: str) -> AssistantOutputState | None:
        with self.database.transaction() as connection:
            row = connection.execute(
                f"""
                SELECT {_STATE_COLUMNS}
                  FROM omnix_memory_v2_assistant_outputs
                 WHERE principal_id = %s AND owner_type = %s AND owner_id = %s
                   AND correlation_id = %s
                """,
                (*_space_values(space), correlation_id),
            ).fetchone()
        return _state_from_row(row) if row is not None else None

    def record_generated(
        self,
        *,
        space: MemorySpaceKey,
        visibility_scope: VisibilityScope,
        correlation_id: str,
        text: str,
        occurred_at: datetime,
        provenance: ObservationProvenance,
    ) -> tuple[AssistantOutputState, Observation]:
        if not correlation_id:
            raise AssistantOutputLifecycleError("correlation_id is required")
        if provenance.source_type != "assistant":
            raise AssistantOutputLifecycleError("generated output provenance must be assistant")
        observation = self.observation_store.append(
            ObservationAppendRequest(
                space=space,
                visibility_scope=visibility_scope,
                event_type="assistant_generated",
                occurred_at=occurred_at,
                provenance=provenance,
                idempotency_key=f"assistant-output:{correlation_id}:generated",
                payload={"text": text},
                correlation_id=correlation_id,
            )
        )
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO omnix_memory_v2_assistant_outputs (
                    principal_id, owner_type, owner_id, correlation_id,
                    visibility_kind, visibility_scope_id, generated_text,
                    generated_observation_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (principal_id, owner_type, owner_id, correlation_id) DO NOTHING
                """,
                (
                    *_space_values(space),
                    correlation_id,
                    visibility_scope.kind,
                    visibility_scope.scope_id,
                    text,
                    observation.observation_id,
                ),
            )
            row = connection.execute(
                f"""
                SELECT {_STATE_COLUMNS}
                  FROM omnix_memory_v2_assistant_outputs
                 WHERE principal_id = %s AND owner_type = %s AND owner_id = %s
                   AND correlation_id = %s
                 FOR UPDATE
                """,
                (*_space_values(space), correlation_id),
            ).fetchone()
            if row is None:  # pragma: no cover
                raise AssistantOutputLifecycleError("failed to persist generated output state")
            state = _state_from_row(row)
            if (
                state.generated_text != text
                or state.generated_observation_id != observation.observation_id
                or state.visibility_scope != visibility_scope
            ):
                raise AssistantOutputLifecycleError(
                    "correlation_id already belongs to different generated output"
                )
        return state, observation

    def record_delivered(
        self,
        *,
        space: MemorySpaceKey,
        correlation_id: str,
        text: str,
        occurred_at: datetime,
        provenance: ObservationProvenance,
    ) -> tuple[AssistantOutputState, Observation]:
        state = self.get(space, correlation_id)
        if state is None:
            raise AssistantOutputLifecycleError("generated output must be recorded before delivery")
        if state.finalized:
            raise AssistantOutputLifecycleError("assistant output is already finalized")
        if not state.generated_text.startswith(text):
            raise AssistantOutputLifecycleError("delivered text must be a prefix of generated text")
        if provenance.source_type not in {"assistant", "system"}:
            raise AssistantOutputLifecycleError("delivered output provenance must be assistant or system")
        observation = self.observation_store.append(
            ObservationAppendRequest(
                space=space,
                visibility_scope=state.visibility_scope,
                event_type="assistant_delivered",
                occurred_at=occurred_at,
                provenance=provenance,
                idempotency_key=f"assistant-output:{correlation_id}:delivered",
                payload={"text": text},
                correlation_id=correlation_id,
            )
        )
        with self.database.transaction() as connection:
            row = connection.execute(
                f"""
                UPDATE omnix_memory_v2_assistant_outputs
                   SET delivered_text = COALESCE(delivered_text, %s),
                       delivered_observation_id = COALESCE(delivered_observation_id, %s),
                       updated_at = CURRENT_TIMESTAMP
                 WHERE principal_id = %s AND owner_type = %s AND owner_id = %s
                   AND correlation_id = %s AND finalized = FALSE
                RETURNING {_STATE_COLUMNS}
                """,
                (text, observation.observation_id, *_space_values(space), correlation_id),
            ).fetchone()
            if row is None:
                raise AssistantOutputLifecycleError("assistant output is unavailable for delivery")
            updated = _state_from_row(row)
            if (
                updated.delivered_text != text
                or updated.delivered_observation_id != observation.observation_id
            ):
                raise AssistantOutputLifecycleError(
                    "delivery stage already committed with different content"
                )
        return updated, observation

    def update_experienced_prefix(
        self,
        *,
        space: MemorySpaceKey,
        correlation_id: str,
        text: str,
    ) -> AssistantOutputState:
        with self.database.transaction() as connection:
            row = connection.execute(
                f"""
                SELECT {_STATE_COLUMNS}
                  FROM omnix_memory_v2_assistant_outputs
                 WHERE principal_id = %s AND owner_type = %s AND owner_id = %s
                   AND correlation_id = %s
                 FOR UPDATE
                """,
                (*_space_values(space), correlation_id),
            ).fetchone()
            if row is None:
                raise AssistantOutputLifecycleError("assistant output not found")
            state = _state_from_row(row)
            if state.finalized:
                if state.experienced_prefix == text:
                    return state
                raise AssistantOutputLifecycleError("assistant output is already finalized")
            if state.delivered_text is None:
                raise AssistantOutputLifecycleError("delivery must be recorded before experience")
            if not state.delivered_text.startswith(text):
                raise AssistantOutputLifecycleError("experienced text must be a prefix of delivered text")
            if not text.startswith(state.experienced_prefix):
                raise AssistantOutputLifecycleError("experienced playback progress cannot move backward")
            updated_row = connection.execute(
                f"""
                UPDATE omnix_memory_v2_assistant_outputs
                   SET experienced_prefix = %s, updated_at = CURRENT_TIMESTAMP
                 WHERE principal_id = %s AND owner_type = %s AND owner_id = %s
                   AND correlation_id = %s
                RETURNING {_STATE_COLUMNS}
                """,
                (text, *_space_values(space), correlation_id),
            ).fetchone()
        if updated_row is None:  # pragma: no cover
            raise AssistantOutputLifecycleError("failed to persist playback progress")
        return _state_from_row(updated_row)

    def finalize_experience(
        self,
        *,
        space: MemorySpaceKey,
        correlation_id: str,
        occurred_at: datetime,
        provenance: ObservationProvenance,
    ) -> tuple[AssistantOutputState, Observation | None]:
        if provenance.source_type not in {"assistant", "system"}:
            raise AssistantOutputLifecycleError("experienced output provenance must be assistant or system")
        values = _space_values(space)
        with self.database.transaction() as connection:
            row = connection.execute(
                f"""
                SELECT {_STATE_COLUMNS}
                  FROM omnix_memory_v2_assistant_outputs
                 WHERE principal_id = %s AND owner_type = %s AND owner_id = %s
                   AND correlation_id = %s
                 FOR UPDATE
                """,
                (*values, correlation_id),
            ).fetchone()
            if row is None:
                raise AssistantOutputLifecycleError("assistant output not found")
            state = _state_from_row(row)
            if state.delivered_text is None:
                raise AssistantOutputLifecycleError("delivery must be recorded before finalization")
            observation: Observation | None
            if state.finalized:
                observation = (
                    self.observation_store.get(space, state.experienced_observation_id)
                    if state.experienced_observation_id
                    else None
                )
                return state, observation

            observation = None
            if state.experienced_prefix:
                observation = self.observation_store.append(
                    ObservationAppendRequest(
                        space=space,
                        visibility_scope=state.visibility_scope,
                        event_type="assistant_experienced",
                        occurred_at=occurred_at,
                        provenance=provenance,
                        idempotency_key=f"assistant-output:{correlation_id}:experienced",
                        payload={"text": state.experienced_prefix},
                        correlation_id=correlation_id,
                    )
                )

            updated_row = connection.execute(
                f"""
                UPDATE omnix_memory_v2_assistant_outputs
                   SET finalized = TRUE,
                       experienced_observation_id = %s,
                       updated_at = CURRENT_TIMESTAMP
                 WHERE principal_id = %s AND owner_type = %s AND owner_id = %s
                   AND correlation_id = %s
                RETURNING {_STATE_COLUMNS}
                """,
                (
                    observation.observation_id if observation is not None else None,
                    *values,
                    correlation_id,
                ),
            ).fetchone()
            if updated_row is None:  # pragma: no cover
                raise AssistantOutputLifecycleError("failed to finalize assistant output")
            finalized = _state_from_row(updated_row)
            if observation is not None and finalized.experienced_observation_id != observation.observation_id:
                raise AssistantOutputLifecycleError(
                    "experience finalization conflicts with existing observation"
                )
            return finalized, observation
