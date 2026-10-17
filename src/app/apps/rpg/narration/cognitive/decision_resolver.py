"""Decision Resolver — Tier 12: Decision Arbitration Layer.

This module implements Tier 12's Decision Arbitration Layer that resolves
conflicts between the four decision influencers in the cognitive system:
- AgentBrain (rules-based)
- IntentEnrichment (LLM-assisted)
- LearningSystem (history-based)
- Identity/Reputation (social dynamics)

Problem:
    These systems can produce contradictory recommendations:
    - AgentBrain: "attack"
    - Learning: "attacks failing → reduce priority"
    - Identity: "target is ally → avoid attack"
    - LLM: "revenge → increase priority"
    
    Result: jittery, inconsistent NPC behavior

Solution:
    DecisionResolver provides a final authority that weighs all inputs
    using a scoring algorithm with configurable weights, producing
    a single coherent decision.

Usage:
    resolver = DecisionResolver()
    final_intent = resolver.resolve(base_intent, enriched_intent, character)

Architecture:
    Scoring Formula:
    final_priority = (
        base_priority * w_base
        + llm_priority * w_llm
        + learning_penalty * w_learning
        + reputation_modifier * w_reputation
    )
    
    Weights default to values that prioritize rules over LLM,
    but learning history has the highest weight.

Design Rules:
    - All input sources are scored and weighted
    - Learning history has highest weight (1.2)
    - LLM has moderate weight (0.7) - useful but not authoritative
    - Base rules have high weight (1.0) - foundational behavior
    - Reputation modifies based on social standing
    - Final priority is clamped to 0-10 range
    - Deterministic given same inputs
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Default weights for decision components
DEFAULT_WEIGHTS = {
    "base": 1.0,       # AgentBrain rules
    "llm": 0.7,        # LLM enrichment
    "learning": 1.2,   # Learning history (highest weight)
    "reputation": 1.0, # Social/reputation standing
}

# Priority bounds
MIN_PRIORITY = 0.0
MAX_PRIORITY = 10.0


class DecisionResolver:
    """Resolves conflicting decision inputs into a single coherent intent.
    
    The DecisionResolver acts as the final authority in the cognitive
    decision pipeline. It takes inputs from multiple subsystems and
    produces a unified decision using weighted scoring.
    
    Usage:
        resolver = DecisionResolver()
        final_intent = resolver.resolve(
            base_intent,
            enriched_intent,
            character,
        )
    
    Attributes:
        weights: Weight configuration for scoring components.
    """
    
    def __init__(self, weights: Optional[Dict[str, float]] = None):
        """Initialize the DecisionResolver.
        
        Args:
            weights: Custom weight configuration. Uses defaults if None.
        """
        self.weights = dict(DEFAULT_WEIGHTS)
        if weights:
            self.weights.update(weights)
        
        self._stats = {
            "resolutions": 0,
            "conflicts_detected": 0,
            "overrides_applied": 0,
        }
    
    def resolve(
        self,
        base_intent: Dict[str, Any],
        enriched_intent: Dict[str, Any],
        character: Any,
    ) -> Dict[str, Any]:
        """Resolve conflicting decision inputs into final intent.
        
        This method takes the base intent from AgentBrain and the
        enriched intent from LLM/Learning/Identity systems, then
        combines them using weighted scoring.
        
        Args:
            base_intent: Original intent from AgentBrain.decide().
            enriched_intent: Enriched intent from cognitive pipeline.
            character: Character object with learning/identity data.
            
        Returns:
            Final resolved intent dict with authoritative priority.
        """
        self._stats["resolutions"] += 1
        
        if base_intent is None:
            return enriched_intent
        
        if enriched_intent is None:
            return base_intent
        
        # Detect conflict between base and enriched
        base_priority = base_intent.get("priority", 5.0)
        enriched_priority = enriched_intent.get("priority", 5.0)
        
        if abs(base_priority - enriched_priority) > 3.0:
            self._stats["conflicts_detected"] += 1
            logger.debug(
                f"Decision conflict detected: "
                f"base={base_priority:.1f} vs enriched={enriched_priority:.1f}"
            )
        
        # Calculate weighted scores
        scores = self._calculate_scores(
            base_intent, enriched_intent, character
        )
        
        # Compute final priority
        final_priority = self._compute_final_priority(scores)
        
        # Create resolved intent
        resolved = dict(enriched_intent)
        
        original_priority = enriched_priority
        resolved["priority"] = max(
            MIN_PRIORITY, min(MAX_PRIORITY, final_priority)
        )
        
        if abs(resolved["priority"] - original_priority) > 1.0:
            self._stats["overrides_applied"] += 1
        
        resolved["resolved_by_arbiter"] = True
        resolved["base_priority"] = base_priority
        resolved["enriched_priority"] = original_priority
        resolved["arbitration_scores"] = scores
        resolved["reasoning"] = (
            f"{resolved.get('reasoning', '')} "
            f"[Arbiter: base={base_priority:.1f}, "
            f"enriched={original_priority:.1f}, "
            f"final={resolved['priority']:.1f}]"
        )
        
        return resolved
    
    def _calculate_scores(
        self,
        base_intent: Dict[str, Any],
        enriched_intent: Dict[str, Any],
        character: Any,
    ) -> Dict[str, float]:
        """Calculate individual component scores.
        
        Args:
            base_intent: Base intent from AgentBrain.
            enriched_intent: Enriched intent.
            character: Character object.
            
        Returns:
            Dict of component_name -> score value.
        """
        scores = {}
        
        # Base intent weight
        scores["base"] = base_intent.get("priority", 5.0)
        
        # LLM intent weight
        scores["llm"] = enriched_intent.get("priority", 5.0)
        
        # Learning modifier (penalty for repeated failures)
        learning_penalty = self._get_learning_penalty(
            base_intent, character
        )
        scores["learning"] = -learning_penalty
        
        # Reputation modifier
        rep_mod = self._get_reputation_modifier(
            base_intent, character
        )
        scores["reputation"] = rep_mod
        
        return scores
    
    def _get_learning_penalty(
        self,
        intent: Dict[str, Any],
        character: Any,
    ) -> float:
        """Get learning-based penalty for intent type.
        
        Penalty is based on recent failures with this action type.
        
        Args:
            intent: Intent dict with type field.
            character: Character with learning data.
            
        Returns:
            Penalty value (higher = more failures).
        """
        if character is None:
            return 0.0
        
        char_id = getattr(character, "id", None)
        if char_id is None:
            return 0.0
        
        intent_type = intent.get("type", "")
        
        # Access learning system if available
        learning = getattr(character, "learning", None)
        if learning and hasattr(learning, "get_failure_counts"):
            counts = learning.get_failure_counts(char_id)
            return counts.get(intent_type, 0) * 0.5
        
        # Check for recent_failures in intent
        recent_failures = intent.get("recent_failures", 0)
        adapted = intent.get("adapted_priority", False)
        if adapted:
            return recent_failures * 0.5
        
        return 0.0
    
    def _get_reputation_modifier(
        self,
        intent: Dict[str, Any],
        character: Any,
    ) -> float:
        """Get reputation-based modifier for intent.
        
        Modifier is based on relationship with target.
        Negative modifier for hostile actions against allies,
        positive modifier for hostile actions against enemies.
        
        Args:
            intent: Intent dict with target field.
            character: Character with identity/reputation data.
            
        Returns:
            Reputation modifier value (float).
        """
        if character is None:
            return 0.0
        
        target = intent.get("target")
        if target is None:
            return 0.0
        
        intent_type = intent.get("type", "")
        
        # Determine if action is hostile
        hostile_actions = {"attack", "attack_target", "betray", "steal", "destroy"}
        is_hostile = intent_type in hostile_actions
        
        identity = getattr(character, "identity", None)
        if identity and hasattr(identity, "get_reputation"):
            rep = identity.get_reputation(getattr(character, "id", ""), target)
            # Ensure we get a float, not a MagicMock
            rep = float(rep) if not isinstance(rep, float) else rep
            
            if is_hostile:
                return -rep * 2.0
            else:
                return rep
        else:
            # Check for relationships on character
            relationships = getattr(character, "relationships", {})
            if isinstance(relationships, dict):
                rep = relationships.get(target, 0.0)
            else:
                rep = 0.0
            rep = float(rep) if not isinstance(rep, (int, float)) else rep
            
            if is_hostile:
                return -rep * 2.0
            else:
                return rep
        
        return 0.0
    
    def _compute_final_priority(
        self,
        scores: Dict[str, float],
    ) -> float:
        """Compute final priority from component scores.
        
        Args:
            scores: Dict of component_name -> score value.
            
        Returns:
            Final priority value.
        """
        final_priority = 0.0
        
        for component, score in scores.items():
            weight = self.weights.get(component, 1.0)
            final_priority += score * weight
        
        # Normalize by total weight
        total_weight = sum(self.weights.values())
        if total_weight > 0:
            final_priority /= total_weight
        
        return max(MIN_PRIORITY, min(MAX_PRIORITY, final_priority))
    
    def get_stats(self) -> Dict[str, int]:
        """Get resolver statistics.
        
        Returns:
            Stats dict.
        """
        return dict(self._stats)
    
    def reset(self) -> None:
        """Reset resolver statistics."""
        self._stats = {
            "resolutions": 0,
            "conflicts_detected": 0,
            "overrides_applied": 0,
        }