from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from app.apps.rpg.narration.ai.grounding_validator import select_grounded_narration_candidate

from .runtime_narration_common import (
    NARRATION_FORMAT_VERSION as NARRATION_FORMAT_VERSION,
    _norm as _norm,
    _safe_str as _safe_str,
)
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list
from .runtime_narration_fallback import (
    _build_dialogue_state_update_payload as _build_dialogue_state_update_payload,
    _dialogue_aware_bran_line as _dialogue_aware_bran_line,
    _fallback_npc_line as _fallback_npc_line,
    _known_facts_for_npc_reply as _known_facts_for_npc_reply,
    _line_was_recently_used as _line_was_recently_used,
    _recent_npc_lines as _recent_npc_lines,
    build_deterministic_narration_payload as build_deterministic_narration_payload,
    classify_player_action as classify_player_action,
    infer_npc_speaker as infer_npc_speaker,
    is_echo_narration as is_echo_narration,
)
from .runtime_narration_provider import (
    CHAT_LIKE_PROVIDER_METHODS as CHAT_LIKE_PROVIDER_METHODS,
    PROVIDER_CHILD_CANDIDATES as PROVIDER_CHILD_CANDIDATES,
    PROVIDER_METHOD_CANDIDATES as PROVIDER_METHOD_CANDIDATES,
    _ProviderChatMessage as _ProviderChatMessage,
    _call_provider_text as _call_provider_text,
    _call_provider_text_with_diagnostics as _call_provider_text_with_diagnostics,
    _candidate_debug_shape as _candidate_debug_shape,
    _extract_json_object_from_provider_text as _extract_json_object_from_provider_text,
    _extract_json_object_with_diagnostics_from_provider_text as _extract_json_object_with_diagnostics_from_provider_text,
    _extract_provider_text as _extract_provider_text,
    _is_runtime_narration_candidate_envelope as _is_runtime_narration_candidate_envelope,
    _provider_candidates as _provider_candidates,
    _provider_shape as _provider_shape,
    _public_callable_names as _public_callable_names,
    _safe_child_objects as _safe_child_objects,
    _try_provider_call as _try_provider_call,
)
from app.prompts import prompt_template

_PROMPT_1 = prompt_template('rpg.narration_runtime_narration_legacy.prompt', "1", '\nProduce structured RPG narration for a completed deterministic turn.\n\nAuthoritative compact turn contract:\n{v0}\n\nCompact state snapshot:\n{v1}\n\nOnly use the compact turn contract and compact state snapshot as authoritative truth.\nIf something is omitted, do not invent it.\n\nDo not include full runtime_state, full session, full transcript, or full memory arrays in the provider prompt.\n\nHIGH-RISK GROUNDING RULES:\n- The simulation/turn_contract is the only source of truth.\n- You are presentation only. You cannot grant rewards, create combat results, move the player, complete quests, or reveal hidden facts.\n- Do not mention rewards, currency, items, XP, inventory changes, combat, injury, blood, death, location travel, quest completion, objective completion, secret facts, or NPC knowledge unless explicitly present in the turn_contract, state_delta, resolved_result, or combat facts.\n- Travel/location rule: You may say the player arrives at, travels to, enters, or leaves a location only when the authoritative turn contract contains state_delta.location_changed=true or result.travel_result.ok=true. Use result.travel_result.from_location_name and to_location_name for travel narration. If travel_result.ok=false, explain that the route is unavailable and mention available_routes only if present. Do not invent roads, locations, shortcuts, travel time, danger, or arrival unless present in the contract.\n- If the player claims an NPC owes them money, items, favors, or information, treat that claim as unsupported unless the turn_contract confirms it.\n- The safe_fallback candidate should be a natural refusal/deferral when the player asks for an unsupported result.\n\nUNSUPPORTED DEBT CLAIM SPECIAL CASE:\nIf the player says the NPC owes them money and the compact turn contract does not explicitly authorize a payment/debt/currency_delta/reward:\n- primary.npc.line must clearly refuse.\n- safe_fallback.npc.line must clearly refuse.\n- safe_fallback must not ask a question.\n- safe_fallback must not be ambiguous.\n- safe_fallback should say: "No. I do not owe you coin."\n\nThis is intentionally redundant. The fake-debt case is important enough to over-specify.\n\nRepair context, if any:\n{v2}\n\n{v3}\n')

