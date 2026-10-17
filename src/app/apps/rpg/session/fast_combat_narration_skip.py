from __future__ import annotations

import logging

import contextvars
from contextlib import contextmanager
from typing import Any, Iterator
from app.apps.rpg.foundation.safe_values import dict_copy as _safe_dict, safe_str as _safe_str

logger = logging.getLogger(__name__)

_FAST_DIRECT_SOURCES = {
    "ce211_fast_direct_runtime_budget_v1",
    "ce212_fast_direct_runtime_budget_v1",
}
_FAST_COMBAT_SKIP_CONTEXT: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "ce212_fast_combat_skip_context",
    default=False,
)


def _safe_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _first_present_int(*values: Any) -> int | None:
    for value in values:
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str):
            try:
                return int(value.strip())
            except Exception:
                logger.debug("suppressed error in %s", "_first_present_int", exc_info=True)
                continue
    return None


def _contains_fast_direct_marker(value: Any, *, depth: int = 0) -> bool:
    if depth > 6:
        return False
    if isinstance(value, dict):
        source = _safe_str(value.get("source") or value.get("fast_direct_source"))
        if source in _FAST_DIRECT_SOURCES:
            return True
        if value.get("fast_direct_runtime") is True or value.get("skip_sync_combat_narration") is True:
            return True
        metadata = value.get("metadata")
        if isinstance(metadata, dict) and _contains_fast_direct_marker(metadata, depth=depth + 1):
            return True
        for key in (
            "turn_contract",
            "action",
            "first_call_action_advisory",
            "first_call_grounding_diagnostics",
            "turn_grounding_packet",
            "fast_direct_action",
            "semantic_action",
        ):
            if key in value and _contains_fast_direct_marker(value.get(key), depth=depth + 1):
                return True
    elif isinstance(value, list):
        return any(_contains_fast_direct_marker(item, depth=depth + 1) for item in value[:20])
    return False


def _is_combat_action(action: dict[str, Any]) -> bool:
    action_type = _safe_str(action.get("action_type") or action.get("type")).strip().lower()
    if action_type == "combat":
        return True
    target = _safe_str(action.get("target_id") or action.get("target_name")).strip().lower()
    requested = " ".join(_safe_str(term).lower() for term in action.get("requested_terms") or [])
    return "bandit" in target and "attack" in requested


def _action_requests_fast_combat_skip(action: Any, performance_override: Any = None) -> bool:
    action_dict = _safe_dict(action)
    performance = _safe_dict(performance_override)
    if performance.get("skip_sync_combat_narration") is True or performance.get("fast_direct_runtime") is True:
        return True
    if _contains_fast_direct_marker(action_dict):
        return True
    return _safe_bool(performance.get("fast_turn_mode")) and _is_combat_action(action_dict)


def _should_skip(payload: dict[str, Any], combat_state: dict[str, Any]) -> bool:
    if _FAST_COMBAT_SKIP_CONTEXT.get(False):
        return True
    payload = _safe_dict(payload)
    combat_state = _safe_dict(combat_state)
    if payload.get("skip_sync_combat_narration") or payload.get("fast_direct_runtime"):
        return True
    if combat_state.get("skip_sync_combat_narration") or combat_state.get("fast_direct_runtime"):
        return True
    if _safe_str(payload.get("fast_direct_source")) in _FAST_DIRECT_SOURCES:
        return True
    if _safe_str(combat_state.get("fast_direct_source")) in _FAST_DIRECT_SOURCES:
        return True
    return _contains_fast_direct_marker(payload) or _contains_fast_direct_marker(combat_state)


