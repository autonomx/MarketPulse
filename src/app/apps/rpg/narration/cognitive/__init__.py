"""Cognitive Layer — Tier 11, 12 & 13: Hybrid Cognitive Simulation + Narrative Convergence + Emotional Layer.

This package implements Tier 11, 12, and 13 of the RPG design specification:

Tier 11: Hybrid Cognitive Simulation Engine (augments deterministic core with
controlled LLM-assisted cognitive capabilities)
    - Layer 1: Intent Enrichment (LLM-assisted, not replaced)
    - Layer 2: Memory-Informed Planning
    - Layer 3: LLM-Enhanced Dialogue Engine
    - Layer 4: Persistent Identity System
    - Layer 5: Coalition System
    - Layer 6: Learning Feedback System

Tier 12: Narrative Convergence Engine (System-Level Coherence Control)
    - Decision Arbitration Layer (conflict resolution)
    - Coalition Commitment Lock (prevent intent oscillation)
    - Narrative Gravity (convergence, payoff, resolution)

Tier 13: Emotional + Experiential Layer (Research-Grade Architecture)
    - Resolution Engine (emotionally satisfying resolutions)
    - Emotion Modifier (emotion -> decision/dialogue mapping)
    - Narrative Memory Layer (historical awareness)
    - Narrative Diversity Injection (prevent over-convergence)
    - Player Narrative Override (player as gravitational center)

Tier 14 Fixes: Player Experience & Perception Layer Patches
    - Emotional Differentiation Drift (prevent homogenization)
    - Resolution Entropy Injection (prevent predictability)
    - Contextual Memory Relevance (prevent overfitting)

Design Principles:
    - Deterministic core remains unchanged
    - LLM only injected in 3 constrained layers
    - No LLM planning every tick
    - No LLM mutating world directly
    - Guardrails prevent LLM from breaking game logic
    - Tier 12 adds system-wide coherence control
    - Tier 13 adds emotional depth and continuity
    - Tier 14 patches prevent long-term degradation

Usage:
    # Basic Tier 11
    cognitive = CognitiveLayer(llm_client=llm)
    
    # With Tier 12 components
    resolver = DecisionResolver()
    lock_manager = CoalitionLockManager()
    gravity = NarrativeGravity()
    
    # With Tier 13 components
    resolution = ResolutionEngine()
    emotion_mod = EmotionModifier()
    narrative_memory = NarrativeMemory()
    
    # Full pipeline
    base_intent = brain.decide(character, world_state)
    emotion_result = emotion_mod.apply(character, base_intent)
    enriched = enrichment.enrich(base_intent, character, world_state)
    final = resolver.resolve(base_intent, enriched, character)
    final = lock_manager.enforce_lock(char_id, final, tick)
"""

from __future__ import annotations

from .coalition import CoalitionSystem
from .coalition_lock import CoalitionLock, CoalitionLockManager
from .cognitive_layer import CognitiveLayer

# Tier 12 exports
from .decision_resolver import DecisionResolver
from .emotion_modifier import (
    DecisionModification,
    EmotionalState,
    EmotionModifier,
    apply_personality_bias,
    inject_variance,
)
from .identity import IdentitySystem

# Tier 11 exports
from .intent_enrichment import IntentEnrichment
from .learning import LearningSystem
from .narrative_gravity import NarrativeGravity, StorylineState, StorylineWeight
from .narrative_memory import (
    ArcMemory,
    EmotionalResidue,
    NarrativeMemory,
    filter_memories_by_relevance,
    relevance_score,
)

# Tier 13 exports
from .resolution_engine import ResolutionEngine, ResolutionResult

__all__ = [
    # Tier 11
    "IntentEnrichment",
    "IdentitySystem",
    "CoalitionSystem",
    "LearningSystem",
    "CognitiveLayer",
    # Tier 12
    "DecisionResolver",
    "CoalitionLockManager",
    "CoalitionLock",
    "NarrativeGravity",
    "StorylineState",
    "StorylineWeight",
    # Tier 13
    "ResolutionEngine",
    "ResolutionResult",
    "EmotionModifier",
    "EmotionalState",
    "DecisionModification",
    "NarrativeMemory",
    "ArcMemory",
    "EmotionalResidue",
    # Tier 14 Fixes
    "apply_personality_bias",
    "inject_variance",
    "relevance_score",
    "filter_memories_by_relevance",
]
