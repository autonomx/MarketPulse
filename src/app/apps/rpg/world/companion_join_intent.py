from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict

from app.apps.rpg.world.npc_party_eligibility import evaluate_npc_party_join_eligibility
from app.apps.rpg.foundation.safe_values import safe_str as _safe_str

JOIN_REQUEST_MARKERS = {
    "join me",
    "come with me",
    "travel with me",
    "fight with me",
    "help me",
    "come along",
    "adventure with me",
}


def player_input_requests_join(player_input: Any) -> bool:
    text = _safe_str(player_input).lower()
    return any(marker in text for marker in JOIN_REQUEST_MARKERS)


def maybe_create_companion_join_intent(
    simulation_state: Dict[str, Any],
    *,
    npc_id: str,
    player_input: str,
) -> Dict[str, Any]:
    requested = player_input_requests_join(player_input)
    eligibility = evaluate_npc_party_join_eligibility(
        simulation_state,
        npc_id=npc_id,
    )

    if not requested:
        return {
            "offered": False,
            "requested": False,
            "reason": "player_did_not_request_join",
            "party_join_eligibility_result": deepcopy(eligibility),
            "source": "deterministic_companion_join_intent",
        }

    if not eligibility.get("eligible"):
        return {
            "offered": False,
            "requested": True,
            "npc_id": npc_id,
            "reason": _safe_str(eligibility.get("reason") or "npc_not_eligible"),
            "party_join_eligibility_result": deepcopy(eligibility),
            "source": "deterministic_companion_join_intent",
        }

    return {
        "offered": True,
        "requested": True,
        "npc_id": npc_id,
        "reason": _safe_str(eligibility.get("reason") or "eligible_to_join"),
        "requires_player_acceptance": True,
        "party_join_eligibility_result": deepcopy(eligibility),
        "source": "deterministic_companion_join_intent",
    }
