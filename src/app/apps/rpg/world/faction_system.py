"""Faction Simulation System — TIER 7: Faction Simulation + Reputation Economy.

This module implements PART 1 of Tier 7 from the RPG design specification:
the Faction Simulation Engine.

Purpose:
    Turn the system from "story reacting to player" into "world evolving
    with or without the player." Factions fight, form alliances, shift
    power balances, and create emergent story arcs independent of player action.

The Problem:
    - World is static backdrop until player interacts
    - NPCs don't have their own agenda
    - No emergent conflicts or alliances
    - Story feels scripted, not alive

The Solution:
    FactionSystem simulates faction resources, morale, relations, and
    territorial influence over time. When relations drop below threshold,
    conflicts emerge. These conflicts feed into the Plot Engine as
    emergent story arcs.

Usage:
    fs = FactionSystem()
    fs.add_faction(Faction("mages_guild", "Mages Guild"))
    fs.add_faction("warriors_hall", "Warriors Hall")
    fs.factions["mages_guild"].relations["warriors_hall"] = -0.8
    
    events = fs.update()  # Advances simulation, returns emergent events

Architecture:
    Faction State (power, resources, morale, relations, influence)
         ↓
    Resource Update (power-driven resource growth)
         ↓
    Morale Update (resource-driven morale shifts)
         ↓
    Conflict Detection (negative relations → conflict events)
         ↓
    Plot Engine (conflicts become war arcs)

Key Features:
    - Faction power/resources/morale simulation
    - Inter-faction relationship tracking
    - Automatic conflict detection when relations < -0.6
    - Territorial influence mapping
    - Event generation for plot engine consumption
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class Faction:
    """A political/military/social organization in the world.
    
    Factions are autonomous entities that compete for power, resources,
    and territory. Their relationships with each other drive emergent
    conflicts and story arcs.
    
    Attributes:
        id: Unique faction identifier.
        name: Human-readable faction name.
        power: Military/political strength (0.0 to 1.0).
        resources: Wealth/supplies (0.0 to 1.0).
        morale: Member loyalty/spirits (0.0 to 1.0).
        relations: Dict of faction_id → relationship (-1.0 to 1.0).
                   Negative = hostile, positive = friendly.
        goals: Strategic objectives the faction pursues.
        traits: Personality tags (e.g., "aggressive", "diplomatic").
        influence: Dict of location_id → control level (0.0 to 1.0).
    """
    
    id: str
    name: str
    
    # State
    power: float = 0.5
    resources: float = 0.5
    morale: float = 0.5
    
    # Relationships
    relations: Dict[str, float] = field(default_factory=dict)
    
    # Behavior
    goals: List[str] = field(default_factory=list)
    traits: List[str] = field(default_factory=list)
    
    # Territory / influence
    influence: Dict[str, float] = field(default_factory=dict)
    
    def get_relation(self, faction_id: str) -> float:
        """Get relationship with another faction.
        
        Args:
            faction_id: Target faction ID.
            
        Returns:
            Relationship value, 0.0 if unknown.
        """
        return self.relations.get(faction_id, 0.0)
    
    def adjust_relation(self, faction_id: str, delta: float) -> float:
        """Adjust relationship by delta.
        
        Args:
            faction_id: Target faction ID.
            delta: Change amount (positive = friendlier).
            
        Returns:
            New relationship value.
        """
        current = self.get_relation(faction_id)
        new_value = max(-1.0, min(1.0, current + delta))
        self.relations[faction_id] = new_value
        return new_value
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict.
        
        Returns:
            Faction data as dictionary.
        """
        return {
            "id": self.id,
            "name": self.name,
            "power": self.power,
            "resources": self.resources,
            "morale": self.morale,
            "relations": dict(self.relations),
            "goals": list(self.goals),
            "traits": list(self.traits),
            "influence": dict(self.influence),
        }


