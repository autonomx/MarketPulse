"""Political System - TIER 8: World Complexity Layer.

This module implements PART 3 of Tier 8 from the RPG design specification:
the Political System.

Purpose:
    Simulate faction leadership dynamics, instability, and coups. Leaders
    affect faction behavior, and political upheaval creates story
    opportunities that feel unscripted.

The Problem:
    - Factions have no leadership or decision-making
    - No political instability or change mechanism
    - Faction behavior is static regardless of circumstances
    - No coup or rebellion mechanics

The Solution:
    PoliticalSystem tracks faction leaders, monitors faction stability
    (based on morale and resources), and triggers leadership changes
    (coups) when instability exceeds threshold. Leader changes affect
    faction goals and relations.

Architecture:
    Leader:
        - name, traits, faction_id
        
    PoliticalSystem:
        leaders: {faction_id -> Leader}
        
        update(faction_system):
            1. Check instability per faction (1.0 - morale)
            2. If instability > 0.7 → possible coup
            3. Install new leader with different goals
            4. Return political events

Key Features:
    - Leader assignment per faction
    - Instability tracking (1.0 - morale)
    - Coup probability (10% when instability > 0.7)
    - Leader traits affect faction behavior
    - Political event generation for quest generation
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from app.apps.rpg.foundation.core.determinism import rng_for_current_turn, stable_sub_index

# Leader trait templates that affect faction behavior
LEADER_TRAITS = {
    "aggressive": {
        "relation_modifier": -0.1,  # Worsens relations with others
        "power_modifier": 0.05,     # Gains power faster
    },
    "diplomatic": {
        "relation_modifier": 0.1,   # Improves relations
        "power_modifier": 0.0,      # Normal power growth
    },
    "ambitious": {
        "relation_modifier": 0.0,   # Neutral initially
        "power_modifier": 0.1,      # Gains power much faster
    },
    "defensive": {
        "relation_modifier": 0.05,  # Slightly better relations
        "power_modifier": -0.05,    # Slower power growth
    },
    "ruthless": {
        "relation_modifier": -0.15, # Sign worse relations
        "power_modifier": 0.08,     # Faster power growth
    },
}


@dataclass
class Leader:
    """A faction leader with name and behavioral traits.
    
    Leaders affect their faction's diplomatic and military behavior.
    When a coup occurs, the new leader may have different traits,
    causing the faction to act differently.
    
    Attributes:
        name: Leader display name.
        traits: List of behavioral trait names.
    """
    
    name: str
    traits: List[str] = field(default_factory=list)
    
    def get_relation_modifier(self) -> float:
        """Get cumulative relation modifier from all traits.
        
        Returns:
            Sum of all trait relation modifiers.
        """
        modifier = 0.0
        for trait in self.traits:
            trait_data = LEADER_TRAITS.get(trait, {})
            modifier += trait_data.get("relation_modifier", 0.0)
        return modifier
    
    def get_power_modifier(self) -> float:
        """Get cumulative power modifier from all traits.
        
        Returns:
            Sum of all trait power modifiers.
        """
        modifier = 0.0
        for trait in self.traits:
            trait_data = LEADER_TRAITS.get(trait, {})
            modifier += trait_data.get("power_modifier", 0.0)
        return modifier
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary.
        
        Returns:
            Leader data as dict.
        """
        return {
            "name": self.name,
            "traits": list(self.traits),
            "relation_modifier": self.get_relation_modifier(),
            "power_modifier": self.get_power_modifier(),
        }


# Constants for political simulation
INSTABILITY_THRESHOLD = 0.7  # Morale below this triggers coup risk
COUP_PROBABILITY = 0.1       # 10% chance per tick when unstable


