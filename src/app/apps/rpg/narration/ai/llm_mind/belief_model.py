from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict
from app.apps.rpg.foundation.safe_values import safe_str as _safe_str

_BELIEF_KEYS = ("trust", "fear", "respect", "hostility")


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _clamp_signed(value: float) -> float:
    return max(-1.0, min(1.0, value))


def _empty_belief_record() -> Dict[str, float]:
    return {
        "trust": 0.0,
        "fear": 0.0,
        "respect": 0.0,
        "hostility": 0.0,
    }


@dataclass
class BeliefModel:
    beliefs: Dict[str, Dict[str, float]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        normalized: Dict[str, Dict[str, float]] = {}
        for target_id, record in sorted(self.beliefs.items()):
            normalized[target_id] = {
                key: _clamp_signed(_safe_float(record.get(key), 0.0))
                for key in _BELIEF_KEYS
            }
        return {"beliefs": normalized}

    @classmethod
    def from_dict(cls, data: Dict[str, Any] | None) -> "BeliefModel":
        data = data or {}
        raw = data.get("beliefs") or {}
        beliefs: Dict[str, Dict[str, float]] = {}
        for target_id, record in raw.items():
            if not isinstance(record, dict):
                continue
            beliefs[_safe_str(target_id)] = {
                key: _clamp_signed(_safe_float(record.get(key), 0.0))
                for key in _BELIEF_KEYS
            }
        return cls(beliefs=beliefs)

    def _ensure_target(self, target_id: str) -> Dict[str, float]:
        target_id = _safe_str(target_id)
        if target_id not in self.beliefs:
            self.beliefs[target_id] = _empty_belief_record()
        return self.beliefs[target_id]

    def update_belief(self, target_id: str, key: str, delta: float) -> float:
        if key not in _BELIEF_KEYS:
            return 0.0
        record = self._ensure_target(target_id)
        record[key] = _clamp_signed(_safe_float(record.get(key), 0.0) + float(delta))
        return record[key]

    def summarize(self, limit: int = 8) -> Dict[str, Dict[str, float]]:
        items = sorted(self.beliefs.items(), key=lambda pair: pair[0])
        out: Dict[str, Dict[str, float]] = {}
        for target_id, record in items[: max(0, limit)]:
            out[target_id] = dict(record)
        return out

    def update_from_event(self, event: Dict[str, Any], npc_context: Dict[str, Any]) -> None:
        event = event or {}
        npc_context = npc_context or {}

        actor = _safe_str(event.get("actor"))
        event_type = _safe_str(event.get("type"))
        target_id = _safe_str(event.get("target_id"))
        faction_id = _safe_str(event.get("faction_id"))
        location_id = _safe_str(event.get("location_id"))

        npc_id = _safe_str(npc_context.get("npc_id"))
        npc_faction_id = _safe_str(npc_context.get("faction_id"))
        npc_location_id = _safe_str(npc_context.get("location_id"))

        # Direct player/NPC relationship updates
        if actor == "player":
            if event_type in {"help", "support", "assist"}:
                self.update_belief("player", "trust", 0.20)
                self.update_belief("player", "respect", 0.10)

            if event_type in {"threaten", "coerce"}:
                self.update_belief("player", "fear", 0.25)
                self.update_belief("player", "hostility", 0.15)
                self.update_belief("player", "trust", -0.15)

            if event_type in {"attack", "betray", "sabotage"}:
                self.update_belief("player", "hostility", 0.40)
                self.update_belief("player", "trust", -0.40)
                self.update_belief("player", "fear", 0.20)

            if event_type in {"negotiate", "parley"}:
                self.update_belief("player", "respect", 0.10)

        # If event affects this NPC directly
        if target_id and target_id == npc_id:
            if actor == "player":
                self.update_belief("player", "respect", 0.10)
                if event_type in {"help", "support"}:
                    self.update_belief("player", "trust", 0.20)
                elif event_type in {"attack", "threaten", "coerce"}:
                    self.update_belief("player", "hostility", 0.30)
                    self.update_belief("player", "fear", 0.20)

        # Faction alignment effects
        if npc_faction_id and faction_id and faction_id == npc_faction_id:
            if actor == "player":
                if event_type in {"help", "support", "stabilize"}:
                    self.update_belief("player", "trust", 0.15)
                    self.update_belief("player", "respect", 0.10)
                elif event_type in {"attack", "sabotage", "destabilize"}:
                    self.update_belief("player", "hostility", 0.20)
                    self.update_belief("player", "trust", -0.20)

        # Locality effects
        if npc_location_id and location_id and location_id == npc_location_id:
            if actor == "player":
                if event_type in {"stabilize", "protect"}:
                    self.update_belief("player", "respect", 0.10)
                elif event_type in {"cause_chaos", "destabilize", "attack"}:
                    self.update_belief("player", "fear", 0.10)
                    self.update_belief("player", "hostility", 0.10)
