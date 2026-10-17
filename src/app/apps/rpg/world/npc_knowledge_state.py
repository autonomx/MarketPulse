from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

MAX_KNOWN_FACTS_PER_NPC = 24
DEFAULT_KNOWLEDGE_TTL_TICKS = 2000


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def ensure_npc_knowledge_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    state = _safe_dict(simulation_state.get("npc_knowledge_state"))
    if not isinstance(state.get("by_npc"), dict):
        state["by_npc"] = {}
    if not isinstance(state.get("debug"), dict):
        state["debug"] = {}
    simulation_state["npc_knowledge_state"] = state
    return state


def prune_npc_knowledge_state(
    simulation_state: Dict[str, Any],
    *,
    current_tick: int,
    max_known_facts_per_npc: int = MAX_KNOWN_FACTS_PER_NPC,
) -> Dict[str, Any]:
    state = ensure_npc_knowledge_state(simulation_state)
    by_npc = _safe_dict(state.get("by_npc"))
    expired_ids: List[str] = []

    for npc_id, npc_state in list(by_npc.items()):
        npc_state = _safe_dict(npc_state)
        kept = []
        for fact in _safe_list(npc_state.get("known_facts")):
            fact = _safe_dict(fact)
            knowledge_id = _safe_str(fact.get("knowledge_id"))
            expires_tick = _safe_int(fact.get("expires_tick"), 0)
            if expires_tick and int(current_tick or 0) >= expires_tick:
                if knowledge_id:
                    expired_ids.append(knowledge_id)
                continue
            kept.append(fact)

        kept.sort(
            key=lambda item: (
                _safe_int(_safe_dict(item).get("confidence"), 0),
                _safe_int(_safe_dict(item).get("tick"), 0),
                _safe_str(_safe_dict(item).get("knowledge_id")),
            ),
            reverse=True,
        )
        npc_state["known_facts"] = kept[: max(1, int(max_known_facts_per_npc or MAX_KNOWN_FACTS_PER_NPC))]
        by_npc[npc_id] = npc_state

    state["by_npc"] = by_npc
    state["debug"] = {
        **_safe_dict(state.get("debug")),
        "last_prune_tick": int(current_tick or 0),
        "expired_knowledge_ids": expired_ids,
        "source": "deterministic_npc_knowledge_runtime",
    }
    return {
        "expired_knowledge_ids": expired_ids,
        "source": "deterministic_npc_knowledge_runtime",
    }


def add_npc_knowledge_from_topic(
    simulation_state: Dict[str, Any],
    *,
    npc_id: str,
    topic: Dict[str, Any],
    tick: int,
    confidence: int = 2,
    ttl_ticks: int = DEFAULT_KNOWLEDGE_TTL_TICKS,
) -> Dict[str, Any]:
    npc_id = _safe_str(npc_id)
    topic = _safe_dict(topic)
    topic_id = _safe_str(topic.get("topic_id"))
    source_id = _safe_str(topic.get("source_id") or topic_id)
    source_kind = _safe_str(topic.get("source_kind") or topic.get("topic_type"))
    summary = _safe_str(topic.get("summary") or topic.get("title")).strip()

    if not npc_id.startswith("npc:"):
        return {"created": False, "reason": "invalid_npc_id"}
    if not topic_id or not summary:
        return {"created": False, "reason": "knowledge_requires_backed_topic"}

    state = ensure_npc_knowledge_state(simulation_state)
    by_npc = _safe_dict(state.get("by_npc"))
    npc_state = _safe_dict(by_npc.get(npc_id))
    facts = _safe_list(npc_state.get("known_facts"))

    current_tick = int(tick or 0)
    knowledge_id = f"know:{npc_id}:{topic_id}"

    entry = {
        "knowledge_id": knowledge_id,
        "npc_id": npc_id,
        "source_topic_id": topic_id,
        "source_id": source_id,
        "source_kind": source_kind,
        "summary": summary[:280],
        "confidence": max(1, min(5, _safe_int(confidence, 2))),
        "tick": current_tick,
        "expires_tick": current_tick + max(1, int(ttl_ticks or DEFAULT_KNOWLEDGE_TTL_TICKS)),
        "source": "deterministic_npc_knowledge_runtime",
    }

    facts = [
        _safe_dict(fact)
        for fact in facts
        if _safe_str(_safe_dict(fact).get("knowledge_id")) != knowledge_id
    ]
    facts.append(entry)
    facts.sort(
        key=lambda item: (
            _safe_int(_safe_dict(item).get("confidence"), 0),
            _safe_int(_safe_dict(item).get("tick"), 0),
        ),
        reverse=True,
    )

    npc_state["known_facts"] = facts[:MAX_KNOWN_FACTS_PER_NPC]
    by_npc[npc_id] = npc_state
    state["by_npc"] = by_npc
    simulation_state["npc_knowledge_state"] = state

    return {
        "created": True,
        "entry": deepcopy(entry),
        "source": "deterministic_npc_knowledge_runtime",
    }


def known_facts_for_npc(
    simulation_state: Dict[str, Any],
    *,
    npc_id: str,
    limit: int = 6,
) -> List[Dict[str, Any]]:
    state = ensure_npc_knowledge_state(simulation_state)
    npc_state = _safe_dict(_safe_dict(state.get("by_npc")).get(_safe_str(npc_id)))
    facts = sorted(
        [_safe_dict(fact) for fact in _safe_list(npc_state.get("known_facts"))],
        key=lambda item: (
            _safe_int(item.get("confidence"), 0),
            _safe_int(item.get("tick"), 0),
        ),
        reverse=True,
    )
    return deepcopy(facts[: max(0, int(limit or 0))])
