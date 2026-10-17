from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

from app.apps.rpg.world.npc_history_state import recent_npc_history
from app.apps.rpg.world.npc_knowledge_state import known_facts_for_npc
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_str as _safe_str

MAX_RECALLS_PER_RESPONSE = 2


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


MEMORY_RECALL_REQUEST_MARKERS = {
    "remember",
    "recall",
    "earlier",
    "before",
    "last time",
    "what i asked",
    "what i said",
    "asked before",
    "said before",
}


def player_input_requests_recall(player_input: Any) -> bool:
    text = _safe_str(player_input).strip().lower()
    if not text:
        return False
    return any(marker in text for marker in MEMORY_RECALL_REQUEST_MARKERS)


def ensure_dialogue_recall_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    state = _safe_dict(simulation_state.get("npc_dialogue_recall_state"))
    if not isinstance(state.get("cooldowns"), dict):
        state["cooldowns"] = {}
    if not isinstance(state.get("debug"), dict):
        state["debug"] = {}
    simulation_state["npc_dialogue_recall_state"] = state
    return state


def _token_overlap_score(a: str, b: str) -> int:
    a_tokens = {token for token in _safe_str(a).lower().replace("_", " ").split() if len(token) > 3}
    b_tokens = {token for token in _safe_str(b).lower().replace("_", " ").split() if len(token) > 3}
    return len(a_tokens & b_tokens)


def select_dialogue_recall(
    simulation_state: Dict[str, Any],
    *,
    npc_id: str,
    topic: Dict[str, Any],
    tick: int,
    player_input: str = "",
    cooldown_ticks: int = 4,
    limit: int = MAX_RECALLS_PER_RESPONSE,
) -> Dict[str, Any]:
    state = ensure_dialogue_recall_state(simulation_state)
    cooldowns = _safe_dict(state.get("cooldowns"))
    npc_id = _safe_str(npc_id)
    topic = _safe_dict(topic)
    topic_text = " ".join(
        _safe_str(topic.get(key))
        for key in ("topic_id", "topic_type", "title", "summary")
    )
    recall_requested = player_input_requests_recall(player_input)

    cooldown_key = f"{npc_id}:{_safe_str(topic.get('topic_id'))}"
    cooldown_until = _safe_int(cooldowns.get(cooldown_key), 0)
    if cooldown_until and int(tick or 0) < cooldown_until and not recall_requested:
        return {
            "selected": False,
            "reason": "recall_on_cooldown",
            "source": "deterministic_npc_dialogue_recall",
        }

    candidates: List[Dict[str, Any]] = []

    for history in recent_npc_history(simulation_state, npc_id=npc_id, limit=8):
        summary = _safe_str(history.get("summary"))
        score = _token_overlap_score(topic_text, summary) + _safe_int(history.get("importance"), 0)
        if recall_requested:
            score = max(score, 3 + _safe_int(history.get("importance"), 0))
        if score <= 0:
            continue
        candidates.append(
            {
                "kind": "history",
                "summary": summary,
                "source_history_id": _safe_str(history.get("history_id")),
                "topic_id": _safe_str(history.get("topic_id")),
                "tick": _safe_int(history.get("tick"), 0),
                "score": score,
            }
        )

    for fact in known_facts_for_npc(simulation_state, npc_id=npc_id, limit=8):
        summary = _safe_str(fact.get("summary"))
        score = _token_overlap_score(topic_text, summary) + _safe_int(fact.get("confidence"), 0)
        if recall_requested:
            score = max(score, 2 + _safe_int(fact.get("confidence"), 0))
        if score <= 0:
            continue
        candidates.append(
            {
                "kind": "knowledge",
                "summary": summary,
                "source_knowledge_id": _safe_str(fact.get("knowledge_id")),
                "topic_id": _safe_str(fact.get("source_topic_id")),
                "tick": _safe_int(fact.get("tick"), 0),
                "score": score,
            }
        )

    if not candidates:
        state["debug"] = {
            "selected": False,
            "reason": "no_relevant_recall",
            "npc_id": npc_id,
            "topic_id": _safe_str(topic.get("topic_id")),
            "recall_requested": bool(recall_requested),
            "tick": int(tick or 0),
            "source": "deterministic_npc_dialogue_recall",
        }
        return {
            "selected": False,
            "reason": "no_relevant_recall",
            "recall_requested": bool(recall_requested),
            "source": "deterministic_npc_dialogue_recall",
        }

    candidates.sort(
        key=lambda item: (
            _safe_int(item.get("score"), 0),
            _safe_int(item.get("tick"), 0),
        ),
        reverse=True,
    )
    selected = candidates[: max(1, int(limit or MAX_RECALLS_PER_RESPONSE))]
    cooldowns[cooldown_key] = int(tick or 0) + max(1, int(cooldown_ticks or 4))
    state["cooldowns"] = cooldowns
    state["debug"] = {
        "selected": True,
        "npc_id": npc_id,
        "topic_id": _safe_str(topic.get("topic_id")),
        "selected_recall": selected,
        "recall_requested": bool(recall_requested),
        "tick": int(tick or 0),
        "source": "deterministic_npc_dialogue_recall",
    }

    return {
        "selected": True,
        "recalls": deepcopy(selected),
        "recall_requested": bool(recall_requested),
        "source": "deterministic_npc_dialogue_recall",
    }


def find_recall_capable_npc(
    simulation_state: Dict[str, Any],
    *,
    candidate_npc_ids: List[str],
    player_input: str,
    topic: Dict[str, Any] | None = None,
    tick: int,
) -> Dict[str, Any]:
    """Return the first present NPC with selectable recall for a recall-shaped player input.

    This is deterministic and bounded. It does not create facts. It only checks
    existing npc_history_state / npc_knowledge_state.
    """
    if not player_input_requests_recall(player_input):
        return {
            "selected": False,
            "reason": "player_input_not_recall_request",
            "source": "deterministic_npc_dialogue_recall",
        }

    topic = _safe_dict(topic)
    for npc_id in candidate_npc_ids[:8]:
        npc_id = _safe_str(npc_id)
        if not npc_id.startswith("npc:"):
            continue
        recall = select_dialogue_recall(
            simulation_state,
            npc_id=npc_id,
            topic=topic,
            tick=tick,
            player_input=player_input,
            cooldown_ticks=1,
            limit=2,
        )
        if recall.get("selected"):
            return {
                "selected": True,
                "npc_id": npc_id,
                "dialogue_recall": recall,
                "source": "deterministic_npc_dialogue_recall",
            }

    return {
        "selected": False,
        "reason": "no_present_npc_with_recall",
        "source": "deterministic_npc_dialogue_recall",
    }
