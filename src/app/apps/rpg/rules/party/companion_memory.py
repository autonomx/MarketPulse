from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, cast

from app.apps.rpg.rules.party.companion_presence import active_party_companions
from app.apps.rpg.rules.party.companion_values import evaluate_companion_value_alignment
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

MAX_COMPANION_MEMORIES_PER_NPC = 24
MAX_COMPANION_RELATIONSHIP_EVENTS_PER_NPC = 24


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def ensure_companion_memory_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    state = _safe_dict(simulation_state.get("companion_memory_state"))
    if not state:
        state = {}
        simulation_state["companion_memory_state"] = state

    if not isinstance(state.get("by_npc"), dict):
        state["by_npc"] = {}

    if not isinstance(state.get("relationship_by_npc"), dict):
        state["relationship_by_npc"] = {}

    if not isinstance(state.get("debug"), dict):
        state["debug"] = {}

    return state


def _ensure_npc_memory_bucket(state: Dict[str, Any], npc_id: str) -> Dict[str, Any]:
    by_npc = _safe_dict(state.get("by_npc"))
    bucket = _safe_dict(by_npc.get(npc_id))
    if not bucket:
        bucket = {
            "npc_id": npc_id,
            "memories": [],
            "source": "deterministic_companion_memory_runtime",
        }
        by_npc[npc_id] = bucket
        state["by_npc"] = by_npc

    if not isinstance(bucket.get("memories"), list):
        bucket["memories"] = []

    return bucket


def loyalty_state_for_score(score: int) -> str:
    score = int(score or 0)
    if score >= 3:
        return "loyal"
    if score <= -3:
        return "at_risk"
    if score <= -1:
        return "strained"
    return "steady"


def _ensure_relationship_bucket(state: Dict[str, Any], npc_id: str) -> Dict[str, Any]:
    relationship_by_npc = _safe_dict(state.get("relationship_by_npc"))
    rel = _safe_dict(relationship_by_npc.get(npc_id))
    if not rel:
        rel = {
            "npc_id": npc_id,
            "trust": 0,
            "respect": 0,
            "morale": 0,
            "loyalty": 0,
            "events": [],
            "loyalty_state": "steady",
            "source": "deterministic_companion_memory_runtime",
        }
        relationship_by_npc[npc_id] = rel
        state["relationship_by_npc"] = relationship_by_npc

    if not isinstance(rel.get("events"), list):
        rel["events"] = []

    for key in ("trust", "respect", "morale", "loyalty"):
        rel[key] = _safe_int(rel.get(key), 0)

    rel["loyalty_state"] = loyalty_state_for_score(_safe_int(rel.get("loyalty"), 0))
    return rel


