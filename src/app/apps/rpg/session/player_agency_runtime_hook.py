"""Presentation-only player agency suggestions for resolved turn results."""
from __future__ import annotations

from typing import Any, Mapping

from app.apps.rpg.session.player_agency_buttons import attach_next_action_buttons
from app.apps.rpg.session.player_agency_contract import attach_player_agency_contract


def _d(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _s(value: Any) -> str:
    return "" if value is None else str(value)


def _b(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lower = value.strip().lower()
        if lower in {"1", "true", "yes", "y", "on"}:
            return True
        if lower in {"0", "false", "no", "n", "off"}:
            return False
    if value is None:
        return default
    return bool(value)


def _optional_flavor_provider(enable_flavor: bool) -> Any:
    if not enable_flavor:
        return None
    from app.apps.rpg.foundation.llm_app_gateway import build_app_llm_gateway

    return build_app_llm_gateway()


def attach_player_agency_to_runtime_result(
    result: dict[str, Any],
    *,
    call_context: Mapping[str, Any],
) -> dict[str, Any]:
    """Attach suggestions whose commands still require normal runtime validation."""

    if not isinstance(result, dict):
        return result
    context = _d(call_context)
    provider = _optional_flavor_provider(_b(context.get("enable_flavor"), False))
    updated = attach_player_agency_contract(
        result,
        player_input=_s(context.get("player_input")),
        session=_d(context.get("session_override")),
        provider=provider,
        max_options=int(context.get("max_options") or 5),
    )
    updated = attach_next_action_buttons(updated)
    buttons = _d(updated.get("next_action_buttons"))
    updated["player_agency_runtime_hook"] = {
        "format_version": "phase14_21_player_agency_runtime_hook_v2",
        "attached": True,
        "button_payload_attached": True,
        "button_count": int(buttons.get("button_count") or 0),
        "provider_flavor_requested": _b(context.get("enable_flavor"), False),
        "provider_flavor_available": provider is not None,
        "presentation_only": True,
        "runtime_validation_required": True,
    }
    nested = _d(updated.get("result"))
    if nested:
        nested["player_agency_runtime_hook"] = dict(
            updated["player_agency_runtime_hook"]
        )
        updated["result"] = nested
    return updated


__all__ = ["attach_player_agency_to_runtime_result"]
