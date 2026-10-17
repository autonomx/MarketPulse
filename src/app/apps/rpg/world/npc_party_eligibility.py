from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict

from app.apps.rpg.world.npc_biography_registry import get_npc_biography
from app.apps.rpg.world.npc_evolution_state import get_npc_evolution
from app.apps.rpg.world.npc_reputation_state import get_npc_reputation
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def evaluate_npc_party_join_eligibility(
    simulation_state: Dict[str, Any],
    *,
    npc_id: str,
) -> Dict[str, Any]:
    npc_id = _safe_str(npc_id)
    if not npc_id.startswith("npc:"):
        return {
            "eligible": False,
            "reason": "invalid_npc_id",
            "source": "deterministic_npc_party_eligibility",
        }

    bio = get_npc_biography(npc_id)
    evo = get_npc_evolution(simulation_state, npc_id=npc_id)
    rep = get_npc_reputation(simulation_state, npc_id=npc_id)

    party = _safe_dict(evo.get("party_join_eligibility"))
    required_trust = _safe_int(party.get("requires_player_trust"), 0)
    trust = _safe_int(rep.get("trust"), 0)

    if not party.get("eligible"):
        return {
            "eligible": False,
            "npc_id": npc_id,
            "name": _safe_str(bio.get("name")) or npc_id.replace("npc:", ""),
            "reason": "no_party_join_arc",
            "source": "deterministic_npc_party_eligibility",
        }

    if trust < required_trust:
        return {
            "eligible": False,
            "npc_id": npc_id,
            "name": _safe_str(bio.get("name")) or npc_id.replace("npc:", ""),
            "reason": "insufficient_trust",
            "required_trust": required_trust,
            "current_trust": trust,
            "party_join_eligibility": deepcopy(party),
            "source": "deterministic_npc_party_eligibility",
        }

    return {
        "eligible": True,
        "npc_id": npc_id,
        "name": _safe_str(bio.get("name")) or npc_id.replace("npc:", ""),
        "reason": _safe_str(party.get("reason") or "party_join_arc_available"),
        "current_role": _safe_str(evo.get("current_role") or bio.get("role")),
        "identity_arc": _safe_str(evo.get("identity_arc")),
        "active_motivations": deepcopy(_safe_list(evo.get("active_motivations"))),
        "party_join_eligibility": deepcopy(party),
        "source": "deterministic_npc_party_eligibility",
    }


