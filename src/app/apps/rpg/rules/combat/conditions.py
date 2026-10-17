from __future__ import annotations

from typing import Any, Dict, List
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

MAX_STATUS_EFFECTS_PER_ACTOR = 12
STACKING_KINDS = {"bleeding", "poisoned", "burning"}
UNIQUE_KINDS = {"stunned", "stabilized", "unconscious", "downed", "prone", "guarded"}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def normalize_status_effect(effect: Dict[str, Any]) -> Dict[str, Any]:
    effect = _safe_dict(effect)
    kind = _safe_str(effect.get("kind")).strip().lower()
    target_actor_id = _safe_str(effect.get("target_actor_id")).strip()

    return {
        "effect_id": _safe_str(
            effect.get("effect_id")
            or f"effect:{kind}:{target_actor_id}"
        ).strip(),
        "kind": kind,
        "source": _safe_str(effect.get("source") or "combat").strip(),
        "source_actor_id": _safe_str(effect.get("source_actor_id")).strip(),
        "target_actor_id": target_actor_id,
        "duration_turns": max(0, _safe_int(effect.get("duration_turns"), 1)),
        "stacks": max(1, _safe_int(effect.get("stacks"), 1)),
        "magnitude": max(0, _safe_int(effect.get("magnitude"), 0)),
        "tick_timing": _safe_str(effect.get("tick_timing") or "start_of_turn").strip(),
    }


def normalize_status_effects(effects: Any) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for effect in _safe_list(effects):
        item = normalize_status_effect(_safe_dict(effect))
        if item.get("kind") and item.get("target_actor_id") and item.get("duration_turns", 0) > 0:
            normalized.append(item)
    return normalized[:MAX_STATUS_EFFECTS_PER_ACTOR]


def get_participant_status_effects(participant: Dict[str, Any]) -> List[Dict[str, Any]]:
    participant = _safe_dict(participant)
    return normalize_status_effects(participant.get("status_effects"))


def actor_has_condition(participant: Dict[str, Any], kind: str) -> bool:
    kind = _safe_str(kind).strip().lower()
    return any(effect.get("kind") == kind for effect in get_participant_status_effects(participant))


