"""Persistent Identity System — Tier 11 Layer 4: Identity Tracking.

This module implements Layer 4 of Tier 11: the Persistent Identity System
that tracks character reputation, fame, and social dynamics across the
simulation. Without this system, LLM-enhanced NPCs would feel forgetful
and inconsistent.

Purpose:
    Track persistent character identity attributes that influence:
    - Dialogue tone and content
    - Planning bias (friendly vs hostile approach)
    - Coalition formation
    - World reactions to character actions

The Problem:
    - Characters don't track their reputation with factions
    - No persistence of fame/infamy across sessions
    - NPCs don't remember rumors about other characters
    - No social consequence tracking for actions

The Solution:
    IdentitySystem tracks:
    - Fame: Public recognition level (0.0-1.0)
    - Reputation: Faction-specific standing (-1.0 to 1.0)
    - Rumors: Gossip and information about the character
    - Historical events: Key actions that shaped reputation

Usage:
    identity = IdentitySystem()
    identity.set_fame("hero_alice", 0.8)
    identity.update_reputation("hero_alice", "mages_guild", 0.3)
    identity.add_rumor("hero_alice", "Defeated the dragon alone!")
    reputation = identity.get_reputation("hero_alice", "mages_guild")

Architecture:
    Character Identity:
    ├── Fame (global recognition)
    ├── Reputation (per-faction standing)
    ├── Rumors (gossip pool)
    └── Actions (reputation-affecting history)

Event Hooks:
    - on_action: Update reputation based on character actions
    - on_interaction: Update relationship when characters interact
    - on_event: Broadcast world events that affect reputation

Design Rules:
    - Reputation changes are proportional to action importance
    - Fame decays slowly without notable actions
    - Rumors spread and fade over time
    - All changes are bounded (-1.0 to 1.0 for reputation)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Configuration
MAX_RUMORS_PER_CHARACTER = 20
RUMOR_FADE_RATE = 0.05  # Reputation decay per idle tick
FAME_DECAY_RATE = 0.01  # Slow fame decay
MAX_REPUTATION_CHANGE = 0.3  # Max single reputation change
MIN_REPUTATION = -1.0
MAX_REPUTATION = 1.0

# Action reputation weights
ACTION_REPUTATION_WEIGHTS: Dict[str, float] = {
    "attack": -0.2,
    "kill": -0.5,
    "damage": -0.1,
    "aid": 0.15,
    "heal": 0.2,
    "help": 0.15,
    "betray": -0.4,
    "alliance": 0.2,
    "trade": 0.05,
    "protect": 0.2,
    "save": 0.3,
    "steal": -0.25,
    "destroy": -0.2,
    "gift": 0.1,
    "insult": -0.1,
    "praise": 0.05,
}


@dataclass
class CharacterIdentity:
    """Persistent identity data for a single character.
    
    Attributes:
        character_id: Unique character identifier.
        fame: Global recognition level (0.0 to 1.0).
        reputation: Faction-specific standing dict.
        rumors: List of current rumors about this character.
        action_history: List of reputation-affecting actions.
        traits: Persistent personality traits.
        relationships: Character-to-character relationship tracking.
    """
    
    character_id: str
    fame: float = 0.0
    reputation: Dict[str, float] = field(default_factory=dict)
    rumors: List[Dict[str, Any]] = field(default_factory=list)
    action_history: List[Dict[str, Any]] = field(default_factory=list)
    traits: Dict[str, float] = field(default_factory=dict)
    relationships: Dict[str, float] = field(default_factory=dict)
    
    def get_reputation(self, faction_id: str) -> float:
        """Get reputation with a faction.
        
        Args:
            faction_id: Faction identifier.
            
        Returns:
            Reputation value, 0.0 if unknown.
        """
        return self.reputation.get(faction_id, 0.0)
    
    def set_reputation(self, faction_id: str, value: float) -> None:
        """Set reputation with a faction.
        
        Args:
            faction_id: Faction identifier.
            value: New reputation value (-1.0 to 1.0).
        """
        self.reputation[faction_id] = max(
            MIN_REPUTATION, min(MAX_REPUTATION, value)
        )
    
    def adjust_reputation(self, faction_id: str, delta: float) -> float:
        """Adjust reputation with a faction by delta.
        
        Args:
            faction_id: Faction identifier.
            delta: Change amount.
            
        Returns:
            New reputation value.
        """
        current = self.get_reputation(faction_id)
        delta = max(-MAX_REPUTATION_CHANGE, min(MAX_REPUTATION_CHANGE, delta))
        new_value = current + delta
        self.set_reputation(faction_id, new_value)
        return new_value
    
    def add_rumor(self, rumor: str, source: str = "unknown") -> None:
        """Add a rumor about this character.
        
        Args:
            rumor: Rumor text.
            source: Source of the rumor.
        """
        self.rumors.append({
            "text": rumor,
            "source": source,
            "strength": 1.0,  # Rumors fade over time
        })
        
        # Prune old rumors
        if len(self.rumors) > MAX_RUMORS_PER_CHARACTER:
            self.rumors = self.rumors[-MAX_RUMORS_PER_CHARACTER:]
    
    def add_action(self, action: str, target: str, importance: float = 0.5) -> None:
        """Record a reputation-affecting action.
        
        Args:
            action: Action type.
            target: Action target.
            importance: Action importance (0.0-1.0).
        """
        self.action_history.append({
            "action": action,
            "target": target,
            "importance": importance,
        })
        
        # Keep only recent history
        if len(self.action_history) > 50:
            self.action_history = self.action_history[-50:]
    
    def fade_rumors(self) -> None:
        """Fade all rumors (call periodically)."""
        for rumor in self.rumors:
            rumor["strength"] = max(0.0, rumor["strength"] - RUMOR_FADE_RATE)
        
        # Remove faded rumors
        self.rumors = [r for r in self.rumors if r["strength"] > 0.1]
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize identity to dict.
        
        Returns:
            Identity data as dictionary.
        """
        return {
            "character_id": self.character_id,
            "fame": self.fame,
            "reputation": dict(self.reputation),
            "rumor_count": len(self.rumors),
            "rumors": [{"text": r["text"], "strength": r["strength"]} for r in self.rumors[:5]],
            "recent_actions": self.action_history[-10:],
            "traits": dict(self.traits),
            "relationships": dict(self.relationships),
        }


