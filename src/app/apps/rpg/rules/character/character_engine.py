"""Character Engine (Beliefs + Goals) — TIER 9: Narrative Intelligence Layer.

This module implements the Character Engine from Tier 9 of the RPG design specification.

Purpose:
    Track individual characters with beliefs, goals, and memory. Characters
    react to world events and evolve their beliefs based on experiences.

The Problem:
    - Characters are static data containers
    - No belief system or goal-driven behavior
    - Characters don't remember past events
    - No personal growth or trauma from events

The Solution:
    CharacterEngine manages Character objects that have:
    - Dynamic beliefs about factions, players, and the world
    - Personal goals that drive behavior
    - Memory of significant events
    - Belief updates triggered by world events

Usage:
    engine = CharacterEngine()
    char = engine.get_or_create("mage_leader", "Archmage Aldric")
    char.add_belief("mages_guild", 0.8)
    char.add_goal("protect the library")
    
    engine.update_from_events(world_events)

Architecture:
    Character Model (beliefs, goals, memory)
         ↓
    Event Processing (belief updates from events)
         ↓
    Goal Generation (events create new goals)
         ↓
    Memory Storage (significant events stored)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Character:
    """An individual character with beliefs, goals, and memory.
    
    Characters are distinct from NPCs — they are narrative actors with
    personal beliefs that drive their behavior and reactions to events.
    
    Attributes:
        id: Unique character identifier.
        name: Human-readable character name.
        beliefs: Dict of entity_id → opinion (-1.0 to 1.0).
                 Entities can be factions, other characters, concepts.
        goals: List of current goals the character pursues.
        memory: List of significant events this character remembers.
        traits: Personality trait tags.
        role: Character's role/status in the world.
    """
    
    id: str
    name: str
    beliefs: Dict[str, float] = field(default_factory=dict)
    goals: List[str] = field(default_factory=list)
    memory: List[Dict[str, Any]] = field(default_factory=list)
    traits: List[str] = field(default_factory=list)
    role: str = "unknown"
    
    def add_belief(self, entity_id: str, value: float) -> None:
        """Set belief about an entity.
        
        Args:
            entity_id: Entity identifier (faction, person, concept).
            value: Belief value (-1.0 hostile to 1.0 supportive).
        """
        self.beliefs[entity_id] = max(-1.0, min(1.0, value))
    
    def adjust_belief(self, entity_id: str, delta: float) -> float:
        """Adjust belief by delta.
        
        Args:
            entity_id: Entity identifier.
            delta: Change amount.
            
        Returns:
            New belief value.
        """
        current = self.beliefs.get(entity_id, 0.0)
        new_value = max(-1.0, min(1.0, current + delta))
        self.beliefs[entity_id] = new_value
        return new_value
    
    def get_belief(self, entity_id: str) -> float:
        """Get belief about an entity.
        
        Args:
            entity_id: Entity identifier.
            
        Returns:
            Belief value, 0.0 if unknown.
        """
        return self.beliefs.get(entity_id, 0.0)
    
    def add_goal(self, goal: str) -> None:
        """Add a goal if not already present.
        
        Args:
            goal: Goal description.
        """
        if goal not in self.goals:
            self.goals.append(goal)
    
    def add_memory(self, event: Dict[str, Any]) -> None:
        """Add an event to memory.
        
        Args:
            event: Event dict to remember.
        """
        self.memory.append(event)
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict.
        
        Returns:
            Character data as dictionary.
        """
        return {
            "id": self.id,
            "name": self.name,
            "beliefs": dict(self.beliefs),
            "goals": list(self.goals),
            "memory_count": len(self.memory),
            "traits": list(self.traits),
            "role": self.role,
        }


