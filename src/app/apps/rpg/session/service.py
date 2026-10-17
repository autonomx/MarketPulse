"""Phase 15.3 — Canonical session service."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from app.apps.rpg.foundation.core.determinism import rng_seed_from_session_id
from app.apps.rpg.world.map_persistence import ensure_session_map_state
from app.apps.rpg.session.ambient_builder import (
    ensure_ambient_runtime_state,
    normalize_ambient_state,
)
from app.apps.rpg.session.durable_store import (
    archive_session_on_disk,
    list_sessions_from_disk,
    load_persisted_rng_seed,
    load_session_from_disk,
    save_session_to_disk,
)
from app.apps.rpg.session.environment import ensure_session_environment_seed_state
from app.apps.rpg.session.list_summaries import list_session_summaries_from_disk
from app.apps.rpg.session.migrations import migrate_session_payload
from app.apps.rpg.session.published_opening_progress import (
    ensure_published_opening_progress,
)
from app.apps.rpg.session.survival_persistence import normalize_session_survival_for_persistence
from app.apps.rpg.rules.validation.integrity import (
    assert_session_integrity,
    validate_session_integrity,
)
from app.apps.rpg.foundation.performance_trace import rpg_pipeline_span_if_active
from app.apps.rpg.foundation.persistence.rpg_session_save_policy import (
    cached_turn_session,
    remember_turn_session,
    session_save_deferred,
    turn_persisted_rng_seed,
)
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict

if TYPE_CHECKING:
    from app.apps.rpg.genesis.contracts import CampaignSessions


def create_or_normalize_session(session: dict[str, Any], *, owned: bool = False) -> dict[str, Any]:
    """Migrate and normalize a session; ``owned`` when the caller just decoded it from storage."""
    session = _safe_dict(session)
    session = migrate_session_payload(session)
    manifest = _safe_dict(session.get("manifest"))
    session["manifest"] = manifest
    session.setdefault("installed_packs", [])
    session.setdefault("simulation_state", {})
    simulation_state = dict(_safe_dict(session.get("simulation_state")))
    rng_seed = simulation_state.get("rng_seed")
    if (
        not isinstance(rng_seed, int)
        or isinstance(rng_seed, bool)
        or not 0 <= rng_seed < 2**64
    ):
        manifest = _safe_dict(session.get("manifest"))
        session_id = str(
            session.get("session_id")
            or session.get("id")
            or manifest.get("session_id")
            or manifest.get("id")
            or ""
        )
        rng_seed = rng_seed_from_session_id(session_id)
        simulation_state["rng_seed"] = rng_seed
    session["simulation_state"] = simulation_state
    session = ensure_session_environment_seed_state(session)
    session = normalize_session_survival_for_persistence(session, owned=owned)
    session = ensure_session_map_state(session)
    # Living-world: ensure ambient runtime state exists and is bounded
    runtime_state = _safe_dict(session.get("runtime_state"))
    runtime_state = ensure_ambient_runtime_state(runtime_state)
    runtime_state = normalize_ambient_state(runtime_state)
    session["runtime_state"] = runtime_state
    return ensure_published_opening_progress(session)


def save_session(session: dict[str, Any], *, compact: bool = False) -> dict[str, Any]:
    manifest = _safe_dict(_safe_dict(session).get("manifest"))
    session_id = str(
        manifest.get("session_id")
        or manifest.get("id")
        or _safe_dict(session).get("session_id")
        or _safe_dict(session).get("id")
        or ""
    )
    with rpg_pipeline_span_if_active(
        "session.save_total",
        fields={"session_id": session_id, "compact": compact},
    ) as span:
        saved = _save_session(session, compact=compact)
        if span is not None:
            saved_manifest = _safe_dict(saved.get("manifest"))
            runtime_state = _safe_dict(saved.get("runtime_state"))
            span["session_id"] = str(
                saved_manifest.get("session_id")
                or saved_manifest.get("id")
                or session_id
            )
            span["interaction_seq"] = runtime_state.get("interaction_seq")
            span["state_revision"] = runtime_state.get("state_revision")
        return saved


def _save_session(session: dict[str, Any], *, compact: bool = False) -> dict[str, Any]:
    session = _safe_dict(session)
    manifest = _safe_dict(session.get("manifest"))
    session_id = str(
        session.get("session_id")
        or session.get("id")
        or manifest.get("session_id")
        or manifest.get("id")
        or ""
    )
    if session_id:
        # The seed is immutable: compare with the saved one, without loading the saved game.
        saved, previous_seed = turn_persisted_rng_seed(session_id, load_persisted_rng_seed)
        if saved:
            if (
                not isinstance(previous_seed, int)
                or isinstance(previous_seed, bool)
                or not 0 <= previous_seed < 2**64
            ):
                previous_seed = rng_seed_from_session_id(session_id)
            simulation_state = dict(_safe_dict(session.get("simulation_state")))
            submitted_seed = simulation_state.get("rng_seed")
            if (
                isinstance(submitted_seed, int)
                and not isinstance(submitted_seed, bool)
                and submitted_seed != previous_seed
            ):
                raise ValueError("RPG session rng_seed is immutable")
            simulation_state["rng_seed"] = previous_seed
            session["simulation_state"] = simulation_state
    # A deferred save (inside a turn) writes nothing yet: normalize the caller's
    # session in place instead of copying the whole saved game (WP-5.6).
    session = create_or_normalize_session(session, owned=session_save_deferred())
    assert_session_integrity(session)
    return save_session_to_disk(session, compact=compact)


def load_session(session_id: str) -> dict[str, Any]:
    with rpg_pipeline_span_if_active(
        "session.load_total",
        fields={"session_id": session_id},
    ) as span:
        turn_scoped = _turn_scoped_loads()
        if turn_scoped:
            cached = cached_turn_session(session_id)
            if cached is not None:
                if span is not None:
                    span["turn_cache_hit"] = True
                return cached
        session = _load_session(session_id)
        if not isinstance(session, dict):
            # A missing session is returned as None (callers check for it).
            return cast(Any, session)
        from .interaction_event_store import load_and_replay_interaction_events

        session = load_and_replay_interaction_events(session_id, session)
        from .interaction_lifecycle import recover_pending_interaction_narration

        try:
            recovered = recover_pending_interaction_narration(session_id, session)
        except Exception as exc:
            from app.apps.rpg.foundation.debug_logging import log_rpg_event

            log_rpg_event(
                "turn.stage.degraded",
                category="performance",
                level="warning",
                session_id=session_id,
                fields={
                    "metric": "rpg_turn_stage_degraded",
                    "stage": "interaction_narration_recovery",
                    "degraded_stage_count": 1,
                    "error_type": type(exc).__name__,
                },
                error=exc,
            )
        else:
            if span is not None:
                span["pending_narration_recovered"] = recovered
        if span is not None:
            manifest = _safe_dict(session.get("manifest"))
            runtime_state = _safe_dict(session.get("runtime_state"))
            span["session_id"] = str(
                manifest.get("session_id")
                or manifest.get("id")
                or session_id
            )
            span["interaction_seq"] = runtime_state.get("interaction_seq")
            span["state_revision"] = runtime_state.get("state_revision")
        if turn_scoped:
            remember_turn_session(session_id, session)
        return session


def _load_session(session_id: str) -> dict[str, Any] | None:
    session = load_session_from_disk(session_id)
    if session is None:
        return None
    # Fix #2: soft validation on load — allow recovery / migration of older sessions
    session = create_or_normalize_session(session, owned=True)
    validate_session_integrity(session)  # log but don't fail
    return session


def _turn_scoped_loads() -> bool:
    """Whether this load is inside a turn that defers its writes to PostgreSQL."""
    from app.persistence.runtime import uses_postgresql_runtime

    return session_save_deferred() and uses_postgresql_runtime()


def list_sessions() -> list[dict[str, Any]]:
    sessions = list_sessions_from_disk()
    out = []
    for item in sessions:
        item = create_or_normalize_session(item)
        # Fix #3: attach integrity info instead of hiding invalid sessions
        integrity = validate_session_integrity(item)
        item["_integrity"] = integrity
        out.append(item)
    return out


def list_session_summaries(*, limit: int | None = None) -> list[dict[str, Any]]:
    """Return bounded session list rows without normalizing full payloads."""

    with rpg_pipeline_span_if_active(
        "session.list_summaries_total",
        fields={"operation": "list_session_summaries"},
    ) as span:
        out = _list_session_summaries(limit=limit)
        if span is not None:
            span["result_count"] = len(out)
        return out


def _list_session_summaries(*, limit: int | None = None) -> list[dict[str, Any]]:
    out = []
    for item in list_session_summaries_from_disk(limit=limit):
        integrity = validate_session_integrity(item)
        item["_integrity"] = integrity
        out.append(item)
    return out


def archive_session(session_id: str) -> dict[str, Any]:
    return archive_session_on_disk(session_id)


# The campaign session store the World Forge uses (genesis.contracts.CampaignSessions,
# R-3): genesis does not import the session pipeline, so the edge and the
# session pass this to the forge functions that load, save, archive or
# create campaign sessions. New-game helpers load lazily, as before.
class SessionCampaignSessions:
    def load(self, session_id: str) -> dict[str, Any] | None:
        return load_session(session_id)

    def save(self, session: dict[str, Any], *, compact: bool = False) -> dict[str, Any]:
        return save_session(session, compact=compact)

    def archive(self, session_id: str) -> dict[str, Any]:
        return archive_session(session_id)

    def new_game_request(self, payload: dict[str, Any]) -> Any:
        from app.apps.rpg.session.new_game import RpgNewGameRequest

        return RpgNewGameRequest.model_validate(payload)

    def create_new_game(self, request: Any) -> dict[str, Any]:
        from app.apps.rpg.session.new_game import create_new_game_session

        return create_new_game_session(request)

    def build_new_game(self, request: Any) -> dict[str, Any]:
        from app.apps.rpg.session.new_game import _build_new_game_session

        return _build_new_game_session(request)

    def save_created(self, session: dict[str, Any]) -> dict[str, Any]:
        from app.apps.rpg.session.new_game import _save_created_session

        return _save_created_session(session)


SESSION_CAMPAIGN_SESSIONS: CampaignSessions = SessionCampaignSessions()
