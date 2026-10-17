from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

MAX_SERVICE_MEMORIES = 80
MAX_NPC_SERVICE_MEMORIES = 40


def _selected_offer(service_result: Dict[str, Any]) -> Dict[str, Any]:
    selected_offer_id = _safe_str(service_result.get("selected_offer_id"))
    for offer in _safe_list(service_result.get("offers")):
        offer = _safe_dict(offer)
        if _safe_str(offer.get("offer_id")) == selected_offer_id:
            return deepcopy(offer)
    return {}


def _ensure_memory_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    state = _safe_dict(simulation_state)
    memory_state = _safe_dict(state.get("memory_state"))
    if not memory_state:
        memory_state = {}
        state["memory_state"] = memory_state

    service_memories = memory_state.get("service_memories")
    if not isinstance(service_memories, list):
        service_memories = []
        memory_state["service_memories"] = service_memories

    npc_memories = memory_state.get("npc_memories")
    if not isinstance(npc_memories, dict):
        npc_memories = {}
        memory_state["npc_memories"] = npc_memories

    return memory_state


def _append_bounded_unique(
    entries: List[Dict[str, Any]],
    entry: Dict[str, Any],
    *,
    max_entries: int,
) -> Dict[str, Any]:
    memory_id = _safe_str(entry.get("memory_id"))
    if memory_id:
        for existing in entries:
            if _safe_str(_safe_dict(existing).get("memory_id")) == memory_id:
                return deepcopy(existing)

    entries.append(deepcopy(entry))
    if max_entries > 0 and len(entries) > max_entries:
        del entries[:-max_entries]
    return deepcopy(entry)


def _memory_kind(
    service_result: Dict[str, Any],
    service_application: Dict[str, Any],
) -> str:
    kind = _safe_str(service_result.get("kind"))
    status = _safe_str(service_result.get("status"))
    purchase = _safe_dict(service_result.get("purchase"))

    if kind == "service_inquiry":
        return "service_inquiry"

    if kind == "service_purchase" and (
        service_application.get("applied")
        or purchase.get("applied")
        or status == "purchased"
    ):
        return "service_purchase"

    if kind == "service_purchase":
        return "service_purchase_blocked"

    return "service_interaction"


def _sentiment_for_memory(kind: str, blocked_reason: str) -> str:
    if kind == "service_purchase":
        return "positive"
    if kind == "service_purchase_blocked":
        if blocked_reason == "insufficient_funds":
            return "slightly_negative"
        return "neutral_negative"
    return "neutral"


def _importance_for_memory(kind: str) -> float:
    if kind == "service_purchase":
        return 0.4
    if kind == "service_purchase_blocked":
        return 0.35
    return 0.25


def _summary_for_memory(
    service_result: Dict[str, Any],
    service_application: Dict[str, Any],
    kind: str,
) -> str:
    provider_name = _safe_str(service_result.get("provider_name") or "the provider")
    service_kind = _safe_str(service_result.get("service_kind") or "service")
    offer = _selected_offer(service_result)
    offer_label = _safe_str(offer.get("label") or service_result.get("selected_offer_id"))
    purchase = _safe_dict(service_result.get("purchase"))
    blocked_reason = _safe_str(
        service_application.get("blocked_reason") or purchase.get("blocked_reason")
    )

    if kind == "service_purchase" and offer_label:
        return f"The player bought {offer_label} from {provider_name}."

    if kind == "service_purchase_blocked" and offer_label:
        if blocked_reason == "insufficient_funds":
            return (
                f"The player tried to buy {offer_label} from {provider_name} "
                "without enough coin."
            )
        return (
            f"The player tried to buy {offer_label} from {provider_name}, "
            "but the purchase was blocked."
        )

    if kind == "service_purchase_blocked":
        return f"The player tried to buy an unavailable service from {provider_name}."

    return f"The player asked {provider_name} about {service_kind}."


def build_service_memory_entry(
    service_result: Dict[str, Any],
    service_application: Dict[str, Any] | None = None,
    *,
    tick: int = 0,
) -> Dict[str, Any]:
    service_result = _safe_dict(service_result)
    service_application = _safe_dict(service_application)
    if not service_result.get("matched"):
        return {}

    provider_id = _safe_str(service_result.get("provider_id"))
    provider_name = _safe_str(service_result.get("provider_name"))
    service_kind = _safe_str(service_result.get("service_kind"))
    selected_offer_id = _safe_str(service_result.get("selected_offer_id"))
    kind = _memory_kind(service_result, service_application)
    purchase = _safe_dict(service_result.get("purchase"))
    blocked_reason = _safe_str(
        service_application.get("blocked_reason") or purchase.get("blocked_reason")
    )

    memory_id = (
        f"memory:service:{tick}:{provider_id or 'provider'}:"
        f"{kind}:{selected_offer_id or service_kind or 'service'}"
    )

    return {
        "memory_id": memory_id,
        "kind": kind,
        "owner_id": provider_id,
        "owner_name": provider_name,
        "subject_id": "player",
        "service_kind": service_kind,
        "offer_id": selected_offer_id,
        "summary": _summary_for_memory(service_result, service_application, kind),
        "sentiment": _sentiment_for_memory(kind, blocked_reason),
        "importance": _importance_for_memory(kind),
        "blocked_reason": blocked_reason,
        "tick": int(tick or 0),
        "source": "deterministic_service_runtime",
    }


def append_service_memory(
    simulation_state: Dict[str, Any],
    service_result: Dict[str, Any],
    service_application: Dict[str, Any] | None = None,
    *,
    tick: int = 0,
) -> Dict[str, Any]:
    entry = build_service_memory_entry(
        service_result,
        service_application,
        tick=tick,
    )
    if not entry:
        return {}

    memory_state = _ensure_memory_state(simulation_state)
    service_memories = _safe_list(memory_state.get("service_memories"))
    appended = _append_bounded_unique(
        service_memories,
        entry,
        max_entries=MAX_SERVICE_MEMORIES,
    )

    owner_id = _safe_str(entry.get("owner_id"))
    if owner_id:
        npc_memories = _safe_dict(memory_state.get("npc_memories"))
        owner_entries = npc_memories.get(owner_id)
        if not isinstance(owner_entries, list):
            owner_entries = []
            npc_memories[owner_id] = owner_entries
        _append_bounded_unique(
            owner_entries,
            entry,
            max_entries=MAX_NPC_SERVICE_MEMORIES,
        )

    return appended
