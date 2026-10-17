"""Phase 6.5 — Group Decision Engine.

Aggregates NPC beliefs within a faction to determine the faction's
collective stance toward a target (typically the player).
"""

from __future__ import annotations

from typing import Any, Dict
from app.apps.rpg.foundation.safe_values import safe_str as _safe_str


class GroupDecisionEngine:
    """Deterministic group decision engine.

    Evaluates each faction's collective stance by averaging member NPC
    beliefs about the player.
    """

    def __init__(self, positions: Dict[str, Dict[str, Any]] | None = None):
        self.positions = dict(positions or {})

    def evaluate_faction(self, faction_id: str, member_npc_minds: Dict[str, Dict[str, Any]], tick: int):
        """Evaluate a faction's position based on member NPC minds.

        Stance is determined by:
        - "oppose" if avg hostility >= 0.30
        - "support" if avg trust >= 0.30
        - "fear" if avg fear >= 0.30
        - "watch" otherwise

        Returns the position dict for this faction.
        """
        faction_id = _safe_str(faction_id)
        trust = 0.0
        hostility = 0.0
        fear = 0.0
        count = 0

        for _, mind in sorted((member_npc_minds or {}).items()):
            beliefs = (mind.get("beliefs") or {}).get("player") or {}
            trust += float(beliefs.get("trust", 0.0) or 0.0)
            hostility += float(beliefs.get("hostility", 0.0) or 0.0)
            fear += float(beliefs.get("fear", 0.0) or 0.0)
            count += 1

        if count <= 0:
            stance = "watch"
            score = 0.0
        else:
            avg_trust = trust / count
            avg_hostility = hostility / count
            avg_fear = fear / count

            if avg_hostility >= 0.30:
                stance = "oppose"
                score = avg_hostility
            elif avg_trust >= 0.30:
                stance = "support"
                score = avg_trust
            elif avg_fear >= 0.30:
                stance = "fear"
                score = avg_fear
            else:
                stance = "watch"
                score = max(avg_trust, avg_hostility, avg_fear)

        self.positions[faction_id] = {
            "target_id": "player",
            "stance": stance,
            "score": round(score, 4),
            "updated_tick": int(tick),
        }
        return dict(self.positions[faction_id])

    def to_dict(self):
        return {k: dict(v) for k, v in self.positions.items()}

    @classmethod
    def from_dict(cls, data):
        return cls(data or {})