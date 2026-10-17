"""Event Injector - Phase-Based Narrative Event Injection.

TIER 18: Narrative Intelligence - Meta-AI Story Director
"""

import random


class EventInjector:
    """Injects narrative events based on story phase."""

    def inject(self, story_state, world):
        events = []
        phase = story_state.phase

        if phase == "climax":
            if random.random() < 0.3:
                events.append({"type": "major_betrayal", "impact": 1.0})
            if random.random() < 0.2:
                events.append({"type": "boss_appearance", "impact": 0.9})

        elif phase == "falling":
            if random.random() < 0.4:
                events.append({"type": "resolution_opportunity", "impact": 0.6})
            if random.random() < 0.3:
                events.append({"type": "loose_end", "impact": 0.4})

        else:  # rising
            if random.random() < 0.3:
                events.append({"type": "complication", "impact": 0.5})
            if random.random() < 0.2:
                events.append({"type": "foreshadowing", "impact": 0.3})
            if random.random() < 0.15:
                events.append({"type": "minor_conflict", "impact": 0.4})

        return events
