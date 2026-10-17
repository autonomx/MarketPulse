from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

DEFAULT_NPC_MEMORY_RECALL_LIMIT = 6


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _memory_tick(memory: Dict[str, Any]) -> int:
    return _safe_int(_safe_dict(memory).get("tick"), 0)


def _iter_memory_roots(simulation_state: Dict[str, Any]) -> List[Dict[str, Any]]:
    simulation_state = _safe_dict(simulation_state)
    memory_state = _safe_dict(simulation_state.get("memory_state"))

    memories: List[Dict[str, Any]] = []

    for memory in _safe_list(memory_state.get("service_memories")):
        memory = _safe_dict(memory)
        if memory:
            memories.append(deepcopy(memory))

    for memory in _safe_list(memory_state.get("social_memories")):
        memory = _safe_dict(memory)
        if memory:
            memories.append(deepcopy(memory))

    for memory in _safe_list(memory_state.get("npc_memories_flat")):
        memory = _safe_dict(memory)
        if memory:
            memories.append(deepcopy(memory))

    npc_memories = _safe_dict(memory_state.get("npc_memories"))
    for owner_entries in npc_memories.values():
        for memory in _safe_list(owner_entries):
            memory = _safe_dict(memory)
            if memory:
                memories.append(deepcopy(memory))

    by_id: Dict[str, Dict[str, Any]] = {}
    without_id: List[Dict[str, Any]] = []
    for memory in memories:
        memory_id = _safe_str(memory.get("memory_id"))
        if memory_id:
            existing = by_id.get(memory_id)
            if not existing or _memory_tick(memory) >= _memory_tick(existing):
                by_id[memory_id] = memory
        else:
            without_id.append(memory)

    return list(by_id.values()) + without_id


def _extract_target_context(
    *,
    narration_context: Dict[str, Any] | None = None,
    turn_contract: Dict[str, Any] | None = None,
    resolved_result: Dict[str, Any] | None = None,
    service_result: Dict[str, Any] | None = None,
) -> Dict[str, str]:
    narration_context = _safe_dict(narration_context)
    turn_contract = _safe_dict(turn_contract or narration_context.get("turn_contract"))
    resolved_result = _safe_dict(
        resolved_result
        or narration_context.get("resolved_result")
        or turn_contract.get("resolved_result")
        or turn_contract.get("resolved_action")
    )
    service_result = _safe_dict(
        service_result
        or narration_context.get("service_result")
        or resolved_result.get("service_result")
    )
    action = _safe_dict(turn_contract.get("action"))

    provider_id = _safe_str(service_result.get("provider_id"))
    provider_name = _safe_str(service_result.get("provider_name"))
    target_id = _safe_str(
        provider_id
        or resolved_result.get("target_id")
        or action.get("target_id")
    )
    target_name = _safe_str(
        provider_name
        or resolved_result.get("target_name")
        or action.get("target_name")
    )
    service_kind = _safe_str(service_result.get("service_kind"))
    selected_offer_id = _safe_str(service_result.get("selected_offer_id"))
    location_id = _safe_str(
        service_result.get("location_id")
        or resolved_result.get("location_id")
        or action.get("location_id")
    )
    action_type = _safe_str(
        resolved_result.get("action_type")
        or action.get("action_type")
        or service_result.get("kind")
    )

    return {
        "target_id": target_id,
        "target_name": target_name,
        "provider_id": provider_id,
        "provider_name": provider_name,
        "service_kind": service_kind,
        "selected_offer_id": selected_offer_id,
        "location_id": location_id,
        "action_type": action_type,
    }


def _score_memory(memory: Dict[str, Any], context: Dict[str, str]) -> float:
    score = 0.0
    owner_id = _safe_str(memory.get("owner_id"))
    owner_name = _safe_str(memory.get("owner_name"))
    subject_id = _safe_str(memory.get("subject_id"))
    target_id = _safe_str(context.get("target_id"))
    target_name = _safe_str(context.get("target_name"))

    if target_id and owner_id == target_id:
        score += 6.0
    elif target_name and owner_name.lower() == target_name.lower():
        score += 5.0

    if subject_id == "player":
        score += 2.0

    if context.get("service_kind") and _safe_str(memory.get("service_kind")) == context["service_kind"]:
        score += 1.5

    if context.get("selected_offer_id") and _safe_str(memory.get("offer_id")) == context["selected_offer_id"]:
        score += 2.0

    if context.get("location_id") and _safe_str(memory.get("location_id")) == context["location_id"]:
        score += 0.75

    kind = _safe_str(memory.get("kind"))
    if kind in {"service_purchase", "social_positive"}:
        score += 1.0
    elif kind in {"service_purchase_blocked", "social_negative"}:
        score += 0.9
    elif kind in {"service_inquiry", "social_interaction"}:
        score += 0.4

    score += min(_safe_float(memory.get("importance"), 0.0), 1.0)
    score += min(_memory_tick(memory) / 100000.0, 0.25)
    return score


