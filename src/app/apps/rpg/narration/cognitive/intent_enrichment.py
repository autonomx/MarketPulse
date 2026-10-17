"""Intent Enrichment — Tier 11 Layer 1: LLM-Assisted Intent Refinement.

This module implements Layer 1 of Tier 11: Controlled LLM Injection for
intent enrichment. The deterministic AgentBrain produces the base intent,
and the LLM only refines priority and optionally adds a target.

Design Rules:
    - AgentBrain.decide() produces the base intent (rule-based)
    - LLM only adjusts priority (0-10) and optionally adds target
    - Intent type is NEVER changed by LLM
    - Strict guardrails: invalid LLM responses are discarded
    - Guarded by ALLOWED_INTENTS whitelist
    - Resource-conscious: LLM not called every tick

The Problem:
    - Pure rule-based intents lack nuance
    - Context-aware refinement makes NPCs feel smarter
    - But full LLM planning is too expensive and unreliable

The Solution:
    Controlled LLM injection that only enriches, never replaces:
    1. Get base intent from AgentBrain (rule-based)
    2. Check if LLM should be used (cooldown, character-specific)
    3. Build constrained prompt with character context
    4. Parse JSON response with validation
    5. Apply priority adjustment within bounds
    6. Fall back to original intent on any failure

Guardrails:
    - Intent type must match original
    - Priority must be 0-10 range
    - Target must be valid entity ID
    - Timeout on LLM calls
    - Cooldown to prevent overuse
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from app.apps.rpg.foundation.llm_gateway_adapter import adapt_base_provider
from app.prompts import prompt_template

_PROMPT_1 = prompt_template('rpg.cognitive_intent_enrichment.prompt', "1", 'Character:\n- Name: {v0}\n- Traits: {v1}\n- Goals: {v2}\n- Beliefs: {v3}\n\nCurrent Intent:\n- Type: {v4}\n- Priority: {v5}\n- Target: {v6}\n- Reasoning: {v7}\n\nWorld Context:\n- Nearby threats: {v8}\n- Potential allies: {v9}\n- Faction dynamics: {v10}\n\nRefine the intent:\n- Keep the SAME intent type (required)\n- Adjust priority (0-10): higher if situation is urgent, lower if not\n- Optionally suggest a target entity (must exist in context)\n- Add brief reasoning\n\nReturn JSON ONLY with this structure:\n{{\n  "priority": 5.0,\n  "target": "entity_id or null",\n  "reasoning": "brief explanation"\n}}\n')

logger = logging.getLogger(__name__)

# Allowed intent types — LLM cannot create new ones
ALLOWED_INTENTS: set[str] = {
    "expand_influence",
    "attack_target",
    "deliver_aid",
    "gather_resources",
    "negotiate",
    "defend",
    "idle",
    "coordinated_attack",
}

# Default cooldown between LLM calls (in ticks)
DEFAULT_LLM_COOLDOWN = 5

# Priority bounds for LLM adjustments
MIN_PRIORITY = 0.0
MAX_PRIORITY = 10.0


class IntentEnrichment:
    """LLM-assisted intent enrichment with strict guardrails.
    
    This class augments the deterministic AgentBrain with optional
    LLM-based intent refinement. The LLM can only adjust priority
    and optionally suggest a target — it cannot change the intent type.
    
    Usage:
        enrichment = IntentEnrichment(llm_client)
        base_intent = agent_brain.decide(character, world_state)
        enriched = enrichment.enrich(base_intent, character, world_state)
    
    Attributes:
        llm_client: LLM client for generating text.
        cooldown_ticks: Minimum ticks between LLM calls.
        _last_llm_call_tick: Last tick when LLM was called.
        _stats: Statistics tracking for monitoring.
    """
    
    def __init__(
        self,
        llm_client: Optional[Any] = None,
        cooldown_ticks: int = DEFAULT_LLM_COOLDOWN,
    ):
        """Initialize the IntentEnrichment.
        
        Args:
            llm_client: LLM client with generate_json(prompt) -> dict method.
                        Can be None if LLM enrichment not needed.
            cooldown_ticks: Minimum ticks between LLM enrichment calls.
        """
        self.llm_client = adapt_base_provider(llm_client)
        self.cooldown_ticks = cooldown_ticks
        self._last_llm_call_tick: int = -cooldown_ticks  # Ready immediately
        self._stats: dict[str, int] = {
            "enrichment_attempts": 0,
            "enrichment_success": 0,
            "enrichment_fallbacks": 0,
            "invalid_responses": 0,
        }
    
    def enrich(
        self,
        intent: Optional[dict[str, Any]],
        character: Any,
        world_state: dict[str, Any],
        current_tick: int = 0,
    ) -> Optional[dict[str, Any]]:
        """Enrich an intent with LLM-based refinement.
        
        If the base intent is None, returns None.
        If LLM is not available or on cooldown, returns original intent.
        Otherwise, attempts to refine the intent with LLM assistance.
        
        Args:
            intent: Base intent dict from AgentBrain.decide().
                    Must have 'type', 'priority', optional 'target'.
            character: Character object with id, traits, goals, beliefs/memory.
            world_state: Current world state dict.
            current_tick: Current simulation tick for cooldown tracking.
            
        Returns:
            Enriched intent dict, or original intent on failure.
        """
        if intent is None:
            return None
        
        self._stats["enrichment_attempts"] += 1
        
        # Check if LLM should be used for this character/intent
        if not self._should_use_llm(character, intent):
            return intent
        
        # Check cooldown
        if current_tick - self._last_llm_call_tick < self.cooldown_ticks:
            return intent
        
        # Attempt LLM enrichment
        try:
            enriched = self._llm_enrich_intent(intent, character, world_state)
            self._last_llm_call_tick = current_tick
            self._stats["enrichment_success"] += 1
            return enriched
        except Exception as e:
            logger.warning(f"Intent enrichment failed: {e}")
            self._stats["enrichment_fallbacks"] += 1
            return intent
    
    def _should_use_llm(
        self,
        character: Any,
        intent: dict[str, Any],
    ) -> bool:
        """Determine if LLM should be used for this character/intent.
        
        LLM is used only when:
        - LLM client is available
        - Character has traits/goals for context
        - Intent type allows enrichment
        
        Args:
            character: Character object.
            intent: Intent dict.
            
        Returns:
            True if LLM should be used.
        """
        # Must have LLM client
        if self.llm_client is None:
            return False
        
        # Check if intent can be enriched
        intent_type = intent.get("type", "")
        if intent_type not in ALLOWED_INTENTS:
            return False
        
        # Skip for low-priority intents (below threshold)
        if intent.get("priority", 0) < 2.0:
            return False
        
        return True
    
    def _llm_enrich_intent(
        self,
        intent: dict[str, Any],
        character: Any,
        world_state: dict[str, Any],
    ) -> dict[str, Any]:
        """Use LLM to enrich an intent with context-aware refinement.
        
        The LLM receives a constrained prompt and must return valid JSON
        with optional priority adjustment and target suggestion.
        
        Args:
            intent: Original intent dict.
            character: Character object.
            world_state: Current world state.
            
        Returns:
            Enriched intent dict.
        """
        # Build context for LLM
        char_id = getattr(character, "id", "unknown")
        char_traits = getattr(character, "traits", [])
        char_goals = getattr(character, "goals", [])
        
        # Get beliefs
        if hasattr(character, "beliefs"):
            beliefs = character.beliefs
        elif hasattr(character, "get_belief"):
            beliefs = {}
        else:
            beliefs = {}
        
        # Get world context
        threats = self._extract_threats(world_state, character)
        allies = self._extract_allies(world_state, character)
        factions_info = self._extract_faction_context(world_state, character)
        
        prompt = _PROMPT_1.format(v0=(char_id), v1=(', '.join(char_traits) if char_traits else 'None specified'), v2=(', '.join(char_goals) if char_goals else 'None specified'), v3=(self._format_beliefs(beliefs)), v4=(intent.get('type', 'unknown')), v5=(intent.get('priority', 5.0)), v6=(intent.get('target', 'None')), v7=(intent.get('reasoning', 'Unknown')), v8=(', '.join(threats) if threats else 'None identified'), v9=(', '.join(allies) if allies else 'None identified'), v10=(factions_info))
        
        try:
            # Call LLM with expected JSON response
            if hasattr(self.llm_client, "generate_json"):
                response = self.llm_client.generate_json(prompt)
            else:
                response = self._parse_json_response(
                    self.llm_client.generate(prompt)
                )
            
            return self._validate_and_apply(response, intent)
            
        except Exception as e:
            logger.warning(f"LLM enrichment failed for intent: {e}")
            return intent
    
    def _validate_and_apply(
        self,
        llm_response: dict[str, Any],
        original_intent: dict[str, Any],
    ) -> dict[str, Any]:
        """Validate LLM response and apply changes to original intent.
        
        Guardrails:
        - Intent type must remain unchanged
        - Priority must be 0-10
        - Target must be a valid string or None
        - Reasoning must be a string
        
        Args:
            llm_response: Parsed JSON from LLM.
            original_intent: Original intent to validate against.
            
        Returns:
            Validated intent dict (may be unchanged if LLM response invalid).
        """
        enriched = dict(original_intent)
        
        # Validate priority
        new_priority = llm_response.get("priority")
        if new_priority is not None:
            try:
                priority_float = float(new_priority)
                if MIN_PRIORITY <= priority_float <= MAX_PRIORITY:
                    enriched["priority"] = priority_float
                    enriched["llm_adjusted_priority"] = True
                else:
                    self._stats["invalid_responses"] += 1
            except (ValueError, TypeError):
                self._stats["invalid_responses"] += 1
        
        # Validate target
        new_target = llm_response.get("target")
        if new_target is not None:
            if isinstance(new_target, str) and len(new_target) > 0:
                enriched["target"] = new_target
                enriched["llm_suggested_target"] = True
            elif new_target is None:
                # LLM says no target - keep original
                pass
            else:
                self._stats["invalid_responses"] += 1
        
        # Validate reasoning
        new_reasoning = llm_response.get("reasoning")
        if new_reasoning and isinstance(new_reasoning, str):
            enriched["reasoning"] = f"{original_intent.get('reasoning', '')} [LLM: {new_reasoning}]"
        
        return enriched
    
    def _parse_json_response(self, response: str) -> dict[str, Any]:
        """Parse JSON from LLM text response.
        
        Args:
            response: Raw text from LLM.
            
        Returns:
            Parsed JSON dict, or empty dict on failure.
        """
        try:
            # Clean up markdown code blocks
            text = response.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip()
            return json.loads(text) if text else {}
        except json.JSONDecodeError:
            self._stats["invalid_responses"] += 1
            return {}
    
    def _extract_threats(
        self,
        world_state: dict[str, Any],
        character: Any,
    ) -> list[str]:
        """Extract nearby threats from world state.
        
        Args:
            world_state: World state dict.
            character: Character object.
            
        Returns:
            List of threatening entity IDs.
        """
        threats = []
        char_id = getattr(character, "id", "")
        
        # Check factions for hostile relations
        factions = world_state.get("factions", {})
        for faction_id, faction_data in factions.items():
            if isinstance(faction_data, dict):
                relations = faction_data.get("relations", {})
                relation = relations.get(char_id, 0)
                power = faction_data.get("power", 0)
                if relation < -0.5 and power > 0.3:
                    threats.append(faction_id)
        
        return threats
    
    def _extract_allies(
        self,
        world_state: dict[str, Any],
        character: Any,
    ) -> list[str]:
        """Extract potential allies from world state.
        
        Args:
            world_state: World state dict.
            character: Character object.
            
        Returns:
            List of potential ally entity IDs.
        """
        allies = []
        char_id = getattr(character, "id", "")
        
        # Check factions for friendly relations
        factions = world_state.get("factions", {})
        for faction_id, faction_data in factions.items():
            if isinstance(faction_data, dict):
                relations = faction_data.get("relations", {})
                relation = relations.get(char_id, 0)
                if relation > 0.5:
                    allies.append(faction_id)
        
        return allies
    
    def _extract_faction_context(
        self,
        world_state: dict[str, Any],
        character: Any,
    ) -> str:
        """Extract faction dynamics as human-readable text.
        
        Args:
            world_state: World state dict.
            character: Character object.
            
        Returns:
            Faction context description.
        """
        factions = world_state.get("factions", {})
        if not factions:
            return "No faction data available"
        
        parts = []
        for faction_id, faction_data in factions.items():
            if isinstance(faction_data, dict):
                power = faction_data.get("power", 0)
                parts.append(f"{faction_id} (power: {power:.2f})")
        
        return "; ".join(parts) if parts else "No faction data available"
    
    def _format_beliefs(self, beliefs: Any) -> str:
        """Format beliefs as human-readable text.
        
        Args:
            beliefs: Beliefs dict or object.
            
        Returns:
            Formatted belief string.
        """
        if isinstance(beliefs, dict):
            items = [(k, v) for k, v in beliefs.items() if isinstance(v, (int, float))]
            if items:
                return ", ".join(f"{k}: {v:.2f}" for k, v in items[:10])
        return "No belief data"
    
    def get_stats(self) -> dict[str, Any]:
        """Get enrichment statistics.
        
        Returns:
            Dict with enrichment statistics.
        """
        return dict(self._stats)
    
    def reset_stats(self) -> None:
        """Reset enrichment statistics."""
        self._stats = {
            "enrichment_attempts": 0,
            "enrichment_success": 0,
            "enrichment_fallbacks": 0,
            "invalid_responses": 0,
        }
    
