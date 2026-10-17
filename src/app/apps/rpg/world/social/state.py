from __future__ import annotations

from typing import Any, Dict, cast
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

DEFAULT_MAX_LEVERAGE_PER_NPC = 20


def clamp_social_value(value: Any, *, minimum: int = -100, maximum: int = 100) -> int:
    try:
        number = int(value)
    except Exception:
        number = 0
    return max(minimum, min(maximum, number))


def _safe_str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def normalize_relationship(value: Dict[str, Any] | None) -> Dict[str, Any]:
    value = _safe_dict(value)
    leverage = [
        dict(item)
        for item in _safe_list(value.get("leverage"))
        if isinstance(item, dict)
    ]
    return {
        "trust": clamp_social_value(value.get("trust")),
        "fear": clamp_social_value(value.get("fear")),
        "respect": clamp_social_value(value.get("respect")),
        "hostility": clamp_social_value(value.get("hostility")),
        "reputation": clamp_social_value(value.get("reputation")),
        "leverage": leverage[-DEFAULT_MAX_LEVERAGE_PER_NPC:],
        "last_stance": _safe_str(value.get("last_stance")) or "neutral",
    }


def normalize_social_profile(value: Dict[str, Any] | None, *, npc_id: str = "") -> Dict[str, Any]:
    value = _safe_dict(value)
    return {
        "npc_id": _safe_str(value.get("npc_id")) or npc_id,
        "bravery": clamp_social_value(value.get("bravery") if value.get("bravery") is not None else 50, minimum=0, maximum=100),
        "greed": clamp_social_value(value.get("greed") if value.get("greed") is not None else 30, minimum=0, maximum=100),
        "honor": clamp_social_value(value.get("honor") if value.get("honor") is not None else 50, minimum=0, maximum=100),
        "stubbornness": clamp_social_value(value.get("stubbornness") if value.get("stubbornness") is not None else 40, minimum=0, maximum=100),
        "social_awareness": clamp_social_value(value.get("social_awareness") if value.get("social_awareness") is not None else 50, minimum=0, maximum=100),
        "authority_respect": clamp_social_value(value.get("authority_respect") if value.get("authority_respect") is not None else 50, minimum=0, maximum=100),
        "risk_tolerance": clamp_social_value(value.get("risk_tolerance") if value.get("risk_tolerance") is not None else 40, minimum=0, maximum=100),
    }


def normalize_social_state(value: Dict[str, Any] | None) -> Dict[str, Any]:
    value = _safe_dict(value)

    relationships: Dict[str, Dict[str, Any]] = {}
    for npc_id, relationship in _safe_dict(value.get("relationships")).items():
        npc_id = str(npc_id or "")
        if not npc_id:
            continue
        relationships[npc_id] = normalize_relationship(relationship)

    profiles: Dict[str, Dict[str, Any]] = {}
    for npc_id, profile in _safe_dict(value.get("profiles")).items():
        npc_id = str(npc_id or "")
        if not npc_id:
            continue
        profiles[npc_id] = normalize_social_profile(profile, npc_id=npc_id)

    global_reputation = {
        str(actor_id): clamp_social_value(score)
        for actor_id, score in _safe_dict(value.get("global_reputation")).items()
        if str(actor_id)
    }

    manual_results: Dict[str, Dict[str, Any]] = {}
    for key, row in _safe_dict(value.get("manual_results")).items():
        if isinstance(row, dict):
            manual_results[str(key)] = dict(row)

    max_leverage = value.get("max_leverage_per_npc")
    try:
        max_leverage = int(cast(Any, max_leverage))
    except Exception:
        max_leverage = DEFAULT_MAX_LEVERAGE_PER_NPC
    if max_leverage <= 0:
        max_leverage = DEFAULT_MAX_LEVERAGE_PER_NPC

    return {
        "version": 1,
        "relationships": relationships,
        "profiles": profiles,
        "global_reputation": global_reputation,
        "manual_results": manual_results,
        "max_leverage_per_npc": max_leverage,
    }


def ensure_social_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(simulation_state, dict):
        raise TypeError("simulation_state_must_be_dict")
    state = normalize_social_state(simulation_state.get("social_state"))
    simulation_state["social_state"] = state
    return state


def ensure_relationship(simulation_state: Dict[str, Any], npc_id: str) -> Dict[str, Any]:
    state = ensure_social_state(simulation_state)
    relationships = state.setdefault("relationships", {})
    if npc_id not in relationships:
        relationships[npc_id] = normalize_relationship({})
    else:
        relationships[npc_id] = normalize_relationship(relationships[npc_id])
    return relationships[npc_id]


def ensure_profile(simulation_state: Dict[str, Any], npc_id: str) -> Dict[str, Any]:
    state = ensure_social_state(simulation_state)
    profiles = state.setdefault("profiles", {})
    if npc_id not in profiles:
        profiles[npc_id] = normalize_social_profile({}, npc_id=npc_id)
    else:
        profiles[npc_id] = normalize_social_profile(profiles[npc_id], npc_id=npc_id)
    return profiles[npc_id]