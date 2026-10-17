"""Cognitive Layer — Tier 11 Unified Interface.

This module provides the main CognitiveLayer interface that unifies all
Tier 11 cognitive systems. It provides a single entry point for:
- Intent enrichment (LLM-assisted)
- Memory-informed planning
- LLM-enhanced dialogue
- Identity management
- Coalition coordination
- Learning feedback

Usage:
    cognitive = CognitiveLayer(llm_client)
    
    # Integrate with agent decision loop
    intent = brain.decide(character, world_state)
    intent = cognitive.enrich_intent(character, intent, world_state, tick)
    
    # Get dialogue
    dialogue = cognitive.generate_dialogue(speaker, listener, context)
    
    # Record outcomes for learning
    cognitive.record_outcome(character_id, action_type, success, tick)
    
    # Update systems periodically
    cognitive.tick_update(tick)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from .coalition import CoalitionSystem
from .identity import IdentitySystem
from .intent_enrichment import IntentEnrichment
from .learning import LearningSystem

logger = logging.getLogger(__name__)


class CognitiveLayer:
    """Unified interface for Tier 11 cognitive simulation systems.
    
    The CognitiveLayer manages all cognitive systems and provides
    a unified interface for integration with the RPG systems.
    
    Usage:
        cognitive = CognitiveLayer(llm_client)
        
        # In the agent decision loop:
        intent = brain.decide(character, world_state)
        intent = cognitive.process_decision(
            character, intent, world_state, tick
        )
        
        # Record outcomes after execution
        cognitive.record_outcome(char_id, action_type, success, tick)
        
        # Periodic maintenance
        cognitive.tick_update(tick)
    
    Attributes:
        intent_enrichment: LLM-powered intent refinement.
        identity: Persistent identity and reputation tracking.
        coalition: Coalition management for coordinated behavior.
        learning: Outcome-based learning system.
    """
    
    def __init__(
        self,
        llm_client: Optional[Any] = None,
        llm_cooldown: int = 5,
        learning_history: int = 30,
        learning_window: int = 10,
        learning_threshold: int = 3,
    ):
        """Initialize the CognitiveLayer.
        
        Args:
            llm_client: LLM client for intent enrichment.
            llm_cooldown: Ticks between LLM enrichment calls.
            learning_history: Max actions to track per character.
            learning_window: Window for failure detection.
            learning_threshold: Failures to trigger adaptation.
        """
        self.intent_enrichment = IntentEnrichment(
            llm_client=llm_client,
            cooldown_ticks=llm_cooldown,
        )
        self.identity = IdentitySystem()
        self.coalition = CoalitionSystem(identity_system=self.identity)
        self.learning = LearningSystem(
            max_history=learning_history,
            failure_window=learning_window,
            failure_threshold=learning_threshold,
        )
        
        self._stats: Dict[str, int] = {
            "decisions_processed": 0,
            "intents_enriched": 0,
            "outcomes_recorded": 0,
            "coalitions_formed": 0,
        }
    
    def process_decision(
        self,
        character: Any,
        intent: Optional[Dict[str, Any]],
        world_state: Dict[str, Any],
        current_tick: int = 0,
    ) -> Optional[Dict[str, Any]]:
        """Process a decision through the full cognitive pipeline.
        
        This is the main method called in the agent decision loop.
        It enriches the intent with LLM, adapts based on learning,
        and considers coalition actions.
        
        Args:
            character: Character object.
            intent: Base intent from AgentBrain.decide().
            world_state: Current world state.
            current_tick: Current simulation tick.
            
        Returns:
            Final processed intent dict.
        """
        self._stats["decisions_processed"] += 1
        
        if intent is None:
            return None
        
        char_id = getattr(character, "id", "unknown")
        
        # Step 1: Learning-based adaptation
        intent = self.learning.adapt_intent(char_id, intent, current_tick)
        
        # Step 2: Intent enrichment (LLM-assisted)
        enriched = self.intent_enrichment.enrich(
            intent, character, world_state, current_tick
        )
        if enriched is not None:
            intent = enriched
            self._stats["intents_enriched"] += 1
        
        # Step 3: Check for coalition actions
        intent = self._consider_coalition(char_id, intent, world_state)
        
        return intent
    
    def enrich_intent(
        self,
        intent: Optional[Dict[str, Any]],
        character: Any,
        world_state: Dict[str, Any],
        current_tick: int = 0,
    ) -> Optional[Dict[str, Any]]:
        """Enrich intent with LLM assistance.
        
        Args:
            intent: Base intent dict.
            character: Character object.
            world_state: Current world state.
            current_tick: Current simulation tick.
            
        Returns:
            Enriched intent dict.
        """
        return self.intent_enrichment.enrich(
            intent, character, world_state, current_tick
        )
    
    def record_outcome(
        self,
        character_id: str,
        action_type: str,
        success: bool,
        current_tick: int = 0,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record action outcome for learning.
        
        Call this after actions are executed.
        
        Args:
            character_id: Character that acted.
            action_type: Type of action.
            success: Whether action succeeded.
            current_tick: Current simulation tick.
            details: Optional action details.
        """
        self.learning.record_outcome(
            character_id, action_type, success, current_tick, details
        )
        self._stats["outcomes_recorded"] += 1
    
    def generate_dialogue(
        self,
        speaker: Any,
        listener: Optional[Any] = None,
        context: Optional[str] = None,
        force_goal: Optional[str] = None,
    ) -> str:
        """Generate dialogue enhanced with identity and reputation.
        
        Creates dialogue that reflects the speaker's persistent identity,
        reputation with listener, and current goals.
        
        Args:
            speaker: Speaker object.
            listener: Listener object (optional).
            context: Scene context (optional).
            force_goal: Override auto-detected goal (optional).
            
        Returns:
            Generated dialogue string.
        """
        speaker_id = getattr(speaker, "id", "unknown")
        listener_id = getattr(listener, "id", None) if listener else None
        
        # Import and use the dialogue engine
        try:
            from rpg.narrative.dialogue_engine import DialogueEngine
            
            # Create engine with identity context
            engine = DialogueEngine()
            
            # Generate dialogue
            if listener_id:
                line = engine.generate_dialogue(
                    speaker_id, listener_id, force_goal
                )
            else:
                line = engine.generate_dialogue(speaker_id, None, force_goal)
            
            # Enhance with reputation context if available
            if listener_id:
                rep = self.identity.get_reputation(speaker_id, listener_id)
                if abs(rep) > 0.3:
                    tone = "friendly" if rep > 0 else "hostile"
                    line = f"[{tone}] {line}"
            
            # Add rumors if relevant
            if context:
                rumors = self.identity.get_rumors_for(speaker_id)
                if rumors and abs(rep) < 0.3:
                    line += f" (Word is: {rumors[0]})"
            
            return line
            
        except ImportError:
            logger.warning("DialogueEngine not available, using fallback")
            return self._fallback_dialogue(speaker_id, listener_id)
    
    def tick_update(self, current_tick: int = 0) -> Dict[str, Any]:
        """Perform periodic system updates.
        
        Call this once per tick to update all cognitive systems.
        
        Args:
            current_tick: Current simulation tick.
            
        Returns:
            Update results dict.
        """
        updates = {}
        
        # Update identity (fade rumors, decay fame)
        identity_updates = self.identity.tick_update()
        updates["identity"] = identity_updates
        
        # Check coalition stability
        coalition_updates = {"stable": 0, "dissolved": 0}
        for coalition_id in list(self.coalition.coalitions.keys()):
            stable = self.coalition.check_coalition_stability(
                coalition_id, current_tick
            )
            if stable:
                coalition_updates["stable"] += 1
            else:
                coalition_updates["dissolved"] += 1
        updates["coalitions"] = coalition_updates
        
        return updates
    
    def get_stats(self) -> Dict[str, Any]:
        """Get comprehensive system statistics.
        
        Returns:
            Stats dict from all subsystems plus aggregate.
        """
        return {
            **self._stats,
            "intent_enrichment": self.intent_enrichment.get_stats(),
            "identity": self.identity.get_stats(),
            "coalition": self.coalition.get_stats(),
            "learning": self.learning.get_stats(),
        }
    
    def reset(self) -> None:
        """Reset all cognitive systems."""
        self.intent_enrichment.reset_stats()
        self.identity.reset()
        self.coalition.reset()
        self.learning.reset()
        self._stats = {
            "decisions_processed": 0,
            "intents_enriched": 0,
            "outcomes_recorded": 0,
            "coalitions_formed": 0,
        }
    
    def _consider_coalition(
        self,
        faction_id: str,
        intent: Dict[str, Any],
        world_state: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Consider coalition coordination for intent.
        
        If faction is in a coalition, check for coordinated action.
        
        Args:
            faction_id: Faction considering action.
            intent: Current intent dict.
            world_state: World state.
            
        Returns:
            Possibly modified intent with coalition context.
        """
        coalition = self.coalition._get_faction_coalition(faction_id)
        if coalition is None:
            return intent
        
        intent_type = intent.get("type", "")
        if intent_type in ("attack_target",):
            coordinated = self.coalition.get_coordinated_action(
                faction_id, "attack", world_state
            )
            if coordinated:
                intent["coalition_action"] = True
                intent["coalition_id"] = coalition.id
                intent["participants"] = coordinated.get("participants", [])
        
        return intent
    
    def _fallback_dialogue(
        self,
        speaker_id: str,
        listener_id: Optional[str],
    ) -> str:
        """Fallback dialogue when DialogueEngine unavailable.
        
        Args:
            speaker_id: Speaker identifier.
            listener_id: Listener identifier.
            
        Returns:
            Fallback dialogue string.
        """
        if listener_id:
            return f"{speaker_id} acknowledges {listener_id}."
        else:
            return f"{speaker_id} muses to themselves."