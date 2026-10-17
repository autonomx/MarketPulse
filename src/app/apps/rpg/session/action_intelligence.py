from __future__ import annotations

import hashlib
import json
from typing import Any, Dict

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from app.providers.structured.legacy import decode_legacy_json_object
from app.apps.rpg.narration.ai.pre_runtime_intent_fast_path import FAST_PATH_SOURCE
from app.apps.rpg.session.turn_grounding import build_turn_grounding_packet
from app.prompts import prompt_template
from app.apps.rpg.foundation.safe_values import dict_copy as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

_PROMPT_1 = prompt_template('rpg.ai_action_intelligence.instructions', "1", (
    "You are the RPG first-call action-intent extraction layer.\n"
        "Return JSON only. Use the provided turn_grounding_packet before classifying intent.\n"
        "World/runtime state is authoritative. Do not decide outcomes, damage, XP, prices, "
        "inventory mutation, quest completion, travel success, or rewards.\n"
        "Return action_type, difficulty, skill_id, intent_tags, narrative_goal, target_id, "
        "target_name, strict boolean stateful, strict boolean needs_runtime_resolution, "
        "visible_response, and reason. Never reveal private context.\n"
        "Prefer candidate_action.action_type unless the text clearly implies a better allowed action.\n"
))

_ALLOWED_ACTION_TYPES = {
    "attack_melee", "attack_ranged", "attack_unarmed", "block", "dodge", "parry",
    "persuade", "intimidate", "deceive", "sneak", "investigate", "hack",
    "cast_spell", "use_item", "pickup_item", "drop_item", "equip_item", "unequip_item",
    "observe", "social_activity", "social_competition", "social_affection",
    "social_performance", "trade", "ritual", "exploration", "threat",
}
_ALLOWED_DIFFICULTIES = {"trivial", "easy", "normal", "hard", "extreme"}
_ALLOWED_SKILLS = {
    "swordsmanship", "archery", "firearms", "defense", "stealth", "persuasion",
    "intimidation", "investigation", "magic", "hacking", "performance", "barter", "ritual",
}


class _StrictActionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ActionNpcLine(_StrictActionModel):
    speaker: str = Field(default="", max_length=80)
    line: str = Field(default="", max_length=900)


class ActionVisibleResponse(_StrictActionModel):
    narration: str = Field(default="", max_length=500)
    npc: ActionNpcLine = Field(default_factory=ActionNpcLine)


