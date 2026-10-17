"""Emotion Modifier — Tier 13: Emotional + Experiential Layer.

This module implements Tier 13's Emotional State Model that maps
character emotions to decision modifiers, creating believable
and emotionally consistent NPC behavior.

Problem:
    NPCs make mechanically optimal decisions regardless of emotional state.
    An angry NPC shouldn't calmly negotiate.
    A fearful NPC shouldn't bravely face danger.
    
    Result: behavior is logical but not believable.

Solution:
    EmotionModifier tracks emotional states and applies modifiers
    to NPC decisions, priorities, and dialogue based on current emotions.

    Emotional State Model:
    - anger: Increases aggression, decreases diplomacy
    - fear: Increases avoidance, decreases confrontation
    - trust: Increases cooperation, decreases betrayal
    - sadness: Decreases initiative, increases passivity
    - joy: Increases risk-taking, increases social behavior

Usage:
    modifier = EmotionModifier()
    modified_intent = modifier.apply(character, base_intent)

Architecture:
    Emotion -> Decision Mapping:
    anger -> aggression (+40%), diplomacy (-30%), revenge (+50%)
    fear -> avoidance (+40%), caution (+30%), flight (+50%)
    trust -> cooperation (+30%), loyalty (+20%), sharing (+25%)
    sadness -> withdrawal (+30%), help-seeking (-20%), passivity (+40%)
    joy -> risk-taking (+30%), generosity (+20%), socializing (+25%)

    Emotion -> Dialogue Mapping:
    anger -> terse, aggressive, threatening language
    fear -> hesitant, uncertain, pleading language
    trust -> warm, cooperative, honest language
    sadness -> subdued, melancholic, withdrawn language
    joy -> enthusiastic, positive, engaging language

Design Rules:
    - Every emotion has quantifiable decision impact
    - Emotions decay over time (handled by emotion_system)
    - Extreme emotions can block certain actions entirely
    - Multiple emotions can combine for complex effects
    - Emotional memory persists for important events
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Tier 14 Fix: Emotional Differentiation Drift
# Prevents emotional homogenization over long runs

def apply_personality_bias(emotions: Dict[str, float], personality: Dict[str, float]) -> Dict[str, float]:
    """Apply personality-based emotional bias to prevent homogenization.
    
    Each character's personality adds a slight bias to their emotions,
    ensuring characters stay emotionally distinct even after many events.
    
    Args:
        emotions: Current emotional state.
        personality: Personality traits that influence emotional responses.
        
    Returns:
        Updated emotional state with personality bias applied.
    """
    result = dict(emotions)
    for e in result:
        bias = personality.get(e, 0.0)
        result[e] = max(0.0, min(1.0, result[e] + bias * 0.1))
    return result


def inject_variance(emotions: Dict[str, float], magnitude: float = 0.05) -> Dict[str, float]:
    """Inject small random variance to prevent emotional flattening.
    
    Adds a noise floor to emotions to prevent everyone from converging
    to the same emotional averages over time.
    
    Args:
        emotions: Current emotional state.
        magnitude: Maximum variance to inject (default 0.05).
        
    Returns:
        Emotional state with injected variance.
    """
    result = dict(emotions)
    for e in result:
        result[e] = max(0.0, min(1.0, result[e] + random.uniform(-magnitude, magnitude)))
    return result

# Default emotion thresholds
ANGER_THRESHOLD = 0.5
FEAR_THRESHOLD = 0.5
TRUST_THRESHOLD = 0.3
SADNESS_THRESHOLD = 0.4
JOY_THRESHOLD = 0.4

# Action blocking thresholds (0.0-1.0)
BLOCKING_THRESHOLDS = {
    "fear_block_confront": 0.8,
    "sadness_block_initiative": 0.8,
    "anger_block_diplomacy": 0.7,
}

# Emotion to decision modifier mapping
EMOTION_DECISION_MODIFIERS = {
    "anger": {
        "aggression": 0.4,
        "diplomacy": -0.3,
        "revenge": 0.5,
        "risk_tolerance": 0.2,
        "patience": -0.4,
    },
    "fear": {
        "avoidance": 0.4,
        "caution": 0.3,
        "flight": 0.5,
        "help_seeking": 0.3,
        "risk_tolerance": -0.4,
    },
    "trust": {
        "cooperation": 0.3,
        "loyalty": 0.2,
        "sharing": 0.25,
        "diplomacy": 0.2,
        "patience": 0.1,
    },
    "sadness": {
        "withdrawal": 0.3,
        "help_seeking": -0.2,
        "passivity": 0.4,
        "risk_tolerance": -0.3,
        "socializing": -0.3,
    },
    "joy": {
        "risk_tolerance": 0.3,
        "generosity": 0.2,
        "socializing": 0.25,
        "cooperation": 0.15,
        "creativity": 0.2,
    },
    "grief": {
        "withdrawal": 0.4,
        "vengeance": 0.3,
        "memorializing": 0.5,
        "risk_tolerance": -0.2,
    },
    "guilt": {
        "reparation": 0.4,
        "avoidance": 0.2,
        "honesty": 0.3,
        "submission": 0.2,
    },
    "pride": {
        "assertiveness": 0.3,
        "risk_tolerance": 0.2,
        "competition": 0.4,
        "diplomacy": -0.1,
    },
}

# Emotion to dialogue style mapping
EMOTION_DIALOGUE_MODIFIERS = {
    "anger": {
        "tone": "aggressive",
        "length": "short",
        "vocabulary": "harsh",
        "directness": 0.8,
        "formality": -0.2,
    },
    "fear": {
        "tone": "hesitant",
        "length": "variable",
        "vocabulary": "uncertain",
        "directness": -0.3,
        "formality": 0.1,
    },
    "trust": {
        "tone": "warm",
        "length": "normal",
        "vocabulary": "honest",
        "directness": 0.2,
        "formality": -0.1,
    },
    "sadness": {
        "tone": "melancholic",
        "length": "short",
        "vocabulary": "subdued",
        "directness": -0.1,
        "formality": 0.0,
    },
    "joy": {
        "tone": "enthusiastic",
        "length": "long",
        "vocabulary": "positive",
        "directness": 0.1,
        "formality": -0.2,
    },
}

# Action type categories for emotion filtering
AGGRESSIVE_ACTIONS = {"attack", "threaten", "intimidate", "destroy", "steal"}
DIPLOMATIC_ACTIONS = {"negotiate", "alliance", "trade", "persuade", "appease"}
AVOIDANCE_ACTIONS = {"flee", "hide", "evade", "retreat", "wait"}
COOPERATIVE_ACTIONS = {"help", "share", "assist", "cooperate", "support"}
REVENGE_ACTIONS = {"revenge", "retaliate", "punish", "counter_attack"}


@dataclass
class EmotionalState:
    """Complete emotional state of a character."""
    
    emotions: Dict[str, float] = field(default_factory=lambda: {
        "anger": 0.0,
        "fear": 0.0,
        "trust": 0.5,
        "sadness": 0.0,
        "joy": 0.0,
        "grief": 0.0,
        "guilt": 0.0,
        "pride": 0.0,
    })
    emotional_volatility: float = 0.5
    emotional_memory: List[Dict[str, Any]] = field(default_factory=list)
    last_update_tick: int = 0
    
    @property
    def dominant_emotion(self) -> str:
        """Get the current dominant emotion."""
        if not self.emotions:
            return "neutral"
        max_emotion = max(self.emotions.items(), key=lambda x: x[1])
        if max_emotion[1] < 0.1:
            return "neutral"
        return max_emotion[0]
    
    def get_dominant_intensity(self) -> float:
        """Get intensity of dominant emotion."""
        if not self.emotions:
            return 0.0
        return max(self.emotions.values())
    
    def is_above_threshold(self, emotion: str, threshold: float) -> bool:
        """Check if specific emotion exceeds threshold."""
        return self.emotions.get(emotion, 0.0) >= threshold
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize emotional state to dict."""
        return {
            "emotions": dict(self.emotions),
            "dominant_emotion": self.dominant_emotion,
            "dominant_intensity": self.get_dominant_intensity(),
            "volatility": self.emotional_volatility,
            "memory_count": len(self.emotional_memory),
        }


