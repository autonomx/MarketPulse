from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class NPCDecision:
    npc_id: str
    tick: int
    intent: str
    action_type: str
    target_id: str
    target_kind: str
    location_id: str
    reason: str
    dialogue_hint: str
    urgency: float

    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__.copy()

    @classmethod
    def fallback(
        cls,
        npc_id: str,
        tick: int,
        location_id: str = "",
        reason: str = "No active goals",
    ) -> "NPCDecision":
        """Return a safe idle decision when no goal is available."""
        return cls(
            npc_id=npc_id,
            tick=tick,
            intent="wait",
            action_type="wait",
            target_id="",
            target_kind="",
            location_id=location_id,
            reason=reason,
            dialogue_hint="The NPC waits and reassesses.",
            urgency=0.0,
        )

    @classmethod
    def from_dict(cls, data: Dict[str, Any] | None):
        data = data or {}
        return cls(
            npc_id=str(data.get("npc_id") or ""),
            tick=int(data.get("tick", 0) or 0),
            intent=str(data.get("intent") or "wait"),
            action_type=str(data.get("action_type") or "wait"),
            target_id=str(data.get("target_id") or ""),
            target_kind=str(data.get("target_kind") or ""),
            location_id=str(data.get("location_id") or ""),
            reason=str(data.get("reason") or ""),
            dialogue_hint=str(data.get("dialogue_hint") or ""),
            urgency=float(data.get("urgency", 0.0) or 0.0),
        )
