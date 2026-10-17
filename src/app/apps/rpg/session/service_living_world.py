from __future__ import annotations

from typing import Any, Dict

from app.apps.rpg.rules.economy.service_stock import apply_service_stock_purchase
from app.apps.rpg.narration.journal.journal_state import add_rumor_journal_entry, get_journal_state
from app.apps.rpg.world.memory.service_memory import append_service_memory
from app.apps.rpg.world.memory.service_social_effects import apply_service_social_effects
from app.apps.rpg.session.state_normalization import _safe_str
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict
from app.apps.rpg.world.rumor_registry import select_rumor_for_service
from app.apps.rpg.world.world_event_log import (
    add_rumor_world_event,
    add_service_world_event,
    get_world_event_state,
)


def _canonicalize_paid_info_rumor_state(
    simulation_state: Dict[str, Any],
    service_result: Dict[str, Any],
    service_application: Dict[str, Any],
    rumor: Dict[str, Any],
) -> None:
    """Make deterministic registry rumor the canonical paid-info output.

    Older service_effects code may create a stub like:
      rumor:bran_paid_rumor:tick:...
      "A paid local rumor is owed or available."

    Phase 8.2+ should expose the deterministic registry rumor everywhere:
      rumor:old_mill_bandits
    """
    rumor = _safe_dict(rumor)
    if not rumor:
        return

    selected_offer_id = _safe_str(service_result.get("selected_offer_id"))
    provider_id = _safe_str(service_result.get("provider_id"))
    provider_name = _safe_str(service_result.get("provider_name"))

    canonical = {
        **rumor,
        "provider_id": provider_id or _safe_str(rumor.get("source_provider_id")),
        "provider_name": provider_name or _safe_str(rumor.get("source_provider_name")),
        "offer_id": selected_offer_id,
        "source": "deterministic_rumor_registry",
    }

    service_application["rumor_added"] = canonical

    transaction_record = _safe_dict(service_application.get("transaction_record"))
    if transaction_record:
        transaction_record["rumor_added"] = canonical
        transaction_record["rumor_id"] = _safe_str(canonical.get("rumor_id"))
        service_application["transaction_record"] = transaction_record

    # Canonicalize common memory rumor roots if present.
    memory_rumors = simulation_state.get("memory_rumors")
    if isinstance(memory_rumors, list):
        filtered = []
        for existing in memory_rumors:
            existing = _safe_dict(existing)
            existing_id = _safe_str(existing.get("rumor_id"))
            existing_offer = _safe_str(existing.get("offer_id") or existing.get("source_offer_id"))
            if existing_id.startswith(f"rumor:{selected_offer_id}:"):
                continue
            if selected_offer_id and existing_offer == selected_offer_id:
                continue
            filtered.append(existing)
        if not any(_safe_str(item.get("rumor_id")) == _safe_str(canonical.get("rumor_id")) for item in filtered):
            filtered.append(canonical)
        simulation_state["memory_rumors"] = filtered

    memory_state = _safe_dict(simulation_state.get("memory_state"))
    if memory_state:
        rumors = memory_state.get("rumors")
        if isinstance(rumors, list):
            filtered = []
            for existing in rumors:
                existing = _safe_dict(existing)
                existing_id = _safe_str(existing.get("rumor_id"))
                existing_offer = _safe_str(existing.get("offer_id") or existing.get("source_offer_id"))
                if existing_id.startswith(f"rumor:{selected_offer_id}:"):
                    continue
                if selected_offer_id and existing_offer == selected_offer_id:
                    continue
                filtered.append(existing)
            if not any(_safe_str(item.get("rumor_id")) == _safe_str(canonical.get("rumor_id")) for item in filtered):
                filtered.append(canonical)
            memory_state["rumors"] = filtered
            simulation_state["memory_state"] = memory_state


def apply_service_living_world_effects(
    simulation_state: Dict[str, Any],
    service_result: Dict[str, Any],
    service_application: Dict[str, Any] | None = None,
    *,
    tick: int = 0,
) -> Dict[str, Any]:
    service_result = _safe_dict(service_result)
    service_application = _safe_dict(service_application)
    if not service_result.get("matched"):
        return {
            "memory_entry": {},
            "social_effects": {},
            "stock_update": {},
        }

    stock_update = {}
    rumor_added = {}
    journal_entry = {}
    service_world_event = {}
    rumor_world_event = {}
    if (
        _safe_str(service_result.get("kind")) == "service_purchase"
        and bool(service_application.get("applied"))
    ):
        stock_update = apply_service_stock_purchase(
            simulation_state,
            service_result,
            tick=tick,
        )
        service_world_event = add_service_world_event(
            simulation_state,
            service_result,
            service_application,
            tick=tick,
        )

        if _safe_str(service_result.get("service_kind")) == "paid_information":
            rumor = select_rumor_for_service(service_result, simulation_state)
            if rumor:
                rumor_added = rumor
                _canonicalize_paid_info_rumor_state(
                    simulation_state,
                    service_result,
                    service_application,
                    rumor,
                )
                rumor_added = _safe_dict(service_application.get("rumor_added")) or rumor
                journal_entry = add_rumor_journal_entry(
                    simulation_state,
                    rumor_added,
                    provider_id=_safe_str(service_result.get("provider_id")),
                    provider_name=_safe_str(service_result.get("provider_name")),
                    tick=tick,
                )
                rumor_world_event = add_rumor_world_event(
                    simulation_state,
                    rumor_added,
                    tick=tick,
                )
    elif service_result.get("matched"):
        service_world_event = add_service_world_event(
            simulation_state,
            service_result,
            service_application,
            tick=tick,
        )

    memory_entry = append_service_memory(
        simulation_state,
        service_result,
        service_application,
        tick=tick,
    )
    social_effects = apply_service_social_effects(
        simulation_state,
        service_result,
        service_application,
        tick=tick,
    )

    return {
        "memory_entry": memory_entry,
        "social_effects": social_effects,
        "stock_update": stock_update,
        "rumor_added": rumor_added,
        "journal_entry": journal_entry,
        "service_world_event": service_world_event,
        "rumor_world_event": rumor_world_event,
        "journal_state": get_journal_state(simulation_state),
        "world_event_state": get_world_event_state(simulation_state),
    }
