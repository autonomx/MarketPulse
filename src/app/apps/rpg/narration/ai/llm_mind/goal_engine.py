from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

_MAX_GOALS = 5


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _goal_sort_key(goal: Dict[str, Any]):
    priority = _safe_float(goal.get("priority"), 0.0)
    goal_id = _safe_str(goal.get("goal_id"))
    return (-priority, goal_id)


@dataclass
class GoalEngine:
    goals: List[Dict[str, Any]] = field(default_factory=list)
    max_goals: int = _MAX_GOALS

    def _nearby_npcs(
        self,
        npc_id: str,
        npc_location_id: str,
        simulation_state: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        npc_index = _safe_dict(simulation_state.get("npc_index"))
        rows: List[Dict[str, Any]] = []
        for other_id, raw in sorted(npc_index.items()):
            other = _safe_dict(raw)
            # Canonical id is the dict key; nested npc_id may be absent or stale.
            other_npc_id = _safe_str(other_id)
            if not other_npc_id or other_npc_id == npc_id:
                continue
            if _safe_str(other.get("location_id")) != npc_location_id:
                continue
            rows.append({
                "npc_id": other_npc_id,
                "name": _safe_str(other.get("name")),
                "faction_id": _safe_str(other.get("faction_id")),
                "role": _safe_str(other.get("role")),
            })
        return rows

    def _recent_local_incident(
        self,
        npc_location_id: str,
        simulation_state: Dict[str, Any],
    ) -> Dict[str, Any]:
        events = _safe_list(simulation_state.get("events"))
        for raw in reversed(events[-20:]):
            event = _safe_dict(raw)
            if _safe_str(event.get("location_id")) != npc_location_id:
                continue
            event_type = _safe_str(event.get("type")).lower()
            if event_type in {"attack", "threaten", "retaliate", "incident", "destabilize", "sabotage"}:
                return event
        return {}

    def _belief_score(self, belief_summary: Dict[str, Dict[str, float]], target_id: str, key: str) -> float:
        beliefs = _safe_dict(
            belief_summary.get(target_id)
            or belief_summary.get(str(target_id))
        )
        return _safe_float(beliefs.get(key), 0.0)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "goals": [dict(goal) for goal in self.goals],
            "max_goals": int(self.max_goals),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any] | None) -> "GoalEngine":
        data = data or {}
        raw_goals = data.get("goals") or []
        max_goals = int(data.get("max_goals", _MAX_GOALS) or _MAX_GOALS)
        goals: List[Dict[str, Any]] = []
        for item in raw_goals:
            if not isinstance(item, dict):
                continue
            goals.append({
                "goal_id": _safe_str(item.get("goal_id")),
                "type": _safe_str(item.get("type")),
                "target_id": _safe_str(item.get("target_id")),
                "priority": _safe_float(item.get("priority"), 0.0),
                "reason": _safe_str(item.get("reason")),
                "status": _safe_str(item.get("status")) or "active",
                "progress": _safe_float(item.get("progress"), 0.0),
            })
        engine = cls(goals=goals, max_goals=max_goals)
        engine.goals = sorted(engine.goals, key=_goal_sort_key)[: engine.max_goals]
        return engine

    def _make_goal(
        self,
        npc_id: str,
        goal_type: str,
        target_id: str,
        priority: float,
        reason: str,
    ) -> Dict[str, Any]:
        goal_id = f"goal:{npc_id}:{goal_type}:{target_id or 'none'}"
        return {
            "goal_id": goal_id,
            "type": goal_type,
            "target_id": target_id,
            "priority": float(priority),
            "reason": reason,
            "status": "active",
            "progress": 0.0,
        }

    def generate_goals(
        self,
        npc_context: Dict[str, Any],
        simulation_state: Dict[str, Any],
        belief_summary: Dict[str, Dict[str, float]],
        memory_summary: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        npc_context = npc_context or {}
        simulation_state = simulation_state or {}
        belief_summary = belief_summary or {}

        npc_id = _safe_str(npc_context.get("npc_id"))
        npc_location_id = _safe_str(npc_context.get("location_id"))
        npc_faction_id = _safe_str(npc_context.get("faction_id"))
        npc_role = _safe_str(npc_context.get("role")).lower()

        player_beliefs = belief_summary.get("player", {})
        trust = _safe_float(player_beliefs.get("trust"), 0.0)
        fear = _safe_float(player_beliefs.get("fear"), 0.0)
        hostility = _safe_float(player_beliefs.get("hostility"), 0.0)

        locations = simulation_state.get("locations") or {}
        location_state = locations.get(npc_location_id) or {}
        location_pressure = _safe_float(location_state.get("pressure"), 0.0)
        nearby_npcs = self._nearby_npcs(
            npc_id=npc_id,
            npc_location_id=npc_location_id,
            simulation_state=simulation_state,
        )
        local_incident = self._recent_local_incident(
            npc_location_id=npc_location_id,
            simulation_state=simulation_state,
        )

        goals: List[Dict[str, Any]] = []

        if location_pressure >= 2.0 and npc_location_id:
            goals.append(self._make_goal(
                npc_id=npc_id,
                goal_type="stabilize_location",
                target_id=npc_location_id,
                priority=0.80 + min(location_pressure * 0.05, 0.15),
                reason="Local pressure is elevated",
            ))

        # Keep faction support available, but lower it so it stops dominating
        # every calm tick when there are actual nearby people to react to.
        if npc_faction_id:
            goals.append(self._make_goal(
                npc_id=npc_id,
                goal_type="support_faction",
                target_id=npc_faction_id,
                priority=0.22,
                reason="Faction loyalty baseline",
            ))

        # React to recent local incidents before defaulting to idle observation.
        local_incident_target = _safe_str(
            local_incident.get("target_id")
            or local_incident.get("actor")
            or local_incident.get("event_id")
        )
        if npc_location_id and local_incident_target:
            goals.append(self._make_goal(
                npc_id=npc_id,
                goal_type="investigate_local_incident",
                target_id=local_incident_target,
                priority=0.58 + min(location_pressure * 0.05, 0.10),
                reason="Recent disturbance nearby",
            ))

        # Nearby NPC interaction goals: this is the main living-world pass.
        for other in nearby_npcs[:4]:
            other_id = _safe_str(other.get("npc_id"))
            other_faction_id = _safe_str(other.get("faction_id"))
            other_role = _safe_str(other.get("role")).lower()
            if not other_id:
                continue

            other_hostility = self._belief_score(belief_summary, other_id, "hostility")
            other_trust = self._belief_score(belief_summary, other_id, "trust")
            same_faction = bool(
                npc_faction_id and other_faction_id and npc_faction_id == other_faction_id
            )

            # Hostile nearby NPC -> confront / retaliate instead of staring forever.
            if other_hostility >= 0.30:
                goals.append(self._make_goal(
                    npc_id=npc_id,
                    goal_type="retaliate_against_nearby_npc",
                    target_id=other_id,
                    priority=0.62 + min(other_hostility * 0.20, 0.18),
                    reason="Nearby NPC is viewed as hostile",
                ))
                continue

            # Same-faction nearby NPC -> coordinate / check in.
            if same_faction:
                goals.append(self._make_goal(
                    npc_id=npc_id,
                    goal_type="check_on_nearby_ally",
                    target_id=other_id,
                    priority=0.44 + min(location_pressure * 0.03, 0.08),
                    reason="Nearby ally is available for coordination",
                ))
                continue

            # Neutral or trusted nearby NPC -> talk, trade, gossip, negotiate.
            if other_trust >= -0.10:
                base_priority = 0.40
                if npc_role in {"merchant", "innkeeper", "bartender", "shopkeeper"}:
                    base_priority = 0.48
                elif other_role in {"merchant", "innkeeper", "guard"}:
                    base_priority = 0.45
                goals.append(self._make_goal(
                    npc_id=npc_id,
                    goal_type="negotiate_with_nearby_npc",
                    target_id=other_id,
                    priority=base_priority + min(max(other_trust, 0.0) * 0.10, 0.08),
                    reason="Nearby NPC is available for social interaction",
                ))

        # If nobody is nearby, move toward activity instead of endlessly observing.
        if not nearby_npcs:
            npc_index = _safe_dict(simulation_state.get("npc_index"))
            location_counts: Dict[str, int] = {}
            for _, raw in sorted(npc_index.items()):
                row = _safe_dict(raw)
                loc = _safe_str(row.get("location_id"))
                if not loc:
                    continue
                location_counts[loc] = location_counts.get(loc, 0) + 1

            best_loc = ""
            best_count = 0
            for loc, count in sorted(location_counts.items()):
                if loc == npc_location_id:
                    continue
                if count > best_count:
                    best_loc = loc
                    best_count = count

            if best_loc and best_count >= 2:
                goals.append(self._make_goal(
                    npc_id=npc_id,
                    goal_type="move_to_populated_location",
                    target_id=best_loc,
                    priority=0.52,
                    reason="Move toward activity",
                ))

        if hostility >= 0.35:
            goals.append(self._make_goal(
                npc_id=npc_id,
                goal_type="retaliate",
                target_id="player",
                priority=0.70 + min(hostility * 0.20, 0.20),
                reason="Player is viewed as hostile",
            ))
        elif fear >= 0.35:
            goals.append(self._make_goal(
                npc_id=npc_id,
                goal_type="avoid_player",
                target_id="player",
                priority=0.65 + min(fear * 0.20, 0.20),
                reason="Player is viewed as threatening",
            ))
        elif trust >= 0.35:
            goals.append(self._make_goal(
                npc_id=npc_id,
                goal_type="approach_player",
                target_id="player",
                priority=0.60 + min(trust * 0.20, 0.20),
                reason="Player is viewed as trustworthy",
            ))

        # HARD FALLBACK — nearby-NPC social interaction.
        # This block is unconditional: if *any* NPC shares the location, a
        # negotiate goal is always emitted so passive observe can never be
        # the dominant behaviour when NPCs are co-located.
        if nearby_npcs:
            other = nearby_npcs[0]
            other_id = _safe_str(other.get("npc_id"))
            if other_id:
                goals.append(self._make_goal(
                    npc_id=npc_id,
                    goal_type="negotiate_with_nearby_npc",
                    target_id=other_id,
                    priority=0.55,
                    reason="Force social interaction — nearby NPC present",
                ))

        # Passive observe is the absolute last resort and may only appear
        # when no other NPC is nearby at all.
        if not nearby_npcs:
            goals.append(self._make_goal(
                npc_id=npc_id,
                goal_type="observe",
                target_id="player",
                priority=0.05,
                reason="Maintain awareness of player",
            ))

        if memory_summary:
            top_memory = memory_summary[0]
            top_target = _safe_str(top_memory.get("target_id"))
            top_type = _safe_str(top_memory.get("type"))
            if top_target and top_target != "player" and top_type in {"incident", "attack", "destabilize", "threaten"}:
                goals.append(self._make_goal(
                    npc_id=npc_id,
                    goal_type="investigate",
                    target_id=top_target,
                    priority=0.55,
                    reason=f"Recent salient memory: {top_type}",
                ))

        goals = sorted(goals, key=_goal_sort_key)

        deduped: List[Dict[str, Any]] = []
        seen = set()
        for goal in goals:
            goal_id = goal["goal_id"]
            if goal_id in seen:
                continue
            seen.add(goal_id)
            deduped.append(goal)

        return deduped[: self.max_goals]

    def merge_goals(self, generated: List[Dict[str, Any]]) -> None:
        generated = generated or []
        merged: dict[Any, Any] = {}
        # Decay older goals so fresh context can take over.
        decayed_existing: List[Dict[str, Any]] = []
        for goal in self.goals:
            row = dict(goal)
            row["priority"] = _safe_float(row.get("priority"), 0.0) * 0.6
            decayed_existing.append(row)

        for goal in decayed_existing + generated:
            goal_id = _safe_str(goal.get("goal_id"))
            if not goal_id:
                continue
            existing = merged.get(goal_id)
            if existing is None or _safe_float(goal.get("priority"), 0.0) > _safe_float(existing.get("priority"), 0.0):
                merged[goal_id] = dict(goal)
        self.goals = sorted(merged.values(), key=_goal_sort_key)[: self.max_goals]

    def top_goal(self) -> Dict[str, Any] | None:
        if not self.goals:
            return None
        return dict(sorted(self.goals, key=_goal_sort_key)[0])

    def advance_from_event(self, event: Dict[str, Any]) -> None:
        event = event or {}
        target_id = _safe_str(event.get("target_id"))
        event_type = _safe_str(event.get("type"))

        updated: List[Dict[str, Any]] = []
        for goal in self.goals:
            new_goal = dict(goal)
            if target_id and target_id == _safe_str(goal.get("target_id")):
                progress = _safe_float(goal.get("progress"), 0.0)
                if event_type in {"help", "support", "stabilize", "investigate", "negotiate"}:
                    progress += 0.25
                elif event_type in {"attack", "sabotage", "retaliate"}:
                    progress += 0.15
                new_goal["progress"] = min(progress, 1.0)
            updated.append(new_goal)
        self.goals = sorted(updated, key=_goal_sort_key)[: self.max_goals]
