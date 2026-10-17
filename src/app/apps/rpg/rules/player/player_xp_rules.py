"""Phase 18.3A — XP computation rules.

Deterministic formulas for awarding XP from combat, quests, discoveries, diplomacy, and theft.
AI may assign difficulty_tier, quest_rank etc. but formulas are deterministic.
"""
from __future__ import annotations

from typing import Any, Dict
from app.apps.rpg.foundation.safe_values import dict_copy as _safe_dict, safe_str as _safe_str

_SKILL_XP_ACTION_ALLOWLIST = {
    "attack_melee",
    "attack_ranged",
    "attack",
    "block",
    "parry",
    "dodge",
    "steal",
    "sneak",
    "hack",
    "cast_spell",
    "persuade",
    "intimidate",
    "deceive",
    "investigate",
}

_PLAYER_XP_LOOT_VALUE_THRESHOLD = 25
_PLAYER_XP_GOLD_THRESHOLD = 25


def _is_positive_outcome(outcome: str) -> bool:
    return outcome in {"success", "critical_success", "hit", "crit"}


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


def compute_enemy_difficulty_xp(enemy_state: Dict[str, Any]) -> int:
    """Compute XP from defeating an enemy based on difficulty_tier."""
    enemy = dict(enemy_state or {})
    tier = _safe_int(enemy.get("difficulty_tier"), 1)
    return 20 + tier * 15


def compute_action_skill_xp(action_result: Dict[str, Any]) -> Dict[str, int]:
    """Compute skill XP from an action result. Returns {skill_id: amount}.

    Only award skill XP for meaningful skill-bearing actions.
    Generic low-stakes actions like casual observation should not always grant XP.
    """
    result = dict(action_result or {})
    action_type = _safe_str(result.get("action_type")).strip()
    skill_id = _safe_str(result.get("skill_id"))
    if not skill_id:
        return {}

    if action_type not in _SKILL_XP_ACTION_ALLOWLIST:
        return {}

    outcome = _safe_str(result.get("outcome"))
    difficulty = _safe_str(result.get("difficulty", "normal"))

    if not _is_positive_outcome(outcome):
        return {}

    # No XP for trivial/easy actions even if they technically touch a skill.
    if difficulty in {"trivial", "easy"}:
        return {}

    # Investigate only grants skill XP if it discovers something meaningful.
    if action_type == "investigate":
        discovered = bool(
            result.get("found_clue")
            or result.get("discovery")
            or result.get("revealed")
            or result.get("important_find")
        )
        if not discovered:
            return {}

    difficulty_bonus = {"trivial": 0, "easy": 0, "normal": 1, "hard": 2, "very_hard": 3, "legendary": 5}.get(difficulty, 1)
    success_bonus = {"success": 2, "critical_success": 4, "hit": 2, "crit": 4, "partial": 1, "graze": 1}.get(outcome, 0)

    amount = 2 + difficulty_bonus + success_bonus
    return {skill_id: amount}


def compute_action_player_xp(action_result: Dict[str, Any]) -> int:
    """Compute player XP from meaningful outcomes only.

    Supported cases:
    - defeating enemies (scaled by enemy difficulty)
    - stealing valuable loot / enough gold
    - important discoveries explicitly flagged by the resolver
    """
    result = _safe_dict(action_result)
    action_type = _safe_str(result.get("action_type")).strip()
    outcome = _safe_str(result.get("outcome")).strip()

    if not _is_positive_outcome(outcome):
        return 0

    xp = 0

    if bool(result.get("enemy_defeated")):
        enemy_state = _safe_dict(result.get("enemy_state"))
        if not enemy_state and result.get("enemy_difficulty_tier") is not None:
            enemy_state = {"difficulty_tier": _safe_int(result.get("enemy_difficulty_tier"), 1)}
        xp += compute_enemy_difficulty_xp(enemy_state)

    if action_type == "steal":
        loot_value = _safe_int(result.get("loot_value"), 0)
        gold_stolen = _safe_int(result.get("gold_stolen"), 0)
        total_value = max(loot_value, gold_stolen)
        if total_value >= max(_PLAYER_XP_LOOT_VALUE_THRESHOLD, _PLAYER_XP_GOLD_THRESHOLD):
            xp += max(10, min(60, total_value // 10))

    if bool(result.get("important_find")):
        difficulty = _safe_str(result.get("difficulty", "normal"))
        discovery_bonus = {"normal": 15, "hard": 25, "very_hard": 35, "legendary": 50}.get(difficulty, 15)
        xp += discovery_bonus

    return xp


def compute_stat_influence_bonus(player_state: Dict[str, Any], action_result: Dict[str, Any]) -> int:
    """Compute bonus XP based on stat influence on action."""
    player = dict(player_state or {})
    result = dict(action_result or {})
    stat_used = _safe_str(result.get("stat_used"))
    if not stat_used:
        return 0
    stats = dict(player.get("stats") or {})
    stat_val = _safe_int(stats.get(stat_used), 5)
    mod = max(0, (stat_val - 10) // 2)
    return mod
