"""Agent Brain — Decision-making for autonomous NPCs.

This module implements the Agent Brain from TIER 10 of the RPG design spec.

Purpose:
    Transform NPC beliefs, goals, and memory into intentions that drive
    autonomous behavior. The Agent Brain evaluates each NPC's internal
    state against the current world state to decide what type of action
    the NPC should take next.

The Problem:
    - NPCs are reactive only, no proactive behavior
    - No goal-driven decision-making
    - NPCs don't adapt to changing world conditions

The Solution:
    AgentBrain evaluates:
        goals + beliefs + world_state → next intention
    Intentions are high-level action types that the Planner converts
    into concrete multi-step plans.

Usage:
    brain = AgentBrain()
    intention = brain.decide(npc_character, world_state)

Architecture:
    Character State (goals, beliefs, memory)
         ↓
    Goal Priority Evaluation
         ↓
    Intention Selection
         ↓
    Output: intention dict for Planner

Design Rules:
    - Deterministic logic (no LLM in core loop)
    - Goal priority: survival > power > social > idle
    - Intentions are abstract, not concrete actions
    - Beliefs modulate goal selection
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# Goal priority weights (higher = more urgent)
GOAL_PRIORITY = {
    "survive": 10.0,
    "escape": 9.0,
    "attack": 8.0,
    "revenge": 7.5,
    "gain power": 7.0,
    "protect": 6.5,
    "defend": 6.0,
    "expand influence": 5.5,
    "gain influence": 5.5,
    "gather": 5.0,
    "accumulate": 5.0,
    "negotiate": 4.5,
    "diplomacy": 4.5,
    "trade": 4.0,
    "help": 3.5,
    "aid": 3.5,
    "explore": 2.0,
    "idle": 0.0,
}

# Intention types the brain can produce
INTENTION_EXPAND_INFLUENCE = "expand_influence"
INTENTION_ATTACK_TARGET = "attack_target"
INTENTION_DELIVER_AID = "deliver_aid"
INTENTION_GATHER_RESOURCES = "gather_resources"
INTENTION_NEGOTIATE = "negotiate"
INTENTION_DEFEND = "defend"
INTENTION_IDLE = "idle"


class AgentBrain:
    """Decision-making engine for autonomous NPCs.
    
    The AgentBrain converts an NPC's goals, beliefs, and the current
    world state into a high-level intention. This intention drives
    the Planner to create concrete multi-step action plans.
    
    Decision Process:
    1. Evaluate goals by priority
    2. Check world state conditions
    3. Modulate by beliefs
    4. Select highest priority intention
    
    Attributes:
        _goal_cache: Cached goal-to-intention mappings for performance.
    """
    
    def __init__(self):
        """Initialize the AgentBrain."""
        self._goal_cache: Dict[str, str] = {}
        
    def decide(
        self,
        character: Any,
        world_state: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Decide the next intention for a character.
        
        Evaluates the character's goals, beliefs, and the world state
        to determine what the character should do next.
        
        Args:
            character: Character object with goals, beliefs, traits.
                       Must have: goals (list), beliefs (dict or get_belief method)
            world_state: Current world state dict with keys:
                - factions: Dict of faction_id → faction data
                - economy: Economy state
                - tick: Current simulation tick
                - events: Recent world events
                
        Returns:
            Intention dict with keys:
            - type: Intention type string
            - target: Optional target entity (faction, character, etc.)
            - priority: Numeric priority (higher = more urgent)
            - reasoning: Human-readable reason for this intention
            
            Returns None if character has no goals or is incapacitated.
        """
        if not self._has_goals(character):
            return None
        
        # Check for survival threats first
        survival = self._check_survival(character, world_state)
        if survival:
            return survival
        
        # Evaluate each goal and pick highest priority intention
        intentions = self._evaluate_goals(character, world_state)
        
        if not intentions:
            return {
                "type": INTENTION_IDLE,
                "target": None,
                "priority": 0.0,
                "reasoning": "No active goals",
            }
        
        # Sort by priority and pick the best
        intentions.sort(key=lambda x: x.get("priority", 0), reverse=True)
        return intentions[0]
    
    def _has_goals(self, character: Any) -> bool:
        """Check if character has any goals.
        
        Args:
            character: Character to check.
            
        Returns:
            True if character has goals.
        """
        goals = getattr(character, "goals", None)
        if goals is None:
            goals = getattr(character, "goals", [])
        return bool(goals)
    
    def _check_survival(
        self,
        character: Any,
        world_state: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Check if character is in immediate danger.
        
        Survival needs override all other goals.
        
        Args:
            character: Character to check.
            world_state: Current world state.
            
        Returns:
            Survival intention if in danger, None otherwise.
        """
        # Check for low resources/morale/power
        power = getattr(character, "power", getattr(character, "resources", None))
        if power is not None and power < 0.2:
            return {
                "type": INTENTION_GATHER_RESOURCES,
                "target": character.id,
                "priority": 10.0,
                "reasoning": "Low resources - need to survive",
            }
        
        # Check for hostile factions targeting this character
        char_id = getattr(character, "id", None)
        if char_id:
            factions = world_state.get("factions", {})
            for faction_id, faction_data in factions.items():
                if isinstance(faction_data, dict):
                    relations = faction_data.get("relations", {})
                    relation = relations.get(char_id, 0)
                    power = faction_data.get("power", 0.5)
                    if relation < -0.7 and power > 0.5:
                        return {
                            "type": INTENTION_DEFEND,
                            "target": faction_id,
                            "priority": 9.0,
                            "reasoning": f"Threatened by {faction_id}",
                        }
        
        return None
    
    def _evaluate_goals(
        self,
        character: Any,
        world_state: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Evaluate character goals and produce intentions.
        
        Args:
            character: Character with goals.
            world_state: Current world state.
            
        Returns:
            List of intention dicts, sorted by priority.
        """
        goals = getattr(character, "goals", [])
        if not goals:
            goals = []
        
        intentions: List[Dict[str, Any]] = []
        
        for goal in goals:
            goal_lower = goal.lower() if isinstance(goal, str) else str(goal).lower()
            intention = self._goal_to_intention(goal_lower, character, world_state)
            if intention:
                intentions.append(intention)
        
        return intentions
    
    def _goal_to_intention(
        self,
        goal_lower: str,
        character: Any,
        world_state: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Convert a single goal to an intention.
        
        Args:
            goal_lower: Lowercase goal string.
            character: Character with the goal.
            world_state: Current world state.
            
        Returns:
            Intention dict, or None if goal not recognized.
        """
        # Check cached mapping first
        if goal_lower in self._goal_cache:
            return self._expand_cached_intention(
                self._goal_cache[goal_lower],
                character,
                world_state,
            )
        
        # Power/Influence goals
        if self._matches_any(goal_lower, ["gain power", "expand", "influence", "dominate", "control"]):
            return self._create_intention(INTENTION_EXPAND_INFLUENCE, character, world_state)
        
        # Aggression goals
        if self._matches_any(goal_lower, ["attack", "destroy", "conquer", "invade", "war"]):
            return self._create_intention(INTENTION_ATTACK_TARGET, character, world_state)
        
        # Revenge goals
        if self._matches_any(goal_lower, ["revenge", "retaliate", "avenge", "retribution"]):
            return self._create_intention(INTENTION_ATTACK_TARGET, character, world_state)
        
        # Resource goals
        if self._matches_any(goal_lower, ["gather", "accumulate", "resource", "wealth", "collect"]):
            return self._create_intention(INTENTION_GATHER_RESOURCES, character, world_state)
        
        # Diplomatic goals
        if self._matches_any(goal_lower, ["negotiate", "diplomacy", "alliance", "treaty", "peace"]):
            return self._create_intention(INTENTION_NEGOTIATE, character, world_state)
        
        # Aid/Help goals
        if self._matches_any(goal_lower, ["help", "aid", "deliver", "support", "protect", "defend"]):
            return self._create_intention(INTENTION_DELIVER_AID, character, world_state)
        
        # Explore goals
        if self._matches_any(goal_lower, ["explore", "discover", "investigate", "scout"]):
            return self._create_intention(INTENTION_EXPAND_INFLUENCE, character, world_state)
        
        # Fallback: try to match by keyword
        priority = GOAL_PRIORITY.get(goal_lower, 1.0)
        if priority > 5.0:
            return self._create_intention(INTENTION_EXPAND_INFLUENCE, character, world_state)
        elif priority > 0:
            return self._create_intention(INTENTION_IDLE, character, world_state)
        
        return None
    
    def _create_intention(
        self,
        intention_type: str,
        character: Any,
        world_state: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Create an intention dict.
        
        Args:
            intention_type: Type of intention.
            character: Character generating the intention.
            world_state: Current world state.
            
        Returns:
            Intention dict with type, target, priority, reasoning.
        """
        # Determine target based on beliefs
        target = self._select_target(character, world_state)
        priority = GOAL_PRIORITY.get(intention_type, 5.0)
        
        # Beliefs modulate priority
        if target:
            belief = self._get_belief(character, target)
            if intention_type in (INTENTION_ATTACK_TARGET,):
                # More negative belief = higher priority to attack
                priority += abs(min(0, belief)) * 2
            elif intention_type in (INTENTION_NEGOTIATE, INTENTION_DELIVER_AID):
                # More positive belief = higher priority to cooperate
                priority += max(0, belief) * 2
        
        return {
            "type": intention_type,
            "target": target,
            "priority": priority,
            "reasoning": f"Character {character.id} decided to {intention_type}",
        }
    
    def _expand_cached_intention(
        self,
        intention_type: str,
        character: Any,
        world_state: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Expand a cached intention type to a full dict.
        
        Args:
            intention_type: Cached intention type.
            character: Character.
            world_state: World state.
            
        Returns:
            Full intention dict.
        """
        return self._create_intention(intention_type, character, world_state)
    
    def _select_target(
        self,
        character: Any,
        world_state: Dict[str, Any],
    ) -> Optional[str]:
        """Select a target for the intention based on beliefs.
        
        Finds the most relevant target entity based on the character's
        strongest beliefs (positive or negative).
        
        Args:
            character: Character with beliefs.
            world_state: Current world state.
            
        Returns:
            Target entity ID, or None.
        """
        beliefs = getattr(character, "beliefs", {})
        if hasattr(beliefs, "items"):
            belief_items = list(beliefs.items())
        else:
            belief_items = []
        
        if not belief_items:
            return None
        
        # Sort by absolute belief value (strongest beliefs first)
        belief_items.sort(key=lambda x: abs(x[1]), reverse=True)
        
        if belief_items:
            return belief_items[0][0]
        return None
    
    def _get_belief(self, character: Any, entity_id: str) -> float:
        """Get character's belief about an entity.
        
        Args:
            character: Character with beliefs.
            entity_id: Entity to check belief about.
            
        Returns:
            Belief value, 0.0 if unknown.
        """
        if hasattr(character, "get_belief"):
            return character.get_belief(entity_id)
        beliefs = getattr(character, "beliefs", {})
        if isinstance(beliefs, dict):
            return beliefs.get(entity_id, 0.0)
        return 0.0
    
    @staticmethod
    def _matches_any(text: str, keywords: List[str]) -> bool:
        """Check if text contains any of the keywords.
        
        Args:
            text: Text to search.
            keywords: Keywords to match.
            
        Returns:
            True if any keyword matches.
        """
        return any(kw in text for kw in keywords)