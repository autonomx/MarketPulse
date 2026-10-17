from __future__ import annotations

from typing import Any, Dict, List

from app.apps.rpg.world.social.leverage import validate_leverage
from app.apps.rpg.world.social.reputation import (
    apply_global_reputation_delta,
    apply_social_deltas,
    get_global_reputation,
)
from app.apps.rpg.world.social.state import ensure_profile, ensure_relationship


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _approach_modifier(approach: str, profile: Dict[str, Any]) -> int:
    approach = str(approach or "polite")
    if approach == "polite":
        return 8 + int(profile.get("honor", 50) / 20)
    if approach == "logical":
        return 6 + int(profile.get("social_awareness", 50) / 25)
    if approach == "emotional":
        return 4
    if approach == "bribe":
        return 6 + int(profile.get("greed", 30) / 10)
    if approach == "deceptive":
        return -5 + int((100 - profile.get("honor", 50)) / 20)
    return 0


def _stance_for_persuasion(ok: bool, score: int, threshold: int, relationship: Dict[str, Any]) -> str:
    if ok and score >= threshold + 20:
        return "cooperative"
    if ok:
        return "cautious"
    if int(relationship.get("hostility") or 0) >= 40:
        return "hostile"
    if score < threshold - 25:
        return "dismissive"
    return "resistant"


def resolve_persuasion(
    simulation_state: Dict[str, Any],
    npc_id: str,
    *,
    actor_id: str = "player",
    request: str,
    difficulty: int = 50,
    approach: str = "polite",
    leverage_id: str | None = None,
    profile: Dict[str, Any] | None = None,
    current_turn: int = 0,
) -> Dict[str, Any]:
    relationship = ensure_relationship(simulation_state, npc_id)
    profile = dict(profile or ensure_profile(simulation_state, npc_id))
    global_reputation = get_global_reputation(simulation_state, actor_id)

    leverage_result = {"ok": False, "bonus": 0, "reason": "not_used"}
    if leverage_id:
        leverage_result = validate_leverage(
            simulation_state,
            npc_id,
            leverage_id,
            actor_id=actor_id,
            request=request,
            current_turn=current_turn,
        )

    score = int(
        50
        + _safe_int(relationship.get("trust")) * 0.4
        + _safe_int(relationship.get("reputation")) * 0.25
        + _safe_int(relationship.get("respect")) * 0.2
        + global_reputation * 0.15
        - _safe_int(relationship.get("hostility")) * 0.35
        - _safe_int(relationship.get("fear")) * 0.1
        + _approach_modifier(approach, profile)
        + _safe_int(leverage_result.get("bonus"))
        - _safe_int(difficulty)
    )

    threshold = 0
    ok = score >= threshold
    stance = _stance_for_persuasion(ok, score, threshold, relationship)

    if ok:
        reason = "trust_and_reputation_sufficient" if not leverage_result.get("ok") else "valid_leverage_helped"
        deltas = {
            "trust": 2 if approach != "deceptive" else -2,
            "respect": 1,
            "hostility": -1,
            "fear": 0,
            "reputation": 1,
            "last_stance": stance,
        }
    else:
        reason = "difficulty_too_high"
        deltas = {
            "trust": -1 if approach in {"deceptive", "bribe"} else 0,
            "respect": 0,
            "hostility": 2,
            "fear": 0,
            "reputation": 0,
            "last_stance": stance,
        }

    delta_result = apply_social_deltas(
        simulation_state,
        npc_id,
        deltas,
        actor_id=actor_id,
    )

    return {
        "ok": ok,
        "kind": "persuasion",
        "npc_id": npc_id,
        "actor_id": actor_id,
        "request": request,
        "difficulty": int(difficulty),
        "score": score,
        "threshold": threshold,
        "approach": approach,
        "stance": stance,
        "reason": reason,
        "deltas": deltas,
        "relationship": delta_result.get("relationship"),
        "leverage_result": leverage_result,
    }


def _stance_for_intimidation(ok: bool) -> str:
    return "fearful" if ok else "hostile"


def resolve_intimidation(
    simulation_state: Dict[str, Any],
    npc_id: str,
    *,
    actor_id: str = "player",
    threat: str,
    severity: int = 50,
    profile: Dict[str, Any] | None = None,
    leverage_id: str | None = None,
    witnesses: List[str] | None = None,
    current_turn: int = 0,
) -> Dict[str, Any]:
    relationship = ensure_relationship(simulation_state, npc_id)
    profile = dict(profile or ensure_profile(simulation_state, npc_id))
    global_reputation = get_global_reputation(simulation_state, actor_id)

    leverage_result = {"ok": False, "bonus": 0, "reason": "not_used"}
    if leverage_id:
        leverage_result = validate_leverage(
            simulation_state,
            npc_id,
            leverage_id,
            actor_id=actor_id,
            request=threat,
            current_turn=current_turn,
        )

    pressure = _safe_int(severity) + int(global_reputation * 0.15) + _safe_int(leverage_result.get("bonus"))
    resistance = int(
        profile.get("bravery", 50)
        + profile.get("stubbornness", 40) * 0.5
        + int(relationship.get("respect") or 0) * 0.2
        - int(relationship.get("fear") or 0) * 0.4
    )

    ok = pressure >= resistance
    stance = _stance_for_intimidation(ok)
    if ok:
        reason = "fear_overcame_resistance"
        deltas = {
            "fear": 15,
            "trust": -10,
            "hostility": 3,
            "respect": -2,
            "reputation": -1,
            "last_stance": stance,
        }
        escalation = False
    else:
        reason = "npc_resisted_threat"
        deltas = {
            "fear": 2,
            "trust": -8,
            "hostility": 15,
            "respect": -1,
            "reputation": -2,
            "last_stance": stance,
        }
        escalation = True

    delta_result = apply_social_deltas(
        simulation_state,
        npc_id,
        deltas,
        actor_id=actor_id,
    )

    witness_effects = []
    witnesses = list(witnesses or [])
    public_reputation_delta = -5 if witnesses else 0
    if public_reputation_delta:
        apply_global_reputation_delta(simulation_state, actor_id, public_reputation_delta)

    for witness_id in witnesses:
        witness_delta = {
            "trust": -3,
            "respect": -2,
            "hostility": 2,
            "fear": 1 if ok else 0,
            "reputation": -2,
        }
        witness_result = apply_social_deltas(
            simulation_state,
            witness_id,
            witness_delta,
            actor_id=actor_id,
        )
        witness_effects.append(
            {
                "witness_id": witness_id,
                "deltas": witness_delta,
                "relationship": witness_result.get("relationship"),
            }
        )

    return {
        "ok": ok,
        "kind": "intimidation",
        "npc_id": npc_id,
        "actor_id": actor_id,
        "threat": threat,
        "severity": int(severity),
        "pressure": pressure,
        "resistance": resistance,
        "stance": stance,
        "reason": reason,
        "deltas": deltas,
        "relationship": delta_result.get("relationship"),
        "public_reputation_delta": public_reputation_delta,
        "global_reputation": get_global_reputation(simulation_state, actor_id),
        "witness_effects": witness_effects,
        "leverage_result": leverage_result,
        "escalation": escalation,
    }