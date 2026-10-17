"""Narrative Director - Meta-AI Story Director.

TIER 18: Narrative Intelligence - Meta-AI Story Director

The Narrative Director is a meta-AI that observes the world and shapes it
into story. It does NOT control NPCs directly. It nudges the system via:
- pacing
- event injection
- tension balancing
- arc resolution
"""

from __future__ import annotations

from .arc_manager import ArcManager
from .event_injector import EventInjector
from .pacing_controller import PacingController
from .story_state import StoryState
from .tension_engine import TensionEngine


class NarrativeDirector:
    """Meta-AI narrative director."""

    def __init__(self):
        self.state = StoryState()
        self.tension_engine = TensionEngine()
        self.arc_manager = ArcManager()
        self.injector = EventInjector()
        self.pacing = PacingController()

    def update(self, world):
        # 1. Update tension
        self.tension_engine.update(self.state, world)
        # 2. Update arcs
        self.arc_manager.update(self.state)
        # 3. Inject events
        events = self.injector.inject(self.state, world)
        for event in events:
            self.state.add_event(event)
        return events

    def reset(self):
        self.state = StoryState()
        self.tension_engine.reset()
        self.arc_manager.reset()
