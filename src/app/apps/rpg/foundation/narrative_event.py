"""Narrative Event Model — STEP 1 of RPG Design Implementation.

This module defines the NarrativeEvent dataclass that serves as the
canonical representation of story-relevant events in the RPG system.

Purpose:
    Convert raw world events into structured narrative events with
    importance scoring, emotional weight, and metadata for narration.

Usage:
    event = NarrativeEvent(
        id="evt_001",
        type="combat",
        description="The knight strikes the dragon",
        actors=["knight", "dragon"],
        location="dragon_lair",
        importance=0.8,
    )

Design Compliance:
    - STEP 1: Narrative Event Model from rpg-design.txt
    - Pure data container, no business logic
    - Compatible with LLM prompts and template narration
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class NarrativeEvent:
    """A narrative event with metadata for storytelling.
    
    This is the core data structure that bridges the simulation layer
    (world events) with the narrative layer (storytelling).
    
    Attributes:
        id: Unique identifier for this event.
        type: Event category (combat, death, dialogue, exploration, etc).
        description: Human-readable description of what happened.
        actors: List of entity IDs involved in this event.
        location: Optional location ID where this event occurred.
        importance: Importance score [0, 1] for narrative prioritization.
        emotional_weight: Emotional intensity [0, 1] for tone matching.
        tags: Optional categorization tags for filtering/matching.
        raw_event: Original event data before transformation.
    """
    
    id: str
    type: str
    description: str
    actors: List[str] = field(default_factory=list)
    location: str | None = None
    importance: float = 0.5
    emotional_weight: float = 0.0
    tags: List[str] = field(default_factory=list)
    raw_event: Dict[str, Any] = field(default_factory=dict)
    intent: str = "progress"  # Narrative purpose: escalate, reveal, resolve, complicate, progress
    urgency: float = 0.0  # How urgently this intent needs to be addressed
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization.
        
        Returns:
            Dict representation of this event.
        """
        return {
            "id": self.id,
            "type": self.type,
            "description": self.description,
            "actors": list(self.actors),
            "location": self.location,
            "importance": self.importance,
            "emotional_weight": self.emotional_weight,
            "tags": list(self.tags),
        }
    
    @classmethod
    def from_dict(
        cls,
        data: Dict[str, Any],
        raw_event: Dict[str, Any] | None = None,
    ) -> "NarrativeEvent":
        """Create a NarrativeEvent from a dictionary.
        
        Args:
            data: Dict with event fields.
            raw_event: Optional original event data.
            
        Returns:
            NarrativeEvent instance populated from the dict.
        """
        return cls(
            id=data.get("id", ""),
            type=data.get("type", "unknown"),
            description=data.get("description", ""),
            actors=data.get("actors", []),
            location=data.get("location"),
            importance=data.get("importance", 0.5),
            emotional_weight=data.get("emotional_weight", 0.0),
            tags=data.get("tags", []),
            raw_event=raw_event or data.get("raw_event", {}),
        )
    
    def narrative_priority(self) -> float:
        """Calculate priority for narrative focus.
        
        Combines importance and emotional weight for sorting.
        
        Returns:
            Priority score (higher = more narratively significant).
        """
        return (self.importance * 0.6) + (self.emotional_weight * 0.4)