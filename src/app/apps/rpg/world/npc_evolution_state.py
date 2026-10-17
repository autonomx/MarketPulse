from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

MAX_PERSONALITY_MODIFIERS = 12
MAX_ACTIVE_MOTIVATIONS = 12
MAX_EVOLUTION_EVENTS = 24


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def ensure_npc_evolution_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    state = _safe_dict(simulation_state.get("npc_evolution_state"))
    if not isinstance(state.get("by_npc"), dict):
        state["by_npc"] = {}
    if not isinstance(state.get("debug"), dict):
        state["debug"] = {}
    simulation_state["npc_evolution_state"] = state
    return state


def get_npc_evolution(
    simulation_state: Dict[str, Any],
    *,
    npc_id: str,
) -> Dict[str, Any]:
    state = ensure_npc_evolution_state(simulation_state)
    entry = _safe_dict(_safe_dict(state.get("by_npc")).get(_safe_str(npc_id)))
    return deepcopy(entry)


def apply_npc_evolution_event(
    simulation_state: Dict[str, Any],
    *,
    npc_id: str,
    event_id: str,
    kind: str,
    current_role: str = "",
    identity_arc: str = "",
    personality_modifier: Dict[str, Any] | None = None,
    motivation: Dict[str, Any] | None = None,
    party_join_eligibility: Dict[str, Any] | None = None,
    tick: int,
) -> Dict[str, Any]:
    npc_id = _safe_str(npc_id)
    event_id = _safe_str(event_id)
    kind = _safe_str(kind)

    if not npc_id.startswith("npc:"):
        return {"applied": False, "reason": "invalid_npc_id"}
    if not event_id:
        return {"applied": False, "reason": "missing_source_event_id"}
    if not kind:
        return {"applied": False, "reason": "missing_evolution_kind"}

    state = ensure_npc_evolution_state(simulation_state)
    by_npc = _safe_dict(state.get("by_npc"))
    entry = _safe_dict(by_npc.get(npc_id))

    seen_event_ids = {
        _safe_str(event.get("event_id"))
        for event in _safe_list(entry.get("evolution_events"))
    }
    if event_id in seen_event_ids:
        return {
            "applied": False,
            "reason": "evolution_event_already_applied",
            "npc_id": npc_id,
            "event_id": event_id,
        }

    if not entry:
        entry = {
            "npc_id": npc_id,
            "current_role": "",
            "identity_arc": "",
            "personality_modifiers": [],
            "active_motivations": [],
            "party_join_eligibility": {},
            "evolution_events": [],
            "source": "deterministic_npc_evolution_runtime",
        }

    if current_role:
        entry["current_role"] = current_role
    if identity_arc:
        entry["identity_arc"] = identity_arc

    modifier = _safe_dict(personality_modifier)
    if modifier:
        modifier.setdefault("modifier_id", f"mod:{npc_id}:{event_id}:{kind}")
        modifier.setdefault("source_event_id", event_id)
        modifier.setdefault("tick", int(tick or 0))
        modifiers = [
            _safe_dict(item)
            for item in _safe_list(entry.get("personality_modifiers"))
            if _safe_str(_safe_dict(item).get("modifier_id")) != _safe_str(modifier.get("modifier_id"))
        ]
        modifiers.insert(0, modifier)
        entry["personality_modifiers"] = modifiers[:MAX_PERSONALITY_MODIFIERS]

    motivation_entry = _safe_dict(motivation)
    if motivation_entry:
        motivation_entry.setdefault("motivation_id", f"motivation:{npc_id}:{event_id}:{kind}")
        motivation_entry.setdefault("source_event_id", event_id)
        motivation_entry.setdefault("tick", int(tick or 0))
        motivations = [
            _safe_dict(item)
            for item in _safe_list(entry.get("active_motivations"))
            if _safe_str(_safe_dict(item).get("motivation_id")) != _safe_str(motivation_entry.get("motivation_id"))
        ]
        motivations.insert(0, motivation_entry)
        entry["active_motivations"] = motivations[:MAX_ACTIVE_MOTIVATIONS]

    party = _safe_dict(entry.get("party_join_eligibility"))
    party_update = _safe_dict(party_join_eligibility)
    if party_update:
        party.update(party_update)
        party.setdefault("source_event_id", event_id)
        party.setdefault("tick", int(tick or 0))
        entry["party_join_eligibility"] = party

    events = _safe_list(entry.get("evolution_events"))
    events.insert(
        0,
        {
            "event_id": event_id,
            "kind": kind,
            "tick": int(tick or 0),
            "source": "deterministic_npc_evolution_runtime",
        },
    )
    entry["evolution_events"] = events[:MAX_EVOLUTION_EVENTS]
    entry["last_updated_tick"] = int(tick or 0)

    by_npc[npc_id] = entry
    state["by_npc"] = by_npc
    state["debug"] = {
        "last_updated_tick": int(tick or 0),
        "last_npc_id": npc_id,
        "last_event_id": event_id,
        "source": "deterministic_npc_evolution_runtime",
    }

    return {
        "applied": True,
        "npc_id": npc_id,
        "event_id": event_id,
        "evolution": deepcopy(entry),
        "source": "deterministic_npc_evolution_runtime",
    }


def merged_npc_identity(
    *,
    base_profile: Dict[str, Any],
    evolution: Dict[str, Any],
) -> Dict[str, Any]:
    base = _safe_dict(base_profile)
    evo = _safe_dict(evolution)

    base_personality = _safe_dict(base.get("personality"))
    modifiers = deepcopy(_safe_list(evo.get("personality_modifiers")))
    motivations = deepcopy(_safe_list(evo.get("active_motivations")))

    base_role = _safe_str(base.get("base_role") or base.get("role") or base.get("starting_role"))
    current_role = _safe_str(evo.get("current_role")) or _safe_str(
        base.get("current_role_hint") or base_role
    )

    merged = {
        **deepcopy(base),
        "base_role": base_role,
        "starting_role": _safe_str(base.get("starting_role") or base_role),
        "current_role": current_role,
        "role": current_role or base_role,
        "identity_arc": _safe_str(evo.get("identity_arc")),
        "personality_modifiers": modifiers,
        "active_motivations": motivations,
        "party_join_eligibility": deepcopy(_safe_dict(evo.get("party_join_eligibility"))),
        "npc_evolution": deepcopy(evo),
        "personality": {
            **deepcopy(base_personality),
            "evolution_modifiers": modifiers,
        },
        "source": "merged_file_profile_and_evolution_state",
    }

    return merged
