"""Agent Scheduler — NPC turn scheduling for autonomous agent system.

This module implements the Agent Scheduler from TIER 10 of the RPG design spec.

Purpose:
    Decide which NPCs act each simulation tick, how often, and in
    what order. This prevents CPU explosion by limiting active NPCs
    per tick while ensuring all NPCs get regular opportunities to act.

The Problem:
    - Every NPC acting every tick is computationally expensive
    - Some NPCs (powerful, relevant) should act more often
    - No prioritization of important characters
    - Random order can miss critical NPC actions

The Solution:
    AgentScheduler selects a subset of NPCs to act each tick based on:
    - Character priority/power (more important = act more often)
    - Randomness for variety
    - Configurable max_per_tick to control CPU usage

Usage:
    scheduler = AgentScheduler(max_per_tick=5)
    active_ids = scheduler.select_agents(characters, world_state)
    for cid in active_ids:
        char = characters[cid]
        # Run agent brain, planner, executor
"""

from __future__ import annotations

import random
from typing import Any, Dict, List, Optional


class AgentScheduler:
    """Selects which NPCs act each simulation tick.
    
    The scheduler manages NPC turn order and throttling. Not every
    NPC acts every tick — the scheduler selects the most important
    NPCs while maintaining some randomness for emergent behavior.
    
    Selection Algorithm:
    1. Calculate priority score for each character
       - Base: random shuffle
       - Bonus: power/importance factor
    2. Select top max_per_tick characters
    3. Return selected character IDs
    
    Attributes:
        max_per_tick: Maximum NPCs to select each tick.
        use_priority: Whether to use priority-based selection.
    """
    
    def __init__(self, max_per_tick: int = 5, use_priority: bool = True):
        """Initialize the AgentScheduler.
        
        Args:
            max_per_tick: Maximum number of NPCs to select per tick.
            use_priority: Whether to use priority-based selection.
                         If False, uses pure random selection.
        """
        self.max_per_tick = max_per_tick
        self.use_priority = use_priority
        self._last_selected: List[str] = []
        self._selection_count: Dict[str, int] = {}
    
    def select_agents(
        self,
        characters: Dict[str, Any],
        world_state: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        """Select which NPCs should act this tick.
        
        Args:
            characters: Dict of char_id → Character object.
                       Characters should have: id, power (optional),
                       goals (optional).
            world_state: Optional world state for priority calculation.
            
        Returns:
            List of character IDs selected to act this tick.
        """
        if not characters:
            return []
        
        char_ids = list(characters.keys())
        
        # If no priority selection, use simple random
        if not self.use_priority:
            random.shuffle(char_ids)
            selected = char_ids[:self.max_per_tick]
            self._last_selected = selected
            self._track_selection(selected)
            return selected
        
        # Priority-based selection
        # Sort characters by priority (most important first)
        scored: List[tuple] = []
        for cid in char_ids:
            char = characters[cid]
            score = self._calculate_priority(char, world_state)
            scored.append((cid, score))
        
        # Add randomness to prevent same NPCs always acting
        # Weight scores with some noise
        noisy_scores = []
        for cid, score in scored:
            noise = random.uniform(0.7, 1.3)
            noisy_scores.append((cid, score * noise))
        
        # Sort by noisy score (descending)
        noisy_scores.sort(key=lambda x: x[1], reverse=True)
        
        # Select top N
        selected = [cid for cid, _ in noisy_scores[:self.max_per_tick]]
        self._last_selected = selected
        self._track_selection(selected)
        return selected
    
    def _calculate_priority(
        self,
        character: Any,
        world_state: Optional[Dict[str, Any]] = None,
    ) -> float:
        """Calculate priority score for a character.
        
        Priority is based on:
        - Character power (if available)
        - Number of active goals
        - Past selection frequency (to avoid starvation)
        - Hostile relationships
        
        Args:
            character: Character object to score.
            world_state: Optional world state.
            
        Returns:
            Priority score (higher = more likely to act).
        """
        base_score = 1.0
        
        # Power bonus (if available)
        power = getattr(character, "power", None)
        if power is not None:
            base_score += power * 0.5
        
        # Goal bonus (more goals = more active)
        goals = getattr(character, "goals", [])
        if goals:
            base_score += len(goals) * 0.2
        
        # Starvation bonus (less recently selected = higher priority)
        char_id = getattr(character, "id", None)
        if char_id:
            times_selected = self._selection_count.get(char_id, 0)
            starvation_bonus = max(0, times_selected) * 0.1
            base_score += starvation_bonus
        
        # Belief-based bonus (strong negative beliefs = more likely to act)
        beliefs = getattr(character, "beliefs", {})
        if isinstance(beliefs, dict):
            max_negative = min(beliefs.values()) if beliefs else 0
            if max_negative < -0.5:
                base_score += abs(max_negative) * 0.3
        
        return base_score
    
    def _track_selection(self, selected_ids: List[str]) -> None:
        """Track which NPCs were selected for future priority calculation.
        
        Args:
            selected_ids: List of character IDs that were selected.
        """
        for cid in selected_ids:
            self._selection_count[cid] = self._selection_count.get(cid, 0) + 1
    
    def reset(self) -> None:
        """Reset scheduler state."""
        self._last_selected = []
        self._selection_count = {}