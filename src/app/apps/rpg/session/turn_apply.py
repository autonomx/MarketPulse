from __future__ import annotations

from app.apps.rpg.foundation.llm_app_gateway import (
    build_app_llm_gateway as build_app_llm_gateway,
)


from types import SimpleNamespace
from typing import Any

from .turn_action_resolution import (
    _prepare_turn_action_context,
    _record_turn_policy_and_apply_advisory,
    _resolve_semantic_action_and_special_turns,
)
from .turn_combat_resolution import (
    _normalize_active_combat_action,
    _resolve_companion_reposition_and_authoritative_action,
    _resolve_companion_reposition_and_authoritative_action_continued_1,
    _normalize_active_combat_action_continued_1,
)
from .turn_finalization import (
    _finish_scene_and_persist_turn_state,
    _prepare_narration_response,
)
from .turn_world_resolution import (
    _advance_turn_contract_and_simulation,
    _apply_ambient_conversation_and_world_effects,
    _update_interactions_and_combat_fallback,
    _update_interactions_and_combat_fallback_continued_1,
)


def _get_llm_gateway(ctx):
    if not ctx._llm_gw_holder:
        ctx._llm_gw_holder.append(build_app_llm_gateway())
    return ctx._llm_gw_holder[0]


_TURN_APPLY_STAGES = (
    _prepare_turn_action_context,
    _record_turn_policy_and_apply_advisory,
    _resolve_semantic_action_and_special_turns,
    _resolve_companion_reposition_and_authoritative_action,
    _resolve_companion_reposition_and_authoritative_action_continued_1,
    _normalize_active_combat_action,
    _normalize_active_combat_action_continued_1,
    _apply_ambient_conversation_and_world_effects,
    _advance_turn_contract_and_simulation,
    _update_interactions_and_combat_fallback,
    _update_interactions_and_combat_fallback_continued_1,
    _finish_scene_and_persist_turn_state,
    _prepare_narration_response,
)


def _apply_turn_authoritative_base(
    session_id: str,
    player_input: str,
    action: dict[str, Any] | None = None,
    *,
    performance_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ctx = SimpleNamespace(
        session_id=session_id,
        player_input=player_input,
        action=action,
        performance_override=performance_override,
        _llm_gw_holder=[],
    )
    ctx._get_llm_gateway = lambda: _get_llm_gateway(ctx)
    for stage in _TURN_APPLY_STAGES:
        if stage(ctx):
            return ctx._result
    raise RuntimeError("turn resolution stages ended without a response")
