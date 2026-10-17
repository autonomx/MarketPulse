"""Publish all downstream consumer views from one canonical response."""
from __future__ import annotations

from typing import Any, Mapping

from .projections import canonical_consumer_bundle
from .publisher_guard import CANONICAL_PUBLISHER, publish_canonical_bundle
from .serialization import canonical_response_from_dict


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _patch_latest_interaction(
    session: dict[str, Any],
    *,
    response_id: str,
    turn_id: str,
    bundle: Mapping[str, Any],
    canonical: Mapping[str, Any],
    grounding: Mapping[str, Any],
    grounding_footer: Mapping[str, Any],
) -> bool:
    """Write the canonical projection into the turn's interaction; False when it was already there."""
    runtime = _mapping(session.get("runtime_state"))
    interactions = runtime.get("recent_interactions")
    if not isinstance(interactions, list) or not interactions:
        return False
    selected: dict[str, Any] | None = None
    for value in reversed(interactions):
        row = value if isinstance(value, dict) else None
        if row is None:
            continue
        row_turn = str(row.get("turn_id") or "")
        row_response = str(row.get("narrative_response_id") or "")
        if row_turn == turn_id or row_response == response_id:
            selected = row
            break
    if selected is None:
        selected = interactions[-1] if isinstance(interactions[-1], dict) else None
    if selected is None:
        return False
    visible = dict(bundle.get("visible_response") or {})
    if (
        selected.get("narrative_response_id") == response_id
        and selected.get("narrative_content_hash") == str(bundle.get("content_hash") or "")
        and selected.get("visible_response") == visible
    ):
        return False
    selected["narrative_response_id"] = response_id
    selected["narrative_content_hash"] = str(bundle.get("content_hash") or "")
    selected["narrative_publisher"] = CANONICAL_PUBLISHER
    selected["canonical_narrative_response"] = dict(canonical)
    selected["narrative_projections"] = dict(bundle)
    selected["visible_response"] = visible
    selected["narration"] = str(visible.get("narration") or "")
    if grounding:
        selected["narrative_grounding"] = dict(grounding)
    if grounding_footer:
        selected["narrative_grounding_footer"] = dict(grounding_footer)
    runtime["recent_interactions"] = interactions
    session["runtime_state"] = runtime
    return True


def patch_session_canonical_projection(result: Mapping[str, Any]) -> bool:
    """Put the canonical projection into the session before the turn commits (WP-5.6).

    The turn's committed state then carries it, so publishing the response
    afterwards finds it there and nothing saves the session a second time. The
    publisher guard's process counters stay out of the durable state.
    """
    canonical_raw = result.get("canonical_narrative_response")
    session = result.get("session")
    if not isinstance(canonical_raw, Mapping) or not isinstance(session, dict):
        return False
    response = canonical_response_from_dict(canonical_raw)
    return _patch_latest_interaction(
        session,
        response_id=response.response_id,
        turn_id=response.turn_id,
        bundle=canonical_consumer_bundle(response),
        canonical=response.as_dict(),
        grounding=_mapping(result.get("narrative_grounding")),
        grounding_footer=_mapping(result.get("narrative_grounding_footer")),
    )


def attach_canonical_consumer_bundle(result: dict[str, Any]) -> dict[str, Any]:
    """Attach canonical projections through the only production publisher gate."""
    if not isinstance(result, dict):
        return result
    canonical_raw = result.get("canonical_narrative_response")
    if not isinstance(canonical_raw, Mapping):
        return result
    response = canonical_response_from_dict(canonical_raw)
    canonical = response.as_dict()
    projected = canonical_consumer_bundle(response)
    bundle, telemetry_snapshot = publish_canonical_bundle(projected)
    telemetry = telemetry_snapshot.as_dict()
    visible = dict(bundle["visible_response"])
    grounding = _mapping(result.get("narrative_grounding"))
    grounding_footer = _mapping(result.get("narrative_grounding_footer"))
    result["canonical_narrative_response"] = canonical
    result["narrative_projections"] = bundle
    result["narrative_publisher"] = CANONICAL_PUBLISHER
    result["narrative_publisher_telemetry"] = telemetry
    result["visible_response"] = visible
    result["narration"] = str(visible.get("narration") or "")
    result["final_narration"] = result["narration"]
    result["summary"] = str(visible.get("plain_text") or "")
    result["narrative_consumer_bundle_attached"] = True

    nested = _mapping(result.get("result"))
    if nested:
        nested["canonical_narrative_response"] = canonical
        nested["narrative_projections"] = bundle
        nested["narrative_publisher"] = CANONICAL_PUBLISHER
        nested["narrative_publisher_telemetry"] = telemetry
        nested["visible_response"] = visible
        nested["narration"] = result["narration"]
        nested["summary"] = result["summary"]
        if grounding:
            nested["narrative_grounding"] = grounding
        if grounding_footer:
            nested["narrative_grounding_footer"] = grounding_footer
        result["result"] = nested

    session = result.get("session")
    if isinstance(session, dict):
        patched = _patch_latest_interaction(
            session,
            response_id=response.response_id,
            turn_id=response.turn_id,
            bundle=bundle,
            canonical=canonical,
            grounding=grounding,
            grounding_footer=grounding_footer,
        )
        result["narrative_session_projection_patched"] = patched
    return result
