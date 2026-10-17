from __future__ import annotations

import logging

from typing import Any, Iterable

# RPG session runtime responsibility module.
# Phase 8.35: when the semantic classifier has already produced a complete
# stateless player-facing response, use it as the canonical visible narration and
# do not spend another blocking LLM call asking the full narrator to rephrase it.
from app.apps.rpg.session.state_normalization import (
    _copy_dict as _copy_dict, _safe_dict as _safe_dict, _safe_str as _safe_str,
)
from app.apps.rpg.session.session_runtime_store import (
    load_runtime_session as load_runtime_session,
)
from app.apps.rpg.session.llm_narration_projection import (
    _phase8_part31_narration_request as _phase8_part31_narration_request,
)
from app.apps.rpg.session.narration_payload_validation import (
    _phase8_part34_existing_completed_llm_narration as _phase8_part34_existing_completed_llm_narration,
)

from .visible_response_sync import _mirror_completed_visible_fields as _PHASE8_PART35_BASE_APPLY_TURN_AUTHORITATIVE
from . import semantic_interaction_runtime as _part04

logger = logging.getLogger(__name__)

_PHASE8_PART35_SOURCE = "semantic_classifier_visible_response"
_PHASE8_PART35_ORIGINAL_COMPILE_SEMANTIC_ACTION_RECORD = _part04._compile_semantic_action_record
_PHASE8_PART35_NON_NPC_SPEAKERS = {
    "scene",
    "narrator",
    "narration",
    "gm",
    "game master",
    "omnix",
    "system",
}



def _phase8_part35_iter_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for key, child in value.items():
            # The embedded saved game is not this turn's response: its stored
            # last-turn data holds an earlier turn's semantic advisory, and
            # walking it cost a whole-state traversal per lookup (WP-5.6).
            if key == "session":
                continue
            yield from _phase8_part35_iter_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _phase8_part35_iter_dicts(child)



def _phase8_part35_clean_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    return "" if text.casefold() in {"", "[]", "{}", "null", "none"} else text



def _phase8_part35_bool_false(value: Any) -> bool:
    if value is False:
        return True
    if isinstance(value, str):
        return value.strip().casefold() in {"false", "no", "0"}
    if isinstance(value, int):
        return value == 0
    return False



