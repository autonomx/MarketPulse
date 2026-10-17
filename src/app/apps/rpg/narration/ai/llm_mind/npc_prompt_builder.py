from __future__ import annotations

from typing import Any, Dict, List
from app.prompts import prompt_template

_PROMPT_1 = prompt_template('rpg.ai_llm_mind_npc_prompt_builder.build_decision_prompt', "1", 'NPC: {v0}\nBeliefs: {v1}\nMemory: {v2}\nGoals: {v3}\nContext keys: {v4}\n')


class NPCPromptBuilder:
    def build_decision_prompt(
        self,
        npc_context: Dict[str, Any],
        belief_summary: Dict[str, Dict[str, float]],
        memory_summary: List[Dict[str, Any]],
        goals: List[Dict[str, Any]],
        simulation_state: Dict[str, Any],
    ) -> str:
        npc_name = str(npc_context.get("name") or npc_context.get("npc_id") or "Unknown NPC")
        return (
            _PROMPT_1.format(v0=(npc_name), v1=(belief_summary), v2=(memory_summary), v3=(goals), v4=(sorted((simulation_state or {}).keys())))
        )