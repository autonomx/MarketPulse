from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict

from app.apps.rpg.rules.party.companion_presence import active_party_companions
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

MAX_COMPANION_QUEST_EVENTS = 32


BRAN_REVENGE_QUEST_ID = "companion_bran_revenge"

BRAN_REVENGE_STAGES = [
    "find_bandit_leads",
    "follow_bandit_lead",
    "track_bandits",
    "confront_bandit_scout",
    "find_bandit_leader",
    "resolve_revenge",
]


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def ensure_companion_quest_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    state = _safe_dict(simulation_state.get("companion_quest_state"))
    if not state:
        state = {}
        simulation_state["companion_quest_state"] = state

    if not isinstance(state.get("by_quest"), dict):
        state["by_quest"] = {}

    if not isinstance(state.get("by_npc"), dict):
        state["by_npc"] = {}

    if not isinstance(state.get("events"), list):
        state["events"] = []

    if not isinstance(state.get("debug"), dict):
        state["debug"] = {}

    return state


def _party_companion(simulation_state: Dict[str, Any], npc_id: str) -> Dict[str, Any]:
    for companion in active_party_companions(simulation_state):
        companion = _safe_dict(companion)
        if _safe_str(companion.get("npc_id")) == npc_id:
            return companion
    return {}


def _npc_evolution(simulation_state: Dict[str, Any], npc_id: str) -> Dict[str, Any]:
    return _safe_dict(
        _safe_dict(_safe_dict(simulation_state.get("npc_evolution_state")).get("by_npc")).get(npc_id)
    )