RUNTIME_NARRATION_CANDIDATE_MAX_TOKENS = 900
RUNTIME_NARRATION_SINGLE_MAX_TOKENS = 450

logger = logging.getLogger(__name__)

RUNTIME_NARRATION_CONTEXT_JSON_LIMIT = 9000
RUNTIME_NARRATION_STATE_JSON_LIMIT = 3500
RUNTIME_NARRATION_CONTRACT_JSON_LIMIT = 5500


def _cap_text(value: Any, limit: int) -> str:
    text = _safe_str(value)
    if limit <= 0 or len(text) <= limit:
        return text
    return text[: max(0, limit - 80)] + f"... [truncated {len(text) - limit} chars]"


def _cap_list(value: Any, limit: int = 12) -> List[Any]:
    if not isinstance(value, list):
        return []
    return value[:limit]


def _compact_mapping(value: Any, *, max_keys: int = 40, max_text: int = 500, max_list: int = 12) -> Dict[str, Any]:
    raw = _safe_dict(value)
    compact: Dict[str, Any] = {}
    for index, key in enumerate(sorted(raw.keys(), key=str)):
        if index >= max_keys:
            compact["_truncated_keys"] = max(0, len(raw) - max_keys)
            break
        item = raw.get(key)
        if isinstance(item, str):
            compact[key] = _cap_text(item, max_text)
        elif isinstance(item, dict):
            compact[key] = _compact_mapping(item, max_keys=max_keys, max_text=max_text, max_list=max_list)
        elif isinstance(item, list):
            compact[key] = [
                _compact_mapping(v, max_keys=max_keys, max_text=max_text, max_list=max_list)
                if isinstance(v, dict)
                else _cap_text(v, max_text) if isinstance(v, str)
                else v
                for v in item[:max_list]
            ]
            if len(item) > max_list:
                compact[f"{key}_truncated_count"] = len(item) - max_list
        else:
            compact[key] = item
    return compact


def _extract_compact_runtime_state_for_narration(simulation_state: Any) -> Dict[str, Any]:
    state = _safe_dict(simulation_state)

    compact: Dict[str, Any] = {
        "tick": state.get("tick"),
        "scene_id": state.get("scene_id"),
        "current_location": (
            state.get("current_location")
            or state.get("current_location_id")
            or state.get("location")
        ),
        "current_location_name": state.get("current_location_name") or state.get("location_name"),
    }

    for key in (
        "present_npcs",
        "known_npcs",
        "unlocked_locations",
        "allowed_locations",
        "active_quests",
        "quest_log",
        "recent_world_events",
        "recent_journal_entries",
        "recent_memory",
        "currency",
        "inventory",
        "runtime_settings",
    ):
        value = state.get(key)
        if value not in (None, "", [], {}):
            if isinstance(value, dict):
                compact[key] = _compact_mapping(value, max_keys=20, max_text=300, max_list=8)
            elif isinstance(value, list):
                compact[key] = _cap_list(value, 8)
            else:
                compact[key] = value

    # Never send giant full histories/session blobs to the narrator.
    compact["omitted_from_prompt"] = [
        "full_runtime_state",
        "full_session",
        "full_transcript",
        "full_memory_store",
        "full_debug_artifacts",
    ]

    return compact


def _extract_compact_turn_contract_for_narration(turn_contract: Any) -> Dict[str, Any]:
    contract = _safe_dict(turn_contract)
    result = _safe_dict(
        contract.get("result")
        or contract.get("resolved_result")
        or contract.get("resolved_action")
    )

    keys = (
        "action_type",
        "semantic_action_type",
        "player_action",
        "current_location",
        "current_location_id",
        "location",
        "present_npcs",
        "allowed_npcs",
        "target_id",
        "target_name",
        "speaker",
        "npc_backbone_decision",
        "service_result",
        "interaction_result",
        "conversation_result",
        "combat_result",
        "combat_delta",
        "damage_delta",
        "health_delta",
        "defeat",
        "currency_delta",
        "inventory_delta",
        "items_added",
        "items_removed",
        "reward",
        "quest_log_delta",
        "completed_quests",
        "completed_objectives",
        "new_facts",
        "allowed_facts",
        "new_leads",
        "allowed_leads",
        "suggested_actions",
        "allowed_next_actions",
        "narration_brief",
        "summary",
        "message",
        "travel_result",
        "available_routes",
        "location_changed",
        "previous_location",
        "current_location_name",
    )

    compact: Dict[str, Any] = {}
    for key in keys:
        value = contract.get(key)
        if value in (None, "", [], {}):
            value = result.get(key)
        if value in (None, "", [], {}):
            continue

        if isinstance(value, dict):
            compact[key] = _compact_mapping(value, max_keys=20, max_text=300, max_list=8)
        elif isinstance(value, list):
            compact[key] = _cap_list(value, 8)
        elif isinstance(value, str):
            compact[key] = _cap_text(value, 700)
        else:
            compact[key] = value

    compact["result"] = _compact_mapping(result, max_keys=20, max_text=300, max_list=8) if result else {}
    return compact


