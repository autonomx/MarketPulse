"""Narrative Gravity — Tier 12: Narrative Convergence Engine.

This module implements Tier 12's Narrative Convergence Engine that forces
storyline convergence, payoff, and resolution. It prevents the narrative
from fragmenting into too many threads that lose player attention.

Problem:
    With agents acting, coalitions forming, rumors spreading, and learning
    adapting, there's no narrative convergence:
    - Too many concurrent threads
    - No resolution of story arcs
    - Player loses clarity and engagement
    
    Result: narrative fragmentation after ~100 ticks

Solution:
    NarrativeGravity applies "gravity" to pull storylines toward:
    1. Convergence (reducing active threads)
    2. Payoff (resolving setup threads)
    3. Resolution (closing completed arcs)

Usage:
    gravity = NarrativeGravity()
    
    # Score events for importance
    score = gravity.score_event(event, characters, world_state)
    
    # Filter active storylines
    focused = gravity.apply_gravity(active_storylines, max_active=3)
    
    # Check if storyline should conclude
    if gravity.should_conclude(storyline, current_tick):
        resolution = gravity.generate_resolution(storyline)

Architecture:
    Event Importance Scoring:
    importance = (
        character_importance * 0.3
        + coalition_size * 0.2
        + player_involvement * 0.3
        + narrative_progress * 0.2
    )
    
    Scene Focus System:
    - Only render top N events
    - Others become background noise
    - Player-centric filtering

Design Rules:
    - Maximum 3 active storylines at once
    - Events score 0.0-1.0 importance
    - Player-involved events get priority boost
    - Old unresolved threads get resolution pressure
    - Background events don't disappear, just deprioritized
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Configuration
MAX_ACTIVE_STORYLINES = 3
MIN_IMPORTANCE_THRESHOLD = 0.1
CONCLUSION_TICK_THRESHOLD = 50
BACKGROUND_DECAY_RATE = 0.02
PLAYER_PRIORITY_BOOST = 0.3

# Event type weights
EVENT_TYPE_WEIGHTS = {
    "conflict": 0.8,
    "alliance": 0.7,
    "betrayal": 0.9,
    "quest_start": 0.6,
    "quest_complete": 0.8,
    "quest_fail": 0.5,
    "discovery": 0.6,
    "battle": 0.9,
    "negotiation": 0.5,
    "diplomatic": 0.4,
    "personal": 0.3,
    "world_event": 0.7,
}


@dataclass
class StorylineWeight:
    """Weight configuration for storyline scoring.
    
    Attributes:
        character_importance: Weight for character significance.
        coalition_size: Weight for coalition participation.
        player_involvement: Weight for player relevance.
        narrative_progress: Weight for story progression.
        recency: Weight for how recent the event is.
    """
    character_importance: float = 0.3
    coalition_size: float = 0.2
    player_involvement: float = 0.3
    narrative_progress: float = 0.2
    recency: float = 0.1


@dataclass
class StorylineState:
    """State of an active storyline.
    
    Attributes:
        id: Unique storyline identifier.
        event_type: Type of event/conflict.
        participants: List of participant character IDs.
        target: Target of the storyline (entity/location/goal).
        start_tick: Tick when storyline started.
        last_active_tick: Tick of last activity.
        importance: Current importance score (0.0-1.0).
        progress: Progress toward resolution (0.0-1.0).
        is_player_involved: Whether player is involved.
        is_background: Whether storyline is in background.
        resolution_pressure: Pressure to conclude this storyline.
        events: List of events in this storyline.
    """
    
    id: str
    event_type: str = ""
    participants: List[str] = field(default_factory=list)
    target: str = ""
    start_tick: int = 0
    last_active_tick: int = 0
    importance: float = 0.5
    progress: float = 0.0
    is_player_involved: bool = False
    is_background: bool = False
    resolution_pressure: float = 0.0
    events: List[Dict[str, Any]] = field(default_factory=list)
    
    def age(self) -> int:
        """Get age of storyline in ticks.
        
        Returns:
            Number of ticks since storyline started.
        """
        return self.last_active_tick - self.start_tick
    
    def update_importance(self, new_importance: float) -> None:
        """Update storyline importance.
        
        Args:
            new_importance: New importance score (0.0-1.0).
        """
        self.importance = max(0.0, min(1.0, new_importance))
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize storyline to dict.
        
        Returns:
            Storyline data dict.
        """
        return {
            "id": self.id,
            "event_type": self.event_type,
            "participants": self.participants,
            "target": self.target,
            "age": self.age(),
            "importance": self.importance,
            "progress": self.progress,
            "is_player_involved": self.is_player_involved,
            "is_background": self.is_background,
            "resolution_pressure": self.resolution_pressure,
            "event_count": len(self.events),
        }