class IdentitySystem:
    """Manages persistent character identities across the simulation.
    
    The IdentitySystem tracks character reputation with factions,
    public fame, and social dynamics. This data feeds into:
    - Dialogue generation (tone based on reputation)
    - Intent enrichment (planning bias)
    - Coalition formation (who trusts whom)
    - World state reactions
    
    Usage:
        identity_sys = IdentitySystem()
        identity_sys.create_identity("hero_alice")
        
        # Update after actions
        identity_sys.process_action("hero_alice", "attack", "bandit_leader")
        
        # Query for NPC behavior
        rep = identity_sys.get_reputation("hero_alice", "town_guard")
        if rep > 0.5:
            guard.dialogue = "Welcome back, hero!"
        else:
            guard.dialogue = "I've heard about you..."
    """
    
    def __init__(self):
        """Initialize the IdentitySystem."""
        self.identities: Dict[str, CharacterIdentity] = {}
        self._stats: Dict[str, int] = {
            "identities_created": 0,
            "reputation_updates": 0,
            "actions_processed": 0,
            "rumors_added": 0,
        }
    
    def get_or_create(self, character_id: str) -> CharacterIdentity:
        """Get existing identity or create new one.
        
        Args:
            character_id: Character identifier.
            
        Returns:
            CharacterIdentity object.
        """
        if character_id not in self.identities:
            self.identities[character_id] = CharacterIdentity(
                character_id=character_id,
            )
            self._stats["identities_created"] += 1
        
        return self.identities[character_id]
    
    def set_fame(self, character_id: str, fame: float) -> None:
        """Set a character's fame level.
        
        Args:
            character_id: Character identifier.
            fame: Fame level (0.0 to 1.0).
        """
        identity = self.get_or_create(character_id)
        identity.fame = max(0.0, min(1.0, fame))
    
    def adjust_fame(self, character_id: str, delta: float) -> float:
        """Adjust fame by delta.
        
        Args:
            character_id: Character identifier.
            delta: Change amount.
            
        Returns:
            New fame value.
        """
        identity = self.get_or_create(character_id)
        identity.fame = max(0.0, min(1.0, identity.fame + delta))
        return identity.fame
    
    def update_reputation(
        self,
        character_id: str,
        faction_id: str,
        delta: float,
    ) -> float:
        """Update character's reputation with a faction.
        
        Args:
            character_id: Character identifier.
            faction_id: Faction identifier.
            delta: Reputation change.
            
        Returns:
            New reputation value.
        """
        identity = self.get_or_create(character_id)
        new_rep = identity.adjust_reputation(faction_id, delta)
        self._stats["reputation_updates"] += 1
        return new_rep
    
    def get_reputation(
        self,
        character_id: str,
        faction_id: str,
    ) -> float:
        """Get character's reputation with a faction.
        
        Args:
            character_id: Character identifier.
            faction_id: Faction identifier.
            
        Returns:
            Reputation value (-1.0 to 1.0), 0.0 if unknown.
        """
        identity = self.identities.get(character_id)
        if identity:
            return identity.get_reputation(faction_id)
        return 0.0
    
    def add_rumor(
        self,
        character_id: str,
        rumor: str,
        source: str = "unknown",
    ) -> None:
        """Add a rumor about a character.
        
        Args:
            character_id: Character identifier.
            rumor: Rumor text.
            source: Source of the rumor.
        """
        identity = self.get_or_create(character_id)
        identity.add_rumor(rumor, source)
        self._stats["rumors_added"] += 1
    
    def process_action(
        self,
        actor_id: str,
        action: str,
        target: str,
        importance: float = 0.5,
        faction_id: Optional[str] = None,
    ) -> Dict[str, float]:
        """Process a reputation-affecting action.
        
        Updates actor's reputation based on action type, importance,
        and faction context.
        
        Args:
            actor_id: Character performing the action.
            action: Action type (attack, aid, heal, etc.).
            target: Action target.
            importance: Action importance (0.0-1.0).
            faction_id: Faction context (optional).
            
        Returns:
            Dict of reputation changes.
        """
        changes: Dict[str, float] = {}
        
        # Get base reputation change for action type
        base_change = ACTION_REPUTATION_WEIGHTS.get(action, 0.0)
        if base_change == 0.0:
            # Unknown action, minimal impact
            return changes
        
        # Scale by importance
        scaled_change = base_change * importance
        
        # Update actor's reputation
        identity = self.get_or_create(actor_id)
        identity.add_action(action, target, importance)
        
        # Apply to relevant factions
        if faction_id:
            new_rep = identity.adjust_reputation(faction_id, scaled_change)
            changes[faction_id] = new_rep
        else:
            # Apply to all factions with known reputation
            for known_faction in list(identity.reputation.keys()):
                # Diminished effect for non-specific actions
                faction_change = scaled_change * 0.5
                identity.adjust_reputation(known_faction, faction_change)
                changes[known_faction] = identity.get_reputation(known_faction)
        
        # Fame changes for notable actions
        if abs(scaled_change) > 0.15:
            # Notable action affects fame
            fame_delta = abs(scaled_change) * 0.2
            if scaled_change > 0:
                self.adjust_fame(actor_id, fame_delta)
            else:
                self.adjust_fame(actor_id, -fame_delta * 0.5)  # Infamy grows slower
        
        self._stats["actions_processed"] += 1
        return changes
    
    def get_rumors_for(
        self,
        character_id: str,
        min_strength: float = 0.2,
    ) -> List[str]:
        """Get active rumors about a character.
        
        Args:
            character_id: Character identifier.
            min_strength: Minimum rumor strength to include.
            
        Returns:
            List of rumor texts.
        """
        identity = self.identities.get(character_id)
        if identity:
            return [
                r["text"] for r in identity.rumors
                if r["strength"] >= min_strength
            ]
        return []
    
    def get_relationship(
        self,
        character_id: str,
        other_id: str,
    ) -> float:
        """Get tracked relationship between two characters.
        
        Args:
            character_id: First character.
            other_id: Second character.
            
        Returns:
            Relationship value (-1.0 to 1.0), 0.0 if unknown.
        """
        identity = self.identities.get(character_id)
        if identity:
            return identity.relationships.get(other_id, 0.0)
        return 0.0
    
    def update_relationship(
        self,
        character_id: str,
        other_id: str,
        delta: float,
    ) -> float:
        """Update relationship between two characters.
        
        Args:
            character_id: First character.
            other_id: Second character.
            delta: Change amount.
            
        Returns:
            New relationship value.
        """
        identity = self.get_or_create(character_id)
        current = identity.relationships.get(other_id, 0.0)
        delta = max(-MAX_REPUTATION_CHANGE, min(MAX_REPUTATION_CHANGE, delta))
        new_value = max(MIN_REPUTATION, min(MAX_REPUTATION, current + delta))
        identity.relationships[other_id] = new_value
        return new_value
    
    def tick_update(self) -> Dict[str, int]:
        """Perform periodic identity updates (call each tick).
        
        Fades rumors and decays fame/relationships slowly.
        
        Returns:
            Update stats dict.
        """
        updates = {"rumors_faded": 0, "fame_decayed": 0}
        
        for identity in self.identities.values():
            # Fade rumors
            before = len(identity.rumors)
            identity.fade_rumors()
            updates["rumors_faded"] += before - len(identity.rumors)
            
            # Slow fame decay
            if identity.fame > 0:
                identity.fame = max(0.0, identity.fame - FAME_DECAY_RATE)
                updates["fame_decayed"] += 1
        
        return updates
    
    def get_stats(self) -> Dict[str, Any]:
        """Get system statistics.
        
        Returns:
            Stats dict.
        """
        return {
            **self._stats,
            "total_identities": len(self.identities),
        }
    
    def reset(self) -> None:
        """Clear all identity data."""
        self.identities.clear()
        self._stats = {
            "identities_created": 0,
            "reputation_updates": 0,
            "actions_processed": 0,
            "rumors_added": 0,
        }