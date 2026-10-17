from __future__ import annotations

from typing import Any

from app.apps.rpg.edge.hermes.adapter_contract import hermes_adapter_preview_payload
from app.apps.rpg.edge.hermes.context import hermes_rpg_context_payload
from app.apps.rpg.edge.hermes.mode_routing import omnix_mode_policy
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _append_suggestion(
    items: list[dict[str, Any]],
    *,
    suggestion_id: str,
    label: str,
    command: str,
    kind: str,
    reason: str,
) -> None:
    if any(item.get("id") == suggestion_id for item in items):
        return
    items.append(
        {
            "id": suggestion_id,
            "label": label,
            "command": command,
            "kind": kind,
            "risk": "safe_player_input",
            "requires_user_click": True,
            "direct_state_write": False,
            "processed_by": "rpg_runtime",
            "reason": reason,
        }
    )


def hermes_rpg_suggestions_from_context(context: dict[str, Any]) -> dict[str, Any]:
    data = _safe_dict(context)
    flags = _safe_dict(data.get("state_flags"))
    player = _safe_dict(data.get("player"))
    objectives = [_safe_str(item).strip() for item in _safe_list(data.get("objectives")) if _safe_str(item).strip()]
    inventory = [_safe_str(item).strip() for item in _safe_list(data.get("inventory")) if _safe_str(item).strip()]
    location = _safe_str(data.get("location") or "the current area").strip()
    active_npc = _safe_str(data.get("active_npc")).strip()
    suggestions: list[dict[str, Any]] = []

    if active_npc:
        _append_suggestion(
            suggestions,
            suggestion_id="ask_active_npc",
            label=f"Ask {active_npc} what they know",
            command=f"ask {active_npc} what they know about the current situation",
            kind="dialogue",
            reason="An active NPC is present in the current context.",
        )

    if objectives:
        objective = objectives[0]
        _append_suggestion(
            suggestions,
            suggestion_id="pursue_objective",
            label=f"Pursue objective: {objective}",
            command=f"focus on the objective: {objective}",
            kind="objective",
            reason="The journal has an active objective.",
        )
    else:
        _append_suggestion(
            suggestions,
            suggestion_id="check_journal",
            label="Check the quest journal",
            command="check the quest journal",
            kind="journal",
            reason="No active objective was visible in the bounded Hermes context.",
        )

    if flags.get("in_combat"):
        _append_suggestion(
            suggestions,
            suggestion_id="assess_combat",
            label="Assess the combat situation",
            command="assess the combat situation",
            kind="combat",
            reason="Combat is active, so tactical inspection is safest before choosing an attack.",
        )
    elif flags.get("in_service"):
        _append_suggestion(
            suggestions,
            suggestion_id="buy_supplies",
            label="Buy basic supplies",
            command="buy two rations",
            kind="service",
            reason="A service interaction is active and buying supplies exercises the economy path.",
        )
    elif flags.get("can_travel", True):
        _append_suggestion(
            suggestions,
            suggestion_id="look_for_travel",
            label=f"Look for exits from {location}",
            command="look for nearby roads and exits",
            kind="travel",
            reason="Travel appears available and no combat gate is active.",
        )

    if not inventory:
        _append_suggestion(
            suggestions,
            suggestion_id="check_inventory",
            label="Check inventory",
            command="check inventory",
            kind="inventory",
            reason="No inventory items were visible in the bounded context.",
        )

    if player.get("level") is not None or player.get("xp") is not None:
        _append_suggestion(
            suggestions,
            suggestion_id="check_progression",
            label="Check character progress",
            command="check my level and experience",
            kind="progression",
            reason="Player progression fields are visible in context.",
        )

    adapter = hermes_adapter_preview_payload(
        {
            "mode": "rpg",
            "intent": "suggest_next_action",
            "context": {"location": location, "active_npc": active_npc, "state_flags": flags},
        }
    )
    policy = omnix_mode_policy("rpg")
    return {
        "ok": True,
        "read_only": True,
        "source": "rpg_context",
        "mode": "rpg",
        "adapter": adapter.get("response_contract"),
        "policy": policy,
        "suggestions": suggestions[:6],
        "count": min(len(suggestions), 6),
    }


def hermes_rpg_suggestions_payload(request: dict[str, Any]) -> dict[str, Any]:
    data = _safe_dict(request)
    context = _safe_dict(data.get("context"))
    if not context:
        context_payload = hermes_rpg_context_payload(data)
        if context_payload.get("ok") is not True:
            return {**context_payload, "suggestions": [], "count": 0}
        context = _safe_dict(context_payload.get("context"))
    return hermes_rpg_suggestions_from_context(context)
