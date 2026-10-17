from __future__ import annotations

import logging

from typing import Any, Iterable, cast

# RPG session runtime responsibility module.
# Phase 8.38: use the current semantic direct-response packet as the visible
# dialogue answer before the expensive full narrator can run.  This preserves the
# intended one-foreground-LLM dialogue path and prevents deterministic service /
# economy fallbacks from overriding safe social dialogue.
from app.apps.rpg.session.state_normalization import (
    _safe_dict as _safe_dict,
)
from app.apps.rpg.session.llm_narration_projection import (
    _phase8_part31_narration_request as _phase8_part31_narration_request,
)

from .turn_bound_narration import _patch_turn_bound_narration as _PHASE8_PART38_BASE_APPLY_TURN_AUTHORITATIVE
from . import semantic_response_projection as _part35

logger = logging.getLogger(__name__)

_PHASE8_PART38_SOURCE = "phase8_fast_semantic_direct_dialogue_response"
_PHASE8_PART38_TEXT_NONE = {"", "[]", "{}", "null", "none", "false", "true"}
_PHASE8_PART38_ORIGINAL_PART35_SEMANTIC_VISIBLE_RESPONSE_FIELDS = _part35._phase8_part35_semantic_visible_response_fields
_PHASE8_PART38_ORIGINAL_PART35_EXISTING_COMPLETED_NARRATION = _part35._phase8_part31_existing_completed_narration
_PHASE8_PART38_ORIGINAL_PART35_PERSIST_SEMANTIC_ARTIFACT = _part35._phase8_part35_persist_semantic_artifact
_PHASE8_PART38_NON_NPC_SPEAKERS = {
    "scene",
    "narrator",
    "narration",
    "gm",
    "game master",
    "omnix",
    "system",
}



def _phase8_part38_clean_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    return "" if text.casefold() in _PHASE8_PART38_TEXT_NONE else text