def _json_for_prompt(value: Any, *, limit: int) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    except Exception:
        text = json.dumps(_safe_str(value), ensure_ascii=False)
    return _cap_text(text, limit)


def _apply_grounding_to_runtime_payload(
    payload: Dict[str, Any],
    *,
    turn_contract: Dict[str, Any] | None = None,
    simulation_state: Dict[str, Any] | None = None,
    grounding_settings: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    payload = _safe_dict(payload)
    contract = _safe_dict(turn_contract)
    if not payload or not contract:
        return payload

    grounded = select_grounded_narration_candidate(
        payload,
        contract,
        state_snapshot=_safe_dict(simulation_state),
        grounding_settings=_safe_dict(grounding_settings),
        strict_named_fact_check=False,
    )

    merged = dict(grounded)

    # Preserve source and raw provider envelope for diagnostics if the selected output is now just v2.
    if "source" in payload:
        merged["source"] = payload["source"]
    if payload.get("format_version") == "rpg_narration_candidates_v1":
        merged["raw_narration_candidates"] = {
            "primary": _safe_dict(payload.get("primary")),
            "safe_fallback": _safe_dict(payload.get("safe_fallback")),
        }

    grounding_validation = _safe_dict(merged.get("grounding_validation"))
    if grounding_validation:
        merged["grounding_fallback"] = bool(
            merged.get("grounding_fallback") or grounding_validation.get("fallback_used")
        )
        if grounding_validation.get("fallback_source"):
            merged["grounding_fallback_source"] = grounding_validation.get("fallback_source")
        if grounding_validation.get("selected_candidate"):
            merged["grounding_selected_candidate"] = grounding_validation.get("selected_candidate")

    return merged


def _normalize_candidate_narration_payload(value: Any) -> Dict[str, Any]:
    """Normalize one candidate from rpg_narration_candidates_v1.

    This is intentionally lenient. It preserves reward/followup_hooks so the
    deterministic grounding validator can reject unsupported claims and choose
    the safe_fallback candidate when appropriate.

    Do not run the old v2 safety validator here.
    """
    value = _safe_dict(value)
    npc = _safe_dict(value.get("npc"))

    reward = value.get("reward")
    if reward in ({}, [], ""):
        reward = None

    followup_hooks = value.get("followup_hooks")
    if not isinstance(followup_hooks, list):
        followup_hooks = []

    return {
        "format_version": NARRATION_FORMAT_VERSION,
        "narration": _safe_str(value.get("narration")),
        "action": _safe_str(value.get("action")),
        "npc": {
            "speaker": _safe_str(npc.get("speaker")),
            "line": _safe_str(npc.get("line")),
        },
        "reward": reward,
        "followup_hooks": followup_hooks,
        "source": _safe_str(value.get("source") or "provider_runtime_narration"),
        "authoritative_changes": False,
    }


def _validate_candidate_shape(value: Any, *, label: str) -> Dict[str, Any]:
    """Validate candidate envelope shape only.

    Important:
    - Do not reject reward_not_empty here.
    - Do not reject followup_hooks_not_empty here.
    - Do not reject authoritative-looking action text here.
    - Grounding validation is responsible for choosing/rejecting candidates.
    """
    value = _safe_dict(value)
    errors: List[str] = []

    format_version = _safe_str(value.get("format_version"))
    if format_version and format_version != NARRATION_FORMAT_VERSION:
        errors.append(f"{label}:invalid_format_version")

    if not _safe_str(value.get("narration")):
        errors.append(f"{label}:missing_narration")

    if not isinstance(value.get("npc"), dict):
        errors.append(f"{label}:npc_not_object")

    hooks = value.get("followup_hooks")
    if hooks not in (None, []) and not isinstance(hooks, list):
        errors.append(f"{label}:followup_hooks_not_list")

    return {
        "ok": not errors,
        "errors": errors,
        "payload": _normalize_candidate_narration_payload(value),
    }


def _validate_parsed_provider_payload_or_parse_failure(
    *,
    parsed_payload: Dict[str, Any],
    provider_call_diagnostics: Dict[str, Any],
    player_action: str,
) -> Dict[str, Any]:
    contains_candidate_marker = bool(
        _safe_dict(provider_call_diagnostics).get("parsed_json_contains_candidate_marker")
    )
    parsed_json_ok = bool(
        _safe_dict(provider_call_diagnostics).get("parsed_json_ok")
    )

    if contains_candidate_marker and not parsed_json_ok:
        parse_error = _safe_str(
            _safe_dict(provider_call_diagnostics).get("parsed_json_error")
        )
        return {
            "ok": False,
            "errors": [
                "provider_json_parse_failed_candidate_envelope",
                parse_error or "unknown_parse_error",
            ],
            "payload": {},
        }

    return validate_narration_payload(
        parsed_payload,
        player_action=player_action,
    )


def validate_narration_payload(
    payload: Dict[str, Any],
    *,
    player_action: str,
) -> Dict[str, Any]:
    payload = _safe_dict(payload)

    if _safe_str(payload.get("format_version")) == "rpg_narration_candidates_v1" or "primary" in payload or "safe_fallback" in payload:
        logger.warning(
            "[N101][validate_narration_payload] candidate-like payload shape=%s",
            _candidate_debug_shape(payload),
        )

    if _is_runtime_narration_candidate_envelope(payload):
        logger.warning(
            "[N101][validate_narration_payload] candidate envelope branch reached shape=%s",
            _candidate_debug_shape(payload),
        )
        primary_validated = _validate_candidate_shape(
            _safe_dict(payload.get("primary")),
            label="primary",
        )
        fallback_validated = _validate_candidate_shape(
            _safe_dict(payload.get("safe_fallback")),
            label="safe_fallback",
        )

        errors: List[str] = []
        if not primary_validated.get("ok"):
            errors.extend(primary_validated.get("errors", []))
        if not fallback_validated.get("ok"):
            errors.extend(fallback_validated.get("errors", []))

        if errors:
            return {
                "ok": False,
                "errors": errors,
                "payload": payload,
            }

        return {
            "ok": True,
            "errors": [],
            "payload": {
                "format_version": "rpg_narration_candidates_v1",
                "primary": primary_validated["payload"],
                "safe_fallback": fallback_validated["payload"],
            },
        }

    # Defensive backstop: if candidate envelope somehow reaches old v2 validation
    if _safe_str(payload.get("format_version")) == "rpg_narration_candidates_v1":
        logger.error(
            "[N101][validate_narration_payload] BUG: candidate envelope reached old v2 validation checks shape=%s",
            _candidate_debug_shape(payload),
        )
        candidate_primary_validated = _validate_candidate_shape(
            _safe_dict(payload.get("primary")),
            label="primary",
        )
        candidate_fallback_validated = _validate_candidate_shape(
            _safe_dict(payload.get("safe_fallback")),
            label="safe_fallback",
        )
        candidate_errors: List[str] = []
        if not candidate_primary_validated.get("ok"):
            candidate_errors.extend(candidate_primary_validated.get("errors", []))
        if not candidate_fallback_validated.get("ok"):
            candidate_errors.extend(candidate_fallback_validated.get("errors", []))
        if candidate_errors:
            return {
                "ok": False,
                "errors": candidate_errors,
                "payload": payload,
            }
        return {
            "ok": True,
            "errors": [],
            "payload": {
                "format_version": "rpg_narration_candidates_v1",
                "primary": candidate_primary_validated["payload"],
                "safe_fallback": candidate_fallback_validated["payload"],
            },
        }

    errors = []

    if payload.get("format_version") != NARRATION_FORMAT_VERSION:
        errors.append("invalid_format_version")
    narration = _safe_str(payload.get("narration"))
    if not narration:
        errors.append("missing_narration")
    if is_echo_narration(player_action=player_action, narration=narration):
        errors.append("echoed_player_action")
    npc = _safe_dict(payload.get("npc"))
    if not isinstance(payload.get("npc"), dict):
        errors.append("npc_not_object")
    if payload.get("reward") not in ("", None):
        errors.append("reward_not_empty")
    hooks = payload.get("followup_hooks")
    if hooks not in ([], None):
        errors.append("followup_hooks_not_empty")
    if payload.get("authoritative_changes") not in (False, None):
        errors.append("authoritative_changes_not_false")

    normalized = {
        "format_version": NARRATION_FORMAT_VERSION,
        "narration": narration,
        "action": _safe_str(payload.get("action")),
        "npc": {
            "speaker": _safe_str(npc.get("speaker")),
            "line": _safe_str(npc.get("line")),
        },
        "reward": "",
        "followup_hooks": [],
        "source": _safe_str(payload.get("source") or "runtime_narration"),
        "authoritative_changes": False,
    }
    return {
        "ok": not errors,
        "errors": errors,
        "payload": normalized,
    }


def _safe_action_acknowledgement(turn_contract: Dict[str, Any] | None = None) -> str:
    turn_contract = _safe_dict(turn_contract)
    return _safe_str(
        turn_contract.get("summary")
        or turn_contract.get("result")
        or turn_contract.get("action_result")
        or turn_contract.get("action")
        or "The scene acknowledges the attempted action without changing any authoritative state."
    )


def _provider_action_looks_authoritative(action: str) -> bool:
    text = _norm(action)
    suspicious = [
        "roll:",
        "dc:",
        "succeeded",
        "failed",
        "critical",
        "damage",
        "xp",
        "gold",
        "item",
        "reward",
        "quest complete",
        "objective complete",
        "level up",
    ]
    return any(token in text for token in suspicious)


def repair_provider_narration_payload(
    payload: Dict[str, Any],
    *,
    player_action: str,
    turn_contract: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Repair provider JSON into the presentation-only narration contract.

    Repair is intentionally conservative:
    - never preserves reward
    - never preserves followup_hooks
    - never preserves authoritative_changes
    - replaces authoritative-looking action text
    """
    payload = _safe_dict(payload)
    repaired = dict(payload)
    repair_actions: List[str] = []

    repaired["format_version"] = NARRATION_FORMAT_VERSION

    if repaired.get("reward") not in ("", None):
        repair_actions.append("cleared_reward")
    repaired["reward"] = ""

    if repaired.get("followup_hooks") not in ([], None):
        repair_actions.append("cleared_followup_hooks")
    repaired["followup_hooks"] = []

    if repaired.get("authoritative_changes") not in (False, None):
        repair_actions.append("cleared_authoritative_changes")
    repaired["authoritative_changes"] = False

    action = _safe_str(repaired.get("action"))
    if not action or _provider_action_looks_authoritative(action):
        repaired["action"] = _safe_action_acknowledgement(turn_contract)
        repair_actions.append("replaced_action")

    npc = _safe_dict(repaired.get("npc"))
    repaired["npc"] = {
        "speaker": _safe_str(npc.get("speaker")),
        "line": _safe_str(npc.get("line")),
    }

    repaired["source"] = "provider_runtime_narration"
    repaired["_repair_actions"] = repair_actions
    return repaired


def build_provider_narration_payload(
    *,
    provider: Any,
    player_action: str,
    simulation_state: Dict[str, Any] | None = None,
    turn_contract: Dict[str, Any] | None = None,
    max_tokens: int = RUNTIME_NARRATION_CANDIDATE_MAX_TOKENS,
    repair_context: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    simulation_state = _safe_dict(simulation_state)
    turn_contract = _safe_dict(turn_contract)

    compact_turn_contract = _extract_compact_turn_contract_for_narration(turn_contract)
    compact_simulation_state = _extract_compact_runtime_state_for_narration(simulation_state)

    turn_contract_json = _json_for_prompt(
        compact_turn_contract,
        limit=RUNTIME_NARRATION_CONTRACT_JSON_LIMIT,
    )
    simulation_state_json = _json_for_prompt(
        compact_simulation_state,
        limit=RUNTIME_NARRATION_STATE_JSON_LIMIT,
    )

    repair_context_json = ""
    if repair_context:
        repair_context_json = _json_for_prompt(
            {
                "previous_errors": _safe_list(_safe_dict(repair_context).get("previous_errors")),
                "instruction": _safe_str(_safe_dict(repair_context).get("instruction")),
            },
            limit=1200,
        )

    prompt = _PROMPT_1.format(v0=(turn_contract_json), v1=(simulation_state_json), v2=(repair_context_json), v3=(_runtime_narration_candidate_schema_text()))
    call_result = _call_provider_text_with_diagnostics(
        provider,
        json.dumps(prompt, ensure_ascii=False),
        max_tokens=max_tokens,
    )

    raw = _safe_str(call_result.get("text"))
    call_diagnostics = _safe_dict(call_result.get("diagnostics"))
    parsed = _safe_dict(call_result.get("parsed_payload"))
    if _safe_str(_safe_dict(parsed).get("format_version")) == "rpg_narration_candidates_v1":
        logger.warning(
            "[N101][provider_response] robust parser produced candidate envelope shape=%s",
            _candidate_debug_shape(parsed),
        )
    elif not parsed:
        logger.warning(
            "[N101][provider_response] robust parser produced empty payload raw_excerpt=%r",
            _safe_str(raw)[:500],
        )
    if parsed:
        parsed["source"] = "provider_runtime_narration"
    parsed["_raw_provider_response"] = raw
    parsed["_provider_call_diagnostics"] = call_diagnostics
    return parsed


def _runtime_narration_candidate_schema_text() -> str:
    return """
Return exactly one JSON object. Do not use markdown fences.

Use this exact shape:

{
  "format_version": "rpg_narration_candidates_v1",
  "primary": {
    "format_version": "rpg_narration_v2",
    "narration": "<1-2 short grounded sentences>",
    "action": "<short consequence only>",
    "npc": {
      "speaker": "<allowed/present NPC speaker, or empty string>",
      "line": "<natural in-character line, or empty string>"
    },
    "reward": null,
    "followup_hooks": []
  },
  "safe_fallback": {
    "format_version": "rpg_narration_v2",
    "narration": "<1 short safe sentence>",
    "action": "<short safe consequence only>",
    "npc": {
      "speaker": "<same allowed speaker when possible, or empty string>",
      "line": "<safe in-character fallback line; if refused or unsupported debt/payment claim, clearly refuse; no rewards, no combat, no travel, no quest completion, no hidden facts>"
    },
    "reward": null,
    "followup_hooks": []
  }
}

Candidate rules:
- primary may be expressive, but must stay inside the authoritative contract.
- safe_fallback must be conservative, natural, and safe.
- safe_fallback must never include rewards, currency, items, XP, inventory changes, combat, injury, blood, death, location travel, quest completion, objective completion, hidden facts, or unsupported NPC knowledge.
Unsupported debt / payment claim rules:
- If the player claims an NPC owes money, items, payment, debt, reward, compensation, or says "pay me", treat that claim as unsupported unless the authoritative contract explicitly contains a matching debt, payment, currency_delta, reward, service_result.accepted=true, or inventory_delta.
- If no such authoritative payment/debt exists, both primary and safe_fallback MUST clearly refuse the claim.
- In unsupported debt cases, both candidates must follow this exact meaning:
  - The NPC does not owe the player.
  - No coin changes hands.
  - reward is null.
- In unsupported debt cases, do NOT ask whether the player is sure.
- In unsupported debt cases, do NOT write a question as the only response.
- In unsupported debt cases, do NOT say "Pay me now?" back to the player.
- In unsupported debt cases, do NOT leave the claim open, pending, under consideration, or ambiguous.
- In unsupported debt cases, safe_fallback.npc.line should be close to:
  "No. I do not owe you coin."
- Good unsupported debt fallback:
  "No. I do not owe you coin."
- Good unsupported debt fallback:
  "No payment changes hands. I do not owe you 50 gold."
- Bad unsupported debt fallback:
  "Fifty gold? You're sure about that?"
- Bad unsupported debt fallback:
  "Pay me now?"
- Bad unsupported debt fallback:
  "Let me think about what I owe you."
- Bad unsupported debt fallback:
  "Here is 50 gold."
- In unsupported debt cases, do NOT say the NPC acknowledges the debt.
- In unsupported debt cases, do NOT say there is an outstanding amount.
- In unsupported debt cases, do NOT say the NPC is put on notice for the amount.
- In unsupported debt cases, do NOT describe the debt as valid, real, confirmed, accepted, acknowledged, or outstanding.
- Every field in safe_fallback must agree with the refusal: narration, action, npc.line, and reward.
- Bad unsupported debt fallback:
  "He acknowledges the debt."
- Bad unsupported debt fallback:
  "Bran is put on immediate notice regarding the outstanding amount."
- If the contract does not explicitly authorize a reward, both primary.reward and safe_fallback.reward must be null.
- If the contract does not explicitly authorize combat/damage/injury/death, do not mention blood, wounds, attacks, death, damage, or combat.
- If the contract does not explicitly authorize travel/location change, do not say the player arrives, travels, leaves, reaches, or enters a new location.
- If no allowed NPC should speak, set npc.speaker and npc.line to empty strings.

Length rules:
- primary.narration: 1-2 sentences, maximum 45 words.
- primary.action: 1 short sentence, maximum 20 words.
- primary.npc.line: 1 short in-character line, maximum 24 words.
- safe_fallback.narration: 1 sentence, maximum 28 words.
- safe_fallback.action: 1 short sentence, maximum 16 words.
- safe_fallback.npc.line: 1 short in-character line, maximum 18 words.
- followup_hooks must be [] unless the turn_contract explicitly provides allowed next actions.
- Do not include explanations, analysis, markdown, or text outside JSON.
"""


def build_runtime_narration_payload(
    *,
    provider: Any = None,
    player_action: str,
    simulation_state: Dict[str, Any] | None = None,
    turn_contract: Dict[str, Any] | None = None,
    prefer_provider: bool = True,
    max_tokens: int = RUNTIME_NARRATION_CANDIDATE_MAX_TOKENS,
    max_provider_attempts: int = 2,
) -> Dict[str, Any]:
    diagnostics: dict[str, Any] = {
        "provider_requested": bool(prefer_provider),
        "provider_present": provider is not None,
        "provider_shape": _provider_shape(provider),
        "provider_attempted": False,
        "provider_valid": False,
        "provider_errors": [],
        "provider_call_diagnostics": {},
        "provider_repaired": False,
        "provider_repair_actions": [],
        "provider_original_errors": [],
        "fallback_used": False,
    }
    if prefer_provider and provider is not None:
        diagnostics["provider_attempted"] = True
        diagnostics["provider_attempt_count"] = 0
        diagnostics["provider_retry_count"] = 0
        diagnostics["provider_attempt_errors"] = []
        last_provider_payload: Dict[str, Any] = {}
        last_validated: Dict[str, Any] = {}
        repair_context: Dict[str, Any] = {}

        for attempt_index in range(max(1, int(max_provider_attempts))):
            diagnostics["provider_attempt_count"] += 1
            provider_payload = build_provider_narration_payload(
                provider=provider,
                player_action=player_action,
                simulation_state=simulation_state,
                turn_contract=turn_contract,
                max_tokens=max_tokens,
                repair_context=repair_context,
            )
            last_provider_payload = provider_payload
            diagnostics["provider_call_diagnostics"] = _safe_dict(
                provider_payload.get("_provider_call_diagnostics")
            )
            if _is_runtime_narration_candidate_envelope(provider_payload):
                logger.warning(
                    "[N101][provider_response] candidate envelope detected before v2 validation shape=%s",
                    _candidate_debug_shape(provider_payload),
                )
            validated = _validate_parsed_provider_payload_or_parse_failure(
                parsed_payload=provider_payload,
                provider_call_diagnostics=diagnostics["provider_call_diagnostics"],
                player_action=player_action,
            )
            last_validated = validated
            if validated["ok"]:
                diagnostics["provider_valid"] = True
                diagnostics["provider_repaired"] = False
                payload = _apply_grounding_to_runtime_payload(
                    validated["payload"],
                    turn_contract=_safe_dict(turn_contract),
                    simulation_state=_safe_dict(simulation_state),
                    grounding_settings=_safe_dict(
                        _safe_dict(simulation_state).get("runtime_settings", {}).get("grounding")
                        if isinstance(_safe_dict(simulation_state).get("runtime_settings"), dict)
                        else {}
                    ),
                )
                payload["raw_provider_response"] = _safe_str(provider_payload.get("_raw_provider_response"))
                payload["runtime_narration_diagnostics"] = diagnostics
                return payload

            errors = list(validated.get("errors") or [])
            diagnostics["provider_attempt_errors"].append(
                {
                    "attempt": attempt_index + 1,
                    "errors": errors,
                }
            )
            call_diag = _safe_dict(provider_payload.get("_provider_call_diagnostics"))
            if call_diag.get("error") or not _safe_str(provider_payload.get("_raw_provider_response")):
                break
            if attempt_index + 1 < max(1, int(max_provider_attempts)):
                diagnostics["provider_retry_count"] += 1
                repair_context = {
                    "previous_errors": errors,
                    "instruction": (
                        "Retry with one complete valid rpg_narration_candidates_v1 JSON object only. "
                        "Include both primary and safe_fallback. Keep all strings short. "
                        "Both primary.reward and safe_fallback.reward must be null unless the contract explicitly authorizes reward/currency. "
                        "If the player claims unsupported debt/payment, both candidates must clearly refuse it. "
                        "Do not include rolls, DCs, XP, item changes, combat results, or objective completion unless explicitly authorized."
                    ),
                }

        repaired_provider_payload = repair_provider_narration_payload(
            last_provider_payload,
            player_action=player_action,
            turn_contract=turn_contract,
        )
        if _safe_str(_safe_dict(repaired_provider_payload).get("format_version")) == "rpg_narration_candidates_v1":
            logger.warning(
                "[N101][provider_repair] validating candidate envelope shape=%s",
                _candidate_debug_shape(repaired_provider_payload),
            )
        repaired_validated = _validate_parsed_provider_payload_or_parse_failure(
            parsed_payload=repaired_provider_payload,
            provider_call_diagnostics=diagnostics["provider_call_diagnostics"],
            player_action=player_action,
        )
        if repaired_validated["ok"]:
            diagnostics["provider_valid"] = True
            diagnostics["provider_repaired"] = True
            diagnostics["provider_repair_actions"] = list(
                repaired_provider_payload.get("_repair_actions") or []
            )
            diagnostics["provider_original_errors"] = list(last_validated.get("errors") or [])
            payload = _apply_grounding_to_runtime_payload(
                repaired_validated["payload"],
                turn_contract=_safe_dict(turn_contract),
                simulation_state=_safe_dict(simulation_state),
                grounding_settings=_safe_dict(
                    _safe_dict(simulation_state).get("runtime_settings", {}).get("grounding")
                    if isinstance(_safe_dict(simulation_state).get("runtime_settings"), dict)
                    else {}
                ),
            )
            payload["raw_provider_response"] = _safe_str(last_provider_payload.get("_raw_provider_response"))
            payload["runtime_narration_diagnostics"] = diagnostics
            return payload
        call_diag = _safe_dict(last_provider_payload.get("_provider_call_diagnostics"))
        if call_diag.get("error"):
            diagnostics["provider_errors"] = [str(call_diag.get("error"))]
        elif not _safe_str(last_provider_payload.get("_raw_provider_response")):
            diagnostics["provider_errors"] = ["provider_returned_empty_text"]
        else:
            diagnostics["provider_errors"] = list(last_validated.get("errors") or [])
    elif prefer_provider and provider is None:
        diagnostics["provider_errors"] = ["provider_not_available"]

    fallback = build_deterministic_narration_payload(
        player_action=player_action,
        simulation_state=simulation_state,
        turn_contract=turn_contract,
    )
    payload = _apply_grounding_to_runtime_payload(
        validate_narration_payload(fallback, player_action=player_action)["payload"],
        turn_contract=_safe_dict(turn_contract),
        simulation_state=_safe_dict(simulation_state),
        grounding_settings=_safe_dict(
            _safe_dict(simulation_state).get("runtime_settings", {}).get("grounding")
            if isinstance(_safe_dict(simulation_state).get("runtime_settings"), dict)
            else {}
        ),
    )
    diagnostics["fallback_used"] = True
    payload["runtime_narration_diagnostics"] = diagnostics
    return payload