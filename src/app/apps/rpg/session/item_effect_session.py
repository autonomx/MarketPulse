"""Session bridge for deterministic item effects."""
from __future__ import annotations

from app.runtime.clock import utc_now

from copy import deepcopy
from typing import Any

from app.apps.rpg.session.inventory_items import consume_inventory_item, display_item_name, find_inventory_item
from app.apps.rpg.session.item_signals import apply_item_signal, normalize_item_signals
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

ITEM_EFFECT_SESSION_SOURCE = "engine_item_effect_session_v1"


def _text(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _norm(value: Any) -> str:
    return _text(value).casefold()


def _turn(state: dict[str, Any]) -> int:
    return int(state.get("current_turn") or state.get("turn_count") or 0)


def _utc_now() -> str:
    return utc_now().isoformat().replace("+00:00", "Z")


def _mechanics(state: dict[str, Any]) -> dict[str, Any]:
    mechanics = _safe_dict(state.get("mechanics"))
    state["mechanics"] = mechanics
    return mechanics


def _prepend_trace(mechanics: dict[str, Any], key: str, trace: dict[str, Any]) -> None:
    traces = _safe_list(mechanics.get(key))
    mechanics[key] = [trace, *traces][:50]


def _selected_effect_consumes(normalized: dict[str, Any], signal_id: str | None) -> bool:
    wanted = _norm(signal_id)
    for raw_signal in _safe_list(normalized.get("signals")):
        signal = _safe_dict(raw_signal)
        if wanted and _norm(signal.get("signal_id")) != wanted:
            continue
        if signal.get("consume") is True:
            return True
    return False


def _enrich_trace(state: dict[str, Any], trace: dict[str, Any], *, source: str, consumed: bool) -> dict[str, Any]:
    enriched = deepcopy(trace)
    enriched["session_event"] = "item_effect_session_applied"
    enriched["session_source"] = _text(source, "item_action")
    enriched["turn"] = _turn(state)
    enriched["timestamp"] = _utc_now()
    enriched["consumed_item"] = consumed
    enriched["mechanics_source"] = ITEM_EFFECT_SESSION_SOURCE
    return enriched


def available_item_effects_for_session(state: dict[str, Any], player: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    player = _safe_dict(player or _safe_dict(state).get("player"))
    summaries: list[dict[str, Any]] = []
    for raw_item in _safe_list(player.get("inventory")):
        item = _safe_dict(raw_item)
        if not item:
            continue
        normalized = normalize_item_signals(item)
        if not normalized.get("ok"):
            continue
        summaries.append(
            {
                "item_id": _text(item.get("item_id") or item.get("id")),
                "name": display_item_name(item),
                "effects": [
                    {
                        "id": _text(_safe_dict(signal).get("signal_id")),
                        "op": _text(_safe_dict(signal).get("op")),
                        "consume": _safe_dict(signal).get("consume") is True,
                    }
                    for signal in _safe_list(normalized.get("signals"))
                ],
                "repairs": list(normalized.get("repairs") or []),
            }
        )
    return summaries


def apply_item_effect_for_session(
    state: dict[str, Any],
    item_name: str | None,
    *,
    effect_id: str | None = None,
    source: str = "item_action",
) -> dict[str, Any]:
    state = _safe_dict(state)
    player = _safe_dict(state.get("player"))
    state["player"] = player
    inventory, index, item = find_inventory_item(player, item_name)
    if item is None or index < 0:
        return {"ok": False, "error": "item_not_found", "item_name": item_name, "effects": []}

    normalized = normalize_item_signals(item)
    if not normalized.get("ok"):
        return {
            "ok": False,
            "error": normalized.get("error") or "item_has_no_effect_contract",
            "item_name": display_item_name(item),
            "effects": [],
            "repairs": list(normalized.get("repairs") or []),
        }

    result = apply_item_signal(state, item, effect_id)
    if not result.get("ok"):
        return {
            "ok": False,
            "error": result.get("error") or "item_effect_failed",
            "item_name": display_item_name(item),
            "effect_id": effect_id,
            "effects": [],
            "repairs": list(result.get("repairs") or []),
        }

    consumed = _selected_effect_consumes(normalized, effect_id)
    if consumed:
        consume_inventory_item(inventory, index, 1)

    trace = _enrich_trace(state, _safe_dict(result.get("trace")), source=source, consumed=consumed)
    mechanics = _mechanics(state)
    _prepend_trace(mechanics, "item_effect_traces", trace)
    _prepend_trace(mechanics, "item_traces", trace)

    detail = f"Activated {display_item_name(item)}."
    if consumed:
        detail = f"Activated and consumed {display_item_name(item)}."
    return {
        "ok": True,
        "item_name": display_item_name(item),
        "effect_ids": list(trace.get("signal_ids") or []),
        "effects": deepcopy(_safe_list(result.get("effects"))),
        "consumed_item": consumed,
        "detail": detail,
        "repairs": list(result.get("repairs") or []),
        "mechanics_trace": trace,
    }
