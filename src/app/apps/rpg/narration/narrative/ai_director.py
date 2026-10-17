"""AI Director — Tension Engine for Narrative Orchestration.

TIER 5: Experience Orchestration — System 1 of 3

Purpose:
    Controls narrative tension and event shaping over time.
    The AI Director ensures the story has build-up, climax, and calm periods
    rather than a flat, monotonous sequence of events.

Architecture:
    The director maintains an internal tension value (0.0 to 1.0) that
    oscillates over time using a sine wave. This tension value determines:
    - Which events are prioritized (high tension → combat/danger, low tension → calm/dialogue)
    - How many events are selected for narration
    - The overall narrative pacing

Usage:
    director = AIDirector()
    director.update()  # Call each tick
    filtered = director.filter_events(narrative_events)

Design Compliance:
    - TIER 5 from rpg-design.txt: AI Director (Tension Engine)
    - Integrates with PlayerLoop to shape event selection
    - Works with PacingController for output length control
"""

from __future__ import annotations

from typing import List, Optional

from app.apps.rpg.foundation.narrative_event import NarrativeEvent

# Tension thresholds
HIGH_TENSION_THRESHOLD = 0.7
LOW_TENSION_THRESHOLD = 0.3

# Event selection limits
HIGH_TENSION_MAX_EVENTS = 3
MID_TENSION_MAX_EVENTS = 5
LOW_TENSION_MAX_EVENTS = 4

# Tension configuration
TENSION_MIN = 0.0
TENSION_MAX = 1.0
TENSION_DECAY = 0.9  # Per-tick decay factor (natural calming effect)
TENSION_EVENT_MULTIPLIER = 0.1  # How much each event's emotional weight affects tension


class AIDirector:
    """Controls narrative tension and event shaping.
    
    The AI Director is a tension engine that shapes which events
    are prioritized based on the current narrative tension level.
    
    Tension is driven by actual story events, not artificial waves:
    - Events with high emotional weight increase tension
    - Tension naturally decays over time (calming effect)
    - Tension is clamped between 0.0 and 1.0
    
    Tension levels:
    - High (0.7-1.0): Combat, danger, dramatic events prioritized
    - Mid (0.3-0.7): Mixed events, balanced narration
    - Low (0.0-0.3): Calm, dialogue, exploration events prioritized
    
    Attributes:
        tick: Current tick count.
        tension: Current tension level (0.0 to 1.0).
    """
    
    def __init__(self):
        """Initialize the AI Director."""
        self.tick = 0
        self.tension = 0.0
        self._tension_history: List[float] = []
        
    def update(self, events: Optional[List[NarrativeEvent]] = None) -> None:
        """Advance the director by one tick and update tension.
        
        Tension rises with dramatic events and decays during calm periods.
        
        Args:
            events: Optional list of narrative events from this tick.
                    If provided, their emotional weight will increase tension.
        """
        self.tick += 1
        
        # Add tension from events (if any)
        if events:
            delta = sum(e.emotional_weight for e in events)
            self.tension += delta * TENSION_EVENT_MULTIPLIER
        
        # Natural decay (tension fades over time without events)
        self.tension *= TENSION_DECAY
        
        # Clamp
        self.tension = max(TENSION_MIN, min(TENSION_MAX, self.tension))
        
        # Track history
        self._tension_history.append(self.tension)
        
    def filter_events(
        self, events: List[NarrativeEvent]
    ) -> List[NarrativeEvent]:
        """Select events based on current tension level.
        
        High tension: favors events with high emotional weight (combat, danger)
        Low tension: favors events with low emotional weight (calm, dialogue)
        Mid tension: mixed selection
        
        Args:
            events: List of narrative events to filter.
            
        Returns:
            Filtered list of events appropriate for current tension.
        """
        if not events:
            return []
            
        if self.tension > HIGH_TENSION_THRESHOLD:
            return self._filter_high_tension(events)
        elif self.tension < LOW_TENSION_THRESHOLD:
            return self._filter_low_tension(events)
        else:
            return self._filter_mid_tension(events)
    
    def _filter_high_tension(
        self, events: List[NarrativeEvent]
    ) -> List[NarrativeEvent]:
        """Filter for high tension: combat, danger, dramatic events.
        
        Args:
            events: All candidate events.
            
        Returns:
            Events with high emotional weight, limited in count.
        """
        # Prioritize high emotional weight events
        dramatic = [
            e for e in events
            if e.emotional_weight > 0.6
            or e.type in ("combat", "death", "damage", "critical_hit")
        ]
        
        if dramatic:
            # Sort by emotional weight descending
            dramatic.sort(
                key=lambda e: (e.emotional_weight, e.importance),
                reverse=True,
            )
            return dramatic[:HIGH_TENSION_MAX_EVENTS]
        
        # Fallback: return top events even if not dramatic
        events.sort(
            key=lambda e: (e.emotional_weight, e.importance),
            reverse=True,
        )
        return events[:HIGH_TENSION_MAX_EVENTS]
    
    def _filter_low_tension(
        self, events: List[NarrativeEvent]
    ) -> List[NarrativeEvent]:
        """Filter for low tension: calm, dialogue, exploration events.
        
        Args:
            events: All candidate events.
            
        Returns:
            Events with low emotional weight, allowing more events.
        """
        # Prioritize low emotional weight events
        calm = [
            e for e in events
            if e.emotional_weight < 0.5
            or e.type in ("speak", "move", "explore", "heal", "rest")
        ]
        
        if calm:
            # Sort by narrative priority (lower weight events first for calm)
            calm.sort(
                key=lambda e: (-e.importance, e.emotional_weight)
            )
            return calm[:LOW_TENSION_MAX_EVENTS]
        
        # Fallback: return all events sorted
        events.sort(key=lambda e: e.importance, reverse=True)
        return events[:LOW_TENSION_MAX_EVENTS]
    
    def _filter_mid_tension(
        self, events: List[NarrativeEvent]
    ) -> List[NarrativeEvent]:
        """Filter for mid tension: balanced mix of events.
        
        Args:
            events: All candidate events.
            
        Returns:
            Mixed selection of events.
        """
        # Sort by combined priority score
        events.sort(
            key=lambda e: (
                e.emotional_weight * 0.4 + e.importance * 0.6
            ),
            reverse=True,
        )
        return events[:MID_TENSION_MAX_EVENTS]
    
    def reset(self) -> None:
        """Reset the AI Director to initial state."""
        self.tick = 0
        self.tension = 0.0