def _phase8_part35_bool_true(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().casefold() in {"true", "yes", "1"}
    if isinstance(value, int):
        return value == 1
    return False



def _phase8_part35_norm(value: Any) -> str:
    text = _phase8_part35_clean_text(value).casefold()
    return " ".join("".join(ch if ch.isalnum() else " " for ch in text).split())



def _phase8_part35_list_text(value: Any) -> str:
    if isinstance(value, list):
        return " ".join(_phase8_part35_clean_text(item) for item in value if _phase8_part35_clean_text(item))
    return _phase8_part35_clean_text(value)



def _phase8_part35_player_utterance(source: dict[str, Any]) -> str:
    source = _safe_dict(source)
    explicit = _phase8_part35_clean_text(
        source.get("player_utterance")
        or source.get("utterance")
        or source.get("player_input")
    )
    if explicit:
        return explicit
    diagnostics = _safe_dict(source.get("first_call_grounding_diagnostics"))
    packet = _safe_dict(diagnostics.get("turn_grounding_packet"))
    packet_input = _phase8_part35_clean_text(packet.get("player_input"))
    if packet_input:
        return packet_input
    evidence = _phase8_part35_list_text(source.get("evidence_spans"))
    if evidence:
        return evidence
    return _phase8_part35_clean_text(source.get("intent_summary"))



def _phase8_part35_line_is_player_restatement(npc_line: str, source: dict[str, Any]) -> bool:
    line = _phase8_part35_clean_text(npc_line)
    if not line:
        return False
    lowered = line.casefold()
    # These phrases are narration of the player speaking, not NPC dialogue.
    player_narration_markers = (
        " you tell ",
        " you say",
        " you respond",
        " you reply",
        " you answer",
        "you tell him",
        "you tell her",
        "you tell them",
        "you say to",
        "you respond to",
        "you reply to",
    )
    padded = f" {lowered} "
    if any(marker in padded for marker in player_narration_markers):
        return True

    utterance = _phase8_part35_player_utterance(source)
    norm_line = _phase8_part35_norm(line)
    norm_utterance = _phase8_part35_norm(utterance)
    # Avoid overmatching one-word replies, but catch the observed failure where
    # an NPC line embeds the player's full response as if the NPC spoke it.
    if norm_line and norm_utterance and len(norm_utterance) >= 18:
        if norm_utterance in norm_line or norm_line in norm_utterance:
            return True
        utterance_words = set(norm_utterance.split())
        line_words = set(norm_line.split())
        if len(utterance_words) >= 5:
            overlap = len(utterance_words & line_words) / max(len(utterance_words), 1)
            if overlap >= 0.80:
                return True
    return False



def _phase8_part35_direct_response_safe(source: dict[str, Any]) -> bool:
    source = _safe_dict(source)
    gate = _safe_dict(source.get("direct_response_gate"))
    if not _phase8_part35_bool_true(gate.get("safe_to_display_now")):
        return False

    semantic_family = _phase8_part35_clean_text(source.get("semantic_family")).casefold()
    action_type = _phase8_part35_clean_text(source.get("action_type")).casefold()
    utterance_mode = _phase8_part35_clean_text(source.get("utterance_mode")).casefold()
    interaction_mode = _phase8_part35_clean_text(source.get("interaction_mode")).casefold()
    risk_domain = _phase8_part35_clean_text(source.get("risk_domain")).casefold()

    return bool(
        semantic_family in {"social", "dialogue", "conversation"}
        or action_type in {"dialogue", "social", "social_activity", "conversation"}
        or utterance_mode in {"response", "reply", "dialogue", "question", "inquiry"}
        or interaction_mode in {"direct", "dialogue", "conversation"}
        or risk_domain == "social"
    )



def _phase8_part35_semantic_visible_candidate(source: dict[str, Any]) -> dict[str, Any]:
    source = _safe_dict(source)
    visible = _safe_dict(source.get("visible_response"))
    if not visible:
        return {}

    # Original one-call shortcut: display only stateless/non-runtime semantic
    # responses immediately.  New direct-response shortcut: a grounded semantic
    # packet may explicitly mark low-risk dialogue as safe to display now even
    # when it also records social state metadata for the runtime.
    safe_direct_dialogue = _phase8_part35_direct_response_safe(source)
    if not safe_direct_dialogue:
        if not _phase8_part35_bool_false(source.get("stateful")):
            return {}
        if not _phase8_part35_bool_false(source.get("needs_runtime_resolution")):
            return {}

    narration = _phase8_part35_clean_text(visible.get("narration") or visible.get("text"))
    npc = _safe_dict(visible.get("npc"))
    npc_line = _phase8_part35_clean_text(npc.get("line") or npc.get("text"))
    if _phase8_part35_line_is_player_restatement(npc_line, source):
        npc_line = ""
    if _phase8_part35_line_is_player_restatement(narration, source):
        narration = ""
    if not narration and not npc_line:
        return {}

    speaker = _phase8_part35_clean_text(
        npc.get("speaker")
        or npc.get("name")
        or source.get("target_name")
        or ""
    )
    if _phase8_part35_norm(speaker) in _PHASE8_PART35_NON_NPC_SPEAKERS:
        return {}
    if not npc_line:
        speaker = ""

    if npc_line and speaker:
        final = f'{narration}\n\n{speaker}: "{npc_line}"' if narration else f'{speaker}: "{npc_line}"'
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
        "llm_purpose": "semantic_visible_response",
        "semantic_visible_response": visible,
        "semantic_action_classification": source,
        "narration_json": {
            "format_version": "semantic_visible_response_v1",
            "narration": narration,
            "action": _safe_str(source.get("activity_label") or source.get("action_type")),
            "npc": {"speaker": speaker, "line": npc_line} if (speaker or npc_line) else None,
            "semantic_family": _safe_str(source.get("semantic_family")),
            "interaction_mode": _safe_str(source.get("interaction_mode")),
            "direct_response_gate": _safe_dict(source.get("direct_response_gate")),
        },
        "npc": {"speaker": speaker, "line": npc_line} if (speaker or npc_line) else {},
        "fallback_narration_source": _PHASE8_PART35_SOURCE,
        "skip_full_structured_narrator": True,
    }



