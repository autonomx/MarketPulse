from __future__ import annotations

import logging

import json
import re
from typing import Any, Dict, Mapping, Optional

from app.apps.rpg.narration.ai.grounding_settings import normalize_grounding_settings
from app.apps.rpg.narration.ai.grounding_validator import validate_narration_grounding
from app.prompts import prompt_template
from app.apps.rpg.foundation.safe_values import dict_copy as _safe_dict, safe_str as _safe_str

_PROMPT_1 = prompt_template('rpg.ai_grounding_soft_audit.build_soft_audit_prompt', "1", 'You are a strict RPG grounding auditor.\n\nYou are checking already-displayed presentation text against an authoritative turn contract.\n\nRules:\n- You are NOT changing game state.\n- You may only append a short in-character correction if the displayed text made an unsupported claim.\n- Do not grant or remove gold, items, XP, quest completion, location travel, damage, death, or hidden facts.\n- If no correction is needed, return correction_needed=false.\n- If a correction is needed, write one short line from the same speaker when possible.\n- The correction itself must be safe and grounded.\n- Output only JSON.\n\nAuthoritative turn contract:\n{v0}\n\nState snapshot:\n{v1}\n\nDisplayed payload:\n{v2}\n\nReturn exactly:\n{{\n  "correction_needed": true/false,\n  "reason": "short reason",\n  "correction": {{\n    "format_version": "rpg_narration_v2",\n    "narration": "",\n    "action": "",\n    "npc": {{"speaker": "", "line": ""}},\n    "reward": null,\n    "followup_hooks": []\n  }}\n}}\n')

logger = logging.getLogger(__name__)


def _extract_json_object(text: Any) -> Dict[str, Any]:
    text = _safe_str(text).strip()
    if not text:
        return {}
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"\s*```$", "", text).strip()
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except Exception:
        logger.debug("suppressed error in %s", "_extract_json_object", exc_info=True)
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            value = json.loads(text[start : end + 1])
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}
    return {}


def _llm_text(llm_gateway: Any, prompt: str) -> str:
    if not llm_gateway:
        return ""

    chat_completion = getattr(llm_gateway, "chat_completion", None)
    call = getattr(llm_gateway, "call", None)
    generate = getattr(llm_gateway, "generate", None)

    try:
        if callable(chat_completion):
            try:
                response = chat_completion(
                    messages=[
                        {
                            "role": "system",
                            "content": "You are a strict RPG grounding auditor. Return only JSON.",
                        },
                        {
                            "role": "user",
                            "content": prompt,
                        },
                    ],
                    temperature=0.0,
                    max_tokens=300,
                )
            except TypeError:
                response = chat_completion(
                    messages=[
                        {
                            "role": "system",
                            "content": "You are a strict RPG grounding auditor. Return only JSON.",
                        },
                        {
                            "role": "user",
                            "content": prompt,
                        },
                    ]
                )
        elif callable(call):
            try:
                response = call("generate", prompt, context={})
            except TypeError:
                response = call(prompt)
        elif callable(generate):
            try:
                response = generate(prompt, context={})
            except TypeError:
                response = generate(prompt)
        else:
            return ""
    except Exception:
        return ""

    if isinstance(response, str):
        return response.strip()

    if isinstance(response, dict):
        choices = response.get("choices")
        if isinstance(choices, list) and choices:
            first = _safe_dict(choices[0])
            message = _safe_dict(first.get("message"))
            if isinstance(message.get("content"), str):
                return message["content"].strip()
            if isinstance(first.get("text"), str):
                return first["text"].strip()

        for key in ("content", "text", "response", "output"):
            if isinstance(response.get(key), str):
                return response[key].strip()

    return _safe_str(response).strip()


def build_soft_audit_prompt(
    *,
    displayed_payload: Mapping[str, Any],
    turn_contract: Mapping[str, Any],
    state_snapshot: Optional[Mapping[str, Any]] = None,
) -> str:
    return _PROMPT_1.format(v0=(json.dumps(_safe_dict(turn_contract), ensure_ascii=False, indent=2)[:6000]), v1=(json.dumps(_safe_dict(state_snapshot), ensure_ascii=False, indent=2)[:2500]), v2=(json.dumps(_safe_dict(displayed_payload), ensure_ascii=False, indent=2)[:3000]))


def run_grounding_soft_audit(
    *,
    displayed_payload: Mapping[str, Any],
    turn_contract: Mapping[str, Any],
    state_snapshot: Optional[Mapping[str, Any]] = None,
    llm_gateway: Any = None,
    grounding_settings: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    settings = normalize_grounding_settings(grounding_settings or {})
    if not settings.get("background_soft_audit", True):
        return {"ok": True, "correction_needed": False, "disabled": True}

    prompt = build_soft_audit_prompt(
        displayed_payload=displayed_payload,
        turn_contract=turn_contract,
        state_snapshot=state_snapshot,
    )
    raw = _llm_text(llm_gateway, prompt)
    parsed = _extract_json_object(raw)
    correction_needed = bool(parsed.get("correction_needed"))

    if not correction_needed:
        return {
            "ok": True,
            "correction_needed": False,
            "raw_audit": raw[:1000],
            "audit": parsed,
        }

    correction = _safe_dict(parsed.get("correction"))
    if not correction:
        return {
            "ok": False,
            "correction_needed": True,
            "error": "missing_correction_payload",
            "raw_audit": raw[:1000],
            "audit": parsed,
        }

    if settings.get("background_soft_audit_validate_correction", True):
        validation = validate_narration_grounding(
            correction,
            turn_contract,
            state_snapshot=state_snapshot,
            strict_named_fact_check=False,
        )
        if not validation.ok:
            return {
                "ok": False,
                "correction_needed": True,
                "error": "correction_failed_grounding_validation",
                "validation": validation.to_dict(),
                "raw_audit": raw[:1000],
                "audit": parsed,
            }

    correction["grounding_soft_correction"] = True
    correction["append_only"] = True
    correction["state_changes"] = []
    correction["replaces_turn_text"] = False

    return {
        "ok": True,
        "correction_needed": True,
        "correction": correction,
        "raw_audit": raw[:1000],
        "audit": parsed,
    }