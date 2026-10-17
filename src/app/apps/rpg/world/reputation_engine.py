"""Reputation Engine — TIER 7: Faction Simulation + Reputation Economy.

This module implements PART 2 of Tier 7 from the RPG design specification:
the Reputation Engine.

Purpose:
    Track the player's standing with each faction and convert actions into
    faction reactions. Reputation drives NPC behavior, plot access, and
    world state changes.

The Problem:
    - Player actions don't affect faction relationships
    - No sense of "standing" with groups
    - Same NPCs treat player the same regardless of history
    - No reputation-based access to content

The Solution:
    ReputationEngine tracks per-faction reputation scores that change
    based on player actions. These scores determine faction attitudes
    (ally/neutral/hostile) which feed into NPC behavior and plot
    availability.

Usage:
    rep = ReputationEngine()
    
    # Player helps a faction
    rep.apply_action("help_mages", {"faction_rep": {"mages_guild": 0.3}})
    
    # Check attitude
    attitude = rep.get_attitude("mages_guild")  # "ally" if > 0.5
    
    # Faction access check
    if rep.get("mages_guild") > 0.6:
        unlock_inner_circle_arc()

Architecture:
    Player Action → Effect Dict (faction_rep changes)
         ↓
    ReputationEngine.apply_action()
         ↓
    Per-faction reputation updated (-1.0 to 1.0)
         ↓
    Attitude Classification (hostile/neutral/ally)
         ↓
    NPC Behavior Modification + Plot Access Control

Key Features:
    - Per-faction reputation tracking
    - Action → reputation mapping via effect dicts
    - Attitude classification (hostile, neutral, friendly, ally)
    - Reputation thresholds for content unlocking
    - Relationship decay over time (optional)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

# Attitude thresholds
ATTITUDE_HOSTILE_THRESHOLD = -0.5
ATTITUDE_FRIENDLY_THRESHOLD = 0.3
ATTITUDE_ALLY_THRESHOLD = 0.6

# Default decay rate per tick (reputation slowly returns to neutral)
DEFAULT_DECAY_RATE = 0.0

# Maximum reputation value
MAX_REPUTATION = 1.0

# Minimum reputation value
MIN_REPUTATION = -1.0


@dataclass
class FactionStanding:
    """Tracks reputation details for a single faction.
    
    Attributes:
        reputation: Current reputation score (-1.0 to 1.0).
        history: List of reputation changes with sources.
        last_change_tick: Tick when reputation last changed.
        locked: If True, reputation cannot be modified (plot-locked).
    """
    
    reputation: float = 0.0
    history: List[Tuple[str, float, int]] = field(default_factory=list)
    last_change_tick: int = -1
    locked: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict.
        
        Returns:
            Standing data as dictionary.
        """
        return {
            "reputation": self.reputation,
            "history_length": len(self.history),
            "last_change_tick": self.last_change_tick,
            "locked": self.locked,
        }


