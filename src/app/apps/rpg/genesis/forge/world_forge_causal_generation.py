"""Provider-facing generation contracts for causal World Forge domains."""
from __future__ import annotations

from typing import Any

from .world_forge_contract import CampaignTopicNode

_CAUSAL_TOPICS = frozenset(
    {
        "history_timeline",
        "regions",
        "places",
        "groups",
        "cultures",
        "actors",
        "causal_links",
    }
)


def causal_generation_contract(node: CampaignTopicNode) -> dict[str, Any]:
    """Return constraints embedded in provider payload metadata."""

    if node.topic_id not in _CAUSAL_TOPICS:
        return {}
    rules = [
        "Use only entity IDs supplied by dependencies, authoritative_entity_ids, or the current topic registry.",
        "When authoritative_entity_ids are present, return that exact ID set with no substitutions.",
        "The campaign_context.planning_slice is an authoritative constraint, not optional inspiration.",
        "Do not place invented IDs inside structured objects or prose.",
        "Treat typed historical references as causal constraints, not loose inspiration.",
        "Keep canonical causes in structured fields; presentation prose may only explain them.",
        "Distinguish a continuing effect from one that terminated, reversed, was absorbed, concealed, or forgotten.",
    ]
    if node.topic_id == "history_timeline":
        rules.extend(
            [
                "Use the historical_epoch_plan event allocation and ordering for each authoritative event ID.",
                "cause_event_ids may reference only earlier events allocated in this topic.",
                "Every continuing legacy must name a concrete present-day trace.",
            ]
        )
    elif node.topic_id == "places":
        rules.extend(
            [
                "For every place ID, copy region_id, founding_event_id, and founding_purpose from settlement_origin_plan into the corresponding typed fields.",
                "Do not move a planned settlement to another region or assign a different founding event.",
            ]
        )
    elif node.topic_id == "cultures":
        rules.extend(
            [
                "For every culture ID, copy homeland regions, origin event, and parent culture from culture_lineage_plan into the corresponding typed fields.",
                "Do not create a lineage cycle or replace a planned parent culture.",
            ]
        )
    elif node.topic_id == "groups":
        rules.append(
            "Represent the group's political_claim_graph entries inside inherited_claims without changing claimant or target IDs."
        )
    elif node.topic_id == "causal_links":
        rules.extend(
            [
                "Every link must connect supplied historical-event IDs to one supplied effect entity ID.",
                "The mechanism must explain the material, political, economic, geographic, or cultural process.",
                "Do not merely restate the effect_type as the mechanism.",
                "Use distinct cause/effect pairs across registry slots.",
            ]
        )
    else:
        rules.extend(
            [
                "Backward-pointing origin fields must agree with the supplied history timeline.",
                "Do not reinterpret an allocated founding or formation event.",
            ]
        )
    return {
        # Preserve the established contract identifier; the additions are backwards-compatible rules.
        "schema_version": "rpg_world_forge_causal_generation_v1",
        "topic_id": node.topic_id,
        "rules": rules,
        "authoritative_entity_ids": list(node.metadata.get("authoritative_entity_ids") or ()),
        "authoritative_fields": [
            str(row.get("field_id") or "")
            for row in node.metadata.get("field_definitions") or ()
            if str(row.get("semantic_role") or "")
            in {
                "caused_by",
                "formed_by",
                "founded_by",
                "originated_in",
                "origin_region",
                "descended_from",
                "shaped_by",
                "cultural_affiliation",
                "cause",
                "effect",
            }
        ],
    }


__all__ = ["causal_generation_contract"]