def _combat_delta_contract(combat_result: dict[str, Any], combat_state: dict[str, Any]) -> dict[str, Any]:
    combat_result = _safe_dict(combat_result)
    combat_state = _safe_dict(combat_state)
    delta = {
        "source": "deterministic_combat_delta_contract_v1",
        "action_type": _safe_str(combat_result.get("action_type") or combat_result.get("action")),
        "reason": _safe_str(combat_result.get("reason") or combat_result.get("result") or combat_result.get("outcome")),
        "actor_id": _safe_str(combat_result.get("actor_id") or combat_result.get("attacker_id") or combat_state.get("actor_id")),
        "target_id": _safe_str(combat_result.get("target_id") or combat_result.get("defender_id") or combat_state.get("target_id")),
        "target_name": _safe_str(combat_result.get("target_name") or combat_state.get("target_name") or "bandit"),
        "damage_applied": _first_present_int(
            combat_result.get("damage_applied"),
            combat_result.get("damage"),
            combat_result.get("damage_dealt"),
            combat_state.get("last_damage"),
        ),
        "target_hp_before": _first_present_int(
            combat_result.get("target_hp_before"),
            combat_result.get("enemy_hp_before"),
            combat_state.get("target_hp_before"),
            combat_state.get("enemy_hp_before"),
        ),
        "target_hp_after": _first_present_int(
            combat_result.get("target_hp_after"),
            combat_result.get("enemy_hp_after"),
            combat_result.get("enemy_hp"),
            combat_state.get("target_hp_after"),
            combat_state.get("enemy_hp_after"),
            combat_state.get("enemy_hp"),
        ),
        "defeated": bool(
            combat_result.get("defeated")
            or combat_result.get("enemy_defeated")
            or combat_result.get("combat_ended")
            or combat_state.get("defeated")
            or combat_state.get("enemy_defeated")
        ),
        "combat_ended": bool(
            combat_result.get("combat_ended")
            or combat_result.get("ended")
            or combat_state.get("combat_ended")
            or combat_state.get("ended")
        ),
    }
    return {key: value for key, value in delta.items() if value not in (None, "")}


def _fallback_summary(combat_result: dict[str, Any], combat_state: dict[str, Any] | None = None) -> str:
    combat_result = _safe_dict(combat_result)
    combat_state = _safe_dict(combat_state)
    delta = _combat_delta_contract(combat_result, combat_state)
    target_name = _safe_str(delta.get("target_name") or "enemy").strip() or "enemy"
    damage = delta.get("damage_applied")
    hp_after = delta.get("target_hp_after")
    defeated = bool(delta.get("defeated") or delta.get("combat_ended"))

    if isinstance(damage, int) and damage > 0:
        if defeated:
            return f"You hit the {target_name} for {damage} damage and defeat them."
        if isinstance(hp_after, int):
            return f"You hit the {target_name} for {damage} damage. The {target_name} has {hp_after} HP remaining."
        return f"You hit the {target_name} for {damage} damage."
    if defeated:
        return f"You defeat the {target_name}."
    for key in ("summary", "result", "outcome", "reason", "action_type"):
        text = _safe_str(combat_result.get(key)).strip()
        if text:
            return f"Result: {text}"
    return "Result: combat_action_resolved"


def _build_contract(builder: Any, combat_result: dict[str, Any], combat_state: dict[str, Any]) -> dict[str, Any]:
    if not callable(builder):
        builder = getattr(builder, "build_combat_narration_contract", None)
    if callable(builder):
        try:
            return _safe_dict(builder(combat_result=combat_result, combat_state=combat_state))
        except Exception:
            return {}
    return {}


def _deterministic_payload(narration: str, combat_delta: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "source": "deterministic_combat_fast_summary",
        "narration": narration,
        "npc": {},
        "combat_delta": _safe_dict(combat_delta),
    }


def _stale_or_empty_fast_combat_text(value: Any) -> bool:
    text = _safe_str(value).strip().casefold()
    return not text or "no injury is resolved" in text or "no injury was resolved" in text