def add_status_effect_to_participant(
    participant: Dict[str, Any],
    effect: Dict[str, Any],
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    participant = dict(_safe_dict(participant))
    incoming = normalize_status_effect(effect)
    effects = get_participant_status_effects(participant)

    if not incoming.get("kind") or not incoming.get("target_actor_id"):
        return participant, {
            "applied": False,
            "reason": "invalid_effect",
            "effects_added": [],
            "effects_updated": [],
        }

    kind = incoming["kind"]
    updated: List[Dict[str, Any]] = []
    added: List[Dict[str, Any]] = []

    merged = False
    next_effects: List[Dict[str, Any]] = []

    for existing in effects:
        if existing.get("kind") == kind:
            existing = dict(existing)
            if kind in STACKING_KINDS:
                existing["stacks"] = min(9, _safe_int(existing.get("stacks"), 1) + _safe_int(incoming.get("stacks"), 1))
                existing["magnitude"] = max(
                    _safe_int(existing.get("magnitude"), 0),
                    _safe_int(incoming.get("magnitude"), 0),
                )
                existing["duration_turns"] = max(
                    _safe_int(existing.get("duration_turns"), 0),
                    _safe_int(incoming.get("duration_turns"), 0),
                )
            else:
                existing["duration_turns"] = max(
                    _safe_int(existing.get("duration_turns"), 0),
                    _safe_int(incoming.get("duration_turns"), 0),
                )
                existing["magnitude"] = max(
                    _safe_int(existing.get("magnitude"), 0),
                    _safe_int(incoming.get("magnitude"), 0),
                )
                existing["stacks"] = max(
                    _safe_int(existing.get("stacks"), 1),
                    _safe_int(incoming.get("stacks"), 1),
                )

            updated.append(existing)
            next_effects.append(existing)
            merged = True
        else:
            next_effects.append(existing)

    if not merged:
        next_effects.append(incoming)
        added.append(incoming)

    participant["status_effects"] = normalize_status_effects(next_effects)

    return participant, {
        "applied": True,
        "reason": "effect_merged" if merged else "effect_added",
        "effects_added": added,
        "effects_updated": updated,
    }


def remove_status_effects_from_participant(
    participant: Dict[str, Any],
    kinds: List[str],
) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
    participant = dict(_safe_dict(participant))
    wanted = {_safe_str(kind).strip().lower() for kind in kinds if _safe_str(kind).strip()}
    removed: List[Dict[str, Any]] = []
    kept: List[Dict[str, Any]] = []

    for effect in get_participant_status_effects(participant):
        if effect.get("kind") in wanted:
            removed.append(effect)
        else:
            kept.append(effect)

    participant["status_effects"] = normalize_status_effects(kept)
    return participant, removed


def tick_start_of_turn_status_effects(
    participant: Dict[str, Any],
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    participant = dict(_safe_dict(participant))
    effects = get_participant_status_effects(participant)

    hp_before = _safe_int(participant.get("hp"), _safe_int(_safe_dict(participant.get("resources")).get("hp"), 0))
    hp_after = hp_before

    ticked: List[Dict[str, Any]] = []
    expired: List[Dict[str, Any]] = []
    kept: List[Dict[str, Any]] = []

    for effect in effects:
        effect = dict(effect)
        kind = effect.get("kind")
        timing = effect.get("tick_timing") or "start_of_turn"
        duration = _safe_int(effect.get("duration_turns"), 0)

        if timing == "start_of_turn":
            damage = 0
            if kind in {"bleeding", "poisoned", "burning"}:
                damage = max(1, _safe_int(effect.get("magnitude"), 1)) * max(1, _safe_int(effect.get("stacks"), 1))
                hp_after = max(0, hp_after - damage)

            duration -= 1
            effect["duration_turns"] = max(0, duration)
            ticked.append({
                "kind": kind,
                "damage": damage,
                "stacks": effect.get("stacks"),
                "magnitude": effect.get("magnitude"),
                "duration_turns_after": effect["duration_turns"],
            })

        if _safe_int(effect.get("duration_turns"), 0) <= 0:
            expired.append(effect)
        else:
            kept.append(effect)

    participant["hp"] = hp_after
    resources = dict(_safe_dict(participant.get("resources")))
    if resources:
        resources["hp"] = hp_after
        participant["resources"] = resources

    participant["status_effects"] = normalize_status_effects(kept)

    return participant, {
        "ticked": bool(ticked),
        "hp_before": hp_before,
        "hp_after": hp_after,
        "effects_ticked": ticked,
        "effects_expired": expired,
    }


def build_condition_effect(
    *,
    kind: str,
    source_actor_id: str,
    target_actor_id: str,
    duration_turns: int,
    magnitude: int = 1,
    stacks: int = 1,
    source: str = "combat",
    tick_timing: str = "start_of_turn",
) -> Dict[str, Any]:
    kind = _safe_str(kind).strip().lower()
    target_actor_id = _safe_str(target_actor_id).strip()
    return normalize_status_effect({
        "effect_id": f"effect:{kind}:{target_actor_id}:{source_actor_id}",
        "kind": kind,
        "source": source,
        "source_actor_id": source_actor_id,
        "target_actor_id": target_actor_id,
        "duration_turns": duration_turns,
        "stacks": stacks,
        "magnitude": magnitude,
        "tick_timing": tick_timing,
    })


def build_condition_result(
    *,
    source: str,
    target_actor_id: str,
    effects_added: List[Dict[str, Any]] | None = None,
    effects_updated: List[Dict[str, Any]] | None = None,
    effects_removed: List[Dict[str, Any]] | None = None,
    tick_result: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    return {
        "applied": bool(effects_added or effects_updated or effects_removed or tick_result),
        "source": source,
        "target_actor_id": target_actor_id,
        "effects_added": effects_added or [],
        "effects_updated": effects_updated or [],
        "effects_removed": effects_removed or [],
        "tick_result": tick_result or {},
    }