from __future__ import annotations

import logging

import copy
from typing import Any, Dict, Iterable, List, Tuple

from app.apps.rpg.session.state_normalization import _safe_int, _safe_str
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

logger = logging.getLogger(__name__)

SURVIVAL_NEEDS: Tuple[str, str, str] = ("hunger", "thirst", "fatigue")
SURVIVAL_WARNING_THRESHOLD = 70
SURVIVAL_RELIEF_AMOUNT = 35
SURVIVAL_SOURCE_SUGGESTION = "n1263_live_runtime_survival_suggestion"
SURVIVAL_SOURCE_RELIEF = "n1263_live_runtime_survival_relief"
SURVIVAL_SOURCE_PROJECTION = "n1263_live_runtime_survival_projection"


def _clamp_need(value: Any) -> int:
    return max(0, min(100, _safe_int(value, 0)))


def _need_from_first(*values: Any) -> int:
    for value in values:
        if value is None:
            continue
        text = _safe_str(value).strip()
        if text == "":
            continue
        return _clamp_need(value)
    return 0


def _extract_survival_needs(simulation_state: Dict[str, Any]) -> Dict[str, int]:
    simulation_state = _safe_dict(simulation_state)
    climate = _safe_dict(simulation_state.get("climate_survival"))
    climate_survival = _safe_dict(climate.get("survival"))
    root_needs = _safe_dict(simulation_state.get("needs"))
    player_state = _safe_dict(simulation_state.get("player_state"))
    player_needs = _safe_dict(player_state.get("needs"))
    player_climate = _safe_dict(player_state.get("climate_survival"))
    player_climate_survival = _safe_dict(player_climate.get("survival"))

    return {
        need: _need_from_first(
            climate_survival.get(need),
            root_needs.get(need),
            player_needs.get(need),
            player_climate_survival.get(need),
        )
        for need in SURVIVAL_NEEDS
    }


def _survival_state_present(simulation_state: Dict[str, Any]) -> bool:
    simulation_state = _safe_dict(simulation_state)
    player_state = _safe_dict(simulation_state.get("player_state"))
    return bool(
        _safe_dict(simulation_state.get("climate_survival"))
        or _safe_dict(simulation_state.get("needs"))
        or _safe_dict(player_state.get("needs"))
        or _safe_dict(player_state.get("climate_survival"))
    )


def _warnings_for_needs(needs: Dict[str, int]) -> List[str]:
    warnings: List[str] = []
    for need in SURVIVAL_NEEDS:
        if _clamp_need(needs.get(need)) >= SURVIVAL_WARNING_THRESHOLD:
            warnings.append(f"{need}_high")
    return warnings


def _relief_action_for_need(need: str) -> str:
    if need == "thirst":
        return "drink waterskin"
    if need == "hunger":
        return "eat trail ration"
    if need == "fatigue":
        return "rest"
    return "check condition"


