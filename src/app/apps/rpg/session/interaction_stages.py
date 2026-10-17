"""Required interaction commit and optional narration lifecycle stages."""
from __future__ import annotations

from typing import Any

from app.apps.rpg.session.jobs.foreground_context import DIRECT_RPG_SUBMISSION_ID
from app.apps.rpg.foundation.performance_trace import rpg_pipeline_span


def commit_interaction(ctx: Any) -> Any:
    """Append and durably commit one resolved turn before post-commit work."""

    result = ctx.result
    if not isinstance(result, dict) or result.get("ok") is not True:
        return ctx
    if result.get("interaction_persisted") is True:
        return ctx

    session = result.get("session")
    session_override = ctx.session_override
    if not isinstance(session, dict) and isinstance(session_override, dict):
        session = session_override
    if not isinstance(session, dict):
        from .service import load_session

        session = load_session(ctx.session_id)
    if not isinstance(session, dict):
        return ctx

    from .interaction_timeline import commit_turn_interaction, mark_interaction_persisted

    submission_id = str(DIRECT_RPG_SUBMISSION_ID.get("") or "").strip()
    with rpg_pipeline_span("turn.interaction_append") as interaction_span:
        session, result, event = commit_turn_interaction(
            session,
            result,
            player_input=ctx.player_input,
            submission_id=submission_id,
            trace_id=str(result.get("trace_id") or ctx.trace_id),
        )
        interaction_span["interaction_id"] = event.get("interaction_id")
        interaction_span["sequence"] = event.get("sequence")
        interaction_span["stateful"] = event.get("stateful")
    result["session"] = session

    from .narrative_engine_bridge import canonicalize_resolved_turn_result

    with rpg_pipeline_span("turn.canonical_after_interaction") as narrative_span:
        result = canonicalize_resolved_turn_result(
            result,
            session_id=ctx.session_id,
            player_input=ctx.player_input,
        )
        canonical = result.get("canonical_narrative_response")
        narrative_span["response_id"] = (
            canonical.get("response_id") if isinstance(canonical, dict) else None
        )
        narrative_span["content_hash"] = (
            canonical.get("content_hash") if isinstance(canonical, dict) else None
        )
        # The lifecycle record and the canonical projection are part of the
        # committed state (WP-5.6): recording them after the commit made a
        # second save, a revision with no turn behind it. The lifecycle goes
        # first because it rebuilds recent_interactions from the timeline.
        from app.apps.rpg.narration.narrative_engine.consumer_publish import (
            patch_session_canonical_projection,
        )

        from .interaction_lifecycle import initialize_interaction_lifecycle

        initialize_interaction_lifecycle(session, result)
        narrative_span["session_projection_patched"] = patch_session_canonical_projection(result)

    if isinstance(session_override, dict):
        session_override.clear()
        session_override.update(session)
        result["interaction_persisted"] = False
        result["interaction_persistence"] = {
            "format_version": "rpg_interaction_persistence_v2",
            "mode": "session_override",
            "persisted": False,
        }
        ctx.result = result
        return ctx

    from app.persistence.runtime import uses_postgresql_runtime

    if uses_postgresql_runtime():
        from app.jobs.foreground_execution import current_foreground_execution
        from app.apps.rpg.session.persistence.rpg_turn_service import persist_foreground_turn

        execution = current_foreground_execution()
        result["session"] = session
        with rpg_pipeline_span("turn.postgresql_commit") as transaction_span:
            transaction = persist_foreground_turn(
                session_id=ctx.session_id,
                player_input=ctx.player_input,
                session=session,
                result=result,
                event=event,
                submission_id=submission_id,
                submission_claim_token=(
                    execution.claim_token
                    if execution is not None
                    and execution.session_id == ctx.session_id
                    and execution.submission_id == submission_id
                    else None
                ),
            )
            transaction_span["interaction_id"] = event.get("interaction_id")
            transaction_span["sequence"] = event.get("sequence")
            transaction_span["submission_id"] = submission_id
            transaction_span["narrative_response_id"] = transaction.get(
                "narrative_response_id"
            )
        mark_interaction_persisted(result)
        result["interaction_persistence"] = {
            "format_version": "rpg_interaction_persistence_v4",
            "interaction_id": event.get("interaction_id"),
            "sequence": event.get("sequence"),
            "state_revision": event.get("state_revision"),
            "submission_id": submission_id,
            "persisted": True,
            "mode": "postgresql_unit_of_work",
            "snapshot_written": transaction.get("snapshot") is not None,
            "turn_id": (transaction.get("turn") or {}).get("id"),
            "job_id": (transaction.get("job") or {}).get("id"),
            "narrative_response_id": transaction.get("narrative_response_id"),
            "narrative_content_hash": transaction.get("narrative_content_hash"),
            "narrative_atomic_with_turn": transaction.get(
                "narrative_atomic_with_turn"
            ) is True,
        }
        ctx.result = result
        return ctx

    from .interaction_event_store import (
        append_interaction_event,
        compact_interaction_event_log,
        interaction_event_log_status,
        interaction_log_requires_compaction,
    )

    with rpg_pipeline_span("turn.interaction_event_write") as event_span:
        append_interaction_event(ctx.session_id, event)
        event_span["sequence"] = event.get("sequence")
    snapshot_required = (
        event.get("stateful") is not False
        or interaction_log_requires_compaction(ctx.session_id)
    )
    persistence_mode = "event_log"
    if snapshot_required:
        from .service import save_session

        with rpg_pipeline_span("turn.session_snapshot_write") as snapshot_span:
            saved = save_session(session, compact=True)
            snapshot_span["sequence"] = event.get("sequence")
            snapshot_span["stateful"] = event.get("stateful")
        result["session"] = saved
        with rpg_pipeline_span("turn.interaction_log_compaction") as compact_span:
            compact_interaction_event_log(
                ctx.session_id,
                through_sequence=int(event.get("sequence") or 0),
            )
            compact_span["through_sequence"] = event.get("sequence")
        persistence_mode = "snapshot_compacted"

    mark_interaction_persisted(result)
    result["interaction_persistence"] = {
        "format_version": "rpg_interaction_persistence_v2",
        "interaction_id": event.get("interaction_id"),
        "sequence": event.get("sequence"),
        "state_revision": event.get("state_revision"),
        "persisted": True,
        "mode": persistence_mode,
        "snapshot_written": snapshot_required,
        "event_log": interaction_event_log_status(ctx.session_id),
    }
    ctx.result = result
    return ctx


