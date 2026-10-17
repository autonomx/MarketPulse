"""Tension Engine - Dynamic Narrative Tension Control.

TIER 18: Narrative Intelligence - Meta-AI Story Director
"""

from __future__ import annotations

from typing import Dict

CLIMAX_THRESHOLD = 0.8
FALLING_THRESHOLD = 0.3
WORLD_TENSION_MULTIPLIER = 0.05

EVENT_TENSION_MODIFIERS: Dict[str, float] = {
    "death": 0.25, "betrayal": 0.20, "combat": 0.15,
    "critical_hit": 0.10, "damage": 0.05, "flee": 0.08,
    "alliance_formed": -0.10, "heal": -0.05, "resolution": -0.15,
    "peaceful_dialogue": -0.08, "trade": -0.03,
    "speak": 0.0, "move": 0.0, "spawn": 0.0, "observe": 0.0,
}


class TensionEngine:
    """Computes and modulates narrative tension from world state."""

    def __init__(self, initial_tension=0.3, world_multiplier=WORLD_TENSION_MULTIPLIER):
        self.tension = max(0.0, min(1.0, initial_tension))
        self.phase = "rising"
        self.previous_tension = self.tension
        self.world_multiplier = world_multiplier
        self._event_buffer = []

    def update(self, story_state, world):
        """Update tension based on world state."""
        self.previous_tension = story_state.tension
        d = world.get("global_tension", 0.0) * self.world_multiplier
        for e in world.get("recent_events", []):
            d += EVENT_TENSION_MODIFIERS.get(e.get("type", "").lower(), 0.0)
        d -= story_state.tension * 0.02
        story_state.tension = max(0.0, min(1.0, story_state.tension + d))
        if story_state.tension > CLIMAX_THRESHOLD:
            story_state.phase = "climax"
        elif story_state.tension < FALLING_THRESHOLD:
            story_state.phase = "falling"
        else:
            story_state.phase = "rising"
        return story_state.tension - self.previous_tension

    def add_event(self, event):
        self._event_buffer.append(event)
        self._event_buffer = self._event_buffer[-50:]

    def reset(self):
        self.tension = 0.3
        self.phase = "rising"
        self.previous_tension = self.tension
        self._event_buffer.clear()
