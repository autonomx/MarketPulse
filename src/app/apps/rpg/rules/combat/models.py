from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass(frozen=True)
class AttackIntent:
    actor_id: str
    target_id: str
    action_type: str = "melee_attack"   # melee_attack | ranged_attack | unarmed_attack
    skill_id: str = ""
    weapon_id: str = ""
    tags: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class CombatRoll:
    roll_type: str
    sides: int
    result: int
    seed_key: str


@dataclass(frozen=True)
class AttackResolution:
    combat_id: str
    actor_id: str
    target_id: str
    target_name: str
    action_type: str
    hit: bool
    crit: bool
    attack_total: int
    defense_total: int
    damage_total: int
    damage_type: str
    target_hp_before: int
    target_hp_after: int
    target_downed: bool
    rolls: List[Dict[str, Any]]
    notes: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "combat_id": self.combat_id,
            "actor_id": self.actor_id,
            "target_id": self.target_id,
            "target_name": self.target_name,
            "action_type": self.action_type,
            "hit": self.hit,
            "crit": self.crit,
            "attack_total": self.attack_total,
            "defense_total": self.defense_total,
            "damage_total": self.damage_total,
            "damage_type": self.damage_type,
            "target_hp_before": self.target_hp_before,
            "target_hp_after": self.target_hp_after,
            "target_downed": self.target_downed,
             "rolls": list(self.rolls),
             "notes": list(self.notes),
         }


@dataclass(frozen=True)
class DefenseResolution:
    combat_id: str
    actor_id: str
    action_type: str
    defense_bonus: int
    duration: str
    notes: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "combat_id": self.combat_id,
            "actor_id": self.actor_id,
            "action_type": self.action_type,
            "defense_bonus": self.defense_bonus,
            "duration": self.duration,
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class FleeResolution:
    combat_id: str
    actor_id: str
    action_type: str
    success: bool
    flee_total: int
    difficulty_total: int
    rolls: List[Dict[str, Any]]
    notes: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "combat_id": self.combat_id,
            "actor_id": self.actor_id,
            "action_type": self.action_type,
            "success": self.success,
            "flee_total": self.flee_total,
            "difficulty_total": self.difficulty_total,
            "rolls": list(self.rolls),
            "notes": list(self.notes),
        }
