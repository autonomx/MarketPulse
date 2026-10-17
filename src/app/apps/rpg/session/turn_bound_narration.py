from __future__ import annotations

import logging

from typing import Any

# RPG session runtime responsibility module.
# Phase 8.37: do not let deterministic/fallback NPC text masquerade as
# completed LLM narration merely because it has an npc-shaped dictionary.  Bind
# completed synchronous narrator output to the exact turn immediately so the SSE
# route and polling path do not queue or render an older fallback response.
from app.apps.rpg.session.state_normalization import (
    _copy_dict as _copy_dict, _safe_dict as _safe_dict, _safe_str as _safe_str,
)
from app.apps.rpg.session.session_runtime_store import (
    load_runtime_session as load_runtime_session,
)
from app.apps.rpg.session.llm_narration_projection import (
    _phase8_part31_iter_payload_dicts as _phase8_part31_iter_payload_dicts,
)
from app.apps.rpg.session.semantic_response_projection import (
    _phase8_part35_payload_tick as _phase8_part35_payload_tick, _phase8_part35_payload_turn_id as _phase8_part35_payload_turn_id,
)

from .semantic_response_projection import _patch_semantic_visible_response as _PHASE8_PART37_BASE_APPLY_TURN_AUTHORITATIVE

logger = logging.getLogger(__name__)

_PHASE8_PART37_SOURCE = "phase8_turn_bound_llm_visible_response"
_PHASE8_PART37_LLM_SOURCES = {
    "provider_sync_visible_turn_narration",
    "phase8_sync_narration_stream_payload_mirror",
    "phase8_llm_narration_authority_over_deterministic_fallback",
    "phase8_current_turn_bound_semantic_visible_response",
    "semantic_classifier_visible_response",
    _PHASE8_PART37_SOURCE,
}


def _phase8_part34_has_structured_llm_payload(source: dict[str, Any]) -> bool:
    """Return true only for payloads that are actually LLM-authored.

    The previous Phase 8.34 guard accepted any completed payload with an ``npc``
    object as structured LLM narration.  Deterministic fallbacks also have npc
    fields, which made lines like ``Careful now...`` count as completed narration
    and prevented the real narrator from running.
    """
    from app.apps.rpg.session.companion_turn_runtime import (
        _narration_artifact_has_structured_llm_content as _narration_artifact_has_structured_llm_content,
        _narration_artifact_is_echo_fallback as _narration_artifact_is_echo_fallback,
    )


    source = _safe_dict(source)
    fallback_source = _safe_str(source.get("fallback_narration_source")).strip()
    if fallback_source in _PHASE8_PART37_LLM_SOURCES:
        return not _narration_artifact_is_echo_fallback(source)
    if source.get("used_llm") is True or source.get("llm_called") is True:
        return _narration_artifact_has_structured_llm_content(source)

    semantic_visible = _safe_dict(
        source.get("semantic_visible_response")
        or source.get("visible_response")
    )
    if semantic_visible and _safe_str(source.get("semantic_family")).strip():
        return True

    raw = source.get("raw_llm_narrative")
    if isinstance(raw, dict) and raw:
        return True
    if isinstance(raw, str) and raw.strip():
        return True

    narration_json = _safe_dict(source.get("narration_json"))
    format_version = _safe_str(narration_json.get("format_version")).strip()
    if format_version in {
        "rpg_narration_v2",
        "rpg_narration_candidates_v1",
        "semantic_visible_response_v1",
    }:
        return True
    if narration_json.get("semantic_family") and source.get("semantic_visible_response"):
        return True

    # Important: npc/action/narration fields alone are not enough.  They are also
    # present on deterministic fallback payloads.
    return False


def _phase8_part37_clean_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    return "" if text.casefold() in {"", "[]", "{}", "null", "none"} else text


def _phase8_part37_completed_llm_fields(payload: dict[str, Any]) -> dict[str, Any]:
    from app.apps.rpg.session.companion_turn_runtime import (
        _narration_artifact_is_echo_fallback as _narration_artifact_is_echo_fallback,
    )

    payload = _safe_dict(payload)
    for source in _phase8_part31_iter_payload_dicts(payload):
        if not _phase8_part34_has_structured_llm_payload(source):
            continue
        text = _phase8_part37_clean_text(
            source.get("final_narration")
            or source.get("narration")
            or source.get("raw_payload_narration")
            or source.get("deterministic_fallback_narration")
        )
        if not text:
            continue
        candidate = dict(source)
        candidate["final_narration"] = text
        candidate["narration"] = text
        if _narration_artifact_is_echo_fallback(candidate):
            continue
        return {
            "narration": text,
            "final_narration": text,
            "raw_payload_narration": text,
            "deterministic_fallback_narration": text,
            "narration_status": "completed",
            "used_llm": True,
            "llm_called": True,
            "raw_llm_narrative": source.get("raw_llm_narrative"),
            "narration_json": _safe_dict(source.get("narration_json")),
            "npc": _safe_dict(source.get("npc")),
            "fallback_narration_source": _PHASE8_PART37_SOURCE,
        }
    return {}


