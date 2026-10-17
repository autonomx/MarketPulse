from __future__ import annotations

from typing import Any, Dict

from app.apps.rpg.world.npc_evolution_state import apply_npc_evolution_event
from app.apps.rpg.world.npc_reputation_state import get_npc_reputation
from app.apps.rpg.foundation.safe_values import safe_str as _safe_str


def evolve_npc_from_reputation_thresholds(
    simulation_state: Dict[str, Any],
    *,
    npc_id: str,
    tick: int,
) -> Dict[str, Any]:
    npc_id = _safe_str(npc_id)
    if not npc_id.startswith("npc:"):
        return {"applied": False, "reason": "invalid_npc_id"}

    rep = get_npc_reputation(simulation_state, npc_id=npc_id)
    trust = int(rep.get("trust") or 0)
    annoyance = int(rep.get("annoyance") or 0)

    if trust >= 4:
        return apply_npc_evolution_event(
            simulation_state,
            npc_id=npc_id,
            event_id=f"reputation:trust:{npc_id}:4",
            kind="trust_threshold",
            personality_modifier={
                "trait": "loyal_to_player",
                "strength": 1,
                "reason": "The player has repeatedly earned trust.",
            },
            motivation={
                "kind": "support_player",
                "summary": "Help the player when it does not violate core values.",
                "strength": 2,
            },
            tick=tick,
        )

    if annoyance >= 4:
        return apply_npc_evolution_event(
            simulation_state,
            npc_id=npc_id,
            event_id=f"reputation:annoyance:{npc_id}:4",
            kind="annoyance_threshold",
            personality_modifier={
                "trait": "guarded_against_player",
                "strength": 1,
                "reason": "The player has repeatedly irritated or pressured this NPC.",
            },
            motivation={
                "kind": "avoid_player_pressure",
                "summary": "Limit what is shared with the player.",
                "strength": 2,
            },
            tick=tick,
        )

    return {
        "applied": False,
        "reason": "no_reputation_threshold_crossed",
        "source": "deterministic_npc_evolution_trigger_runtime",
    }
