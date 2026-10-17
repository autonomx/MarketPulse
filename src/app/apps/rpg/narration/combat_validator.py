from __future__ import annotations

import re
from typing import Any, Dict, List
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_str as _safe_str

DEATH_WORD_RE = re.compile(
    r"\b(dies|dead|killed|slain|lifeless|corpse|finished\s+him|finished\s+her|finished\s+them|death)\b",
    re.I,
)

META_RE = re.compile(
    r"\b(json|contract|simulation|validator|system|prompt|llm|language model)\b",
    re.I,
)


def validate_combat_narration(
    *,
    narration_payload: Dict[str, Any],
    combat_contract: Dict[str, Any],
) -> Dict[str, Any]:
    payload = _safe_dict(narration_payload)
    contract = _safe_dict(combat_contract)
    facts = _safe_dict(contract.get("facts"))

    narration = _safe_str(payload.get("narration"))
    action = _safe_str(payload.get("action"))
    hooks = payload.get("followup_hooks")
    hooks_text = ""
    if isinstance(hooks, list):
        hooks_text = "\n".join(_safe_str(item) for item in hooks)

    full_text = f"{narration}\n{action}\n{hooks_text}".strip()

    warnings: List[str] = []

    if not narration.strip():
        warnings.append("combat_narration_empty")

    if META_RE.search(full_text):
        warnings.append("combat_narration_meta_language")

    defeated = facts.get("defeated") is True
    party_defeated = facts.get("party_defeated") is True

    if not defeated and not party_defeated and DEATH_WORD_RE.search(full_text):
        warnings.append("combat_narration_invented_death")

    if defeated:
        defeat_terms = re.compile(r"\b(defeat|defeated|falls|drops|collapses|goes down|downed|slain|killed)\b", re.I)
        if not defeat_terms.search(full_text):
            warnings.append("combat_narration_missing_defeat_acknowledgement")

    if party_defeated:
        party_terms = re.compile(
            r"\b(defeat|defeated|defeating|overwhelmed|fall|falls|fallen|collapse|collapses|collapsed|downed|goes down|lose|lost)\b",
            re.I,
        )
        if not party_terms.search(full_text):
            warnings.append("combat_narration_missing_party_defeat_acknowledgement")

    target_name = _safe_str(facts.get("target_name"))
    if target_name and target_name.lower() not in full_text.lower():
        warnings.append("combat_narration_missing_target_name")

    actor_name = _safe_str(facts.get("actor_name"))
    actor_id = _safe_str(facts.get("actor_id"))
    if actor_id == "player":
        # Player narration can use "you" instead of player name.
        if "you" not in full_text.lower() and actor_name and actor_name.lower() not in full_text.lower():
            warnings.append("combat_narration_missing_player_actor_reference")
    elif actor_name and actor_name.lower() not in full_text.lower():
        warnings.append("combat_narration_missing_actor_name")

    return {
        "ok": not warnings,
        "warnings": warnings,
        "source": "deterministic_combat_narration_validator",
    }