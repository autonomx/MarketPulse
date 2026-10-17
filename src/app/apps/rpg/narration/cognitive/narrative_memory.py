"""Narrative Memory Layer — Tier 13: Emotional + Experiential Layer.

This module implements Tier 13's Narrative Memory Layer that provides
historical awareness at the narrative level. Past arcs meaningfully
shape future arcs, creating a world with a "legend" feeling.

Problem:
    Past arcs don't meaningfully shape future arcs.
    World lacks continuity across sessions.
    
    Result: world feels disconnected, no legendary history.

Solution:
    NarrativeMemory tracks completed storylines, significant events,
    and their lasting impact on the world. This memory feeds into:
    - AgentBrain (informing decisions based on history)
    - IntentEnrichment (LLM context about past events)
    - NarrativeGravity (determining storyline importance)

Usage:
    memory = NarrativeMemory()
    memory.store_arc("war_of_factions", {"outcome": "A defeated B", "impact": 0.4})
    relevance = memory.get_relevant_history(current_storyline)

Architecture:
    Narrative History:
    [
        {
            "arc": "war of factions",
            "outcome": "A defeated B",
            "participants": ["A", "B", "C"],
            "impact": 0.4,  # Long-term world impact
            "emotions": {"anger": 0.6, "fear": 0.3},
            "tick_resolved": 150,
            "resolution_type": "victory",
        }
    ]
    
    Query Methods:
    - get_relevant_history(actors, event_type) -> similar past arcs
    - get_reputation_history(character_id) -> past behavior patterns
    - get_emotional_resonance(actors) -> leftover emotions

Design Rules:
    - All concluded arcs are stored
    - Impact fades but never fully disappears
    - Similar events trigger memory recall
    - Emotional residue affects current decisions
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Memory decay rate per 100 ticks
MEMORY_DECAY_RATE = 0.1

# Tier 14 Fix: Contextual Memory Relevance threshold
MEMORY_RELEVANCE_THRESHOLD = 0.3


def relevance_score(
    memory: ArcMemory,
    current_context: Dict[str, Any],
) -> float:
    """Calculate contextual relevance score for a memory.
    
    Tier 14 Fix: Prevents NPCs from being too history-bound by making
    memory retrieval selective, situational, and adaptive.
    
    Relevance is determined by:
    - Tag similarity between memory and current context
    - Time decay of the memory
    - Emotional intensity of the memory
    
    Args:
        memory: ArcMemory to score.
        current_context: Current situation context with keys:
            - tags: Set of current context tags/themes
            - current_tick: Current simulation tick
            
    Returns:
        Relevance score (0.0-1.0).
    """
    # Tag similarity
    memory_tags = set(memory.consequences) | {memory.arc_type}
    context_tags = set(current_context.get("tags", []))
    
    if context_tags:
        tag_overlap = len(memory_tags & context_tags) / max(len(context_tags), 1)
    else:
        tag_overlap = 0.0
    
    # Time decay
    ticks_since = current_context.get("current_tick", 0) - memory.tick_resolved
    time_decay_factor = max(0.0, 1.0 - (MEMORY_DECAY_RATE * ticks_since / 100.0))
    
    # Emotional intensity
    emotional_intensity = sum(memory.emotions.values()) / max(len(memory.emotions), 1)
    
    # Combined relevance
    score = (
        tag_overlap * 0.4
        + time_decay_factor * 0.3
        + emotional_intensity * 0.3
    )
    
    return min(1.0, score * memory.relevance)


def filter_memories_by_relevance(
    memories: List[ArcMemory],
    current_context: Dict[str, Any],
    threshold: float = MEMORY_RELEVANCE_THRESHOLD,
) -> List[ArcMemory]:
    """Filter memories to only those contextually relevant.
    
    Prevents NPCs from overreacting to old events by filtering
    out memories that aren't situationally relevant.
    
    Args:
        memories: List of ArcMemory objects to filter.
        current_context: Current situation context.
        threshold: Minimum relevance score to include.
        
    Returns:
        Filtered list of relevant ArcMemory objects.
    """
    return [
        m for m in memories
        if relevance_score(m, current_context) > threshold
    ]

# Maximum stored arcs
MAX_STORED_ARCS = 100

# Similarity threshold for relevant history
SIMILARITY_THRESHOLD = 0.3


@dataclass
class ArcMemory:
    """Memory of a completed story arc.
    
    Attributes:
        arc_id: Unique identifier for the arc.
        arc_type: Type of event/conflict.
        outcome: Description of how the arc resolved.
        participants: All participants in the arc.
        impact: Long-term impact on world (0.0-1.0).
        emotions: Emotional state at resolution.
        tick_started: When the arc began.
        tick_resolved: When the arc concluded.
        resolution_type: How it resolved (victory, tragedy, etc.).
        consequences: List of lasting consequences.
        relevance: Current relevance score (decays over time).
    """
    
    arc_id: str = ""
    arc_type: str = ""
    outcome: str = ""
    participants: List[str] = field(default_factory=list)
    impact: float = 0.5
    emotions: Dict[str, float] = field(default_factory=dict)
    tick_started: int = 0
    tick_resolved: int = 0
    resolution_type: str = "unknown"
    consequences: List[str] = field(default_factory=list)
    relevance: float = 1.0
    
    @property
    def age(self) -> int:
        """Get age of arc in ticks."""
        return self.tick_resolved - self.tick_started
    
    def decay(self, ticks_since_resolution: int) -> float:
        """Apply time-based decay to relevance.
        
        Args:
            ticks_since_resolution: Ticks since this arc resolved.
            
        Returns:
            New relevance score after decay.
        """
        decay_factor = 1.0 - (MEMORY_DECAY_RATE * ticks_since_resolution / 100.0)
        self.relevance = max(0.05, self.relevance * max(0.0, decay_factor))
        return self.relevance
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize arc memory to dict."""
        return {
            "arc_id": self.arc_id,
            "arc_type": self.arc_type,
            "outcome": self.outcome,
            "participants": list(self.participants),
            "impact": self.impact,
            "emotions": dict(self.emotions),
            "tick_started": self.tick_started,
            "tick_resolved": self.tick_resolved,
            "resolution_type": self.resolution_type,
            "consequences": list(self.consequences),
            "relevance": self.relevance,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ArcMemory":
        """Create ArcMemory from dict."""
        memory = cls()
        memory.arc_id = data.get("arc_id", "")
        memory.arc_type = data.get("arc_type", "")
        memory.outcome = data.get("outcome", "")
        memory.participants = list(data.get("participants", []))
        memory.impact = data.get("impact", 0.5)
        memory.emotions = dict(data.get("emotions", {}))
        memory.tick_started = data.get("tick_started", 0)
        memory.tick_resolved = data.get("tick_resolved", 0)
        memory.resolution_type = data.get("resolution_type", "unknown")
        memory.consequences = list(data.get("consequences", []))
        memory.relevance = data.get("relevance", 1.0)
        return memory


@dataclass
class EmotionalResidue:
    """Persistent emotional impact from past events.
    
    Attributes:
        character_id: Character affected.
        target_id: Target of the emotion (optional).
        emotion_type: Type of emotion.
        intensity: Residual intensity (0.0-1.0).
        source_arc: Arc that created this residue.
        created_tick: When the residue was created.
        decay_rate: How quickly the residue fades.
    """
    
    character_id: str = ""
    target_id: str = ""
    emotion_type: str = ""
    intensity: float = 0.0
    source_arc: str = ""
    created_tick: int = 0
    decay_rate: float = 0.05
    
    def decay(self, current_tick: int) -> float:
        """Apply decay to this residue.
        
        Args:
            current_tick: Current simulation tick.
            
        Returns:
            New intensity value.
        """
        elapsed = current_tick - self.created_tick
        self.intensity = max(0.0, self.intensity - self.decay_rate * elapsed / 100.0)
        return self.intensity
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict."""
        return {
            "character_id": self.character_id,
            "target_id": self.target_id,
            "emotion_type": self.emotion_type,
            "intensity": self.intensity,
            "source_arc": self.source_arc,
            "created_tick": self.created_tick,
            "decay_rate": self.decay_rate,
        }


class NarrativeMemory:
    """Tracks historical narrative arcs and their lasting impact.
    
    This system ensures past arcs meaningfully shape future arcs,
    creating continuity across sessions and a "legend" feeling.
    
    Usage:
        memory = NarrativeMemory()
        
        # Store a completed arc
        memory.store_arc({
            "arc_id": "war_of_factions",
            "arc_type": "faction_conflict",
            "outcome": "A defeated B",
            "participants": ["A", "B"],
            "resolution_type": "victory",
            "tick_resolved": 150,
        })
        
        # Get relevant history for current storyline
        relevant = memory.get_relevant_history(
            current_actors=["A", "C"],
            event_type="faction_conflict",
        )
    """
    
    def __init__(self, max_arcs: int = MAX_STORED_ARCS):
        """Initialize narrative memory.
        
        Args:
            max_arcs: Maximum number of arcs to store.
        """
        self.max_arcs = max_arcs
        
        # All stored arc memories
        self._arcs: List[ArcMemory] = []
        
        # Emotional residue from past arcs
        self._emotional_residue: Dict[str, List[EmotionalResidue]] = {}  # char_id -> residues
        
        # Character reputation history
        self._reputation_history: Dict[str, List[Dict[str, Any]]] = {}  # char_id -> behaviors
        
        self._stats = {
            "arcs_stored": 0,
            "arcs_decayed": 0,
            "arcs_purged": 0,
            "queries_made": 0,
        }
    
    def store_arc(self, arc_data: Dict[str, Any]) -> ArcMemory:
        """Store a completed story arc in memory.
        
        Args:
            arc_data: Arc data dict with required fields:
                - arc_id: Unique identifier
                - arc_type: Type of event
                - outcome: Resolution description
                - participants: List of participant IDs
                - tick_resolved: When it concluded
        
        Returns:
            ArcMemory object created from the data.
        """
        memory = ArcMemory.from_dict(arc_data)
        
        # Ensure required fields
        if not memory.arc_id or not memory.arc_type:
            logger.warning("Stored arc missing required fields: %s", arc_data)
            return memory
        
        # Check for duplicate arc_id
        existing = self._find_arc_by_id(memory.arc_id)
        if existing:
            # Update existing arc instead of duplicating
            existing.outcome = memory.outcome
            existing.impact = memory.impact
            existing.resolution_type = memory.resolution_type
            existing.consequences = memory.consequences
            existing.relevance = memory.relevance
            return existing
        
        self._arcs.append(memory)
        self._stats["arcs_stored"] += 1
        
        # Create emotional residue for participants
        for participant in memory.participants:
            for emotion, intensity in memory.emotions.items():
                if intensity > 0.2:  # Only significant emotions
                    residue = EmotionalResidue(
                        character_id=participant,
                        emotion_type=emotion,
                        intensity=intensity * 0.5,  # Residue is half the original
                        source_arc=memory.arc_id,
                        created_tick=memory.tick_resolved,
                    )
                    if participant not in self._emotional_residue:
                        self._emotional_residue[participant] = []
                    self._emotional_residue[participant].append(residue)
        
        # Update reputation history
        resolution_type = memory.resolution_type
        for participant in memory.participants:
            if participant not in self._reputation_history:
                self._reputation_history[participant] = []
            self._reputation_history[participant].append({
                "arc_id": memory.arc_id,
                "arc_type": memory.arc_type,
                "outcome": resolution_type,
                "tick": memory.tick_resolved,
            })
        
        # Purge old arcs if over limit
        if len(self._arcs) > self.max_arcs:
            self._arcs.sort(key=lambda a: a.relevance, reverse=True)
            purged = self._arcs[self.max_arcs:]
            self._arcs = self._arcs[:self.max_arcs]
            self._stats["arcs_purged"] += len(purged)
        
        return memory
    
    def get_relevant_history(
        self,
        current_actors: Optional[List[str]] = None,
        event_type: Optional[str] = None,
        max_results: int = 5,
        current_tick: int = 0,
    ) -> List[Dict[str, Any]]:
        """Get past arcs relevant to the current situation.
        
        Finds arcs that share actors or event types with the current
        situation, helping inform current decisions based on history.
        
        Args:
            current_actors: Current participant IDs.
            event_type: Current event type.
            max_results: Maximum number of relevant arcs to return.
            current_tick: Current simulation tick.
            
        Returns:
            List of relevant arc memory dicts, sorted by relevance.
        """
        self._stats["queries_made"] += 1
        
        # Apply decay to all arcs
        self._decay_all_arcs(current_tick)
        
        # Tier 14 Fix: Filter memories by contextual relevance
        # Build context for relevance scoring
        context_tags = set()
        if event_type:
            context_tags.add(event_type)
        if current_actors:
            context_tags.update(current_actors)
        
        current_context = {
            "tags": context_tags,
            "current_tick": current_tick,
        }
        
        memories = filter_memories_by_relevance(
            self._arcs,
            current_context
        )
        
        scored_arcs = []
        for arc in self._arcs:
            score = 0.0
            
            # Actor overlap
            if current_actors:
                actor_overlap = len(set(current_actors) & set(arc.participants))
                actor_score = actor_overlap / max(len(current_actors), 1)
                score += actor_score * 0.5
            
            # Event type match
            if event_type and arc.arc_type == event_type:
                score += 0.3
            
            # High impact arcs are more relevant
            score += arc.impact * 0.2
            
            # Adjust by current relevance (decayed)
            score *= arc.relevance
            
            if score >= SIMILARITY_THRESHOLD:
                scored_arcs.append((score, arc.to_dict()))
        
        # Sort by score and return top results
        scored_arcs.sort(key=lambda x: x[0], reverse=True)
        return [arc for _, arc in scored_arcs[:max_results]]
    
    def get_reputation_history(
        self,
        character_id: str,
        max_results: int = 10,
    ) -> List[Dict[str, Any]]:
        """Get the behavioral history of a character.
        
        This shows how the character has acted in past arcs,
        informing predictions about future behavior.
        
        Args:
            character_id: Character to look up.
            max_results: Maximum number of entries to return.
            
        Returns:
            List of reputation entries, most recent first.
        """
        history = self._reputation_history.get(character_id, [])
        return list(reversed(history[-max_results:]))
    
    def get_emotional_resonance(
        self,
        character_ids: Optional[List[str]] = None,
        current_tick: int = 0,
    ) -> Dict[str, float]:
        """Get current emotional residue for characters.
        
        Returns the combined emotional state lingering from past events.
        This captures how characters still feel about past arcs.
        
        Args:
            character_ids: Characters to query (all if None).
            current_tick: Current simulation tick (for decay).
            
        Returns:
            Dict of emotion_type -> combined intensity.
        """
        resonance: Dict[str, float] = {}
        
        ids_to_query = character_ids or list(self._emotional_residue.keys())
        
        for char_id in ids_to_query:
            residues = self._emotional_residue.get(char_id, [])
            for residue in residues:
                current_intensity = residue.decay(current_tick)
                if current_intensity > 0.05:
                    emotion = residue.emotion_type
                    resonance[emotion] = resonance.get(emotion, 0.0) + current_intensity
        
        return resonance
    
    def _decay_all_arcs(self, current_tick: int) -> None:
        """Apply decay to all arc memories.
        
        Args:
            current_tick: Current simulation tick.
        """
        decayed = 0
        for arc in self._arcs:
            ticks_since = current_tick - arc.tick_resolved
            if ticks_since > 0:
                old_relevance = arc.relevance
                arc.decay(ticks_since)
                if arc.relevance < old_relevance:
                    decayed += 1
        
        self._stats["arcs_decayed"] = decayed
    
    def _find_arc_by_id(self, arc_id: str) -> Optional[ArcMemory]:
        """Find an arc by its ID.
        
        Args:
            arc_id: Arc identifier.
            
        Returns:
            ArcMemory if found, None otherwise.
        """
        for arc in self._arcs:
            if arc.arc_id == arc_id:
                return arc
        return None
    
    def get_stats(self) -> Dict[str, Any]:
        """Get narrative memory statistics.
        
        Returns:
            Stats dict.
        """
        return {
            **self._stats,
            "stored_arcs": len(self._arcs),
            "characters_with_residue": len(self._emotional_residue),
            "characters_with_reputation": len(self._reputation_history),
        }
    
    def clear(
        self,
        clear_residue: bool = True,
        clear_reputation: bool = True,
    ) -> None:
        """Clear narrative memory.
        
        Args:
            clear_residue: Also clear emotional residue.
            clear_reputation: Also clear reputation history.
        """
        self._arcs.clear()
        if clear_residue:
            self._emotional_residue.clear()
        if clear_reputation:
            self._reputation_history.clear()
        self._stats = {
            "arcs_stored": 0,
            "arcs_decayed": 0,
            "arcs_purged": 0,
            "queries_made": 0,
        }