def _phase8_part35_semantic_visible_response_fields(payload: dict[str, Any]) -> dict[str, Any]:
    payload = _safe_dict(payload)
    for source in _phase8_part35_iter_dicts(payload):
        fields = _phase8_part35_semantic_visible_candidate(source)
        if fields:
            return fields
    return {}



def _phase8_part35_existing_completed_semantic_or_llm_narration(payload: dict[str, Any]) -> str:
    semantic_fields = _phase8_part35_semantic_visible_response_fields(payload)
    if semantic_fields:
        return _safe_str(semantic_fields.get("final_narration") or semantic_fields.get("narration"))
    try:
        return _phase8_part34_existing_completed_llm_narration(payload)
    except Exception:
        return ""



def _phase8_part31_existing_completed_narration(payload: dict[str, Any]) -> str:
    return _phase8_part35_existing_completed_semantic_or_llm_narration(payload)



def _phase8_part31_should_sync_narration(payload: dict[str, Any]) -> bool:
    if _phase8_part35_semantic_visible_response_fields(payload):
        return False
    # Preserve llm_narration_projection's original policy for all other turns.  It will use
    # the patched _phase8_part31_existing_completed_narration above, so real LLM
    # narration still suppresses redundant narrator calls while deterministic
    # fallback text does not.
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



def _compile_semantic_action_record(
    simulation_state: dict[str, Any],
    runtime_state: dict[str, Any],
    player_input: str,
    action: dict[str, Any],
    semantic_advisory: dict[str, Any],
) -> dict[str, Any]:
    record = _safe_dict(
        _PHASE8_PART35_ORIGINAL_COMPILE_SEMANTIC_ACTION_RECORD(
            simulation_state,
            runtime_state,
            player_input,
            action,
            semantic_advisory,
        )
    )
    advisory = _safe_dict(semantic_advisory)
    visible = _safe_dict(advisory.get("visible_response"))
    if visible:
        record["visible_response"] = visible
        record["semantic_visible_response"] = visible
    if "stateful" in advisory:
        record["stateful"] = bool(advisory.get("stateful"))
    if "needs_runtime_resolution" in advisory:
        record["needs_runtime_resolution"] = bool(advisory.get("needs_runtime_resolution"))
    for key in (
        "semantic_family",
        "interaction_mode",
        "activity_label",
        "target_id",
        "target_name",
        "action_type",
        "utterance_mode",
        "risk_domain",
        "intent_summary",
        "player_utterance",
        "direct_response_gate",
        "evidence_spans",
    ):
        value = advisory.get(key)
        if isinstance(value, (dict, list)):
            if value and not record.get(key):
                record[key] = value
        elif _safe_str(value) and not _safe_str(record.get(key)):
            record[key] = value
    return record



def _phase8_part35_payload_turn_id(payload: dict[str, Any]) -> str:
    payload = _safe_dict(payload)
    for source in _phase8_part35_iter_dicts(payload):
        turn_id = _safe_str(source.get("turn_id")).strip()
        if turn_id:
            return turn_id
    return ""



def _phase8_part35_payload_tick(payload: dict[str, Any]) -> int:
    payload = _safe_dict(payload)
    for source in _phase8_part35_iter_dicts(payload):
        try:
            tick = int(source.get("tick") or 0)
        except Exception:
            tick = 0
        if tick:
            return tick
    return 0



