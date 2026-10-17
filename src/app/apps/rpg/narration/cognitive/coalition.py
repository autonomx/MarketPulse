"""Coalition System — Tier 11 Layer 5: Coordinated NPC Behavior.

This module implements Layer 5 of Tier 11: the Coalition System that
enables NPCs to form temporary alliances and coordinate their actions
toward shared goals.

Purpose:
    Allow NPCs to form coalitions based on:
    - Shared interests / common enemies
    - Reputation and trust levels
    - Strategic necessity (weak factions band together)
    - Faction alignment

The Problem:
    - NPCs act independently even when cooperation makes sense
    - No coordination between allied factions
    - Weak NPCs get eliminated by stronger ones without resistance
    - No emergent political behavior

The Solution:
    CoalitionSystem tracks and manages:
    - Coalition formation (who is allied with whom)
    - Coordination execution (who does what in the coalition)
    - Coalition dissolution (when interests diverge)
    - Coalition benefits (strength in numbers)

Usage:
    coalition_sys = CoalitionSystem(identity_system)
    
    # Find potential coalition partners
    partners = coalition_sys.find_partners("small_faction", world_state)
    
    # Form coalition
    coalition = coalition_sys.form_coalition("small_faction", partners)
    
    # Get coordinated action
    action = coalition_sys.get_coordinated_action("small_faction", "attack", world_state)

Architecture:
    Coalition:
    ├── Members: List of faction/character IDs
    ├── Leader: Coordinator faction/character
    ├── Shared Goal: What the coalition is working toward
    ├── Trust Levels: Internal trust between members
    └── Duration: How long the coalition has existed

Coalition Actions:
    - coordinated_attack: Multiple attackers with role assignment
    - coordinated_defense: Shared defense pact
    - coordinated_negotiation: Joint diplomatic approach
    - resource_sharing: Pooling resources between allies

Design Rules:
    - Coalitions form based on reputation and shared enemies
    - Weaker factions seek coalitions more aggressively
    - Coalitions dissolve if trust drops too low or goal is achieved
    - No coalition exceeds MAX_COALITION_SIZE members
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# Configuration
MAX_COALITION_SIZE = 5
MIN_TRUST_FOR_COALITION = 0.3
TRUST_GAIN_PER_SUCCESS = 0.1
TRUST_LOSS_PER_FAILURE = -0.15
MIN_TRUST_FOR_DISSOLUTION = -0.3
COALITION_STABILITY_CHECK_INTERVAL = 10

# Coalition types
COALITION_ATTACK = "coordinated_attack"
COALITION_DEFENSE = "coordinated_defense"
COALITION_NEGOTIATION = "coordinated_negotiation"
COALITION_RESOURCE = "resource_sharing"


@dataclass
class Coalition:
    """A coalition between multiple factions or characters.
    
    Attributes:
        id: Unique coalition identifier.
        members: Set of member faction/character IDs.
        leader: Coalition leader/organizer ID.
        coalition_type: Type of cooperation.
        shared_goal: What the coalition is working toward.
        trust_levels: Trust between members (member_id -> trust value).
        created_tick: Tick when coalition was formed.
        last_action_tick: Tick of last coordinated action.
        stable: Whether coalition is stable or at risk.
        success_count: Number of successful coordinated actions.
        failure_count: Number of failed coordinated actions.
    """
    
    id: str
    members: Set[str] = field(default_factory=set)
    leader: str = ""
    coalition_type: str = ""
    shared_goal: str = ""
    trust_levels: Dict[str, Dict[str, float]] = field(default_factory=dict)
    created_tick: int = 0
    last_action_tick: int = 0
    stable: bool = True
    success_count: int = 0
    failure_count: int = 0
    
    def add_member(self, member_id: str, initial_trust: float = 0.5) -> None:
        """Add a member to the coalition.
        
        Args:
            member_id: Member identifier to add.
            initial_trust: Initial trust level for new member.
        """
        if len(self.members) >= MAX_COALITION_SIZE:
            logger.warning(f"Coalition {self.id} at max size, cannot add {member_id}")
            return
        
        self.members.add(member_id)
        
        # Initialize trust levels with existing members
        for existing in self.members:
            if existing not in self.trust_levels:
                self.trust_levels[existing] = {}
            self.trust_levels[existing][member_id] = initial_trust
            self.trust_levels[member_id] = self.trust_levels.get(member_id, {})
            self.trust_levels[member_id][existing] = initial_trust
    
    def update_trust(
        self,
        member_a: str,
        member_b: str,
        delta: float,
    ) -> float:
        """Update trust between two members.
        
        Args:
            member_a: First member.
            member_b: Second member.
            delta: Trust change.
            
        Returns:
            New trust value.
        """
        if member_a not in self.trust_levels:
            self.trust_levels[member_a] = {}
        
        current = self.trust_levels[member_a].get(member_b, 0.5)
        new_trust = max(-1.0, min(1.0, current + delta))
        self.trust_levels[member_a][member_b] = new_trust
        
        # Symmetric trust
        if member_b not in self.trust_levels:
            self.trust_levels[member_b] = {}
        self.trust_levels[member_b][member_a] = new_trust
        
        return new_trust
    
    def get_average_trust(self) -> float:
        """Get average trust across all members.
        
        Returns:
            Average trust value, 0.0 if no trust data.
        """
        all_trusts: list[Any] = []
        for member_trusts in self.trust_levels.values():
            all_trusts.extend(member_trusts.values())
        
        if not all_trusts:
            return 0.5
        
        return sum(all_trusts) / len(all_trusts)
    
    def record_success(self) -> None:
        """Record a successful coordinated action."""
        self.success_count += 1
        # Boost trust among all members
        for member_a in list(self.members):
            for member_b in self.members:
                if member_a != member_b:
                    self.update_trust(member_a, member_b, TRUST_GAIN_PER_SUCCESS)
    
    def record_failure(self) -> None:
        """Record a failed coordinated action."""
        self.failure_count += 1
        # Reduce trust among all members
        for member_a in list(self.members):
            for member_b in self.members:
                if member_a != member_b:
                    self.update_trust(member_a, member_b, TRUST_LOSS_PER_FAILURE)
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize coalition to dict.
        
        Returns:
            Coalition data as dictionary.
        """
        return {
            "id": self.id,
            "members": list(self.members),
            "leader": self.leader,
            "coalition_type": self.coalition_type,
            "shared_goal": self.shared_goal,
            "average_trust": self.get_average_trust(),
            "created_tick": self.created_tick,
            "stable": self.stable,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
        }