class CharacterEngine:
    """Manages characters with beliefs, goals, and memory.
    
    The CharacterEngine tracks individual characters and updates their
    beliefs and goals based on world events. When significant events
    occur, characters form new goals and adjust their worldviews.
    
    Integration Points:
        - PlayerLoop.step(): update_from_events called each tick
        - Scene Engine: Characters are participants in scenes
        - Narrative Memory: Character memories complement global memory
        - Dialogue Engine: Beliefs drive dialogue responses
    
    Usage:
        engine = CharacterEngine()
        
        # Get or create characters
        leader = engine.get_or_create("mage_leader", "Archmage Aldric")
        leader.add_belief("mages_guild", 0.8)
        leader.add_goal("protect the library")
        
        # Update from world events
        engine.update_from_events(world_events)
    """
    
    def __init__(self, max_memory_per_char: int = 50):
        """Initialize the CharacterEngine.
        
        Args:
            max_memory_per_char: Maximum memories per character before pruning.
        """
        self.characters: Dict[str, Character] = {}
        self.max_memory = max_memory_per_char
    
    def get_or_create(self, char_id: str, name: Optional[str] = None) -> Character:
        """Get existing character or create a new one.
        
        Args:
            char_id: Character identifier.
            name: Character name (defaults to char_id).
            
        Returns:
            Character object.
        """
        if char_id not in self.characters:
            self.characters[char_id] = Character(
                id=char_id,
                name=name or char_id,
            )
        return self.characters[char_id]
    
    def get_character(self, char_id: str) -> Optional[Character]:
        """Get a character by ID.
        
        Args:
            char_id: Character identifier.
            
        Returns:
            Character object, or None if not found.
        """
        return self.characters.get(char_id)
    
    def update_from_events(self, events: List[Dict[str, Any]]) -> None:
        """Update character beliefs and goals from world events.
        
        Processes each event and updates affected characters' beliefs,
        goals, and memories accordingly.
        
        Args:
            events: List of world event dicts.
        """
        for event in events:
            event_type = event.get("type", "unknown")
            
            if event_type == "coup":
                self._handle_coup(event)
            elif event_type == "faction_conflict":
                self._handle_conflict(event)
            elif event_type == "faction_alliance":
                self._handle_alliance(event)
            elif event_type == "shortage":
                self._handle_shortage(event)
            elif event_type == "player_action":
                self._handle_player_action(event)
            
            # All events are stored as memories for involved characters
            self._store_memories(event)
    
    def _handle_coup(self, event: Dict[str, Any]) -> None:
        """Process coup event for character updates.
        
        Args:
            event: Coup event dict.
        """
        old_leader = self.get_or_create(event.get("old_leader", "unknown"))
        new_leader = self.get_or_create(event.get("new_leader", "unknown"))
        faction = event.get("faction", "unknown")
        
        # Old leader loses power belief
        old_leader.adjust_belief("power", -1.0)
        old_leader.add_goal(f"Regain control of {faction}")
        old_leader.adjust_belief(new_leader.id, -0.8)
        
        # New leader gains power belief
        new_leader.adjust_belief("power", 1.0)
        new_leader.add_goal(f"Consolidate power in {faction}")
        
    def _handle_conflict(self, event: Dict[str, Any]) -> None:
        """Process faction conflict event.
        
        Args:
            event: Faction conflict event dict.
        """
        factions = event.get("factions", [])
        
        for faction_id in factions:
            # Get characters associated with this faction
            for char in self.characters.values():
                if char.get_belief(faction_id) > 0.5:
                    # Faction sympathizers become concerned
                    char.adjust_belief(faction_id, -0.1)  # Worry about faction
                    other_factions = [f for f in factions if f != faction_id]
                    for other in other_factions:
                        char.adjust_belief(other, -0.15)  # Hostility to enemies
    
    def _handle_alliance(self, event: Dict[str, Any]) -> None:
        """Process faction alliance event.
        
        Args:
            event: Alliance event dict.
        """
        factions = event.get("factions", [])
        
        for faction_id in factions:
            for char in self.characters.values():
                if char.get_belief(faction_id) > 0.3:
                    # Alliance improves opinion of other faction
                    for other in factions:
                        if other != faction_id:
                            current = char.get_belief(other)
                            char.adjust_belief(other, 0.1)
    
    def _handle_shortage(self, event: Dict[str, Any]) -> None:
        """Process shortage/crisis event.
        
        Args:
            event: Shortage event dict.
        """
        location = event.get("location", "unknown")
        
        # Characters associated with the location develop urgency
        for char in self.characters.values():
            if char.get_belief(location) > 0.3:
                good = event.get("good", "supplies")
                char.add_goal(f"Help {location} get {good}")
    
    def _handle_player_action(self, event: Dict[str, Any]) -> None:
        """Process player action event.
        
        Args:
            event: Player action event dict.
        """
        description = event.get("description", "")
        actors = event.get("actors", [])
        
        # Characters who witness this action form beliefs about player
        for actor_id in actors:
            if actor_id != "player":
                char = self.get_or_create(actor_id)
                # Simple sentiment: positive actions improve belief
                positive_words = ["help", "save", "protect", "give", "heal"]
                negative_words = ["attack", "kill", "steal", "destroy", "hurt"]
                
                desc_lower = description.lower()
                if any(w in desc_lower for w in positive_words):
                    char.adjust_belief("player", 0.1)
                elif any(w in desc_lower for w in negative_words):
                    char.adjust_belief("player", -0.1)
    
    def _store_memories(self, event: Dict[str, Any]) -> None:
        """Store event as memory for relevant characters.
        
        Args:
            event: Event dict.
        """
        importance = event.get("importance", 0.5)
        
        # Extract character-relevant entities from event
        entities = self._extract_entities(event)
        
        for entity_id in entities:
            char = self.get_or_create(entity_id)
            
            # Prune old memories if at capacity
            if len(char.memory) >= self.max_memory:
                char.memory = char.memory[-self.max_memory // 2:]
            
            char.add_memory({
                "type": event.get("type", "unknown"),
                "description": event.get("description", ""),
                "importance": importance,
            })
    
    def _extract_entities(self, event: Dict[str, Any]) -> List[str]:
        """Extract character-relevant entities from event.
        
        Args:
            event: Event dict.
            
        Returns:
            List of entity IDs involved in the event.
        """
        entities: List[str] = []
        
        # Check common fields
        for key in ["faction", "old_leader", "new_leader", "location"]:
            value = event.get(key)
            if value and isinstance(value, str):
                entities.append(value)
        
        # Check factions list
        for faction in event.get("factions", []):
            if isinstance(faction, str):
                entities.append(faction)
        
        # Check actors
        for actor in event.get("actors", []):
            if isinstance(actor, str) and actor != "player":
                entities.append(actor)
        
        return list(set(entities))  # Deduplicate
    
    def reset(self) -> None:
        """Clear all character data."""
        self.characters.clear()