def _quest_event(
    *,
    quest_id: str,
    npc_id: str,
    kind: str,
    reason: str,
    tick: int,
    before_stage: str = "",
    after_stage: str = "",
    metadata: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    return {
        "quest_id": _safe_str(quest_id),
        "npc_id": _safe_str(npc_id),
        "kind": _safe_str(kind),
        "reason": _safe_str(reason),
        "tick": int(tick or 0),
        "before_stage": _safe_str(before_stage),
        "after_stage": _safe_str(after_stage),
        "metadata": deepcopy(_safe_dict(metadata or {})),
        "source": "deterministic_companion_quest_runtime",
    }


def _append_quest_event(state: Dict[str, Any], event: Dict[str, Any]) -> None:
    events = _safe_list(state.get("events"))
    events.append(deepcopy(event))
    state["events"] = events[-MAX_COMPANION_QUEST_EVENTS:]
    state["debug"] = {
        "last_event": deepcopy(event),
        "source": "deterministic_companion_quest_runtime",
    }


def _stage_index(stage: str) -> int:
    try:
        return BRAN_REVENGE_STAGES.index(_safe_str(stage))
    except ValueError:
        return -1


def _bran_revenge_role_for_stage(stage: str) -> str:
    stage = _safe_str(stage)

    if stage == "find_bandit_leads":
        return "Displaced tavern keeper seeking bandit leads"
    if stage == "follow_bandit_lead":
        return "Vengeful companion following a bandit lead"
    if stage == "track_bandits":
        return "Vengeful companion tracking bandits"
    if stage == "confront_bandit_scout":
        return "Hardened companion confronting the bandits"
    if stage == "find_bandit_leader":
        return "Avenger closing in on the bandit leader"
    if stage == "resolve_revenge":
        return "Changed survivor of the Rusty Flagon"
    return "Displaced tavern keeper"


def seed_companion_quest_from_arc(
    simulation_state: Dict[str, Any],
    *,
    npc_id: str,
    tick: int = 0,
) -> Dict[str, Any]:
    """Seed a personal companion quest from a known identity arc.

    This is deterministic and idempotent. It does not create arbitrary quests.
    """
    npc_id = _safe_str(npc_id)
    if not npc_id:
        return {
            "seeded": False,
            "reason": "missing_npc_id",
            "source": "deterministic_companion_quest_runtime",
        }

    companion = _party_companion(simulation_state, npc_id)
    evolution = _npc_evolution(simulation_state, npc_id)

    identity_arc = (
        _safe_str(companion.get("identity_arc"))
        or _safe_str(evolution.get("identity_arc"))
    )

    if identity_arc != "revenge_after_losing_tavern":
        return {
            "seeded": False,
            "npc_id": npc_id,
            "reason": "no_supported_companion_arc",
            "identity_arc": identity_arc,
            "source": "deterministic_companion_quest_runtime",
        }

    state = ensure_companion_quest_state(simulation_state)
    by_quest = _safe_dict(state.get("by_quest"))
    by_npc = _safe_dict(state.get("by_npc"))

    quest_id = BRAN_REVENGE_QUEST_ID

    existing = _safe_dict(by_quest.get(quest_id))
    if existing:
        return {
            "seeded": False,
            "already_exists": True,
            "quest": deepcopy(existing),
            "source": "deterministic_companion_quest_runtime",
        }

    quest = {
        "quest_id": quest_id,
        "npc_id": npc_id,
        "title": "Bran's Revenge",
        "source_arc": identity_arc,
        "status": "active",
        "stage": "find_bandit_leads",
        "stage_index": _stage_index("find_bandit_leads"),
        "summary": "Bran wants to find the bandits who destroyed the Rusty Flagon.",
        "created_tick": int(tick or 0),
        "updated_tick": int(tick or 0),
        "source": "deterministic_companion_quest_runtime",
    }

    by_quest[quest_id] = quest
    npc_quests = _safe_list(by_npc.get(npc_id))
    if quest_id not in npc_quests:
        npc_quests.append(quest_id)
    by_npc[npc_id] = npc_quests

    state["by_quest"] = by_quest
    state["by_npc"] = by_npc

    event = _quest_event(
        quest_id=quest_id,
        npc_id=npc_id,
        kind="companion_quest_seeded",
        reason="identity_arc_revenge_after_losing_tavern",
        tick=tick,
        after_stage="find_bandit_leads",
    )
    _append_quest_event(state, event)

    apply_companion_quest_projection_to_npc(
        simulation_state,
        quest_id=quest_id,
        tick=tick,
    )

    return {
        "seeded": True,
        "quest": deepcopy(quest),
        "event": deepcopy(event),
        "source": "deterministic_companion_quest_runtime",
    }


def seed_companion_quests_for_active_companions(
    simulation_state: Dict[str, Any],
    *,
    tick: int = 0,
) -> Dict[str, Any]:
    results = []
    for companion in active_party_companions(simulation_state):
        npc_id = _safe_str(_safe_dict(companion).get("npc_id"))
        if npc_id:
            results.append(seed_companion_quest_from_arc(simulation_state, npc_id=npc_id, tick=tick))

    return {
        "seeded_any": any(_safe_dict(result).get("seeded") for result in results),
        "results": results,
        "source": "deterministic_companion_quest_runtime",
    }


def classify_companion_quest_progress_from_player_input(
    simulation_state: Dict[str, Any],
    *,
    player_input: str,
) -> Dict[str, Any]:
    text = _safe_str(player_input).lower()

    # V1 is explicit and bounded. It only progresses Bran's known revenge arc.
    mentions_bran = "bran" in text or "npc:bran" in text
    mentions_bandits = "bandit" in text or "bandits" in text
    if not mentions_bran or not mentions_bandits:
        return {
            "matched": False,
            "reason": "no_backed_companion_quest_progress_signal",
            "source": "deterministic_companion_quest_runtime",
        }

    if any(marker in text for marker in ["ask around", "rumor", "rumors", "question the locals", "look for leads", "find leads"]):
        return {
            "matched": True,
            "quest_id": BRAN_REVENGE_QUEST_ID,
            "npc_id": "npc:Bran",
            "target_stage": "follow_bandit_lead",
            "reason": "player_sought_bandit_leads",
            "source": "deterministic_companion_quest_runtime",
        }

    if any(marker in text for marker in ["follow the tracks", "follow tracks", "track the bandits", "trail", "into the woods"]):
        return {
            "matched": True,
            "quest_id": BRAN_REVENGE_QUEST_ID,
            "npc_id": "npc:Bran",
            "target_stage": "track_bandits",
            "reason": "player_followed_bandit_lead",
            "source": "deterministic_companion_quest_runtime",
        }

    if any(marker in text for marker in ["bandit scout", "confront the scout", "capture the scout"]):
        return {
            "matched": True,
            "quest_id": BRAN_REVENGE_QUEST_ID,
            "npc_id": "npc:Bran",
            "target_stage": "confront_bandit_scout",
            "reason": "player_confronted_bandit_scout",
            "source": "deterministic_companion_quest_runtime",
        }

    return {
        "matched": False,
        "reason": "bandit_reference_not_progress_action",
        "source": "deterministic_companion_quest_runtime",
    }


def progress_companion_quest(
    simulation_state: Dict[str, Any],
    *,
    quest_id: str,
    target_stage: str,
    reason: str,
    tick: int = 0,
) -> Dict[str, Any]:
    state = ensure_companion_quest_state(simulation_state)
    by_quest = _safe_dict(state.get("by_quest"))
    quest = _safe_dict(by_quest.get(_safe_str(quest_id)))

    if not quest:
        return {
            "progressed": False,
            "reason": "quest_not_found",
            "quest_id": _safe_str(quest_id),
            "source": "deterministic_companion_quest_runtime",
        }

    if _safe_str(quest.get("status")) != "active":
        return {
            "progressed": False,
            "reason": "quest_not_active",
            "quest": deepcopy(quest),
            "source": "deterministic_companion_quest_runtime",
        }

    before_stage = _safe_str(quest.get("stage"))
    before_index = _stage_index(before_stage)
    target_stage = _safe_str(target_stage)
    target_index = _stage_index(target_stage)

    if target_index < 0:
        return {
            "progressed": False,
            "reason": "unsupported_target_stage",
            "target_stage": target_stage,
            "quest": deepcopy(quest),
            "source": "deterministic_companion_quest_runtime",
        }

    if target_index <= before_index:
        return {
            "progressed": False,
            "reason": "quest_stage_not_advanced",
            "before_stage": before_stage,
            "target_stage": target_stage,
            "quest": deepcopy(quest),
            "source": "deterministic_companion_quest_runtime",
        }

    # Prevent skipping too far in v1.
    if target_index > before_index + 1:
        return {
            "progressed": False,
            "reason": "quest_stage_skip_blocked",
            "before_stage": before_stage,
            "target_stage": target_stage,
            "quest": deepcopy(quest),
            "source": "deterministic_companion_quest_runtime",
        }

    quest["stage"] = target_stage
    quest["stage_index"] = target_index
    quest["updated_tick"] = int(tick or 0)
    quest["current_role_projection"] = _bran_revenge_role_for_stage(target_stage)

    by_quest[_safe_str(quest_id)] = quest
    state["by_quest"] = by_quest

    event = _quest_event(
        quest_id=_safe_str(quest_id),
        npc_id=_safe_str(quest.get("npc_id")),
        kind="companion_quest_progressed",
        reason=reason,
        tick=tick,
        before_stage=before_stage,
        after_stage=target_stage,
    )
    _append_quest_event(state, event)

    projection = apply_companion_quest_projection_to_npc(
        simulation_state,
        quest_id=_safe_str(quest_id),
        tick=tick,
    )

    return {
        "progressed": True,
        "quest": deepcopy(quest),
        "event": deepcopy(event),
        "projection": deepcopy(projection),
        "source": "deterministic_companion_quest_runtime",
    }


def maybe_progress_companion_quest_from_player_input(
    simulation_state: Dict[str, Any],
    *,
    player_input: str,
    tick: int = 0,
) -> Dict[str, Any]:
    seed_result = seed_companion_quests_for_active_companions(
        simulation_state,
        tick=tick,
    )

    classification = classify_companion_quest_progress_from_player_input(
        simulation_state,
        player_input=player_input,
    )

    if not classification.get("matched"):
        return {
            "progressed": False,
            "seed_result": deepcopy(seed_result),
            "classification": deepcopy(classification),
            "reason": _safe_str(classification.get("reason")),
            "source": "deterministic_companion_quest_runtime",
        }

    progress = progress_companion_quest(
        simulation_state,
        quest_id=_safe_str(classification.get("quest_id")),
        target_stage=_safe_str(classification.get("target_stage")),
        reason=_safe_str(classification.get("reason")),
        tick=tick,
    )

    return {
        "progressed": bool(progress.get("progressed")),
        "seed_result": deepcopy(seed_result),
        "classification": deepcopy(classification),
        "progress": deepcopy(progress),
        "source": "deterministic_companion_quest_runtime",
    }


def apply_companion_quest_projection_to_npc(
    simulation_state: Dict[str, Any],
    *,
    quest_id: str,
    tick: int = 0,
) -> Dict[str, Any]:
    state = ensure_companion_quest_state(simulation_state)
    quest = _safe_dict(_safe_dict(state.get("by_quest")).get(_safe_str(quest_id)))

    if not quest:
        return {
            "projected": False,
            "reason": "quest_not_found",
            "quest_id": _safe_str(quest_id),
            "source": "deterministic_companion_quest_runtime",
        }

    npc_id = _safe_str(quest.get("npc_id"))
    stage = _safe_str(quest.get("stage"))
    role_projection = _bran_revenge_role_for_stage(stage)

    projected_companion = {}

    player_state = _safe_dict(simulation_state.get("player_state"))
    party_state = _safe_dict(player_state.get("party_state"))
    companions = _safe_list(party_state.get("companions"))
    for index, companion in enumerate(companions):
        companion = deepcopy(_safe_dict(companion))
        if _safe_str(companion.get("npc_id")) != npc_id:
            continue

        companion["arc_stage"] = stage
        companion["companion_quest_id"] = _safe_str(quest_id)
        companion["current_role"] = role_projection
        companion["last_arc_projection_tick"] = int(tick or 0)
        companions[index] = companion
        projected_companion = deepcopy(companion)
        break

    party_state["companions"] = companions
    player_state["party_state"] = party_state
    simulation_state["player_state"] = player_state

    evolution_state = _safe_dict(simulation_state.get("npc_evolution_state"))
    by_npc = _safe_dict(evolution_state.get("by_npc"))
    npc_evo = _safe_dict(by_npc.get(npc_id))
    if npc_evo:
        npc_evo["arc_stage"] = stage
        npc_evo["companion_quest_id"] = _safe_str(quest_id)
        npc_evo["current_role"] = role_projection
        npc_evo["last_arc_projection_tick"] = int(tick or 0)
        by_npc[npc_id] = npc_evo
        evolution_state["by_npc"] = by_npc
        simulation_state["npc_evolution_state"] = evolution_state

    return {
        "projected": True,
        "npc_id": npc_id,
        "quest_id": _safe_str(quest_id),
        "stage": stage,
        "current_role": role_projection,
        "companion": deepcopy(projected_companion),
        "source": "deterministic_companion_quest_runtime",
    }


def companion_quest_summary(simulation_state: Dict[str, Any], *, npc_id: str = "") -> Dict[str, Any]:
    state = ensure_companion_quest_state(simulation_state)
    by_quest = _safe_dict(state.get("by_quest"))
    by_npc = _safe_dict(state.get("by_npc"))

    if npc_id:
        quest_ids = _safe_list(by_npc.get(_safe_str(npc_id)))
        return {
            "npc_id": _safe_str(npc_id),
            "quests": [deepcopy(_safe_dict(by_quest.get(quest_id))) for quest_id in quest_ids],
            "events": [
                deepcopy(_safe_dict(event))
                for event in _safe_list(state.get("events"))
                if _safe_str(_safe_dict(event).get("npc_id")) == _safe_str(npc_id)
            ],
            "source": "deterministic_companion_quest_runtime",
        }

    return {
        "by_quest": deepcopy(by_quest),
        "by_npc": deepcopy(by_npc),
        "events": deepcopy(_safe_list(state.get("events"))),
        "source": "deterministic_companion_quest_runtime",
    }
