"""Hydrate bounded foreground replays from durable canonical narrative authority."""
from __future__ import annotations

from typing import Any, Mapping

from app.apps.rpg.narration.narrative_engine.repository import NarrativeResponseRepository
from app.apps.rpg.narration.narrative_reference import compact_canonical_narrative_reference


class CanonicalNarrativeReplayError(RuntimeError):
    """Raised when a durable replay reference cannot be satisfied exactly."""


def hydrate_canonical_narrative_replay(
    result: Mapping[str, Any],
    *,
    campaign_id: str,
    repository: NarrativeResponseRepository | None = None,
) -> dict[str, Any]:
    """Attach the immutable canonical response named by a replay-safe reference."""

    output = dict(result)
    if not output.get("idempotent_replay"):
        return output
    if isinstance(output.get("canonical_narrative_response"), Mapping):
        return output

    reference = compact_canonical_narrative_reference(
        output.get("canonical_narrative")
    )
    if not reference:
        output["canonical_narrative_replay"] = {
            "status": "legacy_record_without_reference",
            "hydrated": False,
        }
        return output
    if reference["campaign_id"] != campaign_id:
        raise CanonicalNarrativeReplayError(
            "canonical replay campaign mismatch: "
            f"{reference['campaign_id']}/{campaign_id}"
        )

    if repository is None:
        from app.apps.rpg.narration.narrative_repository import (
            build_production_narrative_repository,
        )

        repository = build_production_narrative_repository()

    response = repository.get_for_turn(campaign_id, reference["turn_id"])
    if response is None:
        response = repository.get(reference["response_id"])
    if response is None:
        raise CanonicalNarrativeReplayError(
            "canonical replay response is unavailable: "
            f"{campaign_id}/{reference['turn_id']}"
        )
    if response.response_id != reference["response_id"]:
        raise CanonicalNarrativeReplayError("canonical replay response identity mismatch")
    if response.content_hash != reference["content_hash"]:
        raise CanonicalNarrativeReplayError("canonical replay content hash mismatch")
    if response.campaign_id != campaign_id or response.turn_id != reference["turn_id"]:
        raise CanonicalNarrativeReplayError("canonical replay turn identity mismatch")

    output["canonical_narrative_response"] = response.as_dict()
    output["canonical_narrative_source"] = "durable_submission_replay_v1"
    output["canonical_narrative_replay"] = {
        "status": "hydrated",
        "hydrated": True,
        "response_id": response.response_id,
        "content_hash": response.content_hash,
        "campaign_id": response.campaign_id,
        "turn_id": response.turn_id,
        "revision": response.revision,
    }
    return output
