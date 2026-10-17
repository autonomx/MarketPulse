"""Phase 6.5 — Betrayal Propagation.

Deterministic propagation of betrayal events into social fallout.
Emits social_shock and trust_collapse events for downstream systems.
"""

from __future__ import annotations

from typing import Any, Dict, List
from app.apps.rpg.foundation.safe_values import safe_str as _safe_str


class BetrayalPropagation:
    """Propagate betrayal events into social fallout signals.

    This is a static utility — no internal state.
    """

    @staticmethod
    def apply(event: Dict[str, Any], social_state: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Apply betrayal event and return social fallout events.

        Returns a list of social_shock and trust_collapse events.
        """
        event = event or {}
        social_state = social_state or {}
        if _safe_str(event.get("type")) != "betrayal":
            return []

        source_id = _safe_str(event.get("source_id"))
        target_id = _safe_str(event.get("target_id"))
        faction_id = _safe_str(event.get("faction_id"))
        location_id = _safe_str(event.get("location_id"))

        out: List[Dict[str, Any]] = []

        if faction_id:
            out.append({
                "type": "social_shock",
                "origin": "betrayal_propagation",
                "source_id": source_id,
                "target_id": faction_id,
                "target_kind": "faction",
                "location_id": location_id,
                "summary": f"Betrayal involving {source_id or 'unknown'} destabilized faction '{faction_id}'.",
                "severity": "negative",
                "salience": 0.8,
            })

        if target_id:
            out.append({
                "type": "trust_collapse",
                "origin": "betrayal_propagation",
                "source_id": source_id,
                "target_id": target_id,
                "target_kind": "entity",
                "location_id": location_id,
                "summary": f"Trust collapsed between '{source_id}' and '{target_id}'.",
                "severity": "negative",
                "salience": 0.9,
            })

        return out[:4]