"""Story Arc Manager — Persistent long-term narrative goals.

This module implements CRITICAL PATCH 2 from the RPG design specification:
"DIRECTOR HAS NO LONG-TERM INTENT"

The Problem: Director plans per turn, scenes exist, but there are no
persistent goals or story arcs. Without arcs, the system produces moments,
not stories.

The Solution: StoryArc and StoryArcManager that track long-term narrative
goals across multiple scenes and turns.

Architecture:
    StoryArc(goal, entities, progress) →
        Update from events →
        Progress toward completion

Usage:
    manager = StoryArcManager()
    arc = manager.create_arc("Defeat the Dark Lord", ["player", "dark_lord"])
    manager.update_arcs(events)
    summary = manager.get_summary()  # For Director prompt injection

Key Features:
    - Persistent goals that survive across turns/scenes
    - Progress tracking from events
    - Arc completion triggers story consequences
    - Multiple concurrent arcs supported
    - Arc dependency (one arc unlocks another)
    - Summary for Director prompt injection
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Set

# [FIX #3] Story arc saturation prevention
MAX_ACTIVE_ARCS = 3
MAX_ARCS_PER_ENTITY = 2


class StoryArc:
    """A persistent long-term story goal with progress tracking.
    
    Story arcs span multiple scenes and turns, giving the narrative
    a sense of direction and purpose. Each arc tracks progress
    toward a goal and triggers consequences when completed.
    
    Attributes:
        id: Unique arc identifier.
        goal: Description of the arc's objective.
        entities: Entity IDs involved in this arc.
        progress: Float progress toward completion (0.0-1.0+).
        completed: Whether arc has been completed.
        created_at: Timestamp when arc was created.
        tags: Arc categorization tags.
        dependency: Arc ID that must complete before this arc activates.
        priority: Arc importance (higher = more Director focus).
        resolution_effect: Event to inject when arc completes.
    """
    
    def __init__(
        self,
        goal: str,
        entities: Set[str],
        arc_id: Optional[str] = None,
        progress: float = 0.0,
        tags: Optional[List[str]] = None,
        dependency: Optional[str] = None,
        priority: float = 1.0,
        resolution_effect: Optional[Dict[str, Any]] = None,
    ):
        """Initialize StoryArc.
        
        Args:
            goal: Arc objective description.
            entities: Entity IDs involved in arc.
            arc_id: Unique identifier (auto-generated if None).
            progress: Initial progress (0.0-1.0 scale).
            tags: Arc categorization tags.
            dependency: Arc ID that must complete first.
            priority: Arc importance for Director focus.
            resolution_effect: Event to inject on completion.
        """
        self.id = arc_id or f"arc_{id(self)}"
        self.goal = goal
        self.entities = entities or set()
        self.progress = max(0.0, min(1.0, progress))
        self.completed = progress >= 1.0
        self.created_at = time.time()
        self.tags = tags or []
        self.dependency = dependency
        self.priority = priority
        self.resolution_effect = resolution_effect
        
        # Internal tracking
        self._events_processed: int = 0
        self._stalled_ticks: int = 0  # Ticks with no progress
        
    def update(self, events: List[Dict[str, Any]]) -> float:
        """Update arc progress based on recent events.
        
        Scans events for relevance to this arc and increments progress.
        
        Args:
            events: List of event dicts to process.
            
        Returns:
            Progress delta from this update.
        """
        if self.completed:
            return 0.0
            
        # Check dependency
        if self.dependency:
            # Dependency check happens at ArcManager level
            pass
            
        delta = 0.0
        
        for event in events:
            if self._is_relevant_event(event):
                delta += self._calculate_progress(event)
                
        self.progress = min(1.0, self.progress + delta)
        self._events_processed += 1
        
        if delta > 0:
            self._stalled_ticks = 0
        else:
            self._stalled_ticks += 1
            
        if self.progress >= 1.0 and not self.completed:
            self.completed = True
            
        return delta
        
    def _is_relevant_event(self, event: Dict[str, Any]) -> bool:
        """Check if an event is relevant to this arc.
        
        Events involving arc entities are relevant.
        
        Args:
            event: Event dict to check.
            
        Returns:
            True if event involves arc entities.
        """
        if not self.entities:
            return False
            
        # Check common event fields for entity IDs
        entity_fields = ["source", "target", "actor", "entity", "speaker"]
        
        for field in entity_fields:
            value = event.get(field, "")
            if value in self.entities:
                return True
                
        return False
        
    def _calculate_progress(self, event: Dict[str, Any]) -> float:
        """Calculate progress contribution from a relevant event.
        
        Args:
            event: Relevant event dict.
            
        Returns:
            Progress delta (0.0-0.3).
        """
        event_type = event.get("type", "")
        
        # Major story events contribute more
        major_events = {"death", "betrayal", "resolution", "ally_joined", "boss_defeated"}
        moderate_events = {"damage", "critical_hit", "fled", "captured"}
        minor_events = {"move", "speak", "observe", "item_picked_up"}
        
        if event_type in major_events:
            return 0.15
        elif event_type in moderate_events:
            return 0.08
        elif event_type in minor_events:
            return 0.03
            
        return 0.02  # Default small progress
        
    def to_dict(self) -> Dict[str, Any]:
        """Serialize arc to dict.
        
        Returns:
            Arc data as dict.
        """
        return {
            "id": self.id,
            "goal": self.goal,
            "entities": list(self.entities),
            "progress": round(self.progress, 3),
            "completed": self.completed,
            "tags": self.tags,
            "dependency": self.dependency,
            "priority": self.priority,
        }


# [FIX #3] Arc prioritization helper
def prioritize_arcs(arcs: List["StoryArc"], max_arcs: int = MAX_ACTIVE_ARCS) -> List["StoryArc"]:
    """Sort arcs by priority and truncate to max_arcs.
    
    [FIX #3] Prevents story arc saturation by limiting active arcs.
    
    Args:
        arcs: List of StoryArc instances.
        max_arcs: Maximum number of active arcs to keep.
        
    Returns:
        Sorted and truncated arc list.
    """
    return sorted(arcs, key=lambda a: a.priority, reverse=True)[:max_arcs]


class StoryArcManager:
    """Manages persistent long-term story arcs.
    
    [FIX #3] Added arc saturation prevention:
    - MAX_ACTIVE_ARCS limits total concurrent arcs
    - MAX_ARCS_PER_ENTITY limits how many arcs one entity can be involved in
    
    The ArcManager:
    - Creates story arcs with goals and entities
    - Updates arcs from events
    - Tracks arc completion and triggers consequences
    - Provides summaries for Director prompt injection
    - Handles arc dependencies
    
    Usage:
        manager = StoryArcManager()
        arc = manager.create_arc("Defeat the Dark Lord", {"player", "dark_lord"})
        manager.update_arcs(events)
        summary = manager.get_summary_for_director()
    """
    
    def __init__(self, max_active_arcs: int = MAX_ACTIVE_ARCS, max_arcs_per_entity: int = MAX_ARCS_PER_ENTITY):
        """Initialize StoryArcManager.
        
        Args:
            max_active_arcs: Maximum number of concurrent active arcs.
            max_arcs_per_entity: Maximum arcs any single entity can be involved in.
        """
        self.active_arcs: List[StoryArc] = []
        self.completed_arcs: List[StoryArc] = []
        self.pending_arcs: List[StoryArc] = []  # Waiting on dependencies
        self._arc_counter = 0
        # [FIX #3] Arc saturation limits
        self.max_active_arcs = max_active_arcs
        self.max_arcs_per_entity = max_arcs_per_entity
        
    def create_arc(
        self,
        goal: str,
        entities: Set[str],
        arc_id: Optional[str] = None,
        progress: float = 0.0,
        tags: Optional[List[str]] = None,
        dependency: Optional[str] = None,
        priority: float = 1.0,
    ) -> Optional[StoryArc]:
        """Create a new story arc.
        
        If the arc has an unmet dependency, it goes into pending.
        
        Args:
            goal: Arc objective.
            entities: Entity IDs involved.
            arc_id: Unique identifier.
            progress: Initial progress.
            tags: Arc tags.
            dependency: Arc ID that must complete first.
            priority: Arc importance.
            
        Returns:
            Newly created StoryArc.
        """
        self._arc_counter += 1
        
        arc = StoryArc(
            goal=goal,
            entities=entities,
            arc_id=arc_id or f"arc_{self._arc_counter}",
            progress=progress,
            tags=tags,
            dependency=dependency,
            priority=priority,
        )
        
        # Check dependency
        if dependency:
            dep_completed = any(
                a.id == dependency and a.completed
                for a in self.completed_arcs
            )
            if not dep_completed:
                self.pending_arcs.append(arc)
                return arc
                
        # [FIX #3] Enforce arc limits when adding
        return self._enforce_arc_limits(arc)
        
    def _enforce_arc_limits(self, new_arc: Optional[StoryArc] = None) -> Optional[StoryArc]:
        """Enforce MAX_ACTIVE_ARCS and MAX_ARCS_PER_ENTITY limits.
        
        [FIX #3] Prevents story arc saturation by:
        1. Limiting total active arcs to MAX_ACTIVE_ARCS
        2. Limiting per-entity arcs to MAX_ARCS_PER_ENTITY
        
        Args:
            new_arc: Optionally check and add a new arc.
            
        Returns:
            The arc if it was added, None if it was rejected.
        """
        # Check per-entity limits
        if new_arc:
            for entity in new_arc.entities:
                entity_arc_count = sum(
                    1 for arc in self.active_arcs if entity in arc.entities
                )
                if entity_arc_count >= self.max_arcs_per_entity:
                    # Reject - entity already at arc limit
                    return None
                    
            # Add arc if it passes entity check
            self.active_arcs.append(new_arc)
                    
        # Trim active arcs to MAX_ACTIVE_ARCS
        if len(self.active_arcs) > self.max_active_arcs:
            self.active_arcs = prioritize_arcs(self.active_arcs, self.max_active_arcs)
            
        return new_arc
        
    def update_arcs(self, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Update all active arcs with recent events.
        
        Processes events for each active arc and checks for completions.
        Also checks if pending arcs can be activated.
        
        [FIX #3] Calls _limit_active_arcs after updates to prevent arc saturation.
        
        Args:
            events: Events from this turn.
            
        Returns:
            List of completion events for newly completed arcs.
        """
        completion_events = []
        
        # Update active arcs
        for arc in list(self.active_arcs):
            delta = arc.update(events)
            
            if arc.completed:
                self.active_arcs.remove(arc)
                self.completed_arcs.append(arc)
                
                # Generate completion event
                completion_events.append({
                    "type": "arc_completion",
                    "arc_id": arc.id,
                    "goal": arc.goal,
                    "entities": list(arc.entities),
                })
                
        # Check pending arcs for activation
        newly_activated = self._check_pending_arcs()
        for arc in newly_activated:
            completion_events.append({
                "type": "arc_activation",
                "arc_id": arc.id,
                "goal": arc.goal,
            })
            
        # Apply resolution effects
        for event in completion_events:
            if event["type"] == "arc_completion":
                completed = next(
                    (a for a in self.completed_arcs if a.id == event["arc_id"]),
                    None
                )
                if completed and completed.resolution_effect:
                    completion_events.append(completed.resolution_effect)
        
        # [FIX #3] Enforce arc limits after updates to prevent saturation
        self._limit_active_arcs()
                    
        return completion_events
        
    def _limit_active_arcs(self) -> None:
        """Limit number of active arcs to prevent story dilution.
        
        [FIX #3] Prevents arc saturation by:
        1. Sorting arcs by priority (descending)
        2. Keeping only top MAX_ACTIVE_ARCS arcs
        3. Moving excess arcs to pending for later activation
        
        This ensures narrative focus and prevents too many story threads.
        """
        if len(self.active_arcs) <= self.max_active_arcs:
            return
        
        # Sort by priority (descending)
        self.active_arcs.sort(key=lambda a: a.priority, reverse=True)
        
        # Move excess arcs to pending
        excess = self.active_arcs[self.max_active_arcs:]
        self.active_arcs = self.active_arcs[:self.max_active_arcs]
        
        for arc in excess:
            self.pending_arcs.append(arc)
        
    def _check_pending_arcs(self) -> List[StoryArc]:
        """Check if pending arcs can be activated (dependency met).
        
        Returns:
            List of newly activated arcs.
        """
        newly_activated = []
        
        for arc in list(self.pending_arcs):
            if arc.dependency:
                dep_completed = any(
                    a.id == arc.dependency and a.completed
                    for a in self.completed_arcs
                )
                if dep_completed:
                    self.pending_arcs.remove(arc)
                    self.active_arcs.append(arc)
                    newly_activated.append(arc)
                    
        return newly_activated
        
    def get_summary_for_director(self) -> str:
        """Get arc summary for Director prompt injection.
        
        Returns:
            Multi-line string summarizing active and completed arcs.
        """
        lines = ["=== Story Arcs ==="]
        
        if self.active_arcs:
            lines.append("Active Arcs:")
            for arc in self.active_arcs:
                pct = int(arc.progress * 100)
                lines.append(f"  - {arc.goal} [{pct}%] (ids: {', '.join(sorted(arc.entities))})")
        else:
            lines.append("Active Arcs: None")
            
        if self.completed_arcs:
            lines.append("Completed Arcs:")
            for arc in self.completed_arcs[-5:]:
                lines.append(f"  ✓ {arc.goal}")
                
        if self.pending_arcs:
            lines.append("Pending Arcs (waiting on dependency):")
            for arc in self.pending_arcs:
                lines.append(f"  - {arc.goal} (requires: {arc.dependency})")
                
        return "\n".join(lines)
        
    def reset(self) -> None:
        """Reset arc manager state."""
        self.active_arcs.clear()
        self.completed_arcs.clear()
        self.pending_arcs.clear()
        self._arc_counter = 0