def record_companion_memory(
    simulation_state: Dict[str, Any],
    *,
    npc_id: str,
    kind: str,
    summary: str,
    tick: int = 0,
    emotional_weight: int = 1,
    source_event_id: str = "",
    metadata: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    npc_id = _safe_str(npc_id)
    kind = _safe_str(kind)
    summary = _safe_str(summary).strip()

    if not npc_id:
        return {
            "recorded": False,
            "reason": "missing_npc_id",
            "source": "deterministic_companion_memory_runtime",
        }

    if not kind or not summary:
        return {
            "recorded": False,
            "reason": "missing_kind_or_summary",
            "npc_id": npc_id,
            "source": "deterministic_companion_memory_runtime",
        }

    state = ensure_companion_memory_state(simulation_state)
    bucket = _ensure_npc_memory_bucket(state, npc_id)
    memories = _safe_list(bucket.get("memories"))

    memory_id = f"companion_memory:{npc_id}:{kind}:{int(tick or 0)}"

    for existing in memories:
        existing = _safe_dict(existing)
        if _safe_str(existing.get("memory_id")) == memory_id:
            return {
                "recorded": False,
                "reason": "duplicate_memory",
                "memory_id": memory_id,
                "npc_id": npc_id,
                "source": "deterministic_companion_memory_runtime",
            }

    memory = {
        "memory_id": memory_id,
        "npc_id": npc_id,
        "kind": kind,
        "summary": summary,
        "tick": int(tick or 0),
        "emotional_weight": max(0, min(5, _safe_int(emotional_weight, 1))),
        "source_event_id": _safe_str(source_event_id),
        "metadata": deepcopy(_safe_dict(metadata or {})),
        "source": "deterministic_companion_memory_runtime",
    }

    memories.append(memory)
    bucket["memories"] = memories[-MAX_COMPANION_MEMORIES_PER_NPC:]

    state["debug"] = {
        "last_recorded_memory": deepcopy(memory),
        "source": "deterministic_companion_memory_runtime",
    }

    return {
        "recorded": True,
        "memory": deepcopy(memory),
        "source": "deterministic_companion_memory_runtime",
    }


def record_companion_join_memory(
    simulation_state: Dict[str, Any],
    *,
    npc_id: str,
    tick: int = 0,
) -> Dict[str, Any]:
    npc_id = _safe_str(npc_id)
    companion = {}
    for candidate in active_party_companions(simulation_state):
        candidate = _safe_dict(candidate)
        if _safe_str(candidate.get("npc_id")) == npc_id:
            companion = candidate
            break

    name = _safe_str(companion.get("name") or npc_id.replace("npc:", ""))
    identity_arc = _safe_str(companion.get("identity_arc"))
    current_role = _safe_str(companion.get("current_role"))

    if identity_arc == "revenge_after_losing_tavern":
        summary = f"{name} joined the player after losing the Rusty Flagon."
        weight = 3
    else:
        summary = f"{name} joined the player's party."
        weight = 2

    return record_companion_memory(
        simulation_state,
        npc_id=npc_id,
        kind="companion_joined_party",
        summary=summary,
        tick=tick,
        emotional_weight=weight,
        metadata={
            "identity_arc": identity_arc,
            "current_role": current_role,
        },
    )


def apply_companion_relationship_drift(
    simulation_state: Dict[str, Any],
    *,
    npc_id: str,
    trust_delta: int = 0,
    respect_delta: int = 0,
    morale_delta: int = 0,
    loyalty_delta: int = 0,
    reason: str,
    tick: int = 0,
    alignment: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    npc_id = _safe_str(npc_id)
    if not npc_id:
        return {
            "applied": False,
            "reason": "missing_npc_id",
            "source": "deterministic_companion_memory_runtime",
        }

    state = ensure_companion_memory_state(simulation_state)
    rel = _ensure_relationship_bucket(state, npc_id)

    before = {
        "trust": _safe_int(rel.get("trust"), 0),
        "respect": _safe_int(rel.get("respect"), 0),
        "morale": _safe_int(rel.get("morale"), 0),
        "loyalty": _safe_int(rel.get("loyalty"), 0),
        "loyalty_state": loyalty_state_for_score(_safe_int(rel.get("loyalty"), 0)),
    }

    rel["trust"] = max(-5, min(5, cast(int, before["trust"]) + int(trust_delta or 0)))
    rel["respect"] = max(-5, min(5, cast(int, before["respect"]) + int(respect_delta or 0)))
    rel["morale"] = max(-5, min(5, cast(int, before["morale"]) + int(morale_delta or 0)))
    rel["loyalty"] = max(-5, min(5, cast(int, before["loyalty"]) + int(loyalty_delta or 0)))
    rel["loyalty_state"] = loyalty_state_for_score(_safe_int(rel.get("loyalty"), 0))

    event = {
        "tick": int(tick or 0),
        "npc_id": npc_id,
        "trust_delta": int(trust_delta or 0),
        "respect_delta": int(respect_delta or 0),
        "morale_delta": int(morale_delta or 0),
        "loyalty_delta": int(loyalty_delta or 0),
        "reason": _safe_str(reason),
        "alignment": deepcopy(_safe_dict(alignment or {})),
        "before": before,
        "after": {
            "trust": rel["trust"],
            "respect": rel["respect"],
            "morale": rel["morale"],
            "loyalty": rel["loyalty"],
            "loyalty_state": rel["loyalty_state"],
        },
        "source": "deterministic_companion_memory_runtime",
    }

    events = _safe_list(rel.get("events"))
    events.append(event)
    rel["events"] = events[-MAX_COMPANION_RELATIONSHIP_EVENTS_PER_NPC:]

    state["debug"] = {
        "last_relationship_event": deepcopy(event),
        "source": "deterministic_companion_memory_runtime",
    }

    return {
        "applied": True,
        "npc_id": npc_id,
        "event": deepcopy(event),
        "relationship": deepcopy(rel),
        "source": "deterministic_companion_memory_runtime",
    }


def _memory_kind_for_alignment(alignment: Dict[str, Any]) -> str:
    reason = _safe_str(alignment.get("reason"))
    if reason == "player_dismissed_companion_core_motivation":
        return "player_dismissed_core_motivation"
    if reason == "player_supported_companion_core_motivation":
        return "player_supported_core_motivation"

    relation = _safe_str(alignment.get("alignment"))
    if relation == "aligned_with_npc":
        return "player_action_aligned_with_values"
    if relation == "conflicts_with_npc":
        return "player_action_conflicted_with_values"
    return "player_action_value_neutral"


def _memory_summary_for_alignment(alignment: Dict[str, Any]) -> str:
    reason = _safe_str(alignment.get("reason"))
    npc_id = _safe_str(alignment.get("npc_id"))
    name = npc_id.replace("npc:", "") or "The companion"

    if reason == "player_dismissed_companion_core_motivation":
        return "The player dismissed Bran's need to answer the bandits who destroyed his tavern."
    if reason == "player_supported_companion_core_motivation":
        return "The player promised to help Bran find the bandits who destroyed his tavern."

    relation = _safe_str(alignment.get("alignment"))
    tags = ", ".join(_safe_list(_safe_dict(alignment.get("evaluated_player_action")).get("player_action_tags")))
    if relation == "aligned_with_npc":
        return f"The player's action aligned with {name}'s values: {tags}."
    if relation == "conflicts_with_npc":
        return f"The player's action conflicted with {name}'s values: {tags}."
    return f"The player's action had little effect on {name}'s values."


def maybe_apply_companion_relationship_drift_from_player_input(
    simulation_state: Dict[str, Any],
    *,
    player_input: str,
    tick: int = 0,
) -> Dict[str, Any]:
    """Apply drift for active companions based on value alignment.

    For v1, apply to active companions mentioned by name. This prevents every
    companion from reacting to every player action before multi-companion party
    dynamics are fully designed.
    """
    text = _safe_str(player_input).lower()
    matched_results: List[Dict[str, Any]] = []

    for companion in active_party_companions(simulation_state):
        companion = _safe_dict(companion)
        npc_id = _safe_str(companion.get("npc_id"))
        name = _safe_str(companion.get("name") or npc_id.replace("npc:", ""))
        if not npc_id:
            continue

        if name.lower() not in text and npc_id.lower() not in text:
            continue

        alignment = evaluate_companion_value_alignment(
            simulation_state,
            npc_id=npc_id,
            player_input=player_input,
        )

        if not alignment.get("matched"):
            matched_results.append({
                "applied": False,
                "npc_id": npc_id,
                "alignment": deepcopy(alignment),
                "reason": _safe_str(alignment.get("reason")),
                "source": "deterministic_companion_memory_runtime",
            })
            continue

        deltas = _safe_dict(alignment.get("deltas"))
        drift = apply_companion_relationship_drift(
            simulation_state,
            npc_id=npc_id,
            trust_delta=_safe_int(deltas.get("trust_delta"), 0),
            respect_delta=_safe_int(deltas.get("respect_delta"), 0),
            morale_delta=_safe_int(deltas.get("morale_delta"), 0),
            loyalty_delta=_safe_int(deltas.get("loyalty_delta"), 0),
            reason=_safe_str(alignment.get("reason")),
            tick=tick,
            alignment=deepcopy(alignment),
        )

        memory = record_companion_memory(
            simulation_state,
            npc_id=npc_id,
            kind=_memory_kind_for_alignment(alignment),
            summary=_memory_summary_for_alignment(alignment),
            tick=tick,
            emotional_weight=3 if _safe_str(alignment.get("alignment")) != "neutral_to_npc" else 1,
            metadata={"alignment": deepcopy(alignment)},
        )

        matched_results.append({
            "applied": True,
            "npc_id": npc_id,
            "alignment": deepcopy(alignment),
            "drift": deepcopy(drift),
            "memory": deepcopy(memory),
            "source": "deterministic_companion_memory_runtime",
        })

    if not matched_results:
        return {
            "applied": False,
            "reason": "no_mentioned_active_companion_value_drift",
            "results": [],
            "source": "deterministic_companion_memory_runtime",
        }

    applied = [item for item in matched_results if item.get("applied")]
    return {
        "applied": bool(applied),
        "results": matched_results,
        "primary": deepcopy(applied[0] if applied else matched_results[0]),
        "source": "deterministic_companion_memory_runtime",
    }


def companion_loyalty_projection(
    simulation_state: Dict[str, Any],
    *,
    npc_id: str,
) -> Dict[str, Any]:
    state = ensure_companion_memory_state(simulation_state)
    rel = _ensure_relationship_bucket(state, _safe_str(npc_id))
    loyalty = _safe_int(rel.get("loyalty"), 0)
    loyalty_state = loyalty_state_for_score(loyalty)

    if loyalty_state == "loyal":
        response_bias = "volunteers_support"
    elif loyalty_state == "strained":
        response_bias = "questions_orders"
    elif loyalty_state == "at_risk":
        response_bias = "may_leave_after_warning"
    else:
        response_bias = "steady_support"

    return {
        "npc_id": _safe_str(npc_id),
        "loyalty": loyalty,
        "loyalty_state": loyalty_state,
        "response_bias": response_bias,
        "relationship": deepcopy(rel),
        "source": "deterministic_companion_memory_runtime",
    }


def companion_memory_summary(simulation_state: Dict[str, Any], *, npc_id: str = "") -> Dict[str, Any]:
    state = ensure_companion_memory_state(simulation_state)
    by_npc = _safe_dict(state.get("by_npc"))
    relationship_by_npc = _safe_dict(state.get("relationship_by_npc"))

    if npc_id:
        npc_id = _safe_str(npc_id)
        return {
            "npc_id": npc_id,
            "memories": deepcopy(_safe_list(_safe_dict(by_npc.get(npc_id)).get("memories"))),
            "relationship": deepcopy(_safe_dict(relationship_by_npc.get(npc_id))),
            "loyalty_projection": companion_loyalty_projection(simulation_state, npc_id=npc_id),
            "source": "deterministic_companion_memory_runtime",
        }

    summaries = {}
    for key in sorted(set(by_npc.keys()) | set(relationship_by_npc.keys())):
        summaries[_safe_str(key)] = {
            "memory_count": len(_safe_list(_safe_dict(by_npc.get(key)).get("memories"))),
            "relationship": deepcopy(_safe_dict(relationship_by_npc.get(key))),
            "loyalty_projection": companion_loyalty_projection(simulation_state, npc_id=_safe_str(key)),
        }

    return {
        "by_npc": summaries,
        "source": "deterministic_companion_memory_runtime",
    }
