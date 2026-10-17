from __future__ import annotations

from typing import Any, Dict, List, cast

from .belief_model import BeliefModel
from .goal_engine import GoalEngine
from .npc_decision import NPCDecision
from .npc_decision_validator import NPCDecisionValidator
from .npc_memory import NPCMemory
from app.apps.rpg.foundation.safe_values import safe_str as _safe_str


class NPCMind:
    def __init__(
        self,
        npc_id: str,
        memory: NPCMemory | None = None,
        beliefs: BeliefModel | None = None,
        goal_engine: GoalEngine | None = None,
        last_decision: Dict[str, Any] | None = None,
        last_seen_tick: int = 0,
    ):
        self.npc_id = npc_id
        self.memory = memory or NPCMemory(npc_id=npc_id)
        self.beliefs = beliefs or BeliefModel()
        self.goal_engine = goal_engine or GoalEngine()
        self.last_decision = dict(last_decision or {})
        self.last_seen_tick = int(last_seen_tick or 0)
        self.validator = NPCDecisionValidator()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "npc_id": self.npc_id,
            "memory": self.memory.to_dict(),
            "beliefs": self.beliefs.to_dict(),
            "goals": self.goal_engine.to_dict(),
            "last_decision": dict(self.last_decision),
            "last_seen_tick": self.last_seen_tick,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any] | None) -> "NPCMind":
        data = data or {}
        npc_id = _safe_str(data.get("npc_id"))
        return cls(
            npc_id=npc_id,
            memory=NPCMemory.from_dict(cast(Any, data.get("memory"))),
            beliefs=BeliefModel.from_dict(data.get("beliefs")),
            goal_engine=GoalEngine.from_dict(data.get("goals")),
            last_decision=data.get("last_decision") or {},
            last_seen_tick=int(data.get("last_seen_tick", 0) or 0),
        )

    def _event_is_relevant(self, event: Dict[str, Any], npc_context: Dict[str, Any]) -> bool:
        event = event or {}
        npc_context = npc_context or {}

        npc_id = _safe_str(npc_context.get("npc_id"))
        npc_faction_id = _safe_str(npc_context.get("faction_id"))
        npc_location_id = _safe_str(npc_context.get("location_id"))

        actor = _safe_str(event.get("actor"))
        target_id = _safe_str(event.get("target_id"))
        faction_id = _safe_str(event.get("faction_id"))
        location_id = _safe_str(event.get("location_id"))
        affected_npc_ids = event.get("affected_npc_ids") or []

        if actor == "player":
            return True
        if target_id and target_id == npc_id:
            return True
        if npc_faction_id and faction_id and faction_id == npc_faction_id:
            return True
        if npc_location_id and location_id and location_id == npc_location_id:
            return True
        if npc_id and npc_id in affected_npc_ids:
            return True
        return False

    def observe_events(self, events: List[Dict[str, Any]], tick: int, npc_context: Dict[str, Any]) -> None:
        relevant: List[Dict[str, Any]] = []
        for event in events or []:
            if self._event_is_relevant(event, npc_context):
                relevant.append(event)

        self.memory.remember_many(relevant, tick=tick)
        for event in relevant:
            self.beliefs.update_from_event(event, npc_context=npc_context)
            self.goal_engine.advance_from_event(event)

        self.last_seen_tick = int(tick)

    def refresh_goals(self, simulation_state: Dict[str, Any], npc_context: Dict[str, Any]) -> None:
        generated = self.goal_engine.generate_goals(
            npc_context=npc_context,
            simulation_state=simulation_state,
            belief_summary=self.beliefs.summarize(limit=8),
            memory_summary=[entry for entry in self.memory.entries[:5] if entry.get("summary")],
        )
        self.goal_engine.merge_goals(generated)

    def decide(self, simulation_state: Dict[str, Any], npc_context: Dict[str, Any], tick: int) -> NPCDecision:
        npc_context = npc_context or {}
        top_goal = self.goal_engine.top_goal()
        location_id = _safe_str(npc_context.get("location_id"))
        npc_name = _safe_str(npc_context.get("name")) or self.npc_id

        if not top_goal:
            decision = NPCDecision.fallback(
                npc_id=self.npc_id,
                tick=tick,
                location_id=location_id,
                reason="No active goals",
            )
            self.last_decision = decision.to_dict()
            return decision

        goal_type = _safe_str(top_goal.get("type"))
        target_id = _safe_str(top_goal.get("target_id"))
        reason = _safe_str(top_goal.get("reason"))
        priority = float(top_goal.get("priority", 0.0) or 0.0)

        mapping = {
            "stabilize_location": ("stabilize", "stabilize", "location", "The NPC acts to restore order."),
            "support_faction": ("support", "support", "faction", "The NPC rallies support for their faction."),
            "check_on_nearby_ally": ("support", "support", "npc", "The NPC moves to coordinate with a nearby ally."),
            "negotiate_with_nearby_npc": ("negotiate", "negotiate", "npc", f"{npc_name} speaks with someone nearby."),
            "retaliate_against_nearby_npc": ("retaliate", "retaliate", "npc", "The NPC confronts a nearby rival."),
            "investigate_local_incident": ("investigate", "investigate", "entity", "The NPC looks into a recent disturbance nearby."),
            "move_to_populated_location": ("move", "move", "location", "The NPC heads toward activity."),
            "retaliate": ("retaliate", "retaliate", "actor", "The NPC moves against a perceived enemy."),
            "avoid_player": ("avoid", "avoid", "actor", "The NPC keeps their distance."),
            "approach_player": ("negotiate", "negotiate", "actor", "The NPC cautiously opens contact."),
            "observe": ("observe", "observe", "actor", "The NPC watches closely."),
            "investigate": ("investigate", "investigate", "entity", "The NPC looks into suspicious developments."),
        }
        intent, action_type, target_kind, dialogue_hint = mapping.get(
            goal_type,
            ("wait", "wait", "", "The NPC waits and reassesses."),
        )

        raw = {
            "npc_id": self.npc_id,
            "tick": int(tick),
            "intent": intent,
            "action_type": action_type,
            "target_id": target_id,
            "target_kind": target_kind,
            "location_id": location_id,
            "reason": reason,
            "dialogue_hint": dialogue_hint,
            "urgency": priority,
        }
        validated = self.validator.validate(raw)
        decision = NPCDecision.from_dict(validated)
        self.last_decision = decision.to_dict()
        return decision

