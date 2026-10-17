from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str


def build_companion_join_dialogue(
    *,
    npc_id: str,
    npc_name: str,
    acceptance_result: Dict[str, Any],
) -> Dict[str, Any]:
    result = _safe_dict(acceptance_result)
    if not result.get("accepted"):
        return {
            "created": False,
            "reason": "companion_not_accepted",
            "source": "deterministic_companion_dialogue",
        }

    eligibility = _safe_dict(result.get("party_join_eligibility_result"))
    identity_arc = _safe_str(eligibility.get("identity_arc"))
    motivations = _safe_list(eligibility.get("active_motivations"))

    motivation_text = ""
    if motivations:
        motivation_text = _safe_str(_safe_dict(motivations[0]).get("summary"))

    name = _safe_str(npc_name) or _safe_str(eligibility.get("name")) or _safe_str(npc_id).replace("npc:", "")

    if identity_arc == "revenge_after_losing_tavern":
        line = (
            f"{name} nods, jaw tight. "
            "\"Then I walk with you until the bandits answer for what they did.\""
        )
    elif motivation_text:
        line = f"{name} nods. \"Then I am with you. {motivation_text}\""
    else:
        line = f"{name} nods. \"Then I am with you.\""

    beat = {
        "kind": "companion_join_dialogue",
        "speaker_id": _safe_str(npc_id),
        "speaker_name": name,
        "line": line,
        "acceptance_result": deepcopy(result),
        "source": "deterministic_companion_dialogue",
    }

    return {
        "created": True,
        "beat": beat,
        "line": line,
        "source": "deterministic_companion_dialogue",
    }


def build_companion_presence_summary(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    player_state = _safe_dict(simulation_state.get("player_state"))
    party_state = _safe_dict(player_state.get("party_state"))
    companions = _safe_list(party_state.get("companions"))

    active = []
    for comp in companions[:6]:
        comp = _safe_dict(comp)
        if _safe_str(comp.get("status") or "active") == "active":
            active.append({
                "npc_id": _safe_str(comp.get("npc_id")),
                "name": _safe_str(comp.get("name")),
                "role": _safe_str(comp.get("role")),
                "identity_arc": _safe_str(comp.get("identity_arc")),
                "current_role": _safe_str(comp.get("current_role")),
            })

    return {
        "active_companions": active,
        "count": len(active),
        "source": "deterministic_companion_dialogue",
    }
