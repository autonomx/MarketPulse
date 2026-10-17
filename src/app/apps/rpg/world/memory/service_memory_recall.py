from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

DEFAULT_SERVICE_MEMORY_RECALL_LIMIT = 5


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _memory_tick(memory: Dict[str, Any]) -> int:
    try:
        return int(memory.get("tick") or 0)
    except Exception:
        return 0


def _memory_score(
    memory: Dict[str, Any],
    *,
    provider_id: str,
    provider_name: str,
    service_kind: str,
    selected_offer_id: str,
) -> float:
    score = 0.0

    owner_id = _safe_str(memory.get("owner_id"))
    owner_name = _safe_str(memory.get("owner_name"))
    if provider_id and owner_id == provider_id:
        score += 5.0
    elif provider_name and owner_name.lower() == provider_name.lower():
        score += 4.0

    if service_kind and _safe_str(memory.get("service_kind")) == service_kind:
        score += 2.0

    if selected_offer_id and _safe_str(memory.get("offer_id")) == selected_offer_id:
        score += 3.0

    kind = _safe_str(memory.get("kind"))
    if kind == "service_purchase":
        score += 1.0
    elif kind == "service_purchase_blocked":
        score += 0.75
    elif kind == "service_inquiry":
        score += 0.25

    score += min(_safe_float(memory.get("importance"), 0.0), 1.0)
    score += min(_memory_tick(memory) / 100000.0, 0.25)
    return score


def _service_result_from_turn_contract(turn_contract: Dict[str, Any]) -> Dict[str, Any]:
    turn_contract = _safe_dict(turn_contract)

    resolved = _safe_dict(
        turn_contract.get("resolved_result")
        or turn_contract.get("resolved_action")
    )
    resolved_service = _safe_dict(resolved.get("service_result"))
    if resolved_service.get("matched"):
        return resolved_service

    direct = _safe_dict(turn_contract.get("service_result"))
    if direct.get("matched"):
        return direct

    action = _safe_dict(turn_contract.get("action"))
    action_service = _safe_dict(action.get("service_result"))
    if action_service.get("matched"):
        return action_service

    metadata = _safe_dict(action.get("metadata"))
    metadata_service = _safe_dict(metadata.get("service_result"))
    if metadata_service.get("matched"):
        return metadata_service

    return {}


def recall_service_memories(
    simulation_state: Dict[str, Any],
    *,
    provider_id: str = "",
    provider_name: str = "",
    service_kind: str = "",
    selected_offer_id: str = "",
    current_tick: int = 0,
    exclude_memory_ids: List[str] | None = None,
    limit: int = DEFAULT_SERVICE_MEMORY_RECALL_LIMIT,
) -> List[Dict[str, Any]]:
    simulation_state = _safe_dict(simulation_state)
    memory_state = _safe_dict(simulation_state.get("memory_state"))
    service_memories = _safe_list(memory_state.get("service_memories"))
    excluded = {
        _safe_str(memory_id)
        for memory_id in (exclude_memory_ids or [])
        if _safe_str(memory_id)
    }

    candidates: List[Dict[str, Any]] = []
    for raw_memory in service_memories:
        memory = _safe_dict(raw_memory)
        if not memory:
            continue

        memory_id = _safe_str(memory.get("memory_id"))
        if memory_id and memory_id in excluded:
            continue

        # Recall must represent prior memory only. The service runtime may have
        # already appended the current turn's memory before narration context is
        # built, so exclude memories written at or after the current turn tick.
        if current_tick and _memory_tick(memory) >= int(current_tick):
            continue

        owner_id = _safe_str(memory.get("owner_id"))
        owner_name = _safe_str(memory.get("owner_name"))

        if provider_id and owner_id and owner_id != provider_id:
            continue
        if not provider_id and provider_name and owner_name.lower() != provider_name.lower():
            continue

        candidates.append(deepcopy(memory))

    candidates.sort(
        key=lambda memory: (
            _memory_score(
                memory,
                provider_id=provider_id,
                provider_name=provider_name,
                service_kind=service_kind,
                selected_offer_id=selected_offer_id,
            ),
            _memory_tick(memory),
            _safe_str(memory.get("memory_id")),
        ),
        reverse=True,
    )

    if limit <= 0:
        return candidates
    return candidates[:limit]


def recall_service_memories_for_narration(
    simulation_state: Dict[str, Any],
    narration_context: Dict[str, Any] | None = None,
    *,
    turn_contract: Dict[str, Any] | None = None,
    service_result: Dict[str, Any] | None = None,
    current_memory_entry: Dict[str, Any] | None = None,
    current_tick: int = 0,
    limit: int = DEFAULT_SERVICE_MEMORY_RECALL_LIMIT,
) -> Dict[str, Any]:
    narration_context = _safe_dict(narration_context)
    turn_contract = _safe_dict(turn_contract or narration_context.get("turn_contract"))

    effective_service_result = _safe_dict(service_result)
    if not effective_service_result:
        effective_service_result = _safe_dict(narration_context.get("service_result"))
    if not effective_service_result:
        resolved = _safe_dict(narration_context.get("resolved_result"))
        effective_service_result = _safe_dict(resolved.get("service_result"))
    if not effective_service_result:
        effective_service_result = _service_result_from_turn_contract(turn_contract)

    if not effective_service_result.get("matched"):
        return {
            "recalled_service_memories": [],
            "debug": {
                "source": "deterministic_service_memory_recall",
                "reason": "not_service",
                "provider_id": "",
                "provider_name": "",
                "service_kind": "",
                "selected_offer_id": "",
            },
        }

    provider_id = _safe_str(effective_service_result.get("provider_id"))
    provider_name = _safe_str(effective_service_result.get("provider_name"))
    service_kind = _safe_str(effective_service_result.get("service_kind"))
    selected_offer_id = _safe_str(effective_service_result.get("selected_offer_id"))
    current_memory_entry = _safe_dict(
        current_memory_entry
        or narration_context.get("memory_entry")
        or _safe_dict(narration_context.get("service_application")).get("memory_entry")
    )
    excluded_memory_ids = []
    current_memory_id = _safe_str(current_memory_entry.get("memory_id"))
    if current_memory_id:
        excluded_memory_ids.append(current_memory_id)

    if not current_tick:
        current_tick = _memory_tick(current_memory_entry)
    if not current_tick:
        try:
            current_tick = int(_safe_dict(simulation_state).get("tick") or 0)
        except Exception:
            current_tick = 0

    memories = recall_service_memories(
        simulation_state,
        provider_id=provider_id,
        provider_name=provider_name,
        service_kind=service_kind,
        selected_offer_id=selected_offer_id,
        current_tick=int(current_tick or 0),
        exclude_memory_ids=excluded_memory_ids,
        limit=limit,
    )

    return {
        "recalled_service_memories": memories,
        "debug": {
            "source": "deterministic_service_memory_recall",
            "reason": "matched",
            "provider_id": provider_id,
            "provider_name": provider_name,
            "service_kind": service_kind,
            "selected_offer_id": selected_offer_id,
            "count": len(memories),
            "memory_ids": [_safe_str(memory.get("memory_id")) for memory in memories],
            "excluded_memory_ids": excluded_memory_ids,
            "current_tick": int(current_tick or 0),
        },
    }


