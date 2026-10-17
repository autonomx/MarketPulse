from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.runtime.clock import Clock as _Clock
from app.runtime.clock import SYSTEM_CLOCK as _SYSTEM_CLOCK
from app.runtime.clock import TurnContext as _TurnContext
from app.runtime.clock import bind_turn_context as _bind_turn_context
from app.apps.rpg.foundation.core.determinism import rng_seed_from_session_id as _rng_seed_from_session_id
from app.apps.rpg.session.response_builder import (
    build_apply_turn_response as _PHASE8_PART40_BASE_BUILD_APPLY_TURN_RESPONSE,
)

from .causal_turn_runtime import advance_causal_runtime_for_turn
from .fast_combat_narration_skip import fast_combat_narration_scope
from .session_runtime_store import load_runtime_session, save_runtime_session
from .player_turn_execution import apply_turn as _PHASE8_PART40_BASE_APPLY_TURN
from .turn_authoritative_guards import _canonicalize_publication, _persist_soft_truth
from app.apps.rpg.foundation.safe_values import safe_str as _safe_str
from app.apps.rpg.foundation.safe_values import mapping_copy as _safe_dict


def build_apply_turn_response(
    authoritative_result: dict[str, Any],
    _base_builder: Any = _PHASE8_PART40_BASE_BUILD_APPLY_TURN_RESPONSE,
) -> dict[str, Any]:
    """Retain the authoritative queued fallback through later presentation selection."""

    payload = _base_builder(authoritative_result)
    source = _safe_dict(authoritative_result)
    authoritative = _safe_dict(source.get("authoritative"))
    source_result = _safe_dict(source.get("result"))
    fallback = _safe_str(
        authoritative.get("deterministic_fallback_narration")
        or source_result.get("deterministic_fallback_narration")
    ).strip()
    if not fallback:
        return payload

    result = dict(_safe_dict(payload))
    nested = dict(_safe_dict(result.get("result")))
    nested["deterministic_fallback_narration"] = fallback
    result["result"] = nested
    result["deterministic_fallback_narration"] = fallback
    return result


def _queued_narration_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    result = _safe_dict(payload)
    nested = _safe_dict(result.get("result"))
    authoritative = _safe_dict(result.get("authoritative"))
    resolved = _safe_dict(
        authoritative.get("resolved_result")
        or nested.get("resolved_result")
        or result.get("resolved_result")
    )
    status = _safe_str(
        nested.get("narration_status") or result.get("narration_status")
    ).casefold()
    narration = _safe_str(
        nested.get("deterministic_fallback_narration")
        or result.get("deterministic_fallback_narration")
        or authoritative.get("deterministic_fallback_narration")
        or resolved.get("deterministic_fallback_narration")
        or nested.get("narration")
        or result.get("narration")
    ).strip()
    if status not in {"queued", "pending"} or not narration:
        return {}
    return {
        "narration": narration,
        "narration_status": status,
        "raw_llm_narrative": _safe_str(
            nested.get("raw_llm_narrative") or result.get("raw_llm_narrative")
        ),
        "used_llm": bool(nested.get("used_llm") or result.get("used_llm")),
        "llm_called": bool(nested.get("llm_called") or result.get("llm_called")),
    }


def _restore_queued_narration(
    payload: dict[str, Any],
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    if not snapshot:
        return payload
    result = dict(_safe_dict(payload))
    result["narration"] = snapshot["narration"]
    result["narration_status"] = snapshot["narration_status"]
    result["raw_llm_narrative"] = snapshot["raw_llm_narrative"]
    result["used_llm"] = snapshot["used_llm"]
    result["llm_called"] = snapshot["llm_called"]
    result["presentation_narration_selection"] = {
        "source": "canonical_deferred_fallback",
        "runtime_payload_source": "deferred_runtime_narration_pending",
    }

    narration_payload = dict(
        _safe_dict(result.get("narration_payload") or result.get("structured_narration"))
    )
    if narration_payload:
        narration_payload["narration"] = snapshot["narration"]
        narration_payload["source"] = "deferred_runtime_narration_pending"
        narration_payload["deferred"] = True
        narration_payload["narration_status"] = snapshot["narration_status"]
        result["narration_payload"] = narration_payload
        result["structured_narration"] = deepcopy(narration_payload)

    nested = dict(_safe_dict(result.get("result")))
    if nested:
        nested["narration"] = snapshot["narration"]
        nested["narration_status"] = snapshot["narration_status"]
        nested["raw_llm_narrative"] = snapshot["raw_llm_narrative"]
        nested["used_llm"] = snapshot["used_llm"]
        nested["llm_called"] = snapshot["llm_called"]
        nested["presentation_narration_selection"] = deepcopy(
            result["presentation_narration_selection"]
        )
        if narration_payload:
            nested["narration_payload"] = deepcopy(narration_payload)
            nested["structured_narration"] = deepcopy(narration_payload)
        result["result"] = nested
    return result


def _advance_causal_turn(payload: dict[str, Any], session_id: str) -> dict[str, Any]:
    try:
        return advance_causal_runtime_for_turn(
            session_id,
            payload,
            loader=load_runtime_session,
            saver=save_runtime_session,
        )
    except Exception as exc:
        result = deepcopy(_safe_dict(payload))
        receipt = {
            "applied": False,
            "reason": "causal_runtime_error",
            "error": f"{type(exc).__name__}: {exc}",
        }
        result["causal_world_runtime"] = receipt
        nested = dict(_safe_dict(result.get("result")))
        if nested:
            nested["causal_world_runtime"] = deepcopy(receipt)
            result["result"] = nested
        return result


def apply_turn(
    session_id: str,
    player_input: str,
    action: dict[str, Any] | None = None,
    *,
    performance_override: dict[str, Any] | None = None,
    clock: _Clock = _SYSTEM_CLOCK,
    _base_apply_turn: Any = _PHASE8_PART40_BASE_APPLY_TURN,
) -> dict[str, Any]:
    session = load_runtime_session(session_id)
    simulation_state = _safe_dict(_safe_dict(session).get("simulation_state"))
    runtime_state = _safe_dict(_safe_dict(session).get("runtime_state"))
    session_seed = simulation_state.get("rng_seed")
    if not isinstance(session_seed, int) or isinstance(session_seed, bool):
        session_seed = _rng_seed_from_session_id(session_id)
    turn_index = simulation_state.get("turn_index")
    if not isinstance(turn_index, int) or isinstance(turn_index, bool):
        history = runtime_state.get("turn_history")
        turn_index = len(history) if isinstance(history, list) else 0
    turn_context = _TurnContext.capture(
        clock,
        session_seed=session_seed,
        turn_index=turn_index,
        session_id=session_id,
    )
    with fast_combat_narration_scope(action, performance_override) as (
        turn_action,
        turn_performance_override,
    ):
        with _bind_turn_context(turn_context):
            payload = _base_apply_turn(
                session_id,
                player_input,
                turn_action,
                performance_override=turn_performance_override,
            )
    queued = _queued_narration_snapshot(payload)
    canonical = _canonicalize_publication(payload, player_input=player_input)
    canonical = _restore_queued_narration(canonical, queued)
    canonical = _persist_soft_truth(canonical, session_id)
    return _advance_causal_turn(canonical, session_id)


__all__ = ['_Clock', '_SYSTEM_CLOCK', '_TurnContext', '_bind_turn_context', '_rng_seed_from_session_id', '_safe_dict', '_safe_str', 'apply_turn', 'build_apply_turn_response']