def _phase8_part37_turn_id(payload: dict[str, Any]) -> str:
    payload = _safe_dict(payload)
    for source in _phase8_part31_iter_payload_dicts(payload):
        turn_id = _safe_str(source.get("turn_id")).strip()
        if turn_id:
            return turn_id
    return _phase8_part35_payload_turn_id(payload)


def _phase8_part37_tick(payload: dict[str, Any]) -> int:
    payload = _safe_dict(payload)
    for source in _phase8_part31_iter_payload_dicts(payload):
        try:
            tick = int(source.get("tick") or 0)
        except Exception:
            tick = 0
        if tick:
            return tick
    return _phase8_part35_payload_tick(payload)


def _phase8_part37_persist_llm_artifact(session_id: str, payload: dict[str, Any], fields: dict[str, Any]) -> None:
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
    turn_id = _phase8_part37_turn_id(payload)
    if not session_id or not turn_id:
        return
    final_text = _phase8_part37_clean_text(fields.get("final_narration") or fields.get("narration"))
    if not final_text:
        return
    try:
        session = load_runtime_session(session_id)
    except Exception:
        logger.debug("suppressed error in %s", "_phase8_part37_persist_llm_artifact", exc_info=True)
        return
    if session is None:
        return

    runtime_state = _copy_dict(session.get("runtime_state"))
    by_turn = _safe_dict(runtime_state.get("narration_artifacts_by_turn"))
    existing = _safe_dict(by_turn.get(turn_id))
    if existing and _narration_artifact_completes_turn(existing):
        return

    artifact = {
        "turn_id": turn_id,
        "tick": _phase8_part37_tick(payload),
        "narration": final_text,
        "final_narration": final_text,
        "narration_json": _safe_dict(fields.get("narration_json")),
        "npc": _safe_dict(fields.get("npc")),
        "used_llm": True,
        "llm_called": True,
        "source": _PHASE8_PART37_SOURCE,
        "fallback_narration_source": _PHASE8_PART37_SOURCE,
        "artifact_kind": "player_turn_narration",
        "staleness_policy": "append_only_by_turn_id",
        "is_append_only_visible_response": True,
    }
    try:
        runtime_state = _store_narration_artifact(runtime_state, artifact)
    except Exception:
        artifacts = _safe_list(runtime_state.get("narration_artifacts"))
        artifacts.append(artifact)
        runtime_state["narration_artifacts"] = artifacts[-50:]
        by_turn[turn_id] = artifact
        runtime_state["narration_artifacts_by_turn"] = by_turn
    session["runtime_state"] = runtime_state
    try:
        save_runtime_session(session)
    except Exception:
        logger.debug("suppressed error in %s", "_phase8_part37_persist_llm_artifact", exc_info=True)
        return


def _phase8_part37_patch_completed_llm_visible(payload: Any, *, session_id: str = "") -> Any:
    if not isinstance(payload, dict):
        return payload
    fields = _phase8_part37_completed_llm_fields(payload)
    if not fields:
        return payload

    patched = dict(payload)
    for key, value in fields.items():
        if key in {"raw_llm_narrative", "npc"} and not value:
            continue
        patched[key] = value

    for nested_key in ("result", "authoritative", "payload"):
        nested = _safe_dict(patched.get(nested_key))
        if not nested:
            continue
        nested = dict(nested)
        for key, value in fields.items():
            if key in {"raw_llm_narrative", "npc"} and not value:
                continue
            nested[key] = value
        patched[nested_key] = nested

    if not _safe_dict(patched.get("result")):
        patched["result"] = {key: value for key, value in patched.items() if key != "authoritative"}
    if not _safe_dict(patched.get("authoritative")):
        patched["authoritative"] = dict(_safe_dict(patched.get("result")))

    _phase8_part37_persist_llm_artifact(session_id, patched, fields)
    return patched


def _patch_turn_bound_narration(
    session_id: str,
    player_input: str,
    action: dict[str, Any] | None = None,
    *,
    performance_override: dict[str, Any] | None = None,
    _base_authoritative: Any = _PHASE8_PART37_BASE_APPLY_TURN_AUTHORITATIVE,
) -> dict[str, Any]:
    payload = _base_authoritative(
        session_id,
        player_input,
        action,
        performance_override=performance_override,
    )
    return _phase8_part37_patch_completed_llm_visible(payload, session_id=session_id)


__all__ = ['_PHASE8_PART37_BASE_APPLY_TURN_AUTHORITATIVE', '_PHASE8_PART37_LLM_SOURCES', '_PHASE8_PART37_SOURCE', '_patch_turn_bound_narration', '_phase8_part34_has_structured_llm_payload', '_phase8_part37_clean_text', '_phase8_part37_completed_llm_fields', '_phase8_part37_patch_completed_llm_visible', '_phase8_part37_persist_llm_artifact', '_phase8_part37_tick', '_phase8_part37_turn_id']