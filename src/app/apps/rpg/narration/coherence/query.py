from __future__ import annotations

from typing import List

from .models import CoherenceState


class CoherenceQueryAPI:
    def __init__(self, state: CoherenceState) -> None:
        self.state = state

    def get_scene_summary(self) -> dict:
        location_fact = self.state.scene_facts.get("scene:location")
        anchor = self.state.continuity_anchors[-1] if self.state.continuity_anchors else None
        return {
            "location": location_fact.value if location_fact else None,
            "summary": anchor.summary if anchor else "",
            "present_actors": anchor.present_actors if anchor else [],
            "active_tensions": anchor.active_tensions if anchor else [],
        }

    def get_active_tensions(self) -> list:
        anchor = self.state.continuity_anchors[-1] if self.state.continuity_anchors else None
        if not anchor:
            return []
        return [{"text": t} for t in anchor.active_tensions]

    def get_unresolved_threads(self) -> list:
        rows = [t.to_dict() for t in self.state.unresolved_threads.values() if t.status != "resolved"]
        rows.sort(key=lambda x: (
            str(x.get("thread_id", "")),
            str(x.get("title", "")),
        ))
        return rows

    def get_actor_commitments(self, actor_id: str) -> list:
        records: List[dict] = []
        for bucket in (self.state.player_commitments, self.state.npc_commitments):
            for commitment in bucket.values():
                if commitment.actor_id == actor_id and commitment.status == "active":
                    records.append(commitment.to_dict())
        return records

    def get_known_facts(self, entity_id: str) -> dict:
        facts: List[dict] = []
        for bucket in (
            self.state.stable_world_facts,
            self.state.scene_facts,
            self.state.temporary_assumptions,
        ):
            for fact in bucket.values():
                if fact.subject == entity_id:
                    facts.append(fact.to_dict())
        return {"entity_id": entity_id, "facts": facts}

    def get_recent_consequences(self, limit: int = 10) -> list:
        items = [c.to_dict() for c in self.state.recent_changes]
        items.sort(key=lambda x: (
            str(x.get("tick", "")),
            str(x.get("consequence_id", "")),
        ))
        return items[-limit:]

    def get_last_good_anchor(self) -> dict | None:
        if not self.state.continuity_anchors:
            return None
        return self.state.continuity_anchors[-1].to_dict()

    # ------------------------------------------------------------------
    # Phase 8.2 — Encounter seeding helpers
    # ------------------------------------------------------------------

    def get_scene_entities(self) -> list[str]:
        """Return entity IDs present in the current scene."""
        anchor = self.state.continuity_anchors[-1] if self.state.continuity_anchors else None
        if not anchor:
            return []
        return list(anchor.present_actors)

    # ------------------------------------------------------------------
    # Phase 8.3 — World simulation seeding helpers
    # ------------------------------------------------------------------

    def get_known_locations(self) -> list[str]:
        """Return known location IDs from stable world facts."""
        locations: list[str] = []
        for key, fact in self.state.stable_world_facts.items():
            if "location" in key.lower():
                if fact.value and isinstance(fact.value, str):
                    locations.append(fact.value)
        # Also include current scene location
        scene_loc = self.get_active_scene_location()
        if scene_loc and scene_loc not in locations:
            locations.append(scene_loc)
        return sorted(set(locations))

    def get_active_scene_location(self) -> str | None:
        """Return the current scene location ID, if any."""
        location_fact = self.state.scene_facts.get("scene:location")
        return location_fact.value if location_fact else None

    def get_active_threads(self) -> list[dict]:
        """Return all active (non-resolved) threads as dicts."""
        return [
            t.to_dict()
            for t in self.state.unresolved_threads.values()
            if t.status != "resolved"
        ]