class ReputationEngine:
    """Tracks and manages player reputation with factions.
    
    The ReputationEngine converts player actions into faction reputation
    changes, then provides query interfaces for NPC behavior, plot access,
    and world state modifications.
    
    Integration Points:
        - PlayerLoop.step(): Apply reputation changes from action results
        - NPC Behavior: Read attitude to modify NPC hostility/trust
        - Plot Engine: Check reputation thresholds for arc unlock
        - World State: Modify faction-controlled territories
    
    Usage:
        rep = ReputationEngine()
        
        # In PlayerLoop.step():
        result = self._execute_player_action(player_input)
        self.reputation.apply_action(player_input, result)
        
        # In NPC behavior logic:
        attitude = world.reputation.get_attitude(npc.faction_id)
        if attitude == "hostile":
            npc.hostility += 0.4
        elif attitude == "ally":
            npc.trust += 0.4
            
        # In plot engine:
        if world.reputation.get("mages_guild") > 0.6:
            self._unlock_arc("inner_circle")
    """
    
    def __init__(self, decay_rate: float = DEFAULT_DECAY_RATE):
        """Initialize the ReputationEngine.
        
        Args:
            decay_rate: Reputation decay per tick toward neutral.
                        Set to 0.0 to disable decay.
        """
        self.reputation: Dict[str, FactionStanding] = {}
        self.decay_rate = decay_rate
        
    def apply_action(self, action: str, effects: Dict[str, Any], 
                     tick: int = 0) -> Dict[str, float]:
        """Apply reputation changes from a player action.
        
        Scans the effects dict for "faction_rep" entries and updates
        reputation for each affected faction.
        
        Args:
            action: Description of the player action.
            effects: Effects dict, should contain "faction_rep" key
                     with {faction_id: delta} mappings.
            tick: Current simulation tick for history tracking.
            
        Returns:
            Dict of {faction_id: new_reputation} for changed factions.
        """
        changes: Dict[str, float] = {}
        faction_rep = effects.get("faction_rep", {})
        
        if not isinstance(faction_rep, dict):
            return changes
            
        for faction_id, delta in faction_rep.items():
            if not isinstance(delta, (int, float)):
                continue
                
            standing = self._get_or_create_standing(faction_id)
            
            if standing.locked:
                continue
                
            old_rep = standing.reputation
            standing.reputation = max(MIN_REPUTATION, min(MAX_REPUTATION, 
                                                         old_rep + delta))
            standing.history.append((action, delta, tick))
            standing.last_change_tick = tick
            
            changes[faction_id] = standing.reputation
            
        return changes
    
    def set(self, faction_id: str, value: float) -> None:
        """Directly set reputation for a faction.
        
        Use sparingly - prefer apply_action for player-driven changes.
        
        Args:
            faction_id: Target faction.
            value: New reputation value (-1.0 to 1.0).
        """
        standing = self._get_or_create_standing(faction_id)
        standing.reputation = max(MIN_REPUTATION, min(MAX_REPUTATION, value))
        
    def get(self, faction_id: str) -> float:
        """Get current reputation with a faction.
        
        Args:
            faction_id: Target faction.
            
        Returns:
            Reputation value (-1.0 to 1.0), 0.0 if unknown.
        """
        return self.reputation.get(faction_id, FactionStanding()).reputation
    
    def get_attitude(self, faction_id: str) -> str:
        """Get the faction's attitude toward the player.
        
        Attitudes:
            - "hostile": rep < -0.5 (faction actively opposes player)
            - "unfriendly": -0.5 <= rep < 0 (faction distrusts player)
            - "neutral": 0 <= rep < 0.3 (faction indifferent to player)
            - "friendly": 0.3 <= rep < 0.6 (faction welcomes player)
            - "ally": rep >= 0.6 (faction deeply trusts player)
        
        Args:
            faction_id: Target faction.
            
        Returns:
            Attitude string.
        """
        rep = self.get(faction_id)
        
        if rep < ATTITUDE_HOSTILE_THRESHOLD:
            return "hostile"
        elif rep < 0.0:
            return "unfriendly"
        elif rep < ATTITUDE_FRIENDLY_THRESHOLD:
            return "neutral"
        elif rep < ATTITUDE_ALLY_THRESHOLD:
            return "friendly"
        else:
            return "ally"
    
    def lock(self, faction_id: str) -> None:
        """Lock faction reputation (prevents changes).
        
        Use for plot-locked factions that shouldn't be affected
        by player actions during certain arcs.
        
        Args:
            faction_id: Target faction.
        """
        standing = self._get_or_create_standing(faction_id)
        standing.locked = True
        
    def unlock(self, faction_id: str) -> None:
        """Unlock faction reputation (allows changes).
        
        Args:
            faction_id: Target faction.
        """
        if faction_id in self.reputation:
            self.reputation[faction_id].locked = False
    
    def decay(self, tick: int) -> Dict[str, float]:
        """Apply reputation decay toward neutral.
        
        Higher decay rates make reputation less stable over time.
        Set decay_rate to 0 to disable.
        
        Args:
            tick: Current simulation tick.
            
        Returns:
            Dict of {faction_id: new_reputation} for changed factions.
        """
        if self.decay_rate <= 0:
            return {}
            
        changes: Dict[str, float] = {}
        
        for faction_id, standing in self.reputation.items():
            if standing.locked:
                continue
                
            old_rep = standing.reputation
            
            # Decay toward zero (neutral)
            if abs(old_rep) > 0.01:  # Only decay if not essentially neutral
                decay_amount = self.decay_rate
                if old_rep > 0:
                    standing.reputation = max(0, old_rep - decay_amount)
                else:
                    standing.reputation = min(0, old_rep + decay_amount)
                    
                if standing.reputation != old_rep:
                    standing.last_change_tick = tick
                    changes[faction_id] = standing.reputation
                    
        return changes
    
    def get_history(self, faction_id: str) -> List[Tuple[str, float, int]]:
        """Get reputation change history for a faction.
        
        Args:
            faction_id: Target faction.
            
        Returns:
            List of (action, delta, tick) tuples.
        """
        standing = self.reputation.get(faction_id)
        return list(standing.history) if standing else []
    
    def _get_or_create_standing(self, faction_id: str) -> FactionStanding:
        """Get existing standing or create new one.
        
        Args:
            faction_id: Target faction.
            
        Returns:
            FactionStanding for the faction.
        """
        if faction_id not in self.reputation:
            self.reputation[faction_id] = FactionStanding()
        return self.reputation[faction_id]
    
    def reset(self) -> None:
        """Clear all reputation data."""
        self.reputation.clear()