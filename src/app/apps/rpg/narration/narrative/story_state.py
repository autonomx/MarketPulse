"""Story State - Global Narrative Memory.

TIER 18: Narrative Intelligence - Meta-AI Story Director
"""


class StoryState:
    """Tracks global narrative progression."""

    def __init__(self):
        self.tension = 0.3
        self.phase = "rising"  # rising | climax | falling
        self.active_arcs = []
        self.resolved_arcs = []
        self.major_events = []

    def add_event(self, event):
        self.major_events.append(event)

