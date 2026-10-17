"""Adventure setup world inspection and deterministic simulation helpers.

World lore generation is intentionally absent. Durable World Forge is the only
production generation authority.
"""
from __future__ import annotations

from typing import Any

from app.apps.rpg.genesis.creator.defaults import apply_adventure_defaults
from app.apps.rpg.genesis.creator.world_player_actions import apply_player_action
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict


def inspect_world(payload: dict[str, Any]) -> dict[str, Any]:
    from app.apps.rpg.genesis.creator.world_graph import inspect_world as _inspect

    return _inspect(apply_adventure_defaults(dict(payload or {})))


def inspect_world_snapshot(
    payload: dict[str, Any],
    label: str | None = None,
) -> dict[str, Any]:
    from app.apps.rpg.genesis.creator.world_snapshot import build_world_snapshot

    data = apply_adventure_defaults(dict(payload or {}))
    return {"success": True, "snapshot": build_world_snapshot(data, label=label)}


def compare_world(
    payload_before: dict[str, Any],
    payload_after: dict[str, Any],
) -> dict[str, Any]:
    from app.apps.rpg.genesis.creator.world_snapshot import build_world_snapshot, compute_graph_diff

    before = build_world_snapshot(
        apply_adventure_defaults(dict(payload_before or {})), label="Before"
    )
    after = build_world_snapshot(
        apply_adventure_defaults(dict(payload_after or {})), label="After"
    )
    return {
        "success": True,
        "before_snapshot_id": before["snapshot_id"],
        "after_snapshot_id": after["snapshot_id"],
        "diff": compute_graph_diff(before["graph"], after["graph"]),
    }


def compare_world_entity(
    payload_before: dict[str, Any],
    payload_after: dict[str, Any],
    entity_id: str,
) -> dict[str, Any]:
    from app.apps.rpg.genesis.creator.world_snapshot import (
        build_world_snapshot,
        compute_entity_history_diff,
    )

    before = build_world_snapshot(
        apply_adventure_defaults(dict(payload_before or {})), label="Before"
    )
    after = build_world_snapshot(
        apply_adventure_defaults(dict(payload_after or {})), label="After"
    )
    return {
        "success": True,
        **compute_entity_history_diff(
            before["inspector"], after["inspector"], entity_id
        ),
    }


def advance_world_simulation(payload: dict[str, Any]) -> dict[str, Any]:
    from app.apps.rpg.genesis.creator.world_graph import inspect_world as _inspect
    from app.apps.rpg.genesis.creator.world_scene_generator import generate_scenes_from_simulation
    from app.apps.rpg.genesis.creator.world_simulation import step_simulation_state
    from app.apps.rpg.session.conversation_hooks import SESSION_CONVERSATION_HOOKS

    data = apply_adventure_defaults(dict(payload or {}))
    step = step_simulation_state(data, conversation_hooks=SESSION_CONVERSATION_HOOKS)
    next_setup = step["next_setup"]
    after_state = step["after_state"]
    inspection = _inspect(next_setup)
    return {
        "success": True,
        **step,
        "updated_setup": next_setup,
        "simulation_state": after_state,
        "simulation_state_base": step.get("after_state_base"),
        "scenes": generate_scenes_from_simulation(after_state),
        "graph": inspection.get("graph"),
        "simulation": inspection.get("simulation"),
        "inspector": inspection.get("inspector"),
    }


def get_simulation_state(payload: dict[str, Any]) -> dict[str, Any]:
    from app.apps.rpg.genesis.creator.world_simulation import build_initial_simulation_state

    data = apply_adventure_defaults(dict(payload or {}))
    metadata = data.get("metadata") or {}
    state = metadata.get("simulation_state")
    if not state or "tick" not in state:
        state = build_initial_simulation_state(data)
    return {"success": True, "simulation_state": state}


def apply_player_action_endpoint(payload: dict[str, Any]) -> dict[str, Any]:
    from app.apps.rpg.genesis.creator.world_graph import inspect_world as _inspect
    from app.apps.rpg.genesis.creator.world_scene_generator import generate_scenes_from_simulation
    from app.apps.rpg.genesis.creator.world_simulation import (
        build_initial_simulation_state,
        step_simulation_state,
    )
    from app.apps.rpg.session.conversation_hooks import SESSION_CONVERSATION_HOOKS

    setup = _safe_dict(payload.get("setup"))
    action = _safe_dict(payload.get("action"))
    metadata = _safe_dict(setup.get("metadata"))
    state = _safe_dict(metadata.get("simulation_state"))
    if not state or "tick" not in state:
        state = build_initial_simulation_state(
            apply_adventure_defaults(dict(setup))
        )
    metadata["simulation_state"] = apply_player_action(state, action)
    setup["metadata"] = metadata
    step = step_simulation_state(setup, conversation_hooks=SESSION_CONVERSATION_HOOKS)
    next_setup = step["next_setup"]
    after_state = step["after_state"]
    inspection = _inspect(next_setup)
    return {
        "success": True,
        **step,
        "updated_setup": next_setup,
        "simulation_state": after_state,
        "scenes": generate_scenes_from_simulation(after_state),
        "graph": inspection.get("graph"),
        "simulation": inspection.get("simulation"),
        "inspector": inspection.get("inspector"),
    }


def _legacy_generation_removed(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    """Compatibility import guard for callers not yet restarted after deployment."""

    raise RuntimeError(
        "legacy_adventure_world_generation_removed:use_durable_world_forge"
    )


# Import-compatible names remain fail-closed during one code transition. They are not
# registered as HTTP routes and cannot return generated or fallback lore.
generate_world_proposal = _legacy_generation_removed
regenerate_world_section = _legacy_generation_removed
regenerate_world_entity = _legacy_generation_removed
apply_generated_package = _legacy_generation_removed


__all__ = [
    "advance_world_simulation",
    "apply_player_action_endpoint",
    "compare_world",
    "compare_world_entity",
    "get_simulation_state",
    "inspect_world",
    "inspect_world_snapshot",
]