class NarrativeGravity:
    """Manages narrative convergence and storyline focus.
    
    The NarrativeGravity system ensures that the narrative doesn't
    fragment into too many concurrent threads. It scores events for
    importance, selects the top storylines to focus on, and applies
    resolution pressure to older threads.
    
    Usage:
        gravity = NarrativeGravity(max_active=3)
        
        # Score an event
        score = gravity.score_event(event_data, characters, world_state)
        
        # Update storylines and get focused set
        focused = gravity.update_storylines(
            active_storylines, current_tick
        )
        
        # Check if storyline should conclude
        if gravity.should_conclude(storyline, current_tick):
            gravity.conclude_storyline(storyline.id)
    """
    
    def __init__(
        self,
        max_active: int = MAX_ACTIVE_STORYLINES,
        player_id: Optional[str] = None,
    ):
        """Initialize the NarrativeGravity.
        
        Args:
            max_active: Maximum number of active storylines.
            player_id: Player character ID for priority detection.
        """
        self.max_active = max_active
        self.player_id = player_id or "player"
        
        # Active storylines: storyline_id -> StorylineState
        self._storylines: Dict[str, StorylineState] = {}
        
        # Background events queue
        self._background_events: List[Dict[str, Any]] = []
        
        # Concluded storylines history
        self._concluded: List[Dict[str, Any]] = []
        
        self._stats = {
            "events_scored": 0,
            "storylines_promoted": 0,
            "storylines_demoted": 0,
            "storylines_concluded": 0,
            "background_events": 0,
        }
    
    def score_event(
        self,
        event: Dict[str, Any],
        characters: Optional[Dict[str, Any]] = None,
        world_state: Optional[Dict[str, Any]] = None,
    ) -> float:
        """Score an event for narrative importance.
        
        Scoring formula (Tier 13 with Diversity Injection):
        importance = (
            character_importance * 0.3
            + coalition_size * 0.2
            + player_involvement * 0.3
            + narrative_progress * 0.2
            + diversity_bonus  # Tier 13: Prevent over-convergence
        )
        
        Args:
            event: Event dict with type, participants, etc.
            characters: Character data dict (optional).
            world_state: World state dict (optional).
            
        Returns:
            Importance score 0.0-1.0.
        """
        self._stats["events_scored"] += 1
        
        # Event type weight
        event_type = event.get("type", "personal")
        type_weight = EVENT_TYPE_WEIGHTS.get(event_type, 0.4)
        
        # Character importance
        participants = event.get("participants", [])
        char_importance = 0.0
        if characters:
            for char_id in participants:
                char_data = characters.get(char_id, {})
                if isinstance(char_data, dict):
                    char_importance += char_data.get("importance", 0.5)
            if participants:
                char_importance /= len(participants)
        else:
            char_importance = min(len(participants) * 0.2, 1.0)
        
        # Coalition size boost
        coalition_size = event.get("coalition_size", 0)
        coalition_boost = min(coalition_size / 5.0, 1.0) * 0.2
        
        # Player involvement
        player_involved = self.player_id in participants
        player_boost = PLAYER_PRIORITY_BOOST if player_involved else 0.0
        
        # Narrative progress (how far along any arc)
        progress = event.get("progress", 0.5)
        
        # Tier 13: Diversity bonus for underrepresented actors
        diversity_bonus = self._diversity_bonus(participants)
        
        # Calculate final score
        weights = StorylineWeight()
        importance = (
            char_importance * weights.character_importance
            + coalition_boost
            + player_boost
            + progress * weights.narrative_progress
            + type_weight * weights.recency
            + diversity_bonus  # Tier 13 addition
        )
        
        return max(0.0, min(1.0, importance))
    
    def _diversity_bonus(
        self,
        participants: List[str],
    ) -> float:
        """Calculate diversity bonus for underrepresented actors.
        
        This Tier 13 patch prevents "main character lock" where
        the same 2-3 characters dominate all storylines.
        Actors with fewer recent appearances get a narrative boost.
        
        Args:
            participants: List of participant character IDs.
            
        Returns:
            Diversity bonus value (0.0-0.15).
        """
        # Track recent appearances across all storylines
        recent_counts: Dict[str, float] = {}
        for storyline in self._storylines.values():
            for participant in storyline.participants:
                recent_counts[participant] = recent_counts.get(participant, 0) + 1
        
        # Also count concluded storylines (with reduced weight)
        for concluded in self._concluded[-10:]:  # Last 10 concluded
            for participant in concluded.get("participants", []):
                recent_counts[participant] = recent_counts.get(participant, 0) + 0.5
        
        # Find the minimum appearances among participants
        if not participants:
            return 0.0
        
        min_appearances = min(
            recent_counts.get(p, 0) for p in participants
        )
        
        # Award bonus for underrepresented actors
        if min_appearances < 2:
            return 0.15  # Strong boost for new/rare actors
        elif min_appearances < 4:
            return 0.05  # Small boost for less common actors
        
        return 0.0
    
    def update_storylines(
        self,
        current_tick: int = 0,
    ) -> List[StorylineState]:
        """Update all storylines and return focused set.
        
        This method:
        1. Updates last_active for all storylines
        2. Recalculates importance scores
        3. Applies background decay to non-focused storylines
        4. Applies resolution pressure to old storylines
        5. Returns top N focused storylines
        
        Args:
            current_tick: Current simulation tick.
            
        Returns:
            List of focused StorylineState objects (top N by importance).
        """
        # Update all storylines
        for storyline in self._storylines.values():
            storyline.last_active_tick = current_tick
            
            # Apply background decay
            if storyline.is_background:
                storyline.update_importance(
                    storyline.importance - BACKGROUND_DECAY_RATE
                )
            
            # Apply resolution pressure to old storylines
            age = storyline.age()
            if age > CONCLUSION_TICK_THRESHOLD * 0.5:
                storyline.resolution_pressure = min(
                    1.0, (age - CONCLUSION_TICK_THRESHOLD * 0.5) / CONCLUSION_TICK_THRESHOLD
                )
        
        # Sort by importance
        sorted_storylines = sorted(
            self._storylines.values(),
            key=lambda s: s.importance,
            reverse=True,
        )
        
        # Separate focused and background
        focused = sorted_storylines[:self.max_active]
        background = sorted_storylines[self.max_active:]
        
        # Update background status
        promoted = 0
        demoted = 0
        
        for storyline in focused:
            if storyline.is_background:
                storyline.is_background = False
                promoted += 1
                self._stats["storylines_promoted"] += 1
        
        for storyline in background:
            if not storyline.is_background:
                storyline.is_background = True
                demoted += 1
                self._stats["storylines_demoted"] += 1
        
        self._stats["background_events"] = len(background)
        
        return focused
    
    def should_conclude(
        self,
        storyline: StorylineState,
        current_tick: int = 0,
    ) -> bool:
        """Check if a storyline should be concluded.
        
        Storylines conclude when:
        - Progress reaches 1.0
        - Age exceeds threshold with no progress
        - Resolution pressure is high and importance is low
        
        Args:
            storyline: Storyline to check.
            current_tick: Current simulation tick.
            
        Returns:
            True if storyline should conclude.
        """
        # Complete storyline
        if storyline.progress >= 1.0:
            return True
        
        # Too old with minimal progress
        age = storyline.age()
        if age > CONCLUSION_TICK_THRESHOLD * 1.5 and storyline.progress < 0.2:
            return True
        
        # High resolution pressure with low importance
        if (storyline.resolution_pressure > 0.7 and
                storyline.importance < MIN_IMPORTANCE_THRESHOLD * 2):
            return True
        
        # Below importance threshold
        if storyline.importance < MIN_IMPORTANCE_THRESHOLD:
            return True
        
        return False
    
    def conclude_storyline(
        self,
        storyline_id: str,
        resolution: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Conclude a storyline and move to history.
        
        Args:
            storyline_id: Storyline to conclude.
            resolution: Optional resolution description.
            
        Returns:
            Concluded storyline data, or None if not found.
        """
        storyline = self._storylines.pop(storyline_id, None)
        if storyline is None:
            return None
        
        concluded_data = storyline.to_dict()
        concluded_data["resolution"] = resolution or "Natural conclusion"
        concluded_data["concluded_tick"] = storyline.last_active_tick
        concluded_data["status"] = "concluded"
        
        self._concluded.append(concluded_data)
        self._stats["storylines_concluded"] += 1
        
        logger.info(
            f"Storyline concluded: {storyline_id} "
            f"(importance: {storyline.importance:.2f}, "
            f"progress: {storyline.progress:.2f})"
        )
        
        return concluded_data
    
    def generate_resolution(
        self,
        storyline: StorylineState,
    ) -> str:
        """Generate a resolution description for a storyline.
        
        Args:
            storyline: Storyline to resolve.
            
        Returns:
            Resolution description string.
        """
        if storyline.progress >= 0.8:
            return f"Storyline '{storyline.id}' reached natural conclusion"
        elif storyline.progress >= 0.5:
            return f"Storyline '{storyline.id}' partially resolved"
        elif storyline.importance < MIN_IMPORTANCE_THRESHOLD:
            return f"Storyline '{storyline.id}' faded from attention"
        else:
            return f"Storyline '{storyline.id}' concluded due to age"
    
    def get_stats(self) -> Dict[str, Any]:
        """Get narrative gravity statistics.
        
        Returns:
            Stats dict.
        """
        return {
            **self._stats,
            "active_storylines": len(self._storylines),
            "concluded_storylines": len(self._concluded),
            "background_events": len(self._background_events),
        }
    
    def reset(self) -> None:
        """Reset all narrative gravity data."""
        self._storylines.clear()
        self._background_events.clear()
        self._concluded.clear()
        self._stats = {
            "events_scored": 0,
            "storylines_promoted": 0,
            "storylines_demoted": 0,
            "storylines_concluded": 0,
            "background_events": 0,
        }