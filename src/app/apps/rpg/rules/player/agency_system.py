"""Player Agency System — Long-term consequence tracking.

This module implements PART 2 of Tier 6 (Narrative Intelligence Systems)
from the RPG design specification: the Agency System.

Purpose:
    Track player choices and their long-term consequences so the world
    remembers what the player did. This transforms moment-to-moment gameplay
    into meaningful narrative arcs where actions have weight.

The Problem:
    - Player acts, world reacts immediately
    - But consequences are short-lived
    - No visible branching or remembered impact
    - Player feels like choices don't truly matter

The Solution:
    AgencySystem records every meaningful choice, extracts consequences,
    stores them as persistent world flags, and provides query interfaces
    for other systems (NPC behavior, plot engine, etc.) to react.

Usage:
    agency = AgencySystem()
    result = agency.record("killed_guard", {"guards_hostile": True})
    
    # Later in NPC behavior:
    if world.agency.get_flag("guards_hostile"):
        npc.hostility += 0.5

Architecture:
    Player Action → AgencySystem.record() → 
        Extract consequences → Store flags → 
        World systems read flags for behavior
    
Key Features:
    - Choice history tracking
    - World flag management
    - Effect extraction from outcomes
    - Flag query interface for NPC/plot systems
    - Branch tracking
    - Consequence chaining
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set


@dataclass
class PlayerChoice:
    """Record of a player action and its consequences.
    
    Each PlayerChoice captures a single player decision, what the
    world state was before, what changed after, and what consequences
    are expected to persist long-term.
    
    Attributes:
        id: Unique identifier for this choice.
        action: The action the player took.
        context: World state context at time of action.
        effects: Dict of key-value changes applied to world.
        consequences: Long-term consequence descriptions.
        timestamp: Simulation tick when choice was made.
        weight: Choice significance (0.0-1.0, higher = more memorable).
        arc_id: Related story arc, if any.
    """
    
    id: str
    action: str
    context: Dict[str, Any] = field(default_factory=dict)
    effects: Dict[str, Any] = field(default_factory=dict)
    consequences: List[str] = field(default_factory=list)
    timestamp: int = 0
    weight: float = 0.5
    arc_id: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict.
        
        Returns:
            Choice data as dictionary.
        """
        return {
            "id": self.id,
            "action": self.action,
            "context": self.context,
            "effects": self.effects,
            "consequences": self.consequences,
            "timestamp": self.timestamp,
            "weight": self.weight,
            "arc_id": self.arc_id,
        }