def _phase8_part35_persist_semantic_artifact(session_id: str, payload: dict[str, Any], fields: dict[str, Any]) -> None:
    from app.apps.rpg.session.companion_turn_runtime import (
        _narration_artifact_completes_turn as _narration_artifact_completes_turn, _store_narration_artifact as _store_narration_artifact,
    )

    from app.apps.rpg.session.state_normalization import (
        _safe_list as _safe_list,
    )

    from app.apps.rpg.session.session_runtime_store import (
        save_runtime_session as save_runtime_session,
    )

    session_id = _safe_str(session_id).strip()
    turn_id = _phase8_part35_payload_turn_id(payload)
    if not session_id or not turn_id:
        return
    try:
        session = load_runtime_session(session_id)
    except Exception:
        logger.debug("suppressed error in %s", "_phase8_part35_persist_semantic_artifact", exc_info=True)
        return
    if session is None:
        return
    runtime_state = _copy_dict(session.get("runtime_state"))
    existing = _safe_dict(_safe_dict(runtime_state.get("narration_artifacts_by_turn")).get(turn_id))
    if existing and _narration_artifact_completes_turn(existing):
        return
    tick = _phase8_part35_payload_tick(payload)
    artifact = {
        "turn_id": turn_id,
        "tick": tick,
        "narration": _safe_str(fields.get("narration") or fields.get("final_narration")),
        "final_narration": _safe_str(fields.get("final_narration") or fields.get("narration")),
        "narration_json": _safe_dict(fields.get("narration_json")),
        "npc": _safe_dict(fields.get("npc")),
        "used_llm": True,
        "source": _PHASE8_PART35_SOURCE,
        "artifact_kind": "player_turn_narration",
        "staleness_policy": "append_only_by_turn_id",
        "is_append_only_visible_response": True,
        "semantic_visible_response": _safe_dict(fields.get("semantic_visible_response")),
    }
    try:
        runtime_state = _store_narration_artifact(runtime_state, artifact)
    except Exception:
        artifacts = _safe_list(runtime_state.get("narration_artifacts"))
        artifacts.append(artifact)
        runtime_state["narration_artifacts"] = artifacts[-50:]
        by_turn = _safe_dict(runtime_state.get("narration_artifacts_by_turn"))
        by_turn[turn_id] = artifact
        runtime_state["narration_artifacts_by_turn"] = by_turn
    session["runtime_state"] = runtime_state
    try:
        save_runtime_session(session)
    except Exception:
        logger.debug("suppressed error in %s", "_phase8_part35_persist_semantic_artifact", exc_info=True)
        return


def _phase8_part35_patch_semantic_visible_response(payload: Any, *, session_id: str = "") -> Any:
    if not isinstance(payload, dict):
        return payload
    fields = _phase8_part35_semantic_visible_response_fields(payload)
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
    _phase8_part35_persist_semantic_artifact(session_id, patched, fields)
    return patched



def _patch_semantic_visible_response(
    session_id: str,
    player_input: str,
    action: dict[str, Any] | None = None,
    *,
    performance_override: dict[str, Any] | None = None,
    _base_authoritative: Any = _PHASE8_PART35_BASE_APPLY_TURN_AUTHORITATIVE,
) -> dict[str, Any]:
    payload = _base_authoritative(
        session_id,
        player_input,
        action,
        performance_override=performance_override,
    )
    return _phase8_part35_patch_semantic_visible_response(payload, session_id=session_id)


__all__ = [
    '_PHASE8_PART35_BASE_APPLY_TURN_AUTHORITATIVE',
    '_PHASE8_PART35_NON_NPC_SPEAKERS',
    '_PHASE8_PART35_ORIGINAL_COMPILE_SEMANTIC_ACTION_RECORD',
    '_PHASE8_PART35_SOURCE',
    '_patch_semantic_visible_response',
    '_phase8_part35_bool_false',
    '_phase8_part35_bool_true',
    '_phase8_part35_clean_text',
    '_phase8_part35_direct_response_safe',
    '_phase8_part35_existing_completed_semantic_or_llm_narration',
    '_phase8_part35_iter_dicts',
    '_phase8_part35_line_is_player_restatement',
    '_phase8_part35_list_text',
    '_phase8_part35_norm',
    '_phase8_part35_patch_semantic_visible_response',
    '_phase8_part35_payload_tick',
    '_phase8_part35_payload_turn_id',
    '_phase8_part35_player_utterance',
    '_phase8_part35_semantic_visible_candidate',
]