def initialize_lifecycle(ctx: Any) -> Any:
    """Enqueue deferred narration for the committed interaction.

    The commit already recorded the interaction's lifecycle; it is recorded
    here only for a result that did not pass through ``commit_interaction``.
    """

    result = ctx.result
    if not isinstance(result, dict) or result.get("ok") is not True:
        return ctx
    session = result.get("session")
    if not isinstance(session, dict) and isinstance(ctx.session_override, dict):
        session = ctx.session_override
    if not isinstance(session, dict):
        from .service import load_session

        session = load_session(ctx.session_id)
    if not isinstance(session, dict):
        return ctx

    from .interaction_lifecycle import (
        initialize_interaction_lifecycle as initialize,
        queue_deferred_narration_for_interaction,
    )

    lifecycle = result.get("interaction_lifecycle")
    if not isinstance(lifecycle, dict) or lifecycle.get("interaction_id") != result.get("interaction_id"):
        lifecycle = initialize(session, result)
    result["session"] = session
    if isinstance(ctx.session_override, dict):
        ctx.session_override.clear()
        ctx.session_override.update(session)
    ctx.result = result
    if lifecycle.get("status") == "narration_pending":
        try:
            queue_deferred_narration_for_interaction(ctx.session_id, result)
        except Exception as exc:
            result["narration_status"] = "failed_to_queue"
            raise exc
    return ctx


__all__ = ["commit_interaction", "initialize_lifecycle"]
