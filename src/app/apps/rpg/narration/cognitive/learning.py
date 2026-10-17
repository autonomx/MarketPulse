"""Learning System — Tier 11 Layer 6: Lightweight Adaptive Behavior.

This module implements Layer 6 of Tier 11: the Learning System that tracks
action outcomes and adapts NPC behavior based on past successes and
failures. This is NOT machine learning — it's simple outcome tracking
with behavioral adaptation.

Purpose:
    Track action outcomes and adapt NPC behavior based on:
    - Recent success/failure rates
    - Action effectiveness trends
    - Pattern recognition (repeated failures)

The Problem:
    - NPCs repeat failing strategies indefinitely
    - No behavioral adaptation from experience
    - No persistence of learned behaviors
    - Repetitive patterns feel mechanical

The Solution:
    LearningSystem tracks:
    - Action history with outcomes (success/failure)
    - Recent failure counts per action type
    - Behavioral adaptation when failures exceed threshold

Usage:
    learning = LearningSystem()
    
    # Record action outcome
    learning.record_outcome("npc_bob", "attack", success=False)
    
    # Get adapted intent (may change if failures detected)
    adapted = learning.adapt_intent("npc_bob", original_intent)
    
    # Check if NPC should change strategy
    if learning.should_change_strategy("npc_bob", "attack"):
        new_intent = learning.suggest_alternative("npc_bob", "attack")

Architecture:
    Action History:
    ├── Character ID → Actions list
    │   ├── Action type
    │   ├── Outcome (success/failure)
    │   └── Tick when performed
    └── Analysis
        ├── Failure counts by action type
        ├── Recent window (last N actions)
        └── Adaptation triggers

Design Rules:
    - Track last N actions per character (not entire history)
    - Failure threshold triggers adaptation
    - Adaptation = reduce priority or suggest alternative
    - No ML, no complex learning algorithms
    - Lightweight and deterministic
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from typing import Any, Deque, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Configuration
MAX_HISTORY_PER_CHARACTER = 30  # Most recent actions to track
FAILURE_WINDOW = 10  # Window to check for failure patterns
FAILURE_THRESHOLD = 3  # Failures in window triggers adaptation
PRIORITY_PENALTY = 0.2  # Priority reduction per failure
ADAPTATION_COOLDOWN = 5  # Ticks between adaptations per character
MIN_ACTION_PRIORITY = 1.0  # Minimum priority after penalties


class LearningSystem:
    """Tracks action outcomes and adapts NPC behavior.
    
    The LearningSystem provides lightweight learning without ML by
    tracking action outcomes and adapting behavior when failures
    exceed thresholds.
    
    Usage:
        learning = LearningSystem()
        
        # Each tick: record outcomes from executed actions
        learning.record_outcome("npc_1", "attack_bandits", success=True)
        learning.record_outcome("npc_1", "attack_bandits", success=False)
        learning.record_outcome("npc_1", "attack_bandits", success=False)
        learning.record_outcome("npc_1", "attack_bandits", success=False)
        
        # Before deciding next action, check for adaptation
        if learning.should_change_strategy("npc_1", "attack"):
            new_intent = learning.suggest_alternative("npc_1", "attack_bandits")
    
    Attributes:
        history: Character action histories.
        adaptation_cooldowns: Tracking adaptation cooldowns.
        _stats: Usage statistics.
    """
    
    def __init__(
        self,
        max_history: int = MAX_HISTORY_PER_CHARACTER,
        failure_window: int = FAILURE_WINDOW,
        failure_threshold: int = FAILURE_THRESHOLD,
    ):
        """Initialize the LearningSystem.
        
        Args:
            max_history: Maximum actions to track per character.
            failure_window: Window size for failure detection.
            failure_threshold: Failures in window to trigger adaptation.
        """
        self.max_history = max_history
        self.failure_window = failure_window
        self.failure_threshold = failure_threshold
        
        # Character action histories: char_id → deque of action records
        self.history: Dict[str, Deque[Dict[str, Any]]] = defaultdict(
            lambda: deque(maxlen=self.max_history)
        )
        
        # Adaptation cooldowns: char_id → last_adaptation_tick
        self.adaptation_cooldowns: Dict[str, int] = {}
        
        # Cached failure counts: char_id → {action_type → count}
        self._failure_counts: Dict[str, Dict[str, int]] = defaultdict(
            lambda: defaultdict(int)
        )
        
        self._stats: Dict[str, int] = {
            "outcomes_recorded": 0,
            "adaptations_triggered": 0,
            "adaptations_cooldown": 0,
        }
    
    def record_outcome(
        self,
        character_id: str,
        action_type: str,
        success: bool,
        current_tick: int = 0,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record action outcome for a character.
        
        Args:
            character_id: Character that performed action.
            action_type: Type of action (attack, negotiate, etc.).
            success: Whether action succeeded.
            current_tick: Current simulation tick.
            details: Optional action details.
        """
        record = {
            "action": action_type,
            "success": success,
            "tick": current_tick,
            "details": details or {},
        }
        
        self.history[character_id].append(record)
        
        # Update failure counts
        if not success:
            self._failure_counts[character_id][action_type] += 1
        else:
            # Reset failure count on success
            self._failure_counts[character_id][action_type] = max(
                0, self._failure_counts[character_id][action_type] - 1
            )
        
        self._stats["outcomes_recorded"] += 1
    
    def should_change_strategy(
        self,
        character_id: str,
        action_type: str,
        current_tick: int = 0,
    ) -> bool:
        """Check if character should change strategy for an action type.
        
        Strategy change is triggered when:
        - Recent failures exceed threshold
        - Character is not on adaptation cooldown
        
        Args:
            character_id: Character to check.
            action_type: Action type to evaluate.
            current_tick: Current simulation tick.
            
        Returns:
            True if strategy should change.
        """
        # Check cooldown
        last_adaptation = self.adaptation_cooldowns.get(character_id, -ADAPTATION_COOLDOWN)
        if current_tick - last_adaptation < ADAPTATION_COOLDOWN:
            self._stats["adaptations_cooldown"] += 1
            return False
        
        # Check failure count
        recent_failures = self._count_recent_failures(
            character_id, action_type
        )
        
        return recent_failures >= self.failure_threshold
    
    def adapt_intent(
        self,
        character_id: str,
        intent: Dict[str, Any],
        current_tick: int = 0,
    ) -> Dict[str, Any]:
        """Adapt an intent based on learning history.
        
        If character has recent failures with this intent type,
        reduce priority and add reasoning.
        
        Args:
            character_id: Character generating intent.
            intent: Original intent dict.
            current_tick: Current simulation tick.
            
        Returns:
            Adapted intent dict.
        """
        if intent is None:
            return None
        
        action_type = intent.get("type", "")
        if not action_type:
            return intent
        
        adapted = dict(intent)
        
        # Check for recent failures
        recent_failures = self._count_recent_failures(
            character_id, action_type
        )
        
        if recent_failures > 0:
            # Apply priority penalty
            original_priority = adapted.get("priority", 5.0)
            penalty = recent_failures * PRIORITY_PENALTY
            new_priority = max(MIN_ACTION_PRIORITY, original_priority - penalty)
            adapted["priority"] = new_priority
            adapted["adapted_priority"] = True
            adapted["recent_failures"] = recent_failures
            adapted["reasoning"] = (
                f"{adapted.get('reasoning', '')} "
                f"[Learning: {recent_failures} recent failures, "
                f"priority reduced from {original_priority:.1f} to {new_priority:.1f}]"
            )
        
        return adapted
    
    def suggest_alternative(
        self,
        character_id: str,
        current_action_type: str,
    ) -> Optional[str]:
        """Suggest an alternative action type for a character.
        
        Based on historical success rates, suggest a different action
        type that the character has had success with.
        
        Args:
            character_id: Character to analyze.
            current_action_type: Current action type to replace.
            
        Returns:
            Alternative action type, or None if none found.
        """
        history = list(self.history.get(character_id, []))
        if not history:
            return None
        
        # Count successes by action type
        success_rates: Dict[str, Tuple[int, int]] = defaultdict(
            lambda: (0, 0)
        )
        
        for record in history:
            action = record["action"]
            if action == current_action_type:
                continue  # Skip current failing action
            
            successes, attempts = success_rates[action]
            attempts += 1
            if record["success"]:
                successes += 1
            success_rates[action] = (successes, attempts)
        
        # Find best alternative (highest success rate with some attempts)
        best_alternative: Optional[str] = None
        best_rate = 0.0
        
        for action, (successes, attempts) in success_rates.items():
            if attempts >= 2:  # Minimum attempts for reliable data
                rate = successes / attempts
                if rate > best_rate:
                    best_rate = rate
                    best_alternative = action
        
        return best_alternative
    
    def _count_recent_failures(
        self,
        character_id: str,
        action_type: str,
    ) -> int:
        """Count recent failures for character's action type.
        
        Args:
            character_id: Character to analyze.
            action_type: Action type to count.
            
        Returns:
            Number of recent failures.
        """
        history = list(self.history.get(character_id, []))
        
        failures = 0
        for record in history[-self.failure_window:]:
            if record["action"] == action_type and not record["success"]:
                failures += 1
        
        return failures
    
    def get_failure_counts(
        self,
        character_id: str,
    ) -> Dict[str, int]:
        """Get failure counts for a character.
        
        Args:
            character_id: Character to query.
            
        Returns:
            Dict of action_type → failure count.
        """
        return dict(self._failure_counts.get(character_id, {}))
    
    def clear_history(
        self,
        character_id: Optional[str] = None,
    ) -> None:
        """Clear learning history.
        
        Args:
            character_id: Optional specific character to clear.
                         If None, clears all history.
        """
        if character_id:
            self.history.pop(character_id, None)
            self._failure_counts.pop(character_id, None)
            self.adaptation_cooldowns.pop(character_id, None)
        else:
            self.history.clear()
            self._failure_counts.clear()
            self.adaptation_cooldowns.clear()
    
    def get_stats(self) -> Dict[str, Any]:
        """Get learning system statistics.
        
        Returns:
            Stats dict.
        """
        return {
            **self._stats,
            "tracked_characters": len(self.history),
            "total_records": sum(len(h) for h in self.history.values()),
        }
    
    def reset(self) -> None:
        """Reset all learning data and statistics."""
        self.clear_history()
        self._stats = {
            "outcomes_recorded": 0,
            "adaptations_triggered": 0,
            "adaptations_cooldown": 0,
        }