class CoalitionSystem:
    """Manages coalitions between factions and characters.
    
    The CoalitionSystem enables NPCs to form temporary alliances
    and coordinate actions. This creates emergent political behavior
    where weak factions band together against strong threats.
    
    Usage:
        coalition_sys = CoalitionSystem(identity_system)
        
        # Check if faction should seek coalition
        if coalition_sys.should_seek_coalition("small_faction", world_state):
            partners = coalition_sys.find_potential_partners(
                "small_faction", world_state
            )
            coalition = coalition_sys.form_coalition(
                "small_faction", partners, "coordinated_defense"
            )
        
        # Get coordinated action
        action = coalition_sys.get_coordinated_action(
            "small_faction", "attack", world_state
        )
    """
    
    def __init__(self, identity_system: Optional[Any] = None):
        """Initialize the CoalitionSystem.
        
        Args:
            identity_system: IdentitySystem for reputation queries.
                            Can be None for standalone operation.
        """
        self.coalitions: Dict[str, Coalition] = {}
        self.identity_system = identity_system
        self._next_coalition_id = 1
        self._stats: Dict[str, int] = {
            "coalitions_formed": 0,
            "coalitions_dissolved": 0,
            "coordinated_actions": 0,
            "coalition_seeks": 0,
        }
    
    def should_seek_coalition(
        self,
        faction_id: str,
        world_state: Dict[str, Any],
    ) -> bool:
        """Check if a faction should seek coalition partners.
        
        Factions seek coalitions when:
        - They are weaker than potential threats
        - They have negative reputation with powerful factions
        - They have low power/resources
        
        Args:
            faction_id: Faction to check.
            world_state: Current world state.
            
        Returns:
            True if faction should seek coalition.
        """
        self._stats["coalition_seeks"] += 1
        
        factions = world_state.get("factions", {})
        faction_data = factions.get(faction_id, {})
        
        if not isinstance(faction_data, dict):
            return False
        
        faction_power = faction_data.get("power", 0.5)
        
        # Weak factions seek coalitions more aggressively
        if faction_power < 0.3:
            return True
        
        # Check for stronger enemies
        for other_id, other_data in factions.items():
            if other_id == faction_id:
                continue
            if isinstance(other_data, dict):
                other_power = other_data.get("power", 0)
                relations = other_data.get("relations", {})
                
                if other_power > faction_power * 1.5:
                    relation = relations.get(faction_id, 0)
                    if relation < -0.3:
                        return True
        
        return False
    
    def find_potential_partners(
        self,
        faction_id: str,
        world_state: Dict[str, Any],
        min_count: int = 1,
        max_count: int = 3,
    ) -> List[str]:
        """Find potential coalition partners for a faction.
        
        Partners are selected based on:
        - Positive relations between factions
        - Similar power levels (strength in numbers)
        - Shared enemies
        
        Args:
            faction_id: Faction seeking partners.
            world_state: Current world state.
            min_count: Minimum partners to find.
            max_count: Maximum partners to find.
            
        Returns:
            List of potential partner faction IDs, sorted by compatibility.
        """
        factions = world_state.get("factions", {})
        faction_data = factions.get(faction_id, {})
        
        if not isinstance(faction_data, dict):
            return []
        
        faction_power = faction_data.get("power", 0.5)
        faction_relations = faction_data.get("relations", {})
        
        # Score each potential partner
        candidates: List[Tuple[float, str]] = []
        
        for other_id, other_data in factions.items():
            if other_id == faction_id:
                continue
            if not isinstance(other_data, dict):
                continue
            
            score = 0.0
            
            # Positive relations boost score
            relation = faction_relations.get(other_id, 0)
            if relation > 0:
                score += relation * 2
            
            # Check reputation from identity system
            if self.identity_system:
                rep = self.identity_system.get_reputation(faction_id, other_id)
                score += rep
            
            # Similar power levels are better (strength in numbers)
            other_power = other_data.get("power", 0)
            power_diff = abs(faction_power - other_power)
            if power_diff < 0.3:
                score += 0.5
            
            # Shared enemies boost score
            other_relations = other_data.get("relations", {})
            for enemy_id, enemy_rel in faction_relations.items():
                if enemy_rel < -0.3:
                    enemy_other_rel = other_relations.get(enemy_id, 0)
                    if enemy_other_rel < -0.3:
                        score += 0.3  # Shared enemy bonus
            
            # Penalize if already in coalition
            if self._faction_in_coalition(other_id):
                score -= 0.5
            
            # Only consider factions with sufficient trust
            if score >= MIN_TRUST_FOR_COALITION:
                candidates.append((score, other_id))
        
        # Sort by score and return top candidates
        candidates.sort(key=lambda x: x[0], reverse=True)
        return [c[1] for c in candidates[:max_count]]
    
    def form_coalition(
        self,
        leader_id: str,
        partners: List[str],
        coalition_type: str = COALITION_DEFENSE,
        shared_goal: str = "",
        current_tick: int = 0,
    ) -> Optional[Coalition]:
        """Form a new coalition.
        
        Args:
            leader_id: Coalition leader faction ID.
            partners: List of partner faction IDs.
            coalition_type: Type of coalition.
            shared_goal: What coalition is working toward.
            current_tick: Current simulation tick.
            
        Returns:
            New Coalition object, or None if formation failed.
        """
        if not partners:
            return None
        
        coalition_id = f"coalition_{self._next_coalition_id}"
        self._next_coalition_id += 1
        
        coalition = Coalition(
            id=coalition_id,
            leader=leader_id,
            coalition_type=coalition_type,
            shared_goal=shared_goal or f"{coalition_type}_{leader_id}",
            created_tick=current_tick,
            last_action_tick=current_tick,
        )
        
        coalition.add_member(leader_id)
        for partner in partners:
            trust = 0.5  # Default trust
            if self.identity_system:
                trust = max(0.3, self.identity_system.get_reputation(
                    leader_id, partner
                ))
            coalition.add_member(partner, initial_trust=trust)
        
        self.coalitions[coalition_id] = coalition
        self._stats["coalitions_formed"] += 1
        
        logger.info(
            f"Coalition formed: {coalition_id} "
            f"({len(coalition.members)} members, leader={leader_id})"
        )
        
        return coalition
    
    def get_coordinated_action(
        self,
        faction_id: str,
        action_type: str,
        world_state: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Get a coordinated action for a faction in a coalition.
        
        If faction is in a coalition, returns coordinated action
        that involves coalition members.
        
        Args:
            faction_id: Faction seeking action.
            action_type: Action type (attack, defense, etc.).
            world_state: Current world state.
            
        Returns:
            Coordinated action dict, or None if no coalition available.
        """
        coalition = self._get_faction_coalition(faction_id)
        if coalition is None:
            return None
        
        if action_type == "attack":
            return self._build_coordinated_attack(coalition, faction_id, world_state)
        elif action_type == "defend":
            return self._build_coordinated_defense(coalition, faction_id, world_state)
        elif action_type == "negotiate":
            return self._build_coordinated_negotiation(coalition, faction_id, world_state)
        
        return None
    
    def _build_coordinated_attack(
        self,
        coalition: Coalition,
        faction_id: str,
        world_state: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Build a coordinated attack action.
        
        Args:
            coalition: Coalition organizing attack.
            faction_id: Faction initiating attack.
            world_state: Current world state.
            
        Returns:
            Coordinated attack action dict.
        """
        # Find shared enemies
        enemies: Dict[str, int] = {}
        for member_id in coalition.members:
            member_data = world_state.get("factions", {}).get(member_id, {})
            if isinstance(member_data, dict):
                relations = member_data.get("relations", {})
                for enemy_id, relation in relations.items():
                    if relation < -0.3:
                        enemies[enemy_id] = enemies.get(enemy_id, 0) + 1
        
        # Pick most common enemy
        target = max(enemies, key=lambda enemy: enemies[enemy]) if enemies else None
        
        # Assign roles based on power
        participants = []
        for member_id in coalition.members:
            member_data = world_state.get("factions", {}).get(member_id, {})
            if isinstance(member_data, dict):
                power = member_data.get("power", 0.5)
                role = "lead" if member_id == coalition.leader else "support"
                participants.append({
                    "id": member_id,
                    "power": power,
                    "role": role,
                })
        
        self._stats["coordinated_actions"] += 1
        coalition.last_action_tick = world_state.get("tick", 0)
        
        return {
            "type": COALITION_ATTACK,
            "coalition_id": coalition.id,
            "target": target,
            "participants": participants,
            "total_power": sum(p["power"] for p in participants),
        }
    
    def _build_coordinated_defense(
        self,
        coalition: Coalition,
        faction_id: str,
        world_state: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Build a coordinated defense action.
        
        Args:
            coalition: Coalition organizing defense.
            faction_id: Faction in danger.
            world_state: Current world state.
            
        Returns:
            Coordinated defense action dict.
        """
        defenders = []
        for member_id in coalition.members:
            member_data = world_state.get("factions", {}).get(member_id, {})
            if isinstance(member_data, dict):
                defenders.append({
                    "id": member_id,
                    "power": member_data.get("power", 0.5),
                })
        
        self._stats["coordinated_actions"] += 1
        
        return {
            "type": COALITION_DEFENSE,
            "coalition_id": coalition.id,
            "defenders": defenders,
            "total_power": sum(d["power"] for d in defenders),
        }
    
    def _build_coordinated_negotiation(
        self,
        coalition: Coalition,
        faction_id: str,
        world_state: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Build a coordinated negotiation action.
        
        Args:
            coalition: Coalition organizing negotiation.
            faction_id: Faction leading negotiation.
            world_state: Current world state.
            
        Returns:
            Coordinated negotiation action dict.
        """
        negotiators = []
        for member_id in coalition.members:
            member_data = world_state.get("factions", {}).get(member_id, {})
            if isinstance(member_data, dict):
                negotiators.append({
                    "id": member_id,
                    "relations": member_data.get("relations", {}),
                })
        
        self._stats["coordinated_actions"] += 1
        
        return {
            "type": COALITION_NEGOTIATION,
            "coalition_id": coalition.id,
            "negotiators": negotiators,
            "shared_goal": coalition.shared_goal,
        }
    
    def check_coalition_stability(
        self,
        coalition_id: str,
        current_tick: int = 0,
    ) -> bool:
        """Check if a coalition is stable and should continue.
        
        Coalitions dissolve when:
        - Average trust drops below threshold
        - Faction leaves or is expelled
        - Goal is achieved
        - Coalition is too old without success
        
        Args:
            coalition_id: Coalition to check.
            current_tick: Current simulation tick.
            
        Returns:
            True if coalition should continue.
        """
        coalition = self.coalitions.get(coalition_id)
        if coalition is None:
            return False
        
        # Check trust
        avg_trust = coalition.get_average_trust()
        if avg_trust < MIN_TRUST_FOR_DISSOLUTION:
            self._dissolve_coalition(coalition_id, reason="low_trust")
            return False
        
        # Check member count
        if len(coalition.members) < 1:
            self._dissolve_coalition(coalition_id, reason="no_members")
            return False
        
        # Check age without success
        age = current_tick - coalition.created_tick
        if age > 50 and coalition.success_count == 0:
            self._dissolve_coalition(coalition_id, reason="stale")
            return False
        
        return True
    
    def _get_faction_coalition(self, faction_id: str) -> Optional[Coalition]:
        """Get coalition containing a faction.
        
        Args:
            faction_id: Faction to find.
            
        Returns:
            Coalition object, or None.
        """
        for coalition in self.coalitions.values():
            if faction_id in coalition.members:
                return coalition
        return None
    
    def _faction_in_coalition(self, faction_id: str) -> bool:
        """Check if faction is in any coalition.
        
        Args:
            faction_id: Faction to check.
            
        Returns:
            True if in coalition.
        """
        return self._get_faction_coalition(faction_id) is not None
    
    def _dissolve_coalition(self, coalition_id: str, reason: str = "") -> None:
        """Dissolve a coalition.
        
        Args:
            coalition_id: Coalition to dissolve.
            reason: Reason for dissolution.
        """
        coalition = self.coalitions.pop(coalition_id, None)
        if coalition:
            self._stats["coalitions_dissolved"] += 1
            logger.info(
                f"Coalition dissolved: {coalition_id} (reason: {reason})"
            )
    
    def get_stats(self) -> Dict[str, Any]:
        """Get system statistics.
        
        Returns:
            Stats dict.
        """
        return {
            **self._stats,
            "active_coalitions": len(self.coalitions),
        }
    
    def reset(self) -> None:
        """Clear all coalition data."""
        self.coalitions.clear()
        self._stats = {
            "coalitions_formed": 0,
            "coalitions_dissolved": 0,
            "coordinated_actions": 0,
            "coalition_seeks": 0,
        }
