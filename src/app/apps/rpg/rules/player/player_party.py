"""Phase 9.2/9.3/10 — Player party view helpers.

Provides helpers to build party views for the player-facing UI,
including Phase 9.3 companion narrative presence summaries
and Phase 10 presentation speaker cards.
"""
from typing import Any, Dict

from app.apps.rpg.rules.party import (
    ensure_party_state,
)
from app.apps.rpg.foundation.safe_values import dict_copy as _safe_dict


def ensure_player_party(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure simulation_state has normalized party state."""
    simulation_state = _safe_dict(simulation_state)
    player_state = _safe_dict(simulation_state.get("player_state"))
    player_state = ensure_party_state(player_state)
    simulation_state["player_state"] = player_state
    return simulation_state