def _phase8_part38_bool_true(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().casefold() in {"true", "yes", "1"}
    if isinstance(value, int):
        return value == 1
    return False



def _phase8_part38_bool_false(value: Any) -> bool:
    if value is False:
        return True
    if isinstance(value, str):
        return value.strip().casefold() in {"false", "no", "0"}
    if isinstance(value, int):
        return value == 0
    return False



def _phase8_part38_norm(value: Any) -> str:
    text = _phase8_part38_clean_text(value).casefold()
    return " ".join("".join(ch if ch.isalnum() else " " for ch in text).split())



def _phase8_part38_direct_safe(source: dict[str, Any]) -> bool:
    from app.apps.rpg.session.state_normalization import (
        _safe_list as _safe_list,
    )

    source = _safe_dict(source)
    gate = _safe_dict(source.get("direct_response_gate"))
    if not _phase8_part38_bool_true(gate.get("safe_to_display_now")):
        return False

    semantic_family = _phase8_part38_clean_text(source.get("semantic_family")).casefold()
    action_type = _phase8_part38_clean_text(source.get("action_type")).casefold()
    utterance_mode = _phase8_part38_clean_text(source.get("utterance_mode")).casefold()
    interaction_mode = _phase8_part38_clean_text(source.get("interaction_mode")).casefold()
    risk_domain = _phase8_part38_clean_text(source.get("risk_domain")).casefold()
    risk_flags = {_phase8_part38_clean_text(item).casefold() for item in _safe_list(gate.get("risk_flags"))}

    is_social_dialogue = bool(
        semantic_family in {"social", "dialogue", "conversation"}
        or action_type in {"dialogue", "social", "social_activity", "conversation"}
        or utterance_mode in {"response", "reply", "dialogue", "question", "questioning", "inquiry", "asking"}
        or interaction_mode in {"direct", "dialogue", "conversation"}
        or risk_domain == "social"
        or "social" in risk_flags
    )
    if not is_social_dialogue:
        return False

    # Safe direct dialogue must not be a transaction/combat/service shortcut.
    blocked = " ".join(
        _phase8_part38_clean_text(source.get(key)).casefold()
        for key in ("action_type", "semantic_family", "activity_label", "intent_summary", "reason")
    )
    if any(word in blocked for word in ("trade", "purchase", "buy", "sell", "combat", "attack", "inventory", "service_transaction")):
        return False
    return True



def _phase8_part38_player_utterance(source: dict[str, Any]) -> str:
    from app.apps.rpg.session.state_normalization import (
        _safe_list as _safe_list,
    )

    explicit = _phase8_part38_clean_text(
        source.get("player_utterance")
        or source.get("utterance")
        or source.get("player_input")
    )
    if explicit:
        return explicit
    diagnostics = _safe_dict(source.get("first_call_grounding_diagnostics"))
    packet = _safe_dict(diagnostics.get("turn_grounding_packet"))
    packet_input = _phase8_part38_clean_text(packet.get("player_input"))
    if packet_input:
        return packet_input
    evidence = " ".join(_phase8_part38_clean_text(x) for x in _safe_list(source.get("evidence_spans")) if _phase8_part38_clean_text(x))
    return evidence or _phase8_part38_clean_text(source.get("intent_summary"))



def _phase8_part38_line_is_player_restatement(npc_line: str, source: dict[str, Any]) -> bool:
    line = _phase8_part38_clean_text(npc_line)
    if not line:
        return False
    lowered = f" {line.casefold()} "
    if any(marker in lowered for marker in (
        " you tell ", " you say", " you respond", " you reply", " you answer",
        "you tell him", "you tell her", "you tell them", "you say to", "you respond to", "you reply to",
    )):
        return True
    utterance = _phase8_part38_player_utterance(source)
    norm_line = _phase8_part38_norm(line)
    norm_utterance = _phase8_part38_norm(utterance)
    if norm_line and norm_utterance and len(norm_utterance) >= 18:
        if norm_utterance in norm_line or norm_line in norm_utterance:
            return True
        words = set(norm_utterance.split())
        if len(words) >= 5:
            overlap = len(words & set(norm_line.split())) / max(len(words), 1)
            if overlap >= 0.80:
                return True
    return False



def _phase8_part38_candidate_from_source(source: dict[str, Any]) -> dict[str, Any]:
    source = _safe_dict(source)
    visible = _safe_dict(source.get("visible_response")) or _safe_dict(source.get("semantic_visible_response"))
    if not visible:
        return {}

    safe_direct = _phase8_part38_direct_safe(source)
    if not safe_direct:
        if not _phase8_part38_bool_false(source.get("stateful")):
            return {}
        if not _phase8_part38_bool_false(source.get("needs_runtime_resolution")):
            return {}

    narration = _phase8_part38_clean_text(visible.get("narration") or visible.get("text"))
    npc = _safe_dict(visible.get("npc"))
    npc_line = _phase8_part38_clean_text(npc.get("line") or npc.get("text"))
    if _phase8_part38_line_is_player_restatement(npc_line, source):
        npc_line = ""
    if _phase8_part38_line_is_player_restatement(narration, source):
        narration = ""
    if not narration and not npc_line:
        return {}

    speaker = _phase8_part38_clean_text(npc.get("speaker") or npc.get("name") or source.get("target_name"))
    if _phase8_part38_norm(speaker) in _PHASE8_PART38_NON_NPC_SPEAKERS:
        return {}
    if not npc_line:
        speaker = ""

    if narration and npc_line and speaker:
        final = f'{narration}\n\n{speaker}: "{npc_line}"'
    elif npc_line and speaker:
        final = f'{speaker}: "{npc_line}"'
    elif npc_line:
        final = f'NPC: "{npc_line}"'
    else:
        final = narration

    return {
        "narration": final,
        "final_narration": final,
        "raw_payload_narration": final,
        "deterministic_fallback_narration": final,
        "narration_status": "completed",
        "used_llm": True,
        "llm_called": True,
        "llm_purpose": "semantic_direct_dialogue_visible_response",
        "semantic_visible_response": visible,
        "semantic_action_classification": source,
        "narration_json": {
            "format_version": "semantic_visible_response_v1",
            "narration": narration,
            "action": _phase8_part38_clean_text(source.get("activity_label") or source.get("action_type")),
            "npc": {"speaker": speaker, "line": npc_line} if (speaker or npc_line) else None,
            "semantic_family": _phase8_part38_clean_text(source.get("semantic_family")),
            "interaction_mode": _phase8_part38_clean_text(source.get("interaction_mode")),
            "direct_response_gate": _safe_dict(source.get("direct_response_gate")),
        },
        "npc": {"speaker": speaker, "line": npc_line} if (speaker or npc_line) else {},
        "fallback_narration_source": _PHASE8_PART38_SOURCE,
        "skip_full_structured_narrator": True,
    }



def _phase8_part38_iter_candidate_sources(payload: dict[str, Any]) -> Iterable[dict[str, Any]]:
    payload = _safe_dict(payload)
    roots: list[dict[str, Any]] = []
    if payload:
        roots.append(payload)
    for key in ("result", "authoritative", "payload"):
        nested = _safe_dict(payload.get(key))
        if nested:
            roots.append(nested)
    seen: set[int] = set()
    for root in roots:
        root_id = id(root)
        if root_id not in seen:
            seen.add(root_id)
            yield root
        for key in (
            "semantic_action", "semantic_action_record", "semantic_advisory", "action",
            "current_action_response", "resolved_result", "presentation_intent",
        ):
            nested = cast(Any, root.get(key))
            if isinstance(nested, dict):
                nested_id = id(nested)
                if nested_id not in seen:
                    seen.add(nested_id)
                    yield nested
            elif isinstance(nested, list):
                for item in nested:
                    if isinstance(item, dict):
                        item_id = id(item)
                        if item_id not in seen:
                            seen.add(item_id)
                            yield item



def _phase8_part38_semantic_visible_fields(payload: dict[str, Any]) -> dict[str, Any]:
    for source in _phase8_part38_iter_candidate_sources(payload):
        fields = _phase8_part38_candidate_from_source(source)
        if fields:
            return fields
    try:
        fields = _PHASE8_PART38_ORIGINAL_PART35_SEMANTIC_VISIBLE_RESPONSE_FIELDS(payload)
        if fields:
            fields = dict(fields)
            fields["fallback_narration_source"] = _PHASE8_PART38_SOURCE
            return fields
    except Exception:
        return {}
    return {}



def _phase8_part31_existing_completed_narration(payload: dict[str, Any]) -> str:
    fields = _phase8_part38_semantic_visible_fields(payload)
    if fields:
        return _phase8_part38_clean_text(fields.get("final_narration") or fields.get("narration"))
    try:
        return _PHASE8_PART38_ORIGINAL_PART35_EXISTING_COMPLETED_NARRATION(payload)
    except Exception:
        return ""



def _phase8_part31_should_sync_narration(payload: dict[str, Any]) -> bool:
    # Critical latency gate: safe semantic dialogue already contains the final
    # visible NPC response from the foreground LLM. Do not run the 20k-token full
    # narrator synchronously for that turn.
    if _phase8_part38_semantic_visible_fields(payload):
        return False
    request = _phase8_part31_narration_request(payload)
    if not request:
        return False
    perf = _safe_dict(request.get("performance"))
    if perf.get("enable_live_narration_llm") is False:
        return False
    if _phase8_part31_existing_completed_narration(payload):
        return False
    scene = _safe_dict(request.get("scene"))
    context = _safe_dict(request.get("narration_context"))
    return bool(scene or context)



def _phase8_part38_patch_semantic_visible(payload: Any, *, session_id: str = "") -> Any:
    if not isinstance(payload, dict):
        return payload
    fields = _phase8_part38_semantic_visible_fields(payload)
    if not fields:
        return payload
    patched = dict(payload)
    for key, value in fields.items():
        if key == "npc" and not value:
            continue
        patched[key] = value
    for nested_key in ("result", "authoritative", "payload"):
        nested = _safe_dict(patched.get(nested_key))
        if not nested:
            continue
        nested = dict(nested)
        for key, value in fields.items():
            if key == "npc" and not value:
                continue
            nested[key] = value
        patched[nested_key] = nested
    if not _safe_dict(patched.get("result")):
        patched["result"] = {key: value for key, value in patched.items() if key != "authoritative"}
    if not _safe_dict(patched.get("authoritative")):
        patched["authoritative"] = dict(_safe_dict(patched.get("result")))
    try:
        _PHASE8_PART38_ORIGINAL_PART35_PERSIST_SEMANTIC_ARTIFACT(session_id, patched, fields)
    except Exception:
        logger.debug("suppressed error in %s", "_phase8_part38_patch_semantic_visible", exc_info=True)
    return patched



def _patch_semantic_direct_response(
    session_id: str,
    player_input: str,
    action: dict[str, Any] | None = None,
    *,
    performance_override: dict[str, Any] | None = None,
    _base_authoritative: Any = _PHASE8_PART38_BASE_APPLY_TURN_AUTHORITATIVE,
) -> dict[str, Any]:
    payload = _base_authoritative(
        session_id,
        player_input,
        action,
        performance_override=performance_override,
    )
    return _phase8_part38_patch_semantic_visible(payload, session_id=session_id)


__all__ = ['_PHASE8_PART38_BASE_APPLY_TURN_AUTHORITATIVE', '_PHASE8_PART38_NON_NPC_SPEAKERS', '_PHASE8_PART38_ORIGINAL_PART35_EXISTING_COMPLETED_NARRATION', '_PHASE8_PART38_ORIGINAL_PART35_PERSIST_SEMANTIC_ARTIFACT', '_PHASE8_PART38_ORIGINAL_PART35_SEMANTIC_VISIBLE_RESPONSE_FIELDS', '_PHASE8_PART38_SOURCE', '_PHASE8_PART38_TEXT_NONE', '_patch_semantic_direct_response', '_phase8_part31_existing_completed_narration', '_phase8_part31_should_sync_narration', '_phase8_part38_bool_false', '_phase8_part38_bool_true', '_phase8_part38_candidate_from_source', '_phase8_part38_clean_text', '_phase8_part38_direct_safe', '_phase8_part38_iter_candidate_sources', '_phase8_part38_line_is_player_restatement', '_phase8_part38_norm', '_phase8_part38_patch_semantic_visible', '_phase8_part38_player_utterance', '_phase8_part38_semantic_visible_fields']