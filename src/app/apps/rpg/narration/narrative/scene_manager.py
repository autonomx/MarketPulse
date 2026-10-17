"""Scene Engine — STEP 3 of RPG Design Implementation.

This module implements the SceneManager, which tracks the active scene
and accumulates events for coherent narration.

Purpose:
    Group related events into scenes and track scene context (location,
    participants, recent events) so the narrator can generate cohesive text.

Architecture:
    Narrative Events → SceneManager → Scene Context → Story Generation

Usage:
    sm = SceneManager()
    sm.update_scene(narrative_events)
    context = sm.get_scene_context()
    
Design Compliance:
    - STEP 3: Scene Engine from rpg-design.txt
    - Does NOT generate text — only tracks scene state
    - Keeps memory bounded by limiting stored events
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Scene:
    """An active scene being tracked by the SceneManager.
    
    A scene represents a coherent narrative moment with shared
    location and participants.
    
    Attributes:
        id: Unique scene identifier.
        location: Location ID or name of the scene.
        participants: List of entity IDs in this scene.
        recent_events: Recent events that happened in this scene.
        mood: Overall mood/atmosphere of the scene.
        tick_started: Simulation tick when this scene began.
        event_count: Total events that occurred in this scene.
    """
    
    id: str
    location: str
    participants: List[str] = field(default_factory=list)
    recent_events: List[Dict[str, Any]] = field(default_factory=list)
    mood: str = "neutral"
    tick_started: int = 0
    event_count: int = 0


class SceneManager:
    """Manages the active scene and scene transitions.
    
    The SceneManager:
    1. Tracks the current active scene
    2. Batches related events together
    3. Provides scene-context for the narrative generator
    4. Handles scene transitions when location/participants change
    
    Attributes:
        active_scene: Currently active scene (None if no scene).
        max_events_per_scene: Maximum events to keep per scene.
        scenes_completed: History of completed scenes.
    """
    
    def __init__(self, max_events_per_scene: int = 10, max_completed_scenes: int = 20):
        """Initialize the SceneManager.
        
        Args:
            max_events_per_scene: Max events to store per scene.
            max_completed_scenes: Max completed scene history.
        """
        self.active_scene: Optional[Scene] = None
        self.max_events_per_scene = max_events_per_scene
        self.scenes_completed: List[Scene] = []
        self._max_completed_scenes = max_completed_scenes
        self._scene_counter = 0
        
    def update_scene(self, events: List[Dict[str, Any]]) -> None:
        """Update the active scene with new events.
        
        Creates a new scene if none is active. Adds events to current
        scene, managing participant list and mood.
        
        Args:
            events: List of event dicts from the narrative director.
        """
        if not events:
            return
        
        primary = events[0]
        
        if self.active_scene is None:
            self._create_new_scene(primary)
        assert self.active_scene is not None
        
        # Check if scene should transition (location change)
        new_location = primary.get("location")
        if new_location and new_location != self.active_scene.location:
            self._transition_scene(new_location)
        
        # Add events
        self.active_scene.recent_events.extend(events)
        self.active_scene.event_count += len(events)
        
        # Keep memory bounded
        self.active_scene.recent_events = (
            self.active_scene.recent_events[-self.max_events_per_scene:]
        )
        
        # Update participants
        for event in events:
            for actor in event.get("actors", []):
                if actor not in self.active_scene.participants:
                    self.active_scene.participants.append(actor)
        
        # Update mood based on event types
        self._update_mood(events)
    
    def get_scene_context(self) -> Dict[str, Any]:
        """Get the context of the active scene for narration.
        
        Returns:
            Dict with location, participants, recent_events, mood.
            Empty dict if no active scene.
        """
        if self.active_scene is None:
            return {
                "location": "unknown",
                "participants": [],
                "recent_events": [],
                "mood": "neutral",
            }
        
        return {
            "location": self.active_scene.location,
            "participants": list(self.active_scene.participants),
            "recent_events": list(self.active_scene.recent_events),
            "mood": self.active_scene.mood,
            "scene_id": self.active_scene.id,
        }
    
    def end_scene(self) -> Optional[Scene]:
        """End the current scene and store in history.
        
        Returns:
            The completed scene, or None if no active scene.
        """
        if self.active_scene is None:
            return None
        
        completed = self.active_scene
        self.active_scene = None
        
        self.scenes_completed.append(completed)
        self.scenes_completed = self.scenes_completed[-self._max_completed_scenes:]
        
        return completed
    
    def _create_new_scene(self, primary_event: Dict[str, Any]) -> None:
        """Create a new active scene from the first event.
        
        Args:
            primary_event: The event that triggers scene creation.
        """
        self._scene_counter += 1
        self.active_scene = Scene(
            id=f"scene_{self._scene_counter}",
            location=primary_event.get("location", "unknown"),
            participants=list(primary_event.get("actors", [])),
            mood=self._determine_initial_mood(primary_event),
        )
    
    def _transition_scene(self, new_location: str) -> Scene:
        """Transition from the old scene to a new one.
        
        Args:
            new_location: New location to transition to.
            
        Returns:
            New Scene instance.
        """
        if self.active_scene:
            self.end_scene()
        
        self._scene_counter += 1
        self.active_scene = Scene(
            id=f"scene_{self._scene_counter}",
            location=new_location,
            tick_started=0,
        )
        return self.active_scene
    
    def _update_mood(self, events: List[Dict[str, Any]]) -> None:
        """Update scene mood based on event types.
        
        Mood tracks the emotional atmosphere of the scene.
        
        Args:
            events: List of events to consider for mood update.
        """
        mood_scores: Dict[str, int] = {
            "combat": 1,
            "death": 2,
            "critical_hit": 1,
            "betrayal": 2,
            "heal": -1,
            "move": -1,
            "speak": 0,
        }
        
        total = 0
        for event in events:
            event_type = event.get("type", "unknown")
            total += mood_scores.get(event_type, 0)
        assert self.active_scene is not None
        
        if total >= 3:
            self.active_scene.mood = "dark"
        elif total >= 1:
            self.active_scene.mood = "tense"
        elif total <= -2:
            self.active_scene.mood = "peaceful"
        elif total < 0:
            self.active_scene.mood = "calm"
        else:
            self.active_scene.mood = "neutral"
    
    @staticmethod
    def _determine_initial_mood(event: Dict[str, Any]) -> str:
        """Determine initial mood from the first event in a scene.
        
        Args:
            event: The primary event dict.
            
        Returns:
            Initial mood string.
        """
        event_type = event.get("type", "unknown")
        
        if event_type in ["combat", "death", "critical_hit"]:
            return "dark"
        elif event_type in ["speak", "move"]:
            return "calm"
        elif event_type in ["heal", "alliance_formed"]:
            return "peaceful"
        else:
            return "neutral"