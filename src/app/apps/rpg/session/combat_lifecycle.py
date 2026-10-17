from __future__ import annotations

import hashlib
from typing import Any, Dict, List

PLAYER_COMBAT_HP_BASELINE = 10
ENEMY_DAMAGE_BASELINE = 1
ENEMY_HP_BASELINE_FOR_FAST_COMBAT = 4
BANDIT_DEFEAT_XP_REWARD = 25
BANDIT_DEFEAT_COPPER_REWARD = 7


def _d(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _l(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _s(value: Any) -> str:
    return "" if value is None else str(value)


def _i(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except Exception:
            return default
    return default


def _stable_id(*parts: Any) -> str:
    raw = ":".join(_s(part) for part in parts)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"combat:{digest}"


def _combat_payload(result: Dict[str, Any]) -> Dict[str, Any]:
    result = _d(result)
    nested = _d(result.get("result"))
    for payload in (
        _d(result.get("combat_narration_payload")),
        _d(nested.get("combat_narration_payload")),
        _d(result.get("narration_payload")),
        _d(nested.get("narration_payload")),
        _d(result.get("structured_narration")),
        _d(nested.get("structured_narration")),
    ):
        if _s(payload.get("source")) == "deterministic_combat_fast_summary" and _d(payload.get("combat_delta")):
            return payload
    return {}


def _combat_delta(result: Dict[str, Any]) -> Dict[str, Any]:
    payload = _combat_payload(result)
    delta = _d(payload.get("combat_delta")) or _d(payload.get("combat_delta_contract"))
    if delta:
        return delta
    result = _d(result)
    nested = _d(result.get("result"))
    return _d(result.get("combat_delta_contract")) or _d(nested.get("combat_delta_contract"))


def player_hp_before_for_enemy_turn(*, turn_index: int, enemy_hp_before: Any = None) -> int:
    """Return deterministic authoritative combat HP before an enemy turn."""

    enemy_hp = _i(enemy_hp_before, -1)
    if enemy_hp >= 0:
        prior_enemy_turns = max(0, ENEMY_HP_BASELINE_FOR_FAST_COMBAT - enemy_hp)
    else:
        prior_enemy_turns = max(0, _i(turn_index, 0) - 2)
    return max(1, PLAYER_COMBAT_HP_BASELINE - prior_enemy_turns * ENEMY_DAMAGE_BASELINE)


def build_enemy_damage_contract(
    *,
    player_hp_before: int = PLAYER_COMBAT_HP_BASELINE,
    damage_applied: int = ENEMY_DAMAGE_BASELINE,
) -> Dict[str, Any]:
    """Build the PR.1.6 enemy damage contract."""

    player_hp_before = max(1, _i(player_hp_before, PLAYER_COMBAT_HP_BASELINE))
    damage_applied = max(0, _i(damage_applied, ENEMY_DAMAGE_BASELINE))
    damage_applied = min(damage_applied, max(0, player_hp_before - 1))
    player_hp_after = max(1, player_hp_before - damage_applied)
    return {
        "schema": "enemy_damage_contract_v1",
        "source": "pr1_6_authoritative_player_combat_hp",
        "metadata_only": False,
        "player_state_mutated": True,
        "damage_applied": damage_applied,
        "player_hp_before": player_hp_before,
        "player_hp_after": player_hp_after,
        "player_damage_pending": False,
        "player_hp_delta": player_hp_after - player_hp_before,
        "nonlethal_guard": True,
        "authoritative_player_combat_hp": True,
        "survival_state_mutated": False,
    }


def build_combat_reward_result(*, target_id: str, target_name: str, turn_index: int) -> Dict[str, Any]:
    """Build deterministic PR.1.8 reward resolution for combat completion."""

    return {
        "schema": "combat_reward_result_v1",
        "source": "deterministic_combat_reward_v1",
        "target_id": target_id,
        "target_name": target_name,
        "turn_index": turn_index,
        "resolved": True,
        "xp_awarded": BANDIT_DEFEAT_XP_REWARD,
        "loot_awarded": {
            "currency": {"copper": BANDIT_DEFEAT_COPPER_REWARD},
            "items": [],
        },
        "reward_lines": [
            f"Gained {BANDIT_DEFEAT_XP_REWARD} XP.",
            f"Looted {BANDIT_DEFEAT_COPPER_REWARD} copper.",
        ],
        "player_state_mutated": False,
        "inventory_state_mutated": False,
        "promotion_pending": True,
        "reason": "deterministic_bandit_defeat_reward_recorded_for_pr1_8",
    }


def build_enemy_turn_resolution(lifecycle: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve the first enemy turn as deterministic combat HP damage."""

    lifecycle = _d(lifecycle)
    initiative = _d(lifecycle.get("initiative"))
    enemy_turn = _d(lifecycle.get("enemy_turn"))
    if enemy_turn.get("pending") is not True:
        return {}
    actor_id = _s(enemy_turn.get("actor_id") or initiative.get("next_actor_id"))
    if not actor_id:
        return {}
    round_index = _i(initiative.get("round_index"), 1)
    player_rows = [row for row in _l(lifecycle.get("combat_log")) if _d(row).get("phase") == "player_action"]
    enemy_hp_before = _d(player_rows[0]).get("target_hp_before") if player_rows else None
    damage_contract = build_enemy_damage_contract(
        player_hp_before=player_hp_before_for_enemy_turn(
            turn_index=round_index,
            enemy_hp_before=enemy_hp_before,
        ),
    )
    entry = {
        "schema": "combat_log_entry_v1",
        "entry_id": _stable_id(round_index, actor_id, "player", "enemy_damage_contract_v1"),
        "turn_index": round_index,
        "round_index": round_index,
        "phase": "enemy_action",
        "actor_id": actor_id,
        "actor_side": "enemy",
        "target_id": "player",
        "target_name": "player",
        "target_side": "player",
        "action_type": "counterattack",
        "hit": damage_contract["damage_applied"] > 0,
        "damage_applied": damage_contract["damage_applied"],
        "target_hp_before": damage_contract["player_hp_before"],
        "target_hp_after": damage_contract["player_hp_after"],
        "player_hp_before": damage_contract["player_hp_before"],
        "player_hp_after": damage_contract["player_hp_after"],
        "player_damage_pending": damage_contract["player_damage_pending"],
        "player_hp_delta": damage_contract["player_hp_delta"],
        "player_state_mutated": True,
        "authoritative_player_combat_hp": True,
        "survival_state_mutated": False,
        "defeated": False,
        "combat_ended": False,
        "source": "deterministic_enemy_damage_contract_v1",
        "enemy_damage_contract": damage_contract,
    }
    return {
        "schema": "enemy_turn_resolution_v1",
        "source": "pr1_6_authoritative_player_combat_hp",
        "resolved": True,
        "pending": False,
        "actor_id": actor_id,
        "target_id": "player",
        "action_type": "counterattack",
        "damage_applied": damage_contract["damage_applied"],
        "player_damage_pending": damage_contract["player_damage_pending"],
        "player_hp_before": damage_contract["player_hp_before"],
        "player_hp_after": damage_contract["player_hp_after"],
        "player_hp_delta": damage_contract["player_hp_delta"],
        "player_state_mutated": True,
        "authoritative_player_combat_hp": True,
        "survival_state_mutated": False,
        "reason": "enemy_damage_applied_to_authoritative_combat_hp_in_pr1_6",
        "enemy_damage_contract": damage_contract,
        "combat_log_entry": entry,
    }


def resolve_enemy_turn_in_lifecycle(lifecycle: Dict[str, Any]) -> Dict[str, Any]:
    lifecycle = _d(lifecycle)
    resolution = build_enemy_turn_resolution(lifecycle)
    if not resolution:
        return lifecycle
    log = [_d(row) for row in _l(lifecycle.get("combat_log"))]
    log.append(_d(resolution.get("combat_log_entry")))
    enemy_turn = _d(lifecycle.get("enemy_turn"))
    enemy_turn.update(
        {
            "pending": False,
            "resolved": True,
            "resolution": {
                key: value
                for key, value in resolution.items()
                if key != "combat_log_entry"
            },
            "reason": resolution.get("reason"),
        }
    )
    initiative = _d(lifecycle.get("initiative"))
    initiative["turn_phase"] = "player_turn_ready"
    initiative["active_actor_id"] = "player"
    initiative["next_actor_id"] = "player"
    lifecycle["initiative"] = initiative
    lifecycle["enemy_turn"] = enemy_turn
    lifecycle["combat_log"] = log
    lifecycle["enemy_turn_resolution"] = resolution
    lifecycle["player_combat_hp"] = {
        "schema": "player_combat_hp_v1",
        "source": "pr1_6_authoritative_player_combat_hp",
        "before": resolution["player_hp_before"],
        "after": resolution["player_hp_after"],
        "delta": resolution["player_hp_delta"],
        "authoritative": True,
        "survival_state_mutated": False,
    }
    return lifecycle


def build_combat_lifecycle_snapshot(result: Dict[str, Any]) -> Dict[str, Any]:
    """Build deterministic lifecycle metadata from an already-resolved combat delta."""

    result = _d(result)
    nested = _d(result.get("result"))
    delta = _combat_delta(result)
    if not delta:
        return {}

    target_id = _s(delta.get("target_id") or nested.get("target_id") or "enemy:road_bandit")
    target_name = _s(delta.get("target_name") or nested.get("target_name") or "bandit") or "enemy"
    actor_id = _s(delta.get("actor_id") or "player") or "player"
    damage = _i(delta.get("damage_applied"), 0)
    hp_before = delta.get("target_hp_before")
    hp_after = delta.get("target_hp_after")
    defeated = bool(delta.get("defeated") or delta.get("combat_ended"))
    combat_ended = bool(delta.get("combat_ended") or defeated)
    turn_index = _i(result.get("tick") or nested.get("tick") or result.get("turn_index") or nested.get("turn_index"), 0)

    entry = {
        "schema": "combat_log_entry_v1",
        "entry_id": _stable_id(turn_index, actor_id, target_id, damage, hp_before, hp_after, defeated),
        "turn_index": turn_index,
        "round_index": max(1, turn_index),
        "phase": "player_action",
        "actor_id": actor_id,
        "actor_side": "player",
        "target_id": target_id,
        "target_name": target_name,
        "target_side": "enemy",
        "action_type": _s(delta.get("action_type") or "attack") or "attack",
        "hit": damage > 0 or defeated,
        "damage_applied": damage,
        "target_hp_before": hp_before,
        "target_hp_after": hp_after,
        "defeated": defeated,
        "combat_ended": combat_ended,
        "source": "deterministic_combat_delta_contract_v1",
    }

    next_actor_id = "" if combat_ended else target_id
    progression_hooks = {
        "schema": "combat_progression_hooks_v1",
        "xp_pending": defeated,
        "loot_pending": defeated,
        "resolved": False,
        "reason": "placeholder_for_phase1_xp_loot_resolution",
    }
    lifecycle = {
        "schema": "combat_lifecycle_v1",
        "source": "pr1_combat_lifecycle_foundation",
        "initiative": {
            "schema": "combat_initiative_v1",
            "order": [actor_id, target_id],
            "active_actor_id": actor_id,
            "next_actor_id": next_actor_id,
            "round_index": max(1, turn_index),
            "turn_phase": "combat_complete" if combat_ended else "awaiting_enemy_turn",
        },
        "enemy_turn": {
            "schema": "enemy_turn_skeleton_v1",
            "pending": not combat_ended,
            "actor_id": next_actor_id,
            "reason": "enemy_turn_not_yet_resolved_in_pr1_foundation" if not combat_ended else "combat_ended",
        },
        "combat_log": [entry],
        "progression_hooks": progression_hooks,
    }
    if combat_ended:
        reward = build_combat_reward_result(target_id=target_id, target_name=target_name, turn_index=max(1, turn_index))
        progression_hooks.update(
            {
                "xp_pending": False,
                "loot_pending": False,
                "resolved": True,
                "source": "deterministic_combat_reward_v1",
                "xp_awarded": reward["xp_awarded"],
                "loot_awarded": reward["loot_awarded"],
                "reward_result": reward,
                "reason": "combat_rewards_resolved_in_pr1_8",
            }
        )
        lifecycle["combat_reward_result"] = reward
    else:
        lifecycle = resolve_enemy_turn_in_lifecycle(lifecycle)
    return lifecycle


def enrich_combat_lifecycle_result(result: Dict[str, Any]) -> Dict[str, Any]:
    result = _d(result)
    lifecycle = build_combat_lifecycle_snapshot(result)
    if not lifecycle:
        return result
    result["combat_lifecycle"] = lifecycle
    result["combat_log"] = lifecycle.get("combat_log", [])
    if "result" in result:
        nested = _d(result.get("result"))
        nested["combat_lifecycle"] = lifecycle
        nested["combat_log"] = lifecycle.get("combat_log", [])
        result["result"] = nested
    for key in ("narration_payload", "structured_narration", "combat_narration_payload"):
        payload = _d(result.get(key))
        if payload:
            payload["combat_lifecycle"] = lifecycle
            payload["combat_log"] = lifecycle.get("combat_log", [])
            result[key] = payload
    return result
