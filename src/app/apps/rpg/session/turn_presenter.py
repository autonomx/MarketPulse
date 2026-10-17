"""Single production entry point from authoritative turn result to presentation."""
from __future__ import annotations

from typing import Any

from .narrative_engine_bridge import canonicalize_resolved_turn_result


class TurnPresentationInvariantError(RuntimeError):
    pass


def present_authoritative_turn(
    result: dict[str, Any],
    *,
    session_id: str,
    player_input: str,
) -> dict[str, Any]:
    """Create or adopt exactly one canonical response for one interaction."""

    if not isinstance(result, dict) or result.get("ok") is not True:
        return result
    before = result.get("canonical_narrative_response")
    presented = canonicalize_resolved_turn_result(
        result,
        session_id=session_id,
        player_input=player_input,
    )
    canonical = presented.get("canonical_narrative_response")
    if not isinstance(canonical, dict):
        raise TurnPresentationInvariantError(
            "authoritative turn produced no canonical narrative response"
        )
    before_id = before.get("response_id") if isinstance(before, dict) else None
    after_id = canonical.get("response_id")
    if before_id and before_id != after_id:
        raise TurnPresentationInvariantError(
            f"canonical response identity changed: {before_id} -> {after_id}"
        )
    count = int(presented.get("turn_presentation_request_count") or 0)
    if count not in {0, 1}:
        raise TurnPresentationInvariantError(
            f"interaction already has {count} presentation requests"
        )
    presented["turn_presentation_request_count"] = 1
    presented["turn_presentation_response_id"] = str(after_id or "")
    presented["turn_presentation_entry_point"] = "present_authoritative_turn_v1"
    return presented
