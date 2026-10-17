from __future__ import annotations

from typing import Any, Dict

from app.apps.rpg.world.social.reputation import get_global_reputation, get_relationship


def build_social_dialogue_context(
    simulation_state: Dict[str, Any],
    npc_id: str,
    *,
    actor_id: str = "player",
) -> Dict[str, Any]:
    relationship = get_relationship(simulation_state, npc_id)
    leverage = list(relationship.get("leverage") or [])[:10]
    return {
        "npc_id": npc_id,
        "actor_id": actor_id,
        "relationship": {
            "trust": relationship.get("trust"),
            "fear": relationship.get("fear"),
            "respect": relationship.get("respect"),
            "hostility": relationship.get("hostility"),
            "reputation": relationship.get("reputation"),
            "last_stance": relationship.get("last_stance"),
        },
        "global_reputation": get_global_reputation(simulation_state, actor_id),
        "available_leverage": [
            {
                "leverage_id": row.get("leverage_id"),
                "kind": row.get("kind"),
                "summary": row.get("summary"),
                "strength": row.get("strength"),
                "valid": row.get("valid"),
                "tags": list(row.get("tags") or [])[:8],
            }
            for row in leverage
            if isinstance(row, dict)
        ],
        "social_truth_rule": (
            "This social context is deterministic simulation state. "
            "Do not invent trust, fear, leverage, reputation, or social outcomes."
        ),
    }