class ActionAdvisoryPayload(_StrictActionModel):
    action_type: str
    difficulty: str
    skill_id: str = ""
    intent_tags: list[str] = Field(default_factory=list, max_length=6)
    narrative_goal: str = Field(default="", max_length=120)
    target_id: str = Field(default="", max_length=180)
    target_name: str = Field(default="", max_length=80)
    stateful: StrictBool
    needs_runtime_resolution: StrictBool
    visible_response: ActionVisibleResponse = Field(default_factory=ActionVisibleResponse)
    reason: str = Field(default="", max_length=160)

    @field_validator("action_type")
    @classmethod
    def validate_action_type(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in _ALLOWED_ACTION_TYPES:
            raise ValueError("unsupported_action_type")
        return normalized

    @field_validator("difficulty")
    @classmethod
    def validate_difficulty(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in _ALLOWED_DIFFICULTIES:
            raise ValueError("unsupported_difficulty")
        return normalized

    @field_validator("skill_id")
    @classmethod
    def validate_skill(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized and normalized not in _ALLOWED_SKILLS:
            raise ValueError("unsupported_skill_id")
        return normalized


def _safe_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "y", "on"}:
            return True
        if lowered in {"0", "false", "no", "n", "off"}:
            return False
    if value is None:
        return default
    return bool(value)


def _clip_text(text: Any, limit: int = 240) -> str:
    return _safe_str(text).strip()[:limit]


def _prompt_payload(prompt: str) -> Dict[str, Any]:
    if "INPUT:\n" not in prompt:
        return {}
    try:
        return _safe_dict(json.loads(prompt.split("INPUT:\n", 1)[1]))
    except Exception:
        return {}


def _attach_first_call_diagnostics(
    advisory: Dict[str, Any],
    *,
    prompt: str = "",
    raw_result: Any,
    raw_text: str = "",
    source: str,
    provider_called: bool = False,
    provider_error: str = "",
    parse_ok: bool | None = None,
    fallback_used: bool = False,
) -> Dict[str, Any]:
    advisory = _safe_dict(advisory)
    prompt = _safe_str(prompt)
    payload = _prompt_payload(prompt) if prompt else {}
    raw_text = _safe_str(raw_text)
    parsed_visible_response = bool(_safe_dict(advisory.get("visible_response")))
    if parse_ok is None:
        parse_ok = (
            bool(_safe_dict(raw_result))
            if isinstance(raw_result, dict)
            else bool(decode_legacy_json_object(raw_text))
        )
    provider_response_empty = provider_called and not raw_text.strip() and not parse_ok
    provider_malformed_json = provider_called and bool(raw_text.strip()) and not parse_ok
    if source == FAST_PATH_SOURCE:
        provider_status = "fast_path"
    elif provider_error:
        provider_status = "provider_error"
    elif provider_response_empty:
        provider_status = "empty_response"
    elif provider_malformed_json:
        provider_status = "malformed_json"
    elif parse_ok:
        provider_status = "valid_json"
    elif provider_called:
        provider_status = "called_without_parseable_json"
    else:
        provider_status = "not_called"
    diagnostics = {
        "source": source,
        # The prompt itself stays out of the turn's diagnostics: they are kept in
        # the saved game (the action, the last turn, each history entry), where
        # copies of an 8 KB prompt were most of a turn's state change (WP-5.6).
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest() if prompt else "",
        "prompt_chars": len(prompt),
        "prompt_preview": prompt[:240],
        "prompt_truncated": len(prompt) > 240,
        "prompt_built": bool(prompt),
        "prompt_available": bool(prompt),
        "turn_grounding_packet": _safe_dict(payload.get("turn_grounding_packet")),
        "normalized_result": {
            key: value
            for key, value in advisory.items()
            if key != "first_call_grounding_diagnostics"
        },
        "raw_text": _clip_text(raw_text, 4000),
        "raw_text_length": len(raw_text),
        "raw_result_type": type(raw_result).__name__,
        "provider_requested": source != FAST_PATH_SOURCE,
        "provider_called": provider_called,
        "provider_status": provider_status,
        "provider_error": provider_error,
        "provider_response_empty": provider_response_empty,
        "provider_parse_ok": bool(parse_ok),
        "provider_malformed_json": provider_malformed_json,
        "provider_visible_response_present": parsed_visible_response,
        "provider_non_stateful": not _safe_bool(advisory.get("stateful"), True),
        "provider_needs_runtime_resolution": _safe_bool(
            advisory.get("needs_runtime_resolution"), True
        ),
        "provider_fallback_used": fallback_used,
        "intent_fast_path_used": source == FAST_PATH_SOURCE,
        "intent_llm_used": bool(provider_called and source != FAST_PATH_SOURCE),
        "intent_fast_path_reason": _safe_str(
            advisory.get("pre_runtime_intent_fast_path_reason")
        ),
        "intent_fast_path_source": _safe_str(
            advisory.get("pre_runtime_intent_fast_path_source")
        ),
        "format_version": "first_call_grounding_diagnostics_v4",
    }
    advisory["first_call_grounding_diagnostics"] = diagnostics
    return advisory


def _complete_raw_text(llm_gateway: Any, prompt: str) -> tuple[Any, str, str]:
    if hasattr(llm_gateway, "complete"):
        result = llm_gateway.complete(prompt)
        raw_text = (
            _safe_str(result.get("text") or result.get("content") or "")
            if isinstance(result, dict)
            else _safe_str(result)
        )
        return result, raw_text, "action_intelligence.complete"
    if hasattr(llm_gateway, "complete_json"):
        result = llm_gateway.complete_json(prompt)
        raw_text = (
            json.dumps(result, ensure_ascii=False, sort_keys=True)
            if isinstance(result, dict) and result
            else ""
        )
        return result, raw_text, "action_intelligence.complete_json"
    return {}, "", "action_intelligence.no_provider_method"


def build_action_intelligence_prompt(
    player_input: str,
    simulation_state: Dict[str, Any],
    runtime_state: Dict[str, Any],
    candidate_action: Dict[str, Any],
) -> str:
    simulation_state = _safe_dict(simulation_state)
    runtime_state = _safe_dict(runtime_state)
    candidate_action = _safe_dict(candidate_action)
    grounding_packet = build_turn_grounding_packet(
        player_input=player_input,
        simulation_state=simulation_state,
        runtime_state=runtime_state,
        candidate_action=candidate_action,
    )
    payload = {
        "player_input": _clip_text(player_input, 500),
        "turn_grounding_packet": grounding_packet,
        "allowed_action_types": sorted(_ALLOWED_ACTION_TYPES),
        "allowed_difficulties": sorted(_ALLOWED_DIFFICULTIES),
        "allowed_skills": sorted(_ALLOWED_SKILLS),
    }
    instructions = (
        _PROMPT_1.text
    )
    return instructions + "\nINPUT:\n" + json.dumps(payload, sort_keys=True)


def normalize_action_advisory(
    advisory: Dict[str, Any],
    candidate_action: Dict[str, Any],
) -> Dict[str, Any]:
    advisory = _safe_dict(advisory)
    candidate_action = _safe_dict(candidate_action)
    action_type = _safe_str(advisory.get("action_type")).strip().lower()
    if action_type not in _ALLOWED_ACTION_TYPES:
        action_type = _safe_str(candidate_action.get("action_type")).strip().lower()
    if action_type not in _ALLOWED_ACTION_TYPES:
        action_type = "investigate"
    difficulty = _safe_str(advisory.get("difficulty")).strip().lower()
    if difficulty not in _ALLOWED_DIFFICULTIES:
        difficulty = _safe_str(candidate_action.get("difficulty")).strip().lower()
    if difficulty not in _ALLOWED_DIFFICULTIES:
        difficulty = "normal"
    skill_id = _safe_str(advisory.get("skill_id")).strip().lower()
    if skill_id not in _ALLOWED_SKILLS:
        skill_id = _safe_str(candidate_action.get("skill_id")).strip().lower()
    if skill_id not in _ALLOWED_SKILLS:
        skill_id = ""
    intent_tags = []
    for value in _safe_list(advisory.get("intent_tags"))[:6]:
        tag = _safe_str(value).strip().lower().replace(" ", "_")
        if tag:
            intent_tags.append(tag[:32])
    visible_response = _safe_dict(advisory.get("visible_response"))
    normalized_visible_response = {}
    if visible_response:
        npc = _safe_dict(visible_response.get("npc"))
        normalized_visible_response = {
            "narration": _clip_text(visible_response.get("narration"), 500),
            "npc": {
                "speaker": _clip_text(npc.get("speaker"), 80),
                "line": _clip_text(npc.get("line"), 900),
            },
        }
    stateful = _safe_bool(advisory.get("stateful"), True)
    needs_runtime_resolution = _safe_bool(
        advisory.get("needs_runtime_resolution"), stateful
    )
    normalized = {
        "action_type": action_type,
        "difficulty": difficulty,
        "skill_id": skill_id,
        "intent_tags": intent_tags,
        "narrative_goal": _clip_text(advisory.get("narrative_goal"), 120),
        "target_id": _safe_str(
            advisory.get("target_id")
            or candidate_action.get("target_id")
            or candidate_action.get("npc_id")
        ).strip(),
        "target_name": _clip_text(advisory.get("target_name"), 80),
        "stateful": stateful,
        "needs_runtime_resolution": needs_runtime_resolution,
        "visible_response": normalized_visible_response,
        "grounding_packet_version": "turn_grounding_packet_v1",
        "reason": _clip_text(advisory.get("reason"), 160),
    }
    for key in (
        "pre_runtime_intent_fast_path",
        "pre_runtime_intent_fast_path_reason",
        "pre_runtime_intent_fast_path_source",
    ):
        if key in advisory:
            normalized[key] = advisory[key]
    return normalized


def merge_action_advisory(
    candidate_action: Dict[str, Any],
    advisory: Dict[str, Any],
) -> Dict[str, Any]:
    candidate_action = _safe_dict(candidate_action)
    advisory = _safe_dict(advisory)
    merged = dict(candidate_action)
    merged["action_type"] = _safe_str(
        advisory.get("action_type") or candidate_action.get("action_type")
    ).strip()
    for key in ("difficulty", "skill_id", "target_id", "target_name"):
        if advisory.get(key):
            merged[key] = advisory.get(key)
    metadata = _safe_dict(merged.get("metadata"))
    metadata["intent_tags"] = _safe_list(advisory.get("intent_tags"))
    metadata["narrative_goal"] = _safe_str(advisory.get("narrative_goal"))
    metadata["llm_reason"] = _safe_str(advisory.get("reason"))
    metadata["llm_advisory"] = True
    metadata["stateful"] = _safe_bool(advisory.get("stateful"), True)
    metadata["needs_runtime_resolution"] = _safe_bool(
        advisory.get("needs_runtime_resolution"), metadata["stateful"]
    )
    metadata["visible_response_if_no_runtime_needed"] = _safe_dict(
        advisory.get("visible_response")
    )
    metadata["grounding_packet_version"] = _safe_str(
        advisory.get("grounding_packet_version") or "turn_grounding_packet_v1"
    )
    metadata["first_call_grounding_diagnostics"] = _safe_dict(
        advisory.get("first_call_grounding_diagnostics")
    )
    merged["metadata"] = metadata
    return merged


def get_action_advisory(
    llm_gateway: Any,
    player_input: str,
    simulation_state: Dict[str, Any],
    runtime_state: Dict[str, Any],
    candidate_action: Dict[str, Any],
) -> Dict[str, Any]:
    if llm_gateway is None:
        return {}
    prompt = build_action_intelligence_prompt(
        player_input,
        simulation_state,
        runtime_state,
        candidate_action,
    )
    raw_result: Any = {}
    raw_text = ""
    source = "action_intelligence.complete"
    parsed: Dict[str, Any] = {}
    provider_error = ""
    fallback_used = False
    try:
        if hasattr(llm_gateway, "generate_typed"):
            typed = llm_gateway.generate_typed(
                prompt,
                output_model=ActionAdvisoryPayload,
                contract_id="rpg.action_intelligence.advisory",
                contract_version=2,
                timeout_s=12.0,
                max_provider_calls=2,
                max_format_downgrades=1,
                max_validation_regenerations=1,
                temperature=0.0,
                max_tokens=1600,
                schema_profile="local",
                schema_name="rpg_action_advisory",
            )
            parsed = typed.model_dump(mode="python")
            raw_result = parsed
            raw_text = json.dumps(parsed, ensure_ascii=False, sort_keys=True)
            source = "action_intelligence.generate_typed"
        else:
            raw_result, raw_text, source = _complete_raw_text(llm_gateway, prompt)
            parsed = (
                _safe_dict(raw_result)
                if source.endswith("complete_json")
                else decode_legacy_json_object(raw_text)
            )
    except Exception as exc:
        provider_error = f"{type(exc).__name__}: {exc}"
        fallback_used = True
    if not parsed:
        fallback_used = True
    advisory = normalize_action_advisory(parsed, candidate_action)
    if fallback_used:
        advisory["reason"] = advisory.get("reason") or "deterministic_candidate_fallback"
    return _attach_first_call_diagnostics(
        advisory,
        prompt=prompt,
        raw_result=raw_result,
        raw_text=raw_text,
        source=source,
        provider_called=not source.endswith("no_provider_method"),
        provider_error=provider_error,
        parse_ok=bool(parsed),
        fallback_used=fallback_used,
    )
