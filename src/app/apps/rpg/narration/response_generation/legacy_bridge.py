from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Mapping

from app.apps.rpg.narration.ai.world_scene_narrator_runtime import (
    SceneNarrator as _LegacySceneNarrator,
    apply_legacy_narration_emphasis,
    narrate_scene as _legacy_narrate_scene,
)

from .contracts import ResponseMode, coerce_response_mode
from .production_pipeline import CANONICAL_NARRATION_SOURCE
from .strict_pipeline import (
    AuthoritativeProfileBoundProvider,
    StrictRpgProductionResponsePipeline,
)


_CONTEXT_CONTROL_KEYS = (
    "turn_id",
    "response_rollout_stage",
    "response_mode",
    "resolver_status",
    "recovery_needed",
    "mechanic_resolved",
    "clear_player_intent",
    "state_delta",
    "resolved_result",
    "provider_policy",
)


def _pipeline() -> StrictRpgProductionResponsePipeline:
    return StrictRpgProductionResponsePipeline()


def _inherit_context_controls(
    context: Mapping[str, Any],
    contract: Mapping[str, Any] | None,
    state: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    enriched_contract = dict(contract or {})
    enriched_state = dict(state or {})
    for key in _CONTEXT_CONTROL_KEYS:
        value = context.get(key)
        if value not in (None, "", [], {}):
            enriched_contract.setdefault(key, value)
    rollout = context.get("response_rollout_stage")
    if rollout not in (None, ""):
        enriched_state.setdefault("response_rollout_stage", rollout)
    for key in ("session_id", "world_id", "scene_id", "location_id"):
        value = context.get(key)
        if value not in (None, ""):
            enriched_state.setdefault(key, value)
    return enriched_contract, enriched_state


def narrate_scene_canonical(
    scene: Mapping[str, Any],
    narration_context: Mapping[str, Any],
    llm_gateway: Any | None = None,
    tone: str = "dramatic",
    retry_on_invalid: bool = True,
    debug_logging: bool = False,
    on_chunk: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Compatibility entry point with strict canonical publication ownership."""

    scene_payload = dict(scene or {})
    context = dict(narration_context or {})
    player_input = str(context.get("player_input") or "")
    raw_contract, raw_state = _inherit_context_controls(
        context,
        _mapping(context.get("turn_contract")),
        _mapping(
            context.get("simulation_state")
            or context.get("state_snapshot")
            or context.get("state")
        ),
    )
    raw_state.setdefault("scene_id", scene_payload.get("scene_id"))
    pipeline = _pipeline()
    prepared = pipeline.prepare_generation_inputs(
        player_input=player_input,
        simulation_state=raw_state,
        turn_contract=raw_contract,
    )
    state = prepared["simulation_state"]
    turn_contract = prepared["turn_contract"]
    profile = prepared["profile"]
    ignored = prepared["ignored_profile_overrides"]
    context["simulation_state"] = state
    context["turn_contract"] = turn_contract
    context["narration_brief"] = turn_contract.get("narration_brief", {})
    context["response_profile"] = profile.debug_payload()
    context["response_rollout_stage"] = state.get(
        "response_rollout_stage",
        turn_contract.get("response_rollout_stage"),
    )
    mode = profile.mode
    recovery_needed = bool(
        context.get("recovery_needed")
        or str(context.get("resolver_status") or "").casefold()
        in {"unresolved", "partial", "unsupported", "no_match"}
        or mode in {ResponseMode.RECOVERY, ResponseMode.INVESTIGATION}
    )
    profile_gateway = (
        AuthoritativeProfileBoundProvider(llm_gateway, profile)
        if llm_gateway is not None and profile.use_provider
        else None
    )
    legacy = _legacy_narrate_scene(
        scene_payload,
        context,
        llm_gateway=profile_gateway,
        tone=tone,
        retry_on_invalid=bool(profile.retry_count and retry_on_invalid),
        debug_logging=debug_logging,
        on_chunk=None,
    )
    if not isinstance(legacy, Mapping):
        legacy = {"narration": str(legacy or "")}
    payload = dict(legacy)
    narration_json = _mapping(payload.get("narration_json"))
    npc = _mapping(narration_json.get("npc"))
    response_mode = _response_mode(context, narration_json)
    legacy_payload = {
        "source": "legacy_world_scene",
        "legacy_visible_text": str(payload.get("narration") or ""),
        "response_mode": response_mode.value,
        "narration": narration_json.get("narration") or payload.get("narration") or "",
        "action": narration_json.get("action") or "",
        "npc": {
            "speaker": npc.get("speaker") or npc.get("name") or "",
            "line": npc.get("line") or npc.get("text") or "",
        },
        "forward_strategy": context.get("forward_strategy") or "answer_directly",
        "agency_effect": context.get("agency_effect") or "none",
        "reversibility": context.get("reversibility") or "fully_reversible",
    }
    authoritative_result = {
        **turn_contract,
        "turn_id": context.get("turn_id") or turn_contract.get("turn_id"),
        "state_delta": _mapping(
            context.get("state_delta") or turn_contract.get("state_delta")
        ),
        "resolved_result": _mapping(
            context.get("resolved_result")
            or turn_contract.get("resolved_result")
            or turn_contract.get("resolved_action")
        ),
        "response_mode": response_mode.value,
        "production_rpg_response": True,
        "strict_claim_refs": True,
        "grounding_required": True,
        "mechanic_resolved": bool(
            context.get("mechanic_resolved")
            or turn_contract.get("mechanic_resolved")
        ),
        "recovery_needed": recovery_needed,
        "resolver_status": context.get("resolver_status") or turn_contract.get("resolver_status"),
        "speaker_id": npc.get("speaker") or "",
        "provider_policy": _mapping(
            context.get("provider_policy") or context.get("response_profile")
        ),
    }
    canonical = pipeline.finalize_payload(
        legacy_payload,
        player_input=player_input,
        authoritative_turn_result=authoritative_result,
        simulation_state=state,
        turn_contract=turn_contract,
        profile=profile,
        ignored_profile_overrides=ignored,
        provider_profile_applied=(
            dict(profile_gateway.applied) if profile_gateway is not None else {}
        ),
        runtime_mode=(
            "legacy_world_scene_recovery"
            if recovery_needed
            else "legacy_world_scene_compatibility"
        ),
        on_chunk=on_chunk,
    )
    payload.update(canonical)
    payload["canonical_response_source"] = CANONICAL_NARRATION_SOURCE
    return payload


class SceneNarrator(_LegacySceneNarrator):
    """Public scene narrator whose visible narrative is canonically validated."""

    def narrate_scene(  # type: ignore[override]  # adds generation controls
        self,
        scene: dict[str, Any],
        state: dict[str, Any],
        *,
        tone: str | None = None,
    ):
        pipeline = _pipeline()
        player_input = str(state.get("player_input") or "")
        raw_contract, raw_state = _inherit_context_controls(
            state,
            _mapping(state.get("turn_contract")),
            _mapping(state.get("simulation_state") or state.get("state_snapshot") or state),
        )
        raw_state.setdefault("scene_id", scene.get("scene_id"))
        prepared = pipeline.prepare_generation_inputs(
            player_input=player_input,
            simulation_state=raw_state,
            turn_contract=raw_contract,
        )
        generation_state = dict(state)
        generation_state["simulation_state"] = prepared["simulation_state"]
        generation_state["turn_contract"] = prepared["turn_contract"]
        generation_state["narration_brief"] = prepared["turn_contract"].get(
            "narration_brief", {}
        )
        generation_state["response_profile"] = prepared["profile"].debug_payload()
        generation_state["response_rollout_stage"] = prepared["simulation_state"].get(
            "response_rollout_stage",
            prepared["turn_contract"].get("response_rollout_stage"),
        )
        profile = prepared["profile"]
        ignored = prepared["ignored_profile_overrides"]
        mode = profile.mode
        original_gateway = self.llm_gateway
        profile_gateway = None
        if original_gateway is not None and profile.use_provider:
            profile_gateway = AuthoritativeProfileBoundProvider(original_gateway, profile)
            self.llm_gateway = profile_gateway
        try:
            result = super().narrate_scene(scene, generation_state, tone=tone)
        finally:
            self.llm_gateway = original_gateway
        canonical = pipeline.finalize_payload(
            {
                "source": "legacy_scene_narrator",
                "legacy_visible_text": result.narrative,
                "response_mode": mode.value,
                "narration": result.narrative,
                "npc": {},
            },
            player_input=player_input,
            authoritative_turn_result={
                **prepared["turn_contract"],
                "turn_id": state.get("turn_id") or prepared["turn_contract"].get("turn_id"),
                "response_mode": mode.value,
                "production_rpg_response": True,
                "strict_claim_refs": True,
                "grounding_required": True,
            },
            simulation_state=prepared["simulation_state"],
            turn_contract=prepared["turn_contract"],
            profile=profile,
            ignored_profile_overrides=ignored,
            provider_profile_applied=(
                dict(profile_gateway.applied) if profile_gateway is not None else {}
            ),
            runtime_mode="public_scene_narrator",
        )
        metadata = {
            **dict(result.metadata),
            "canonical_response": canonical["canonical_response"],
            "canonical_response_source": CANONICAL_NARRATION_SOURCE,
        }
        return replace(result, narrative=canonical["narration"], metadata=metadata)


def play_scene(
    scene: dict[str, Any],
    state: dict[str, Any],
    *,
    llm_gateway: Any | None = None,
    tone: str = "dramatic",
) -> dict[str, Any]:
    """Play a scene through the public canonical narrator wrapper."""

    narrator = SceneNarrator(
        llm_gateway=llm_gateway,
        default_tone=tone,
        simulate_mode=not bool(llm_gateway),
    )
    result = narrator.narrate_scene(scene, state, tone=tone)
    return {
        "narrative": result.narrative,
        "choices": result.choices,
        "npc_reactions": [
            {
                "npc_id": row.npc_id,
                "npc_name": row.npc_name,
                "dialogue": row.dialogue,
                "emotion": row.emotion,
                "intent": row.intent,
            }
            for row in result.npc_reactions
        ],
        "dialogue_blocks": result.dialogue_blocks,
        "metadata": result.metadata,
    }


def _response_mode(
    context: Mapping[str, Any],
    narration_json: Mapping[str, Any],
) -> ResponseMode:
    resolved = _mapping(context.get("resolved_result"))
    contract = _mapping(context.get("turn_contract"))
    return coerce_response_mode(
        narration_json.get("response_mode")
        or context.get("response_mode")
        or resolved.get("response_mode")
        or resolved.get("semantic_family")
        or contract.get("semantic_family")
        or resolved.get("action_type")
        or contract.get("action_type"),
        ResponseMode.RECOVERY
        if str(context.get("resolver_status") or "").casefold()
        in {"unresolved", "partial", "unsupported", "no_match"}
        else ResponseMode.ACTION,
    )


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


__all__ = [
    "CANONICAL_NARRATION_SOURCE",
    "SceneNarrator",
    "apply_legacy_narration_emphasis",
    "narrate_scene_canonical",
    "play_scene",
]