def _apply_fast_skip(
    contract_builder: Any,
    payload: dict[str, Any],
    *,
    combat_result: dict[str, Any],
    combat_state: dict[str, Any],
) -> dict[str, Any]:
    payload = _safe_dict(payload)
    combat_result = _safe_dict(combat_result)
    combat_state = _safe_dict(combat_state)
    combat_delta = _combat_delta_contract(combat_result, combat_state)
    narration = _fallback_summary(combat_result, combat_state)
    deterministic_payload = _deterministic_payload(narration, combat_delta)

    payload["combat_narration_attempted"] = False
    payload["combat_narration_skipped_for_fast_mode"] = True
    payload["combat_narration_skip_source"] = "ce212_fast_combat_narration_skip_v1"
    payload["combat_delta_contract"] = dict(combat_delta)
    payload["llm_called"] = False
    payload["llm_purpose"] = "deterministic_combat_fast_summary"
    payload["combat_narration_error"] = ""
    payload["combat_narration_contract"] = _build_contract(contract_builder, combat_result, combat_state)
    payload["combat_narration_validation"] = {
        "ok": False,
        "warnings": ["combat_narration_skipped_for_fast_mode"],
        "source": "ce212_fast_combat_narration_skip_v1",
    }
    payload["combat_narration_payload"] = dict(deterministic_payload)
    payload["narration_payload"] = dict(deterministic_payload)
    payload["structured_narration"] = dict(deterministic_payload)
    payload["combat_narration_accepted"] = False
    payload["combat_narration_rejected"] = False

    for key in ("narration", "final_narration", "narration_preview", "raw_payload_narration"):
        if _stale_or_empty_fast_combat_text(payload.get(key)):
            payload[key] = narration
    nested = _safe_dict(payload.get("result"))
    if nested:
        nested["combat_narration_skipped_for_fast_mode"] = True
        nested["combat_narration_skip_source"] = "ce212_fast_combat_narration_skip_v1"
        nested["combat_delta_contract"] = dict(combat_delta)
        nested["combat_narration_payload"] = dict(deterministic_payload)
        nested["narration_payload"] = dict(deterministic_payload)
        nested["structured_narration"] = dict(deterministic_payload)
        for key in ("narration", "final_narration", "narration_preview", "raw_payload_narration"):
            if _stale_or_empty_fast_combat_text(nested.get(key)):
                nested[key] = narration
        payload["result"] = nested
    return payload


def _with_fast_combat_flags(
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    *,
    action: Any,
    performance_override: Any,
) -> tuple[tuple[Any, ...], dict[str, Any]]:
    patched_kwargs = dict(kwargs)
    patched_performance = _safe_dict(performance_override)
    patched_performance["skip_sync_combat_narration"] = True
    patched_performance["fast_direct_runtime"] = True
    patched_kwargs["performance_override"] = patched_performance

    if isinstance(action, dict):
        patched_action = dict(action)
        metadata = _safe_dict(patched_action.get("metadata"))
        metadata["skip_sync_combat_narration"] = True
        metadata["fast_direct_runtime"] = True
        metadata.setdefault("source", "ce212_fast_direct_runtime_budget_v1")
        patched_action["metadata"] = metadata
        if "action" in patched_kwargs:
            patched_kwargs["action"] = patched_action
        elif len(args) >= 3:
            patched_args = list(args)
            patched_args[2] = patched_action
            args = tuple(patched_args)
        else:
            patched_kwargs["action"] = patched_action
    return args, patched_kwargs


def apply_fast_combat_narration_skip(
    payload: dict[str, Any],
    *,
    combat_result: dict[str, Any],
    combat_state: dict[str, Any],
    contract_builder: Any,
) -> dict[str, Any] | None:
    """Return a deterministic payload when fast combat skips provider narration."""
    if not _should_skip(_safe_dict(payload), _safe_dict(combat_state)):
        return None
    return _apply_fast_skip(
        contract_builder,
        payload,
        combat_result=combat_result,
        combat_state=combat_state,
    )


@contextmanager
def fast_combat_narration_scope(
    action: Any,
    performance_override: Any,
) -> Iterator[tuple[Any, Any]]:
    """Bind fast-direct combat metadata for one explicitly composed turn call."""
    if not _action_requests_fast_combat_skip(action, performance_override):
        yield action, performance_override
        return

    _, patched_kwargs = _with_fast_combat_flags(
        (),
        {"action": action},
        action=action,
        performance_override=performance_override,
    )
    token = _FAST_COMBAT_SKIP_CONTEXT.set(True)
    try:
        yield patched_kwargs.get("action", action), patched_kwargs["performance_override"]
    finally:
        _FAST_COMBAT_SKIP_CONTEXT.reset(token)
