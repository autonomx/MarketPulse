"""Narrative Director — STEP 2 of RPG Design Implementation.

This module implements the NarrativeDirector, which converts raw world
events into NarrativeEvent objects with importance and emotional scoring.

Purpose:
    Serve as the bridge between the simulation layer (world events)
    and the narrative layer (storytelling). The director decides
    which events are narratively significant.

Architecture:
    World Events → NarrativeDirector → NarrativeEvents → Focus Selection

Usage:
    director = NarrativeDirector()
    narrative_events = director.convert_events(world_events)
    focus = director.select_focus_events(narrative_events)

Design Compliance:
    - STEP 2: Narrative Director from rpg-design.txt
    - Does NOT generate text — only scores and filters events
    - Importance/emotion scoring is deterministic (no LLM needed)
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List

from app.apps.rpg.foundation.narrative_event import NarrativeEvent

# Event type importance modifiers
IMPORTANCE_MODIFIERS: Dict[str, float] = {
    "combat": 0.3,
    "death": 0.5,
    "betrayal": 0.4,
    "alliance_formed": 0.3,
    "story_event": 0.4,
    "critical_hit": 0.2,
    "heal": 0.1,
    "speak": 0.05,
    "move": 0.0,
    "spawn": 0.0,
    "flee": 0.15,
    "damage": 0.1,
}

# Event type emotional weight scores
EMOTION_WEIGHTS: Dict[str, float] = {
    "death": 1.0,
    "betrayal": 0.9,
    "combat": 0.6,
    "critical_hit": 0.7,
    "alliance_formed": 0.5,
    "flee": 0.6,
    "heal": 0.3,
    "speak": 0.2,
    "damage": 0.3,
    "move": 0.0,
    "spawn": 0.0,
    "story_event": 0.4,
}


class NarrativeDirector:
    """Converts raw world events into scored NarrativeEvents.
    
    The NarrativeDirector is responsible for:
    1. Converting raw event dicts into NarrativeEvent objects
    2. Scoring events by narrative importance
    3. Scoring events by emotional weight
    4. Selecting which events should be narrated (focus selection)
    
    This class is deterministic — no LLM calls are made. The LLM
    is only invoked later during narrative generation.
    
    Attributes:
        recent_events: Buffer of recently converted events.
        max_buffer: Maximum events to keep in buffer.
    """
    
    def __init__(self, max_buffer: int = 50):
        """Initialize the NarrativeDirector.
        
        Args:
            max_buffer: Maximum recent events to store.
        """
        self.recent_events: List[NarrativeEvent] = []
        self.max_buffer = max_buffer
        
    def assign_intent(self, event: NarrativeEvent) -> str:
        """Assign narrative purpose to event.

        Maps event types to narrative intents that determine
        their role in the story arc:
        - escalate: Increase tension/conflict
        - reveal: Expose information/character
        - resolve: Bring closure to a situation
        - complicate: Add complexity to existing situation
        - progress: Move story forward

        Args:
            event: NarrativeEvent to analyze.
            
        Returns:
            Intent type string.
        """
        if event.type in ("combat", "critical_hit", "damage"):
            return "escalate"
        if event.type == "death":
            return "resolve"
        if event.type in ("speak", "dialogue"):
            return "reveal"
        if event.type in ("betrayal", "flee"):
            return "complicate"
        return "progress"
    
    def compute_urgency(self, event: NarrativeEvent) -> float:
        """Compute how urgently an event's intent needs addressing.
        
        Args:
            event: NarrativeEvent to analyze.
            
        Returns:
            Urgency score [0, 1].
        """
        intent = event.intent if event.intent else self.assign_intent(event)
        base_urgency = {
            "escalate": 0.7,
            "complicate": 0.6,
            "resolve": 0.5,
            "reveal": 0.4,
            "progress": 0.3,
        }.get(intent, 0.3)
        
        # Scale by emotional weight
        return min(1.0, base_urgency + event.emotional_weight * 0.3)
    
    def convert_events(
        self,
        world_events: List[Dict[str, Any]],
    ) -> List[NarrativeEvent]:
        """Convert raw world events into narrative events.

        Each world event is transformed into a NarrativeEvent with
        calculated importance and emotional_weight scores.

        Args:
            world_events: List of raw event dicts from the world simulation.
            
        Returns:
            List of NarrativeEvent objects with calculated scores.
        """
        output = []
        
        for event in world_events:
            ne = NarrativeEvent(
                id=str(uuid.uuid4()),
                type=event.get("type", "unknown"),
                description=self._extract_description(event),
                actors=event.get("actors", []),
                location=event.get("location"),
                importance=self.score_importance(event),
                emotional_weight=self.score_emotion(event),
                tags=event.get("tags", []),
                raw_event=event,
            )
            # Assign narrative intent and urgency
            ne.intent = self.assign_intent(ne)
            ne.urgency = self.compute_urgency(ne)
            output.append(ne)
        
        # Update buffer
        self.recent_events.extend(output)
        self.recent_events = self.recent_events[-self.max_buffer:]
        
        return output
    
    def score_importance(self, event: Dict[str, Any]) -> float:
        """Score how narratively important an event is.
        
        Calculates importance based on event type and actors involved.
        Higher scores mean the event is more worth narrating.
        
        Args:
            event: Raw event dict.
            
        Returns:
            Importance score [0, 1].
        """
        base = 0.3
        event_type = event.get("type", "unknown")
        
        # Type modifier
        base += IMPORTANCE_MODIFIERS.get(event_type, 0.0)
        
        # Player involvement bonus
        actors = event.get("actors", [])
        if "player" in actors:
            base += 0.2
        
        # Multiple actors = more important
        if len(actors) > 1:
            base += 0.1
        
        return min(base, 1.0)
    
    def score_emotion(self, event: Dict[str, Any]) -> float:
        """Score the emotional weight of an event.
        
        Calculates emotional intensity based on event type.
        Higher scores mean the event is more emotionally impactful.
        
        Args:
            event: Raw event dict.
            
        Returns:
            Emotional weight score [0, 1].
        """
        event_type = event.get("type", "unknown")
        return EMOTION_WEIGHTS.get(event_type, 0.1)
    
    def select_focus_events(
        self,
        events: List[NarrativeEvent],
        max_events: int = 5,
    ) -> List[NarrativeEvent]:
        """Select the most narratively significant events for focus.
        
        Sorts events by combined importance + emotional weight and
        returns the top max_events.
        
        Args:
            events: List of NarrativeEvents to filter.
            max_events: Maximum number of events to return.
            
        Returns:
            Top NarrativeEvents sorted by narrative priority.
        """
        sorted_events = sorted(
            events,
            key=lambda e: e.narrative_priority(),
            reverse=True,
        )
        return sorted_events[:max_events]
    
    @staticmethod
    def _extract_description(event: Dict[str, Any]) -> str:
        """Extract or generate a description from a raw event.
        
        Args:
            event: Raw event dict.
            
        Returns:
            Human-readable description string.
        """
        description = event.get("description", "")
        if description:
            return description
        
        # Generate from type and actors
        event_type = event.get("type", "unknown")
        actors = event.get("actors", [])
        
        if actors:
            actor_str = ", ".join(str(a) for a in actors[:3])
            return f"{event_type} event involving {actor_str}"
        else:
            return f"{event_type} event"