# Conflict detection threshold: relations below this trigger conflicts
CONFLICT_THRESHOLD = -0.6

# Resource growth rate per tick (scaled by power)
RESOURCE_GROWTH_RATE = 0.01

# Morale adjustment rate (scaled by resource surplus/deficit)
MORALE_ADJUSTMENT_RATE = 0.05


class FactionSystem:
    """Simulates faction dynamics: resources, morale, conflicts, alliances.
    
    The FactionSystem runs independently of player action, creating an
    evolving world where factions compete for power and territory.
    When hostilities exceed thresholds, conflict events are generated
    that can feed into the Plot Engine as emergent story arcs.
    
    Integration Points:
        - PlayerLoop.step(): Faction system tick generates world events
        - Plot Engine: Conflict events become war/arcs
        - Reputation Engine: Player actions affect faction relations
        - NPC Behavior: Faction affiliation drives NPC loyalty/hostility
    
    Usage:
        fs = FactionSystem()
        
        # Setup factions
        mages = Faction("mages_guild", "Mages Guild")
        warriors = Faction("warriors_hall", "Warriors Hall")
        mages.relations["warriors_hall"] = -0.8
        
        fs.add_faction(mages)
        fs.add_faction(warriors)
        
        # Each tick
        events = fs.update()
        for event in events:
            if event["type"] == "faction_conflict":
                plot_engine.start_war_arc(event["factions"])
    """
    
    def __init__(self):
        """Initialize the FactionSystem."""
        self.factions: Dict[str, Faction] = {}
        
    def add_faction(self, faction: Faction) -> None:
        """Register a faction in the simulation.
        
        Args:
            faction: The Faction to add.
        """
        self.factions[faction.id] = faction
        
    def update(self) -> List[Dict[str, Any]]:
        """Advance the faction simulation by one tick.
        
        Processes all factions in order:
        1. Update resources (power-driven growth)
        2. Update morale (resource-driven shifts)
        3. Detect conflicts (negative relations)
        4. Detect alliances (positive relations)
        
        Returns:
            List of emergent events for plot engine consumption.
            Event types include:
            - "faction_conflict": Hostile factions clashing
            - "faction_alliance": Friendly factions cooperating
            - "faction_power_shift": Significant power change
            - "faction_territory_gain": Influence expansion
        """
        events: List[Dict[str, Any]] = []
        
        # Phase 1: Update individual faction state
        for faction in self.factions.values():
            self._update_resources(faction)
            self._update_morale(faction)
            self._update_power(faction)
        
        # Phase 2: Detect inter-faction events
        events.extend(self._resolve_conflicts())
        events.extend(self._detect_alliances())
        events.extend(self._detect_power_shifts())
        
        return events
    
    def _update_resources(self, faction: Faction) -> None:
        """Update faction resources based on power.
        
        More powerful factions generate more resources per tick.
        
        Args:
            faction: Faction to update.
        """
        growth = RESOURCE_GROWTH_RATE * faction.power
        faction.resources = min(1.0, faction.resources + growth)
    
    def _update_morale(self, faction: Faction) -> None:
        """Update faction morale based on resource levels.
        
        Resource-rich factions have higher morale. Starving factions
        lose morale quickly.
        
        Args:
            faction: Faction to update.
        """
        # Resource surplus/deficit from neutral (0.5)
        resource_delta = faction.resources - 0.5
        faction.morale += resource_delta * MORALE_ADJUSTMENT_RATE
        faction.morale = max(0.0, min(1.0, faction.morale))
    
    def _update_power(self, faction: Faction) -> None:
        """Update faction power based on resources and morale.
        
        Power grows with high resources and morale, decays otherwise.
        
        Args:
            faction: Faction to update.
        """
        # Power factor: average of resources and morale
        power_factor = (faction.resources + faction.morale) / 2.0
        
        if power_factor > 0.5:
            # Growing faction
            faction.power = min(1.0, faction.power + 0.005)
        elif power_factor < 0.3:
            # Declining faction
            faction.power = max(0.0, faction.power - 0.01)
    
    def _resolve_conflicts(self) -> List[Dict[str, Any]]:
        """Detect and resolve conflicts between hostile factions.
        
        Conflicts occur when relations between two factions drop
        below the CONFLICT_THRESHOLD.
        
        Returns:
            List of conflict events.
        """
        events: List[Dict[str, Any]] = []
        processed_pairs: set = set()
        
        for faction_a in self.factions.values():
            for faction_b_id, relation in faction_a.relations.items():
                # Avoid duplicate pairs (A,B) and (B,A)
                pair_key = tuple(sorted([faction_a.id, faction_b_id]))
                if pair_key in processed_pairs:
                    continue
                processed_pairs.add(pair_key)
                
                # Check if relations are hostile enough for conflict
                if relation < CONFLICT_THRESHOLD:
                    # Conflict importance scales with hostility
                    hostility = abs(relation)
                    importance = 0.5 + (hostility - abs(CONFLICT_THRESHOLD)) / (1.0 - abs(CONFLICT_THRESHOLD)) * 0.5
                    
                    # Power influences conflict outcome probability
                    faction_b = self.factions.get(faction_b_id)
                    if faction_b:
                        power_ratio = faction_a.power / max(0.1, faction_a.power + faction_b.power)
                    else:
                        power_ratio = 0.5
                    
                    events.append({
                        "type": "faction_conflict",
                        "factions": [faction_a.id, faction_b_id],
                        "importance": importance,
                        "power_ratio": power_ratio,
                        "description": f"Conflict between {faction_a.name} and {self.factions.get(faction_b_id, Faction(faction_b_id, faction_b_id)).name}",
                    })
                    
        return events
    
    def _detect_alliances(self) -> List[Dict[str, Any]]:
        """Detect positive relationships that could form alliances.
        
        Alliances form when relations exceed 0.6.
        
        Returns:
            List of alliance events.
        """
        events: List[Dict[str, Any]] = []
        processed_pairs: set = set()
        
        for faction_a in self.factions.values():
            for faction_b_id, relation in faction_a.relations.items():
                pair_key = tuple(sorted([faction_a.id, faction_b_id]))
                if pair_key in processed_pairs:
                    continue
                processed_pairs.add(pair_key)
                
                if relation > 0.6:
                    faction_b = self.factions.get(faction_b_id)
                    if faction_b:
                        events.append({
                            "type": "faction_alliance",
                            "factions": [faction_a.id, faction_b_id],
                            "importance": relation,
                            "description": f"Alliance between {faction_a.name} and {faction_b.name}",
                        })
                        
        return events
    
    def _detect_power_shifts(self) -> List[Dict[str, Any]]:
        """Detect significant power changes in factions.
        
        Power shifts occur when a faction's power crosses major thresholds.
        
        Returns:
            List of power shift events.
        """
        events: List[Dict[str, Any]] = []
        
        for faction in self.factions.values():
            # Rising power
            if faction.power > 0.8:
                events.append({
                    "type": "faction_rising",
                    "faction": faction.id,
                    "importance": faction.power - 0.8,
                    "description": f"{faction.name} is becoming a dominant power",
                })
            # Falling power
            elif faction.power < 0.2:
                events.append({
                    "type": "faction_declining",
                    "faction": faction.id,
                    "importance": 0.2 - faction.power,
                    "description": f"{faction.name} is losing influence",
                })
                
        return events
    
    def get_summary(self) -> Dict[str, Any]:
        """Get summary of all faction states.
        
        Returns:
            Dict with faction summaries.
        """
        return {
            faction_id: faction.to_dict()
            for faction_id, faction in self.factions.items()
        }
    
    def reset(self) -> None:
        """Clear all faction data."""
        self.factions.clear()