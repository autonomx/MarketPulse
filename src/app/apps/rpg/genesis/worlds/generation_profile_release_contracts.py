"""Explicit launch contracts for modern profile-generated World Forge graphs."""
from __future__ import annotations

from typing import Any, Mapping

_STARTING_MARKET_DOMAINS = ("places", "actors", "equipment_vehicles")
_STARTER_BUBBLE_DOMAINS = (
    "regions",
    "places",
    "actors",
    "equipment_vehicles",
)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def require_profile_release_contracts(
    graph: Mapping[str, Any],
) -> dict[str, Any]:
    """Pin required playable-release contracts for supported modern profiles."""

    payload = dict(graph)
    metadata = _mapping(payload.get("metadata"))
    modern_profile = bool(
        metadata.get("genre_profile_id")
        or metadata.get("resolved_profile")
        or str(payload.get("graph_version") or "").startswith(
            "rpg_profile_topic_graph_"
        )
    )
    if not modern_profile:
        return payload
    domain_ids = {
        str(row.get("topic_id") or "")
        for row in payload.get("nodes") or ()
        if isinstance(row, Mapping) and str(row.get("topic_id") or "")
    }
    if set(_STARTING_MARKET_DOMAINS).issubset(domain_ids):
        metadata["starting_market_contract"] = {
            **_mapping(metadata.get("starting_market_contract")),
            "schema_version": "rpg_world_starting_market_contract_v1",
            "required": True,
            "required_before_launch": True,
            "domain_ids": list(_STARTING_MARKET_DOMAINS),
        }
    if set(_STARTER_BUBBLE_DOMAINS).issubset(domain_ids):
        metadata["starter_bubble_contract"] = {
            **_mapping(metadata.get("starter_bubble_contract")),
            "schema_version": "rpg_world_starter_bubble_contract_v1",
            "required": True,
            "required_before_launch": True,
            "domain_ids": list(_STARTER_BUBBLE_DOMAINS),
        }
    payload["metadata"] = metadata
    return payload


__all__ = ["require_profile_release_contracts"]
