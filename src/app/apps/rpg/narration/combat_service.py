from __future__ import annotations

import logging

import json
from typing import Any, Callable, Dict

from app.apps.rpg.narration.combat_contract import build_combat_narration_contract
from app.apps.rpg.narration.combat_prompt import build_combat_narration_prompt
from app.apps.rpg.narration.combat_validator import validate_combat_narration
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_str as _safe_str

logger = logging.getLogger(__name__)


def _parse_json_object(text: Any) -> Dict[str, Any]:
    text = _safe_str(text).strip()
    if not text:
        return {}

    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        logger.debug("suppressed error in %s", "_parse_json_object", exc_info=True)

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(text[start : end + 1])
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    return {}


def _normalize_llm_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    payload = _safe_dict(payload)

    npc = payload.get("npc")
    if not isinstance(npc, dict):
        npc = {"speaker": "", "line": ""}

    hooks = payload.get("followup_hooks")
    if not isinstance(hooks, list):
        hooks = []

    return {
        "format_version": "rpg_narration_v2",
        "narration": _safe_str(payload.get("narration")),
        "action": _safe_str(payload.get("action")),
        "npc": {
            "speaker": _safe_str(npc.get("speaker")),
            "line": _safe_str(npc.get("line")),
        },
        "reward": _safe_str(payload.get("reward")),
        "followup_hooks": hooks,
    }


def generate_combat_narration_sync(
    *,
    combat_result: Dict[str, Any],
    combat_state: Dict[str, Any],
    llm_json_call: Callable[[str], Any],
) -> Dict[str, Any]:
    """Build, call, parse, and validate combat narration.

    llm_json_call must call the real configured provider and return either:
    - a raw string containing JSON
    - a dict payload
    """

    contract = build_combat_narration_contract(
        combat_result=combat_result,
        combat_state=combat_state,
    )
    prompt = build_combat_narration_prompt(contract)

    raw = llm_json_call(prompt)

    if isinstance(raw, dict):
        payload = raw
        raw_text = json.dumps(raw, ensure_ascii=False)
    else:
        raw_text = _safe_str(raw)
        payload = _parse_json_object(raw_text)

    payload = _normalize_llm_payload(payload)

    validation = validate_combat_narration(
        narration_payload=payload,
        combat_contract=contract,
    )

    return {
        "llm_called": True,
        "llm_purpose": "combat_narration",
        "accepted": validation.get("ok") is True,
        "raw": raw_text,
        "payload": payload,
        "combat_narration_contract": contract,
        "combat_narration_validation": validation,
        "source": "combat_narration_service",
    }