def _build_survival_suggestions(needs: Dict[str, int]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for need in SURVIVAL_NEEDS:
        value = _clamp_need(needs.get(need))
        if value < SURVIVAL_WARNING_THRESHOLD:
            continue
        rows.append(
            {
                "kind": "survival_relief",
                "need": need,
                "value": value,
                "warning": f"{need}_high",
                "action": _relief_action_for_need(need),
                "reason": f"{need}_high",
                "priority": max(1, value),
                "source": SURVIVAL_SOURCE_SUGGESTION,
            }
        )
    rows.sort(key=lambda row: (-_safe_int(row.get("priority"), 0), _safe_str(row.get("need"))))
    return rows


def _sync_survival_state(
    session: Dict[str, Any],
    needs: Dict[str, int],
    *,
    source: str,
) -> Dict[str, Any]:
    session = _safe_dict(session)
    simulation_state = _safe_dict(session.get("simulation_state"))
    player_state = _safe_dict(simulation_state.get("player_state"))
    needs = {need: _clamp_need(needs.get(need)) for need in SURVIVAL_NEEDS}
    warnings = _warnings_for_needs(needs)

    climate_survival = _safe_dict(simulation_state.get("climate_survival"))
    climate_survival.setdefault("format_version", "n1263_climate_survival_state_v1")
    climate_survival["runtime_enforced"] = True
    climate_survival["source"] = source
    climate_survival.setdefault("minutes_per_turn", 15)
    climate_survival["tick"] = _safe_int(
        simulation_state.get("tick")
        or _safe_dict(session.get("runtime_state")).get("tick"),
        0,
    )
    climate_survival["survival"] = {
        **needs,
        "warnings": warnings,
    }

    simulation_state["needs"] = dict(needs)
    simulation_state["climate_survival"] = climate_survival
    player_state["needs"] = dict(needs)
    player_state["climate_survival"] = copy.deepcopy(climate_survival)
    simulation_state["player_state"] = player_state
    session["simulation_state"] = simulation_state

    setup_payload = _safe_dict(session.get("setup_payload"))
    metadata = _safe_dict(setup_payload.get("metadata"))
    metadata["simulation_state"] = simulation_state
    setup_payload["metadata"] = metadata
    session["setup_payload"] = setup_payload
    return session


def _candidate_player_input_values(
    authoritative_result: Dict[str, Any],
    result_payload: Dict[str, Any],
    turn_contract: Dict[str, Any],
    resolved_result: Dict[str, Any],
) -> Iterable[Any]:
    containers = [
        authoritative_result,
        _safe_dict(authoritative_result).get("authoritative"),
        _safe_dict(authoritative_result).get("result"),
        result_payload,
        turn_contract,
        resolved_result,
    ]
    for container in containers:
        container = _safe_dict(container)
        if not container:
            continue
        for key in ("player_input", "input", "text", "raw_input", "command"):
            yield container.get(key)
        for nested_key in (
            "narration_context",
            "action",
            "semantic_action",
            "semantic_action_record",
            "action_record",
            "resolved_action",
            "resolved_result",
        ):
            nested = _safe_dict(container.get(nested_key))
            for key in ("player_input", "input", "text", "raw_input", "command", "summary"):
                yield nested.get(key)


def _extract_player_input(
    authoritative_result: Dict[str, Any],
    result_payload: Dict[str, Any],
    turn_contract: Dict[str, Any],
    resolved_result: Dict[str, Any],
) -> str:
    for value in _candidate_player_input_values(authoritative_result, result_payload, turn_contract, resolved_result):
        text = _safe_str(value).strip()
        if text:
            return text
    return ""


def _normalise_item_text(item: Dict[str, Any]) -> str:
    item = _safe_dict(item)
    return " ".join(
        _safe_str(value).strip().lower().replace("_", " ")
        for value in (
            item.get("item_id"),
            item.get("id"),
            item.get("name"),
            item.get("display_name"),
            item.get("kind"),
        )
        if _safe_str(value).strip()
    )


def _item_matches(item: Dict[str, Any], aliases: Iterable[str]) -> bool:
    item_text = _normalise_item_text(item)
    if not item_text:
        return False
    for alias in aliases:
        alias_text = _safe_str(alias).strip().lower().replace("_", " ")
        if alias_text and alias_text in item_text:
            return True
    return False


def _inventory_items(player_state: Dict[str, Any]) -> List[Dict[str, Any]]:
    player_state = _safe_dict(player_state)
    inventory_state = _safe_dict(player_state.get("inventory_state"))
    items = _safe_list(inventory_state.get("items"))
    if items:
        return [_safe_dict(item) for item in items if isinstance(item, dict)]
    inventory = _safe_dict(player_state.get("inventory"))
    return [_safe_dict(item) for item in _safe_list(inventory.get("items")) if isinstance(item, dict)]


def _write_inventory_items(player_state: Dict[str, Any], items: List[Dict[str, Any]]) -> Dict[str, Any]:
    player_state = _safe_dict(player_state)
    inventory_state = _safe_dict(player_state.get("inventory_state"))
    inventory_state["items"] = items
    player_state["inventory_state"] = inventory_state

    if isinstance(player_state.get("inventory"), dict):
        inventory = _safe_dict(player_state.get("inventory"))
        inventory["items"] = items
        player_state["inventory"] = inventory
    return player_state


def _consume_inventory_item(
    simulation_state: Dict[str, Any],
    aliases: Iterable[str],
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], str]:
    simulation_state = _safe_dict(simulation_state)
    player_state = _safe_dict(simulation_state.get("player_state"))
    items = _inventory_items(player_state)
    consumed: List[Dict[str, Any]] = []

    for index, item in enumerate(items):
        if not _item_matches(item, aliases):
            continue
        quantity_before = _safe_int(item.get("quantity"), 1)
        if quantity_before <= 0:
            continue
        quantity_after = max(0, quantity_before - 1)
        item = dict(item)
        item["quantity"] = quantity_after
        items[index] = item
        consumed.append(
            {
                "item_id": _safe_str(item.get("item_id") or item.get("id")),
                "name": _safe_str(item.get("name") or item.get("display_name")),
                "quantity_before": quantity_before,
                "quantity_after": quantity_after,
                "quantity_delta": quantity_after - quantity_before,
                "source": SURVIVAL_SOURCE_RELIEF,
            }
        )
        player_state = _write_inventory_items(player_state, items)
        simulation_state["player_state"] = player_state
        return simulation_state, consumed, ""

    return simulation_state, [], "missing_consumable"