class PoliticalSystem:
    """Simulates faction leadership and political stability.
    
    The PoliticalSystem tracks who leads each faction and monitors
    faction stability. When a faction's morale drops too low, the
    leadership becomes vulnerable to coups. New leaders may have
    radically different goals and relations, creating emergent story.
    
    Integration Points:
        - FactionSystem: Leader affects faction behavior modifiers
        - DynamicQuestGenerator: Coups generate rebellion quests
        - PlayerLoop: Political events added to world event stream
        - Plot Engine: Leadership changes drive story arcs
    
    Usage:
        politics = PoliticalSystem()
        
        # Assign initial leaders
        factions.add_faction(Faction("mages_guild", "Mages Guild"))
        politics.set_leader("mages_guild", Leader("Archmage Elara", ["diplomatic"]))
        
        # Each tick - check for coups
        events = politics.update(faction_system)
        for event in events:
            if event["type"] == "coup":
                # New leadership in place - story opportunity
                pass
    """
    
    def __init__(self, random_module: Any = None):
        """Initialize the PoliticalSystem.
        
        Args:
            random_module: Optional random module replacement for testing.
                          If None, decisions use the active seeded RPG turn.
        """
        self.leaders: Dict[str, Leader] = {}
        self._random = random_module

    def _rng(self, purpose: str, subject: str):
        if self._random is not None:
            return self._random
        return rng_for_current_turn(purpose, stable_sub_index(subject))
        
    def set_leader(self, faction_id: str, leader: Leader) -> None:
        """Assign a leader to a faction.
        
        Args:
            faction_id: Faction identifier.
            leader: Leader object to assign.
        """
        self.leaders[faction_id] = leader
        
    def update(self, faction_system: Any) -> List[Dict[str, Any]]:
        """Check for political upheaval in all factions.
        
        Examines each faction's morale and determines if a coup
        is likely. Unstable factions have a chance to replace
        their leader.
        
        Args:
            faction_system: FactionSystem to check for instability.
            
        Returns:
            List of political events:
            - "coup": Leadership change due to instability
            - "political_shift": Leader trait effects on faction
        """
        events: List[Dict[str, Any]] = []
        
        for faction in faction_system.factions.values():
            instability = 1.0 - faction.morale
            
            if instability > INSTABILITY_THRESHOLD:
                # Unstable faction - possible coup
                if self._rng("world:political_coup", faction.id).random() < COUP_PROBABILITY:
                    events.extend(self._trigger_coup(faction))
        
        return events
    
    def _trigger_coup(self, faction: Any) -> List[Dict[str, Any]]:
        """Trigger a leadership coup in a faction.
        
        Replaces the current leader with a new one that has
        potentially different traits and goals.
        
        Args:
            faction: Faction experiencing the coup.
            
        Returns:
            List containing the coup event dict.
        """
        old_leader = self.leaders.get(faction.id)
        
        # Generate new leader with random traits
        new_traits = self._generate_leader_traits(faction.id)
        new_leader = Leader(
            name=self._generate_leader_name(faction.id),
            traits=new_traits,
        )
        
        # Install new leader
        self.leaders[faction.id] = new_leader
        
        # Apply leader effects to faction immediately
        relation_mod = new_leader.get_relation_modifier()
        for target_id in faction.relations:
            faction.adjust_relation(target_id, relation_mod * 0.5)
        
        return [{
            "type": "coup",
            "faction": faction.id,
            "faction_name": faction.name,
            "old_leader": old_leader.name if old_leader else None,
            "new_leader": new_leader.name,
            "new_leader_traits": new_traits,
            "importance": 0.9,
            "description": f"Revolution in {faction.name}: {new_leader.name} takes power{' from ' + old_leader.name if old_leader else ''}",
        }]
    
    def _generate_leader_traits(self, faction_id: str) -> List[str]:
        """Generate random leader traits.
        
        Returns:
            List of 1-2 trait names.
        """
        rng = self._rng("world:political_leader_traits", faction_id)
        num_traits = rng.randint(1, 2)
        return rng.sample(list(LEADER_TRAITS.keys()), num_traits)
    
    def _generate_leader_name(self, faction_id: str) -> str:
        """Generate a leader name based on faction.
        
        Args:
            faction_id: Faction identifier for name seed.
            
        Returns:
            Generated leader name.
        """
        prefixes = {
            "mages": ["Archmage", "Magister", "Sorcerer"],
            "warriors": ["Commander", "General", "Warlord"],
            "merchants": ["Merchant Prince", "Guildmaster", "Broker"],
            "peasants": ["Elder", "Representative", "Champion"],
        }
        
        # Find matching prefix
        for key, prefix_list in prefixes.items():
            if key in faction_id.lower():
                prefix = self._rng("world:political_leader_title", faction_id).choice(prefix_list)
                break
        else:
            prefix = "Leader"
        
        # Generate unique name
        names = ["Aldric", "Brenna", "Cedric", "Diana", "Edmund",
                 "Freya", "Gareth", "Helena", "Isaac", "Julia",
                 "Klaus", "Lena", "Marcus", "Nora", "Oscar",
                 "Petra", "Roland", "Sigrid", "Theo", "Ursula"]
        name = self._rng("world:political_leader_name", faction_id).choice(names)
        
        return f"{prefix} {name}"
    
    def get_summary(self) -> Dict[str, Dict[str, Any]]:
        """Get summary of all faction leadership.
        
        Returns:
            Dict mapping faction_id to leader data.
        """
        return {
            faction_id: leader.to_dict()
            for faction_id, leader in self.leaders.items()
        }
    
    def reset(self) -> None:
        """Clear all leadership data."""
        self.leaders.clear()
