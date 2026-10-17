from __future__ import annotations

import json
from typing import Any, Dict
from app.prompts import prompt_template

_PROMPT_1 = prompt_template('rpg.narration_combat_prompt.build_combat_narration_prompt', "1", 'You are the narration layer for a deterministic RPG combat system.\n\nYou MUST narrate only the resolved combat facts provided below.\nYou MUST NOT invent combat outcomes.\nYou MUST NOT decide hits, misses, damage, defeat, loot, death, or turn order.\nYou MUST NOT mention JSON, system, prompt, contract, simulation, validation, or LLM.\n{v0}\nReturn strict JSON with exactly these keys:\n{{\n  "format_version": "rpg_narration_v2",\n  "narration": "2-5 sentences of grounded combat narration.",\n  "action": "Short resolved outcome, not the player\'s command. If party_defeated is true, explicitly include defeated/downed/overwhelmed/fall.",\n  "npc": {{"speaker": "", "line": ""}},\n  "reward": "",\n  "followup_hooks": []\n}}\n\nCombat contract:\n{v1}\n')
_PROMPT_2 = prompt_template('rpg.narration_combat_prompt.party_defeat_instruction', "1", (
    "\nSPECIAL REQUIREMENT: The party_defeated fact is true. "
            "Your narration MUST explicitly say that you/the party are defeated, downed, overwhelmed, or fall. "
            "Do not only imply defeat with phrases like 'the fight ends' or 'the final blow lands'.\n"
))


def build_combat_narration_prompt(contract: Dict[str, Any]) -> str:
    compact = json.dumps(contract, ensure_ascii=False, sort_keys=True)
    facts = raw_facts if isinstance(raw_facts := contract.get("facts"), dict) else {}
    party_defeat_instruction = ""
    if facts.get("party_defeated") is True:
        party_defeat_instruction = (
            _PROMPT_2.text
        )

    return _PROMPT_1.format(v0=(party_defeat_instruction), v1=(compact))