def _classify_survival_relief(player_input: str) -> Dict[str, Any]:
    text = " ".join(_safe_str(player_input).strip().lower().split())
    if not text:
        return {}

    if any(token in text for token in ("drink", "sip", "water", "waterskin")):
        return {
            "action": "drink_waterskin",
            "need": "thirst",
            "aliases": ["waterskin", "water"],
            "requires_item": True,
        }

    if any(token in text for token in ("eat", "ration", "food", "meal", "consume")):
        return {
            "action": "eat_trail_ration",
            "need": "hunger",
            "aliases": ["trail_ration", "trail ration", "ration", "food"],
            "requires_item": True,
        }

    if any(token in text for token in ("rest", "sleep", "nap", "camp")):
        return {
            "action": "rest",
            "need": "fatigue",
            "aliases": [],
            "requires_item": False,
        }

    return {}


def _zero_survival_deltas() -> Dict[str, int]:
    return {f"{need}_delta": 0 for need in SURVIVAL_NEEDS}


def _resolve_survival_relief(
    simulation_state: Dict[str, Any],
    player_input: str,
    before_needs: Dict[str, int],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    simulation_state = _safe_dict(simulation_state)
    before_needs = {need: _clamp_need(before_needs.get(need)) for need in SURVIVAL_NEEDS}
    action = _classify_survival_relief(player_input)
    if not action:
        return simulation_state, {
            "applied": False,
            "reason": "no_survival_relief_intent",
            "before": before_needs,
            "after": before_needs,
            "deltas": _zero_survival_deltas(),
            "inventory_consumed": [],
            "source": SURVIVAL_SOURCE_RELIEF,
        }

    inventory_consumed: List[Dict[str, Any]] = []
    blocked_reason = ""
    if action.get("requires_item"):
        simulation_state, inventory_consumed, blocked_reason = _consume_inventory_item(
            simulation_state,
            _safe_list(action.get("aliases")),
        )
        if blocked_reason:
            return simulation_state, {
                "applied": False,
                "blocked": True,
                "blocked_reason": blocked_reason,
                "reason": blocked_reason,
                "action": _safe_str(action.get("action")),
                "need": _safe_str(action.get("need")),
                "before": before_needs,
                "after": before_needs,
                "deltas": _zero_survival_deltas(),
                "inventory_consumed": [],
                "source": SURVIVAL_SOURCE_RELIEF,
            }

    need = _safe_str(action.get("need"))
    after_needs = dict(before_needs)
    after_needs[need] = max(0, _clamp_need(before_needs.get(need)) - SURVIVAL_RELIEF_AMOUNT)
    deltas = _zero_survival_deltas()
    for key in SURVIVAL_NEEDS:
        deltas[f"{key}_delta"] = _clamp_need(after_needs.get(key)) - _clamp_need(before_needs.get(key))

    return simulation_state, {
        "applied": True,
        "blocked": False,
        "blocked_reason": "",
        "reason": "survival_relief_applied",
        "kind": "survival_relief",
        "action": _safe_str(action.get("action")),
        "need": need,
        "before": before_needs,
        "after": after_needs,
        "deltas": deltas,
        "inventory_consumed": inventory_consumed,
        "source": SURVIVAL_SOURCE_RELIEF,
    }


def _build_resource_changes(survival_action: Dict[str, Any]) -> Dict[str, Any]:
    survival_action = _safe_dict(survival_action)
    deltas = _safe_dict(survival_action.get("deltas")) or _zero_survival_deltas()
    return {
        "hunger_delta": _safe_int(deltas.get("hunger_delta"), 0),
        "thirst_delta": _safe_int(deltas.get("thirst_delta"), 0),
        "fatigue_delta": _safe_int(deltas.get("fatigue_delta"), 0),
        "survival": {
            "hunger": _safe_int(deltas.get("hunger_delta"), 0),
            "thirst": _safe_int(deltas.get("thirst_delta"), 0),
            "fatigue": _safe_int(deltas.get("fatigue_delta"), 0),
        },
        "effect_result": {
            "survival_action": survival_action,
        },
        "source": SURVIVAL_SOURCE_RELIEF,
    }


def _merge_resource_changes(existing: Dict[str, Any], survival_changes: Dict[str, Any]) -> Dict[str, Any]:
    existing = copy.deepcopy(_safe_dict(existing))
    survival_changes = _safe_dict(survival_changes)
    if not existing:
        return copy.deepcopy(survival_changes)

    for key in ("hunger_delta", "thirst_delta", "fatigue_delta"):
        existing[key] = _safe_int(existing.get(key), 0) + _safe_int(survival_changes.get(key), 0)
    existing["survival"] = _safe_dict(survival_changes.get("survival"))

    effect_result = _safe_dict(existing.get("effect_result"))
    effect_result["survival_action"] = _safe_dict(
        _safe_dict(survival_changes.get("effect_result")).get("survival_action")
    )
    existing["effect_result"] = effect_result
    existing["source"] = SURVIVAL_SOURCE_RELIEF
    return existing


def _build_climate_payload(
    *,
    needs: Dict[str, int],
    suggestions: List[Dict[str, Any]],
    survival_action: Dict[str, Any],
    resource_changes: Dict[str, Any],
) -> Dict[str, Any]:
    needs = {need: _clamp_need(needs.get(need)) for need in SURVIVAL_NEEDS}
    warnings = _warnings_for_needs(needs)
    return {
        "format_version": "n1263_live_runtime_survival_payload_v1",
        "runtime_enforced": True,
        "source": SURVIVAL_SOURCE_PROJECTION,
        "survival": {
            **needs,
            "warnings": warnings,
        },
        "warnings": warnings,
        "survival_suggestions": copy.deepcopy(suggestions),
        "suggestions": copy.deepcopy(suggestions),
        "resource_changes": copy.deepcopy(resource_changes),
        "effect_result": {
            "survival_action": copy.deepcopy(survival_action),
        },
        "source_gate": {
            "gate": "live_runtime_survival_suggestions_and_relief",
            "ok": True,
            "advisory_only": False,
            "source": "n1263_live_runtime_survival_repair",
            "coverage": {
                "climate_survival_rows": 1,
                "warning_rows": 1 if warnings else 0,
                "survival_suggestion_rows": 1 if suggestions else 0,
                "relief_applied_rows": 1 if survival_action.get("applied") is True else 0,
                "resource_change_rows": 1 if any(_safe_int(resource_changes.get(k), 0) for k in ("hunger_delta", "thirst_delta", "fatigue_delta")) else 0,
            },
        },
    }


def _append_runtime_history(runtime_state: Dict[str, Any], survival_action: Dict[str, Any]) -> Dict[str, Any]:
    runtime_state = _safe_dict(runtime_state)
    if not survival_action.get("applied"):
        return runtime_state
    rows = _safe_list(runtime_state.get("survival_relief_history"))
    rows.append(copy.deepcopy(survival_action))
    runtime_state["survival_relief_history"] = rows[-64:]
    return runtime_state


def _persist_session_best_effort(session: Dict[str, Any]) -> None:
    try:
        from app.apps.rpg.session.service import save_session

        save_session(session)
    except Exception:
        # The caller still returns the enriched session. Manual smoke tests also
        # write their own artifacts, so persistence failure should surface there
        # rather than crashing ordinary gameplay response shaping.
        logger.debug("suppressed error in %s", "_persist_session_best_effort", exc_info=True)
        return


def attach_survival_runtime_payloads(
    *,
    authoritative_result: Dict[str, Any],
    session: Dict[str, Any],
    turn_contract: Dict[str, Any],
    result_payload: Dict[str, Any],
    resolved_result: Dict[str, Any],
) -> Dict[str, Any]:
    """Project live survival suggestions and deterministic relief into turn output.

    This is deliberately response-path scoped: it sees the real session returned
    by live ``apply_turn`` and mutates only bounded survival fields. The LLM may
    narrate the outcome later, but hunger/thirst/fatigue, suggestions, item
    consumption, and relief deltas are deterministic here.
    """
    authoritative_result = _safe_dict(authoritative_result)
    session = _safe_dict(session)
    turn_contract = _safe_dict(turn_contract)
    result_payload = _safe_dict(result_payload)
    resolved_result = _safe_dict(resolved_result)
    simulation_state = _safe_dict(session.get("simulation_state"))
    if not _survival_state_present(simulation_state):
        return {
            "session": session,
            "turn_contract": turn_contract,
            "result_payload": result_payload,
        }

    before_needs = _extract_survival_needs(simulation_state)
    player_input = _extract_player_input(
        authoritative_result,
        result_payload,
        turn_contract,
        resolved_result,
    )

    simulation_state, survival_action = _resolve_survival_relief(
        simulation_state,
        player_input,
        before_needs,
    )
    after_needs = _safe_dict(survival_action.get("after")) or before_needs
    session["simulation_state"] = simulation_state
    session = _sync_survival_state(
        session,
        after_needs,
        source=SURVIVAL_SOURCE_RELIEF if survival_action.get("applied") else SURVIVAL_SOURCE_PROJECTION,
    )
    simulation_state = _safe_dict(session.get("simulation_state"))
    runtime_state = _safe_dict(session.get("runtime_state"))

    suggestions = _build_survival_suggestions(after_needs)
    resource_changes = _build_resource_changes(survival_action)
    climate_payload = _build_climate_payload(
        needs=after_needs,
        suggestions=suggestions,
        survival_action=survival_action,
        resource_changes=resource_changes,
    )

    turn_contract["climate_survival"] = climate_payload
    if suggestions:
        existing_suggested = _safe_list(turn_contract.get("suggested_actions"))
        existing_suggested.extend(copy.deepcopy(suggestions))
        turn_contract["suggested_actions"] = existing_suggested[:12]

    resolved_result["climate_survival"] = climate_payload
    resolved_result["resource_changes"] = _merge_resource_changes(
        _safe_dict(resolved_result.get("resource_changes")),
        resource_changes,
    )
    resolved_effect = _safe_dict(resolved_result.get("effect_result"))
    resolved_effect["survival_action"] = survival_action
    resolved_result["effect_result"] = resolved_effect

    result_payload["resolved_result"] = resolved_result
    result_payload["climate_survival"] = climate_payload
    result_payload["survival_suggestions"] = copy.deepcopy(suggestions)
    result_payload["resource_changes"] = _merge_resource_changes(
        _safe_dict(result_payload.get("resource_changes")),
        resource_changes,
    )
    result_effect = _safe_dict(result_payload.get("effect_result"))
    result_effect["survival_action"] = survival_action
    result_payload["effect_result"] = result_effect

    runtime_last = _safe_dict(runtime_state.get("last_turn_result"))
    runtime_last["climate_survival"] = climate_payload
    runtime_last["resource_changes"] = _merge_resource_changes(
        _safe_dict(runtime_last.get("resource_changes")),
        resource_changes,
    )
    runtime_effect = _safe_dict(runtime_last.get("effect_result"))
    runtime_effect["survival_action"] = survival_action
    runtime_last["effect_result"] = runtime_effect
    runtime_state["last_turn_result"] = runtime_last
    runtime_state = _append_runtime_history(runtime_state, survival_action)
    session["runtime_state"] = runtime_state

    _persist_session_best_effort(session)

    return {
        "session": session,
        "turn_contract": turn_contract,
        "result_payload": result_payload,
    }