def recall_npc_memories(
    simulation_state: Dict[str, Any],
    *,
    narration_context: Dict[str, Any] | None = None,
    turn_contract: Dict[str, Any] | None = None,
    resolved_result: Dict[str, Any] | None = None,
    service_result: Dict[str, Any] | None = None,
    current_tick: int = 0,
    exclude_memory_ids: List[str] | None = None,
    limit: int = DEFAULT_NPC_MEMORY_RECALL_LIMIT,
) -> Dict[str, Any]:
    context = _extract_target_context(
        narration_context=narration_context,
        turn_contract=turn_contract,
        resolved_result=resolved_result,
        service_result=service_result,
    )

    excluded = {
        _safe_str(memory_id)
        for memory_id in (exclude_memory_ids or [])
        if _safe_str(memory_id)
    }

    memories: List[Dict[str, Any]] = []
    for memory in _iter_memory_roots(simulation_state):
        memory_id = _safe_str(memory.get("memory_id"))
        if memory_id and memory_id in excluded:
            continue
        if current_tick and _memory_tick(memory) >= int(current_tick):
            continue

        owner_id = _safe_str(memory.get("owner_id"))
        owner_name = _safe_str(memory.get("owner_name"))
        target_id = context.get("target_id", "")
        target_name = context.get("target_name", "")

        if target_id and owner_id and owner_id != target_id:
            continue
        if not target_id and target_name and owner_name.lower() != target_name.lower():
            continue

        memories.append(deepcopy(memory))

    memories.sort(
        key=lambda memory: (
            _score_memory(memory, context),
            _memory_tick(memory),
            _safe_str(memory.get("memory_id")),
        ),
        reverse=True,
    )

    if limit > 0:
        memories = memories[:limit]

    return {
        "recalled_memories": memories,
        "debug": {
            "source": "deterministic_npc_memory_recall",
            "target_id": context.get("target_id", ""),
            "target_name": context.get("target_name", ""),
            "service_kind": context.get("service_kind", ""),
            "selected_offer_id": context.get("selected_offer_id", ""),
            "location_id": context.get("location_id", ""),
            "action_type": context.get("action_type", ""),
            "count": len(memories),
            "memory_ids": [_safe_str(memory.get("memory_id")) for memory in memories],
            "excluded_memory_ids": sorted(excluded),
            "current_tick": int(current_tick or 0),
        },
    }


def memory_reference_is_backed(
    line: str,
    recalled_memories: List[Dict[str, Any]],
) -> bool:
    lower = _safe_str(line).lower()
    if not lower:
        return True

    markers = (
        "remember",
        "last time",
        "again",
        "before",
        "earlier",
        "still short",
        "short on coin",
        "you bought",
        "you tried",
        "you asked",
    )
    if not any(marker in lower for marker in markers):
        return True

    if not recalled_memories:
        return False

    for memory in recalled_memories:
        memory = _safe_dict(memory)
        kind = _safe_str(memory.get("kind"))
        summary = _safe_str(memory.get("summary")).lower()
        blocked_reason = _safe_str(memory.get("blocked_reason"))

        if "short" in lower and blocked_reason == "insufficient_funds":
            return True
        if "coin" in lower and blocked_reason == "insufficient_funds":
            return True
        if "bought" in lower and kind == "service_purchase":
            return True
        if "tried" in lower and kind in {"service_purchase_blocked", "social_negative"}:
            return True
        if "asked" in lower and kind in {"service_inquiry", "social_interaction"}:
            return True
        if summary and any(token in summary for token in lower.split() if len(token) > 5):
            return True

    generic_terms = ("again", "before", "earlier", "last time", "remember")
    specific_terms = ("short", "coin", "bought", "paid", "purchased", "failed", "tried")
    if any(term in lower for term in generic_terms) and not any(term in lower for term in specific_terms):
        return bool(recalled_memories)

    return False