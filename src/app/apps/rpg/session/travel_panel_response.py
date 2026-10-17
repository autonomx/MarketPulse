from __future__ import annotations

from typing import Any

# RPG session runtime responsibility module.
# Phase 8.29 follow-up: keep the visible narration fallback wrapper from
# leaking a generic _base_apply_turn_authoritative symbol into the runtime
# facade. The facade intentionally mirrors globals back into all split modules,
# so generic base aliases can accidentally rewrite earlier wrappers into
# self-recursion. This part owns the final authoritative turn wrapper with
# phase-specific captured defaults.
from .combat_quest_narrative import _apply_combat_quest_narrative as _PHASE8_PART29_BASE_APPLY_TURN_AUTHORITATIVE
from .turn_payload_projection import (
    _apply_phase4_session_travel_command,
    _copy_dict,
    _ensure_simulation_state,
    _phase8_combat_panel_payload,
    _phase8_objective_journal_panel_payload,
    _phase8_player_visible_hud_payload,
    _safe_dict,
    _safe_str,
    load_runtime_session,
)
from .visible_narration_fallback import _phase8_patch_visible_fallback

_PHASE8_PART29_SOURCE = "deterministic_phase8_authoritative_turn_fallback_recursion_guard"


def _phase8_attach_player_input(payload: Any, player_input: str) -> Any:
    if not isinstance(payload, dict):
        return payload
    player_input = _safe_str(player_input).strip()
    if not player_input:
        return payload
    payload.setdefault("player_input", player_input)
    for key in ("result", "authoritative", "payload"):
        nested = _safe_dict(payload.get(key))
        if nested:
            patched = dict(nested)
            patched.setdefault("player_input", player_input)
            payload[key] = patched
    return payload


def _apply_visible_fallback_with_travel_panels(
    session_id: str,
    player_input: str,
    action: dict[str, Any] | None = None,
    *,
    performance_override: dict[str, Any] | None = None,
    _base_authoritative: Any = _PHASE8_PART29_BASE_APPLY_TURN_AUTHORITATIVE,
    _fallback_patch: Any = _phase8_patch_visible_fallback,
) -> dict[str, Any]:
    """Apply a player turn without depending on mutable facade base aliases.

    runtime.py mirrors the final merged globals into every runtime_partXX module.
    Earlier wrappers that call a global named _base_apply_turn_authoritative can
    be rewritten by later parts and recurse into themselves. This final wrapper
    mirrors turn_payload_projection behavior but captures the Phase 26 base as a default
    argument, then applies the Phase 8.29 visible fallback patch.
    """

    session = load_runtime_session(session_id)
    if session is None:
        base_payload = _base_authoritative(
            session_id,
            player_input,
            action,
            performance_override=performance_override,
        )
        return _fallback_patch(_phase8_attach_player_input(base_payload, player_input))

    session = _copy_dict(session)
    simulation_state = _ensure_simulation_state(_safe_dict(session.get("simulation_state")))
    runtime_state = _copy_dict(session.get("runtime_state"))

    travel_payload = _apply_phase4_session_travel_command(
        session_id,
        _safe_str(player_input).strip(),
        session=session,
        simulation_state=simulation_state,
        runtime_state=runtime_state,
    )
    if travel_payload:
        travel_payload.setdefault("recursion_guard_source", _PHASE8_PART29_SOURCE)
        return _fallback_patch(_phase8_attach_player_input(travel_payload, player_input))

    base_payload = _base_authoritative(
        session_id,
        player_input,
        action,
        performance_override=performance_override,
    )
    if isinstance(base_payload, dict):
        base_payload = _phase8_attach_player_input(base_payload, player_input)
        base_payload.setdefault("player_hud", _phase8_player_visible_hud_payload(simulation_state, runtime_state))
        base_payload.setdefault(
            "objective_journal_panel",
            _phase8_objective_journal_panel_payload(simulation_state, runtime_state),
        )
        base_payload.setdefault("combat_action_panel", _phase8_combat_panel_payload(simulation_state, runtime_state))
        base_payload.setdefault("recursion_guard_source", _PHASE8_PART29_SOURCE)
    return _fallback_patch(base_payload)


__all__ = ['_PHASE8_PART29_BASE_APPLY_TURN_AUTHORITATIVE', '_PHASE8_PART29_SOURCE', '_apply_visible_fallback_with_travel_panels', '_phase8_attach_player_input']