class AgencySystem:
    """Tracks player choices and their long-term consequences.
    
    The AgencySystem is the core of player agency in the RPG. It records
    every meaningful player decision, extracts the consequences, and stores
    them as persistent world flags that other systems can read.
    
    This transforms the game from "cool moments" to "a story where your
    choices actually mattered."
    
    Integration Points:
        - PlayerLoop: Records each player action result
        - NPC Behavior: Reads flags to modify NPC attitudes
        - Plot Engine: Reads flags to advance arcs
        - World State: Reads flags to change world conditions
    
    Usage:
        agency = AgencySystem()
        
        # In PlayerLoop.step():
        result = self._execute_player_action(player_input)
        self.agency.record(player_input, result)
        
        # In NPC behavior logic:
        if world.agency.killed_villager:
            npc.hostility += 0.5
            
        # In world logic:
        if world.agency.get_flag("burned_bridge"):
            world.paths["north"] = "blocked"
    """
    
    def __init__(self, max_history: int = 500):
        """Initialize the AgencySystem.
        
        Args:
            max_history: Maximum number of choices to keep in history.
                         Older choices are pruned to limit memory usage.
        """
        self.history: List[PlayerChoice] = []
        self.flags: Dict[str, Any] = {}
        self._choice_counter = 0
        self._max_history = max_history
        
        # Track important categories
        self._killed_entities: Set[str] = set()
        self._betrayed_entities: Set[str] = set()
        self._ally_entities: Set[str] = set()
        self._visited_locations: Set[str] = set()
        
    def record(self, action: str, result: Dict[str, Any], 
               context: Optional[Dict[str, Any]] = None,
               timestamp: int = 0) -> PlayerChoice:
        """Record a player action and extract its consequences.
        
        This is the main entry point. Takes the player action and its
        result, then extracts long-term effects as flags.
        
        Args:
            action: Player action description.
            result: Result dict from world.simulate() with 'effects' key.
            context: Optional world state context at time of action.
            timestamp: Current simulation tick.
            
        Returns:
            The recorded PlayerChoice.
        """
        self._choice_counter += 1
        
        # Extract effects from result
        effects = result.get("effects", {})
        consequences = result.get("consequences", [])
        weight = result.get("weight", 0.5)
        arc_id = result.get("arc_id")
        
        # Create choice record
        choice = PlayerChoice(
            id=f"choice_{self._choice_counter}",
            action=action,
            context=context or {},
            effects=effects,
            consequences=consequences if isinstance(consequences, list) else [consequences] if consequences else [],
            timestamp=timestamp,
            weight=weight,
            arc_id=arc_id,
        )
        
        # Apply effects to flags
        self._apply_effects(effects)
        
        # Track categories
        self._track_categories(choice)
        
        # Store in history
        self.history.append(choice)
        
        # Prune old history
        if len(self.history) > self._max_history:
            self.history = self.history[-self._max_history:]
            
        return choice
    
    def _apply_effects(self, effects: Dict[str, Any]) -> None:
        """Apply effects from a choice to the persistent flags.
        
        Args:
            effects: Dict of effect key-value pairs.
        """
        if not isinstance(effects, dict):
            return
            
        for key, value in effects.items():
            # For boolean flags
            if isinstance(value, bool):
                self.flags[key] = value
            # For numeric flags, accumulate or overwrite
            elif isinstance(value, (int, float)):
                if key in self.flags:
                    old_value = self.flags[key]
                    if isinstance(old_value, (int, float)):
                        # Accumulate numeric effects
                        self.flags[key] = old_value + value
                    else:
                        self.flags[key] = value
                else:
                    self.flags[key] = value
            # For string/other types, overwrite
            else:
                self.flags[key] = value
                
    def _track_categories(self, choice: PlayerChoice) -> None:
        """Track special categories from a choice for quick lookup.
        
        Args:
            choice: The PlayerChoice to categorize.
        """
        effects = choice.effects
        action_lower = choice.action.lower()
        
        # Track killed entities
        if "killed" in action_lower or "death" in action_lower:
            for field_name in ["target", "entity", "victim"]:
                target = effects.get(field_name)
                if target:
                    self._killed_entities.add(str(target))
                    
        # Track betrayed entities
        if "betray" in action_lower:
            for field_name in ["target", "faction", "entity"]:
                target = effects.get(field_name)
                if target:
                    self._betrayed_entities.add(str(target))
                    
        # Track allies
        if "ally" in action_lower or "recruit" in action_lower or "save" in action_lower:
            for field_name in ["target", "entity", "ally"]:
                target = effects.get(field_name)
                if target:
                    self._ally_entities.add(str(target))
                    
        # Track visited locations
        for field_name in ["location", "place", "area"]:
            loc = effects.get(field_name)
            if loc:
                self._visited_locations.add(str(loc))
                
    def get_flag(self, key: str, default: Any = None) -> Any:
        """Get a specific world flag.
        
        Args:
            key: Flag key.
            default: Default if flag doesn't exist.
            
        Returns:
            Flag value or default.
        """
        return self.flags.get(key, default)
    
    @property
    def killed_entities(self) -> Set[str]:
        """Get set of entities the player has killed.
        
        Returns:
            Set of entity IDs.
        """
        return self._killed_entities.copy()
    
    @property
    def betrayed_entities(self) -> Set[str]:
        """Get set of entities the player has betrayed.
        
        Returns:
            Set of entity IDs.
        """
        return self._betrayed_entities.copy()
    
    @property
    def ally_entities(self) -> Set[str]:
        """Get set of entities the player has befriended.
        
        Returns:
            Set of entity IDs.
        """
        return self._ally_entities.copy()
    
    @property
    def visited_locations(self) -> Set[str]:
        """Get set of locations the player has visited.
        
        Returns:
            Set of location IDs.
        """
        return self._visited_locations.copy()
    
    def get_summary(self) -> Dict[str, Any]:
        """Get summary of agency state for Director prompt.
        
        Returns:
            Dict with agency summary.
        """
        return {
            "total_choices": len(self.history),
            "flags": dict(self.flags),
            "killed": list(self._killed_entities),
            "betrayed": list(self._betrayed_entities),
            "allies": list(self._ally_entities),
            "key_flags": {
                k: v for k, v in self.flags.items()
                if isinstance(v, bool) and v
            },
        }
    
    def reset(self) -> None:
        """Clear all agency data."""
        self.history.clear()
        self.flags.clear()
        self._choice_counter = 0
        self._killed_entities.clear()
        self._betrayed_entities.clear()
        self._ally_entities.clear()
        self._visited_locations.clear()