"""Phase 18.3A — Canonical player progression state."""
from __future__ import annotations

from typing import Any, Dict

_MAX_PROGRESSION_LOG = 50


def _normalize_xp_meter(player_state: Dict[str, Any]) -> None:
    xp = player_state.get("xp")
    if not isinstance(xp, dict):
        return

    current = xp.get("current", 0)
    maximum = xp.get("max", 100)
    try:
        player_state["xp"] = max(0, int(current))
    except (TypeError, ValueError):
        player_state["xp"] = 0
    if "xp_to_next" not in player_state:
        try:
            player_state["xp_to_next"] = max(1, int(maximum))
        except (TypeError, ValueError):
            player_state["xp_to_next"] = 100


def ensure_player_progression_state(player_state: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure player_state has canonical progression fields. Idempotent."""
    player_state = dict(player_state or {})
    _normalize_xp_meter(player_state)
    player_state.setdefault("name", "Player")
    player_state.setdefault("class_id", "")
    player_state.setdefault("background_id", "")
    player_state.setdefault("species_id", "")
    player_state.setdefault("level", 1)
    player_state.setdefault("xp", 0)
    player_state.setdefault("xp_to_next", 100)
    player_state.setdefault("unspent_points", 0)
    player_state.setdefault("unspent_skill_points", 0)
    player_state.setdefault("combat_rating", 0)
    player_state.setdefault("stats", {
        "strength": 5, "dexterity": 5, "constitution": 5,
        "intelligence": 5, "wisdom": 5, "charisma": 5,
    })
    player_state.setdefault("skills", {
        "swordsmanship": {"level": 0, "xp": 0, "xp_to_next": 25},
        "archery": {"level": 0, "xp": 0, "xp_to_next": 25},
        "firearms": {"level": 0, "xp": 0, "xp_to_next": 25},
        "defense": {"level": 0, "xp": 0, "xp_to_next": 25},
        "stealth": {"level": 0, "xp": 0, "xp_to_next": 25},
        "persuasion": {"level": 0, "xp": 0, "xp_to_next": 25},
        "intimidation": {"level": 0, "xp": 0, "xp_to_next": 25},
        "investigation": {"level": 0, "xp": 0, "xp_to_next": 25},
        "magic": {"level": 0, "xp": 0, "xp_to_next": 25},
        "hacking": {"level": 0, "xp": 0, "xp_to_next": 25},
    })
    player_state.setdefault("perk_flags", [])
    player_state.setdefault("progression_log", [])
    # Trim progression_log
    if len(player_state["progression_log"]) > _MAX_PROGRESSION_LOG:
        player_state["progression_log"] = player_state["progression_log"][-_MAX_PROGRESSION_LOG:]
    return player_state

def allocate_starting_stats(player_state: Dict[str, Any], allocation: Dict[str, int]) -> Dict[str, Any]:
    """Apply stat allocation from character creation. allocation is like {"strength": 2, "dexterity": 1}."""
    player_state = ensure_player_progression_state(player_state)
    stats = dict(player_state["stats"])
    for stat_name, points in (allocation or {}).items():
        stat_name = str(stat_name)
        if stat_name in stats:
            stats[stat_name] = max(1, min(20, stats[stat_name] + int(points)))
    player_state["stats"] = stats
    return player_state

def get_stat_modifier(value: int) -> int:
    """Return modifier for a stat value. (value - 10) // 2, like D&D."""
    return (int(value) - 10) // 2

def get_skill_level(player_state: Dict[str, Any], skill_id: str) -> int:
    """Return skill level for a given skill_id, 0 if not found."""
    player_state = ensure_player_progression_state(player_state)
    skill = player_state.get("skills", {}).get(str(skill_id), {})
    if isinstance(skill, dict):
        return int(skill.get("level", 0))
    return 0

def award_player_xp(player_state: Dict[str, Any], amount: int, source: str = "") -> Dict[str, Any]:
    """Award XP to the player. Returns updated player_state with xp_awarded info."""
    player_state = ensure_player_progression_state(player_state)
    amount = max(0, int(amount))
    player_state["xp"] = player_state.get("xp", 0) + amount
    log = list(player_state.get("progression_log", []))
    log.append({"type": "xp_award", "amount": amount, "source": str(source)})
    player_state["progression_log"] = log[-_MAX_PROGRESSION_LOG:]
    return player_state

def award_skill_xp(player_state: Dict[str, Any], skill_id: str, amount: int, source: str = "") -> Dict[str, Any]:
    """Award XP to a specific skill."""
    player_state = ensure_player_progression_state(player_state)
    skill_id = str(skill_id)
    amount = max(0, int(amount))
    skills = dict(player_state.get("skills", {}))
    if skill_id not in skills:
        skills[skill_id] = {"level": 0, "xp": 0, "xp_to_next": 25}
    skill = dict(skills[skill_id])
    skill["xp"] = skill.get("xp", 0) + amount
    skills[skill_id] = skill
    player_state["skills"] = skills
    log = list(player_state.get("progression_log", []))
    log.append({"type": "skill_xp_award", "skill_id": skill_id, "amount": amount, "source": str(source)})
    player_state["progression_log"] = log[-_MAX_PROGRESSION_LOG:]
    return player_state

def resolve_level_ups(player_state: Dict[str, Any]) -> Dict[str, Any]:
    """Check and apply level-ups. Deterministic: xp >= xp_to_next triggers level-up."""
    player_state = ensure_player_progression_state(player_state)
    level_ups = []
    while player_state["xp"] >= player_state["xp_to_next"]:
        player_state["xp"] -= player_state["xp_to_next"]
        player_state["level"] += 1
        player_state["unspent_points"] += 2
        player_state["unspent_skill_points"] += 1
        # Scaling XP requirement: level * 100
        player_state["xp_to_next"] = player_state["level"] * 100
        level_ups.append({"level": player_state["level"]})
    if level_ups:
        log = list(player_state.get("progression_log", []))
        for lu in level_ups:
            log.append({"type": "level_up", "new_level": lu["level"]})
        player_state["progression_log"] = log[-_MAX_PROGRESSION_LOG:]
    player_state["_level_ups"] = level_ups
    return player_state

def resolve_skill_level_ups(player_state: Dict[str, Any]) -> Dict[str, Any]:
    """Check and apply skill level-ups for all skills."""
    player_state = ensure_player_progression_state(player_state)
    skill_level_ups = []
    skills = dict(player_state.get("skills", {}))
    for skill_id, skill_data in skills.items():
        skill = dict(skill_data) if isinstance(skill_data, dict) else {"level": 0, "xp": 0, "xp_to_next": 25}
        while skill.get("xp", 0) >= skill.get("xp_to_next", 25):
            skill["xp"] -= skill["xp_to_next"]
            skill["level"] = skill.get("level", 0) + 1
            # Scaling: (level+1) * 25
            skill["xp_to_next"] = (skill["level"] + 1) * 25
            skill_level_ups.append({"skill_id": skill_id, "new_level": skill["level"]})
        skills[skill_id] = skill
    player_state["skills"] = skills
    if skill_level_ups:
        log = list(player_state.get("progression_log", []))
        for slu in skill_level_ups:
            log.append({"type": "skill_level_up", "skill_id": slu["skill_id"], "new_level": slu["new_level"]})
        player_state["progression_log"] = log[-_MAX_PROGRESSION_LOG:]
    player_state["_skill_level_ups"] = skill_level_ups
    return player_state