@dataclass
class DecisionModification:
    """Result of applying emotional modifiers to a decision."""
    
    original_intent: Dict[str, Any]
    modified_intent: Dict[str, Any]
    emotion_applied: Dict[str, float]
    modifiers_used: List[str]
    was_blocked: bool = False
    blocking_reason: str = ""
    confidence: float = 0.8
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict."""
        return {
            "original_intent": dict(self.original_intent),
            "modified_intent": dict(self.modified_intent),
            "emotions_applied": dict(self.emotion_applied),
            "modifiers_used": list(self.modifiers_used),
            "was_blocked": self.was_blocked,
            "blocking_reason": self.blocking_reason,
            "confidence": self.confidence,
        }


class EmotionModifier:
    """Applies emotional state modifiers to NPC decisions."""
    
    def __init__(self, thresholds: Optional[Dict[str, float]] = None):
        """Initialize the EmotionModifier."""
        self.thresholds = {
            "anger": ANGER_THRESHOLD,
            "fear": FEAR_THRESHOLD,
            "trust": TRUST_THRESHOLD,
            "sadness": SADNESS_THRESHOLD,
            "joy": JOY_THRESHOLD,
        }
        if thresholds:
            self.thresholds.update(thresholds)
        
        self._stats: dict[str, Any] = {
            "modifications_applied": 0,
            "actions_blocked": 0,
            "emotions_influencing": 0,
            "dominant_emotions": {},
        }
    
    def apply(
        self,
        character: Any,
        intent: Dict[str, Any],
        tick: int = 0,
    ) -> DecisionModification:
        """Apply emotional modifiers to a character's intent."""
        self._stats["modifications_applied"] += 1
        
        emotional_state = self._get_emotional_state(character)
        
        # Tier 14 Fix: Force personality bias and variance into the pipeline
        # This prevents emotional homogenization over long runs
        if emotional_state is not None:
            personality = getattr(character, "personality", {})
            emotional_state.emotions = apply_personality_bias(
                emotional_state.emotions,
                personality
            )
            emotional_state.emotions = inject_variance(
                emotional_state.emotions
            )
        if emotional_state is None:
            return DecisionModification(
                original_intent=intent,
                modified_intent=dict(intent),
                emotion_applied={},
                modifiers_used=[],
                confidence=0.5,
            )
        
        dominant = emotional_state.dominant_emotion
        self._stats["dominant_emotions"][dominant] = (
            self._stats["dominant_emotions"].get(dominant, 0) + 1
        )
        
        # Check for action blocking
        blocking_result = self._check_action_blocking(intent, emotional_state)
        if blocking_result["blocked"]:
            self._stats["actions_blocked"] += 1
            return DecisionModification(
                original_intent=intent,
                modified_intent=self._create_blocked_intent(intent),
                emotion_applied=emotional_state.emotions,
                modifiers_used=[],
                was_blocked=True,
                blocking_reason=blocking_result["reason"],
                confidence=0.9,
            )
        
        # Apply emotion-based modifiers
        modified_intent = dict(intent)
        active_emotions = {}
        modifiers_used = []
        
        for emotion, intensity in emotional_state.emotions.items():
            if intensity < self.thresholds.get(emotion, 0.3):
                continue
            
            active_emotions[emotion] = intensity
            self._stats["emotions_influencing"] += 1
            
            emotion_modifiers = EMOTION_DECISION_MODIFIERS.get(emotion, {})
            for modifier_key, modifier_value in emotion_modifiers.items():
                modified_intent = self._apply_single_modifier(
                    modified_intent, modifier_key, modifier_value, intensity
                )
                if modifier_key not in modifiers_used:
                    modifiers_used.append(modifier_key)
        
        # Adjust priority based on dominant emotion
        modified_intent["priority"] = self._adjust_priority(
            modified_intent.get("priority", 5.0),
            emotional_state,
        )
        
        # Add emotional reasoning to intent
        modified_intent["emotional_context"] = {
            "dominant_emotion": dominant,
            "dominant_intensity": emotional_state.get_dominant_intensity(),
            "active_emotions": dict(active_emotions),
            "emotion_influence": sum(active_emotions.values()) / max(len(active_emotions), 1),
        }
        
        emotional_state.last_update_tick = tick
        
        total_emotion = sum(active_emotions.values())
        confidence = min(0.95, 0.5 + total_emotion * 0.1) if active_emotions else 0.5
        
        return DecisionModification(
            original_intent=intent,
            modified_intent=modified_intent,
            emotion_applied=emotional_state.emotions,
            modifiers_used=modifiers_used,
            was_blocked=False,
            confidence=confidence,
        )
    
    def _get_emotional_state(self, character: Any) -> Optional[EmotionalState]:
        """Extract emotional state from character."""
        if character is None:
            return None
        
        emotional_state = getattr(character, "emotional_state", None)
        if isinstance(emotional_state, EmotionalState):
            return emotional_state
        
        if isinstance(emotional_state, dict):
            state = EmotionalState()
            state.emotions.update(emotional_state.get("emotions", {}))
            state.emotions.update(emotional_state)
            return state
        
        emotions = getattr(character, "emotions", None)
        if isinstance(emotions, dict):
            state = EmotionalState()
            state.emotions.update(emotions)
            return state
        
        return None
    
    def _check_action_blocking(
        self,
        intent: Dict[str, Any],
        emotional_state: EmotionalState,
    ) -> Dict[str, Any]:
        """Check if intent should be blocked due to extreme emotion."""
        action_type = intent.get("type", "").lower()
        
        # Very fearful NPCs avoid confrontation
        if action_type in AGGRESSIVE_ACTIONS | REVENGE_ACTIONS:
            if emotional_state.is_above_threshold("fear", BLOCKING_THRESHOLDS["fear_block_confront"]):
                return {"blocked": True, "reason": "Fear prevents confrontation"}
        
        # Very sad NPCs don't take initiative
        if action_type in COOPERATIVE_ACTIONS | AGGRESSIVE_ACTIONS:
            if emotional_state.is_above_threshold("sadness", BLOCKING_THRESHOLDS["sadness_block_initiative"]):
                return {"blocked": True, "reason": "Sadness prevents action"}
        
        # Very angry NPCs can't be diplomatic
        if action_type in DIPLOMATIC_ACTIONS:
            if emotional_state.is_above_threshold("anger", BLOCKING_THRESHOLDS["anger_block_diplomacy"]):
                return {"blocked": True, "reason": "Anger prevents diplomacy"}
        
        return {"blocked": False, "reason": ""}
    
    def _create_blocked_intent(self, intent: Dict[str, Any]) -> Dict[str, Any]:
        """Create a blocked intent that waits for emotion to pass."""
        blocked_intent = dict(intent)
        blocked_intent["blocked"] = True
        blocked_intent["block_reason"] = "emotional_override"
        blocked_intent["priority"] = 0.0
        return blocked_intent
    
    def _apply_single_modifier(
        self,
        intent: Dict[str, Any],
        modifier_key: str,
        modifier_value: float,
        intensity: float,
    ) -> Dict[str, Any]:
        """Apply a single emotion modifier to the intent."""
        modified_intent = dict(intent)
        effective_modifier = modifier_value * intensity
        
        if modifier_key in ("aggression", "cooperation", "avoidance"):
            current_priority = modified_intent.get("priority", 5.0)
            new_priority = current_priority + effective_modifier * 2
            modified_intent["priority"] = max(0.0, min(10.0, new_priority))
        
        modifiers = modified_intent.get("emotion_modifiers", {})
        modifiers[modifier_key] = modifiers.get(modifier_key, 0.0) + effective_modifier
        modified_intent["emotion_modifiers"] = modifiers
        
        return modified_intent
    
    def _adjust_priority(
        self,
        base_priority: float,
        emotional_state: EmotionalState,
    ) -> float:
        """Adjust intent priority based on overall emotional state."""
        adjustment = 0.0
        adjustment += emotional_state.emotions.get("anger", 0.0) * 1.5
        adjustment += emotional_state.emotions.get("fear", 0.0) * 1.0
        adjustment -= emotional_state.emotions.get("sadness", 0.0) * 1.5
        adjustment += emotional_state.emotions.get("joy", 0.0) * 0.5
        return max(0.0, min(10.0, base_priority + adjustment))
    
    def get_stats(self) -> Dict[str, Any]:
        """Get emotion modifier statistics."""
        return {
            **self._stats,
            "action_block_rate": (
                self._stats["actions_blocked"] / 
                max(self._stats["modifications_applied"], 1)
            ),
        }
    
    def reset(self) -> None:
        """Reset modifier statistics."""
        self._stats = {
            "modifications_applied": 0,
            "actions_blocked": 0,
            "emotions_influencing": 0,
            "dominant_emotions": {},
        }