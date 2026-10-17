"""Deterministic tactical movement, cover, action budgets, and reactions."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Literal

from pydantic import Field

from app.apps.rpg.rules.combat.apply import apply_attack_resolution
from app.apps.rpg.rules.combat.models import AttackIntent
from app.apps.rpg.rules.combat.resolver import resolve_attack
from app.apps.rpg.rules.combat.state import (
    combat_is_active,
    get_current_actor_id,
    normalize_combat_state,
)

from app.apps.rpg.world.map_actor_footprints import actor_footprint_cells
from app.apps.rpg.world.map_effective_geometry import effective_is_walkable
from app.apps.rpg.world.map_grid_contracts import GridActorPlacement, GridMapDefinition, GridPoint
from app.apps.rpg.world.map_instance_runtime import (
    ActorMovedEvent,
    CampaignMapInstanceSnapshot,
    FrozenRuntimeModel,
    MoveActorCommand,
    resolve_move_command,
)
from app.apps.rpg.world.map_observer_runtime import has_line_of_sight


class TacticalSpatialError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}:{detail}" if detail else code)


class TacticalSpatialPolicy(FrozenRuntimeModel):
    default_movement_budget: int = Field(default=60, ge=1, le=10_000)
    default_action_budget: int = Field(default=1, ge=1, le=10)
    half_cover_bonus: int = Field(default=2, ge=0, le=20)
    full_cover_bonus: int = Field(default=5, ge=0, le=20)
    max_reactions_per_move: int = Field(default=8, ge=0, le=64)


class TacticalMoveCommand(FrozenRuntimeModel):
    submission_id: str = Field(min_length=1)
    command_id: str = Field(min_length=1)
    actor_id: str = Field(min_length=1)
    destination: GridPoint
    expected_map_state_revision: int = Field(ge=0)
    expected_campaign_revision: int = Field(ge=0)


class TacticalAttackCommand(FrozenRuntimeModel):
    submission_id: str = Field(min_length=1)
    command_id: str = Field(min_length=1)
    actor_id: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    action_type: Literal[
        "melee_attack",
        "ranged_attack",
        "unarmed_attack",
    ] = "melee_attack"
    expected_campaign_revision: int = Field(ge=0)


class CoverAssessment(FrozenRuntimeModel):
    attacker_id: str
    target_id: str
    level: Literal["none", "half", "full", "blocked"]
    defense_bonus: int = Field(ge=0)
    cover_cells: tuple[GridPoint, ...] = ()
    exposed_target_cells: tuple[GridPoint, ...] = ()
    line_of_sight: bool = True


class ReactionOpportunity(FrozenRuntimeModel):
    reactor_id: str
    target_id: str
    trigger_path_index: int = Field(ge=0)
    from_anchor: GridPoint
    to_anchor: GridPoint


def resolve_tactical_move(
    definition: GridMapDefinition,
    snapshot: CampaignMapInstanceSnapshot,
    simulation_state: dict[str, Any],
    command: TacticalMoveCommand,
    *,
    policy: TacticalSpatialPolicy | None = None,
    turn_id: str | None = None,
) -> tuple[
    ActorMovedEvent,
    CampaignMapInstanceSnapshot,
    dict[str, Any],
    dict[str, Any],
]:
    rules = policy or TacticalSpatialPolicy()
    next_state = deepcopy(simulation_state)
    combat_state = _require_actor_turn(next_state, command.actor_id, rules)
    tactical = dict(combat_state["tactical_state"])
    movement_remaining = dict(tactical["movement_remaining"])
    remaining = int(movement_remaining.get(command.actor_id, 0))

    move_event, moved_snapshot = resolve_move_command(
        definition,
        snapshot,
        MoveActorCommand(
            command_id=command.command_id,
            actor_id=command.actor_id,
            destination=command.destination,
            expected_map_state_revision=command.expected_map_state_revision,
        ),
    )
    if move_event.movement_cost > remaining:
        raise TacticalSpatialError(
            "tactical_movement_budget_exceeded",
            f"required={move_event.movement_cost}:remaining={remaining}",
        )
    movement_remaining[command.actor_id] = remaining - move_event.movement_cost
    tactical["movement_remaining"] = movement_remaining

    opportunities = reaction_opportunities(
        snapshot,
        combat_state,
        mover_id=command.actor_id,
        path=move_event.path,
    )[: rules.max_reactions_per_move]
    reaction_results: list[dict[str, Any]] = []
    reaction_available = dict(tactical["reaction_available"])
    combat_state["tactical_state"] = tactical
    for opportunity in opportunities:
        if not reaction_available.get(opportunity.reactor_id, False):
            continue
        trigger_snapshot = _snapshot_with_actor_anchor(
            snapshot,
            command.actor_id,
            opportunity.from_anchor,
        )
        cover = evaluate_cover(
            definition,
            trigger_snapshot,
            attacker_id=opportunity.reactor_id,
            target_id=command.actor_id,
            policy=rules,
        )
        _apply_cover_modifier(combat_state, cover)
        resolution = resolve_attack(
            next_state,
            combat_state,
            AttackIntent(
                actor_id=opportunity.reactor_id,
                target_id=command.actor_id,
                action_type="reaction_attack",
                tags=["movement_reaction"],
            ),
            turn_id=(
                f"{turn_id or command.submission_id}:reaction:"
                f"{opportunity.trigger_path_index}:{opportunity.reactor_id}"
            ),
            tick=int(combat_state.get("round", 0) or 0),
        ).to_dict()
        next_state, combat_state = apply_attack_resolution(
            next_state,
            combat_state,
            resolution,
        )
        tactical = dict(combat_state.get("tactical_state") or tactical)
        reaction_available = dict(tactical.get("reaction_available") or reaction_available)
        reaction_available[opportunity.reactor_id] = False
        tactical["reaction_available"] = reaction_available
        combat_state["tactical_state"] = tactical
        reaction_results.append(
            {
                "opportunity": opportunity.model_dump(mode="json"),
                "cover": cover.model_dump(mode="json"),
                "resolution": resolution,
                "resolution_timing": "after_movement",
            }
        )
        if _actor_hp(next_state, command.actor_id) <= 0:
            break

    destination_cover = strongest_hostile_cover(
        definition,
        moved_snapshot,
        combat_state,
        target_id=command.actor_id,
        policy=rules,
    )
    _set_participant_cover(combat_state, command.actor_id, destination_cover.level)
    combat_state["tactical_state"] = tactical
    recent = list(combat_state.get("recent_events") or [])
    recent.append(
        {
            "type": "tactical_move_resolution",
            "actor_id": command.actor_id,
            "movement_cost": move_event.movement_cost,
            "movement_remaining": movement_remaining[command.actor_id],
            "reaction_count": len(reaction_results),
            "destination_cover": destination_cover.model_dump(mode="json"),
        }
    )
    combat_state["recent_events"] = recent[-24:]
    combat_state["last_tactical_resolution"] = {
        "type": "move",
        "command_id": command.command_id,
        "map_event_id": move_event.event_id,
        "movement_cost": move_event.movement_cost,
        "movement_remaining": movement_remaining[command.actor_id],
        "reaction_results": reaction_results,
        "destination_cover": destination_cover.model_dump(mode="json"),
    }
    next_state["combat_state"] = combat_state
    return (
        move_event,
        moved_snapshot,
        next_state,
        dict(combat_state["last_tactical_resolution"]),
    )


def resolve_tactical_attack(
    definition: GridMapDefinition,
    snapshot: CampaignMapInstanceSnapshot,
    simulation_state: dict[str, Any],
    command: TacticalAttackCommand,
    *,
    policy: TacticalSpatialPolicy | None = None,
    turn_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    rules = policy or TacticalSpatialPolicy()
    next_state = deepcopy(simulation_state)
    combat_state = _require_actor_turn(next_state, command.actor_id, rules)
    if not _hostile(combat_state, command.actor_id, command.target_id):
        raise TacticalSpatialError("tactical_target_not_hostile", command.target_id)
    attacker = snapshot.actor(command.actor_id)
    target = snapshot.actor(command.target_id)
    distance = footprint_distance(attacker, target)
    if command.action_type in {"melee_attack", "unarmed_attack"} and distance > 1:
        raise TacticalSpatialError("tactical_melee_target_out_of_reach", command.target_id)
    cover = evaluate_cover(
        definition,
        snapshot,
        attacker_id=command.actor_id,
        target_id=command.target_id,
        policy=rules,
    )
    if not cover.line_of_sight:
        raise TacticalSpatialError("tactical_target_no_line_of_sight", command.target_id)

    tactical = dict(combat_state["tactical_state"])
    actions_remaining = dict(tactical["actions_remaining"])
    remaining = int(actions_remaining.get(command.actor_id, 0))
    if remaining <= 0:
        raise TacticalSpatialError("tactical_action_budget_exhausted", command.actor_id)
    _apply_cover_modifier(combat_state, cover)
    resolution = resolve_attack(
        next_state,
        combat_state,
        AttackIntent(
            actor_id=command.actor_id,
            target_id=command.target_id,
            action_type=command.action_type,
            tags=[f"cover:{cover.level}"],
        ),
        turn_id=turn_id or command.submission_id,
        tick=int(combat_state.get("round", 0) or 0),
    ).to_dict()
    next_state, combat_state = apply_attack_resolution(
        next_state,
        combat_state,
        resolution,
    )
    tactical = dict(combat_state.get("tactical_state") or tactical)
    actions_remaining = dict(tactical.get("actions_remaining") or actions_remaining)
    actions_remaining[command.actor_id] = remaining - 1
    tactical["actions_remaining"] = actions_remaining
    combat_state["tactical_state"] = tactical
    combat_state["last_tactical_resolution"] = {
        "type": "attack",
        "command_id": command.command_id,
        "cover": cover.model_dump(mode="json"),
        "resolution": resolution,
        "actions_remaining": actions_remaining[command.actor_id],
    }
    next_state["combat_state"] = combat_state
    return next_state, dict(combat_state["last_tactical_resolution"])


def evaluate_cover(
    definition: GridMapDefinition,
    snapshot: CampaignMapInstanceSnapshot,
    *,
    attacker_id: str,
    target_id: str,
    policy: TacticalSpatialPolicy | None = None,
) -> CoverAssessment:
    rules = policy or TacticalSpatialPolicy()
    attacker = snapshot.actor(attacker_id)
    target = snapshot.actor(target_id)
    attacker_cells = actor_footprint_cells(attacker)
    target_cells = actor_footprint_cells(target)
    exposed = tuple(
        cell
        for cell in target_cells
        if any(
            has_line_of_sight(
                definition,
                source,
                cell,
                snapshot=snapshot,
            )
            for source in attacker_cells
        )
    )
    if not exposed:
        return CoverAssessment(
            attacker_id=attacker_id,
            target_id=target_id,
            level="blocked",
            defense_bonus=0,
            exposed_target_cells=(),
            line_of_sight=False,
        )

    dx, dy = _direction_toward(attacker_cells, target_cells)
    cover_cells = tuple(
        sorted(
            {
                (cell[0] + dx, cell[1] + dy)
                for cell in target_cells
                if _inside(definition, (cell[0] + dx, cell[1] + dy))
                and not effective_is_walkable(
                    definition,
                    snapshot,
                    (cell[0] + dx, cell[1] + dy),
                )
            },
            key=lambda cell: (cell[1], cell[0]),
        )
    )
    if not cover_cells:
        level: Literal["none", "half", "full", "blocked"] = "none"
        bonus = 0
    elif len(cover_cells) >= len(target_cells):
        level = "full"
        bonus = rules.full_cover_bonus
    else:
        level = "half"
        bonus = rules.half_cover_bonus
    return CoverAssessment(
        attacker_id=attacker_id,
        target_id=target_id,
        level=level,
        defense_bonus=bonus,
        cover_cells=cover_cells,
        exposed_target_cells=exposed,
        line_of_sight=True,
    )


def strongest_hostile_cover(
    definition: GridMapDefinition,
    snapshot: CampaignMapInstanceSnapshot,
    combat_state: dict[str, Any],
    *,
    target_id: str,
    policy: TacticalSpatialPolicy | None = None,
) -> CoverAssessment:
    rules = policy or TacticalSpatialPolicy()
    assessments = [
        evaluate_cover(
            definition,
            snapshot,
            attacker_id=actor.actor_id,
            target_id=target_id,
            policy=rules,
        )
        for actor in snapshot.actors
        if actor.actor_id != target_id
        and _hostile(combat_state, actor.actor_id, target_id)
        and _actor_hp_from_combat(combat_state, actor.actor_id) > 0
    ]
    if not assessments:
        return CoverAssessment(
            attacker_id="",
            target_id=target_id,
            level="none",
            defense_bonus=0,
        )
    rank = {"none": 0, "half": 1, "full": 2, "blocked": 3}
    return max(
        assessments,
        key=lambda row: (rank[row.level], row.defense_bonus, row.attacker_id),
    )


def reaction_opportunities(
    snapshot: CampaignMapInstanceSnapshot,
    combat_state: dict[str, Any],
    *,
    mover_id: str,
    path: tuple[GridPoint, ...],
) -> tuple[ReactionOpportunity, ...]:
    if len(path) < 2:
        return ()
    mover = snapshot.actor(mover_id)
    tactical = dict(combat_state.get("tactical_state") or {})
    available = dict(tactical.get("reaction_available") or {})
    initiative = dict(combat_state.get("initiative") or {})
    rows: list[ReactionOpportunity] = []
    for reactor in snapshot.actors:
        if reactor.actor_id == mover_id:
            continue
        if not available.get(reactor.actor_id, False):
            continue
        if not _hostile(combat_state, reactor.actor_id, mover_id):
            continue
        if _actor_hp_from_combat(combat_state, reactor.actor_id) <= 0:
            continue
        for index, (before, after) in enumerate(zip(path, path[1:]), start=1):
            if _adjacent_at(mover, before, reactor) and not _adjacent_at(
                mover,
                after,
                reactor,
            ):
                rows.append(
                    ReactionOpportunity(
                        reactor_id=reactor.actor_id,
                        target_id=mover_id,
                        trigger_path_index=index,
                        from_anchor=before,
                        to_anchor=after,
                    )
                )
                break
    return tuple(
        sorted(
            rows,
            key=lambda row: (
                row.trigger_path_index,
                -int(initiative.get(row.reactor_id, 0) or 0),
                row.reactor_id,
            ),
        )
    )


def footprint_distance(left: GridActorPlacement, right: GridActorPlacement) -> int:
    return min(
        max(abs(a[0] - b[0]), abs(a[1] - b[1]))
        for a in actor_footprint_cells(left)
        for b in actor_footprint_cells(right)
    )


def _require_actor_turn(
    simulation_state: dict[str, Any],
    actor_id: str,
    policy: TacticalSpatialPolicy,
) -> dict[str, Any]:
    combat_state = normalize_combat_state(simulation_state.get("combat_state"))
    if not combat_is_active(combat_state):
        raise TacticalSpatialError("tactical_combat_not_active")
    current_actor_id = get_current_actor_id(combat_state)
    if current_actor_id != actor_id:
        raise TacticalSpatialError(
            "tactical_actor_not_current",
            f"expected={current_actor_id}:received={actor_id}",
        )
    if actor_id not in dict(combat_state.get("participants") or {}):
        raise TacticalSpatialError("tactical_actor_not_participant", actor_id)
    combat_state["tactical_state"] = _ensure_round_state(combat_state, policy)
    return combat_state


def _ensure_round_state(
    combat_state: dict[str, Any],
    policy: TacticalSpatialPolicy,
) -> dict[str, Any]:
    round_number = max(1, int(combat_state.get("round", 1) or 1))
    combat_id = str(combat_state.get("combat_id") or "")
    participants = dict(combat_state.get("participants") or {})
    current = dict(combat_state.get("tactical_state") or {})
    if (
        int(current.get("round", 0) or 0) != round_number
        or str(current.get("combat_id") or "") != combat_id
    ):
        return {
            "combat_id": combat_id,
            "round": round_number,
            "movement_remaining": {
                actor_id: _movement_budget(participant, policy)
                for actor_id, participant in sorted(participants.items())
            },
            "actions_remaining": {
                actor_id: policy.default_action_budget
                for actor_id in sorted(participants)
            },
            "reaction_available": {
                actor_id: _actor_hp_from_participant(participant) > 0
                for actor_id, participant in sorted(participants.items())
            },
        }
    movement = dict(current.get("movement_remaining") or {})
    actions = dict(current.get("actions_remaining") or {})
    reactions = dict(current.get("reaction_available") or {})
    for actor_id, participant in sorted(participants.items()):
        movement.setdefault(actor_id, _movement_budget(participant, policy))
        actions.setdefault(actor_id, policy.default_action_budget)
        reactions.setdefault(actor_id, _actor_hp_from_participant(participant) > 0)
    return {
        "combat_id": combat_id,
        "round": round_number,
        "movement_remaining": movement,
        "actions_remaining": actions,
        "reaction_available": reactions,
    }


def _movement_budget(
    participant: dict[str, Any],
    policy: TacticalSpatialPolicy,
) -> int:
    explicit = int(participant.get("movement_budget", 0) or 0)
    if explicit > 0:
        return explicit
    stats = dict(participant.get("stats") or {})
    speed = int(stats.get("speed", 0) or 0)
    return max(10, speed * 10) if speed > 0 else policy.default_movement_budget


def _hostile(combat_state: dict[str, Any], left_id: str, right_id: str) -> bool:
    participants = dict(combat_state.get("participants") or {})
    left = dict(participants.get(left_id) or {})
    right = dict(participants.get(right_id) or {})
    if not left or not right:
        return False
    return _team(left) != _team(right)


def _team(participant: dict[str, Any]) -> str:
    value = str(
        participant.get("combat_team")
        or participant.get("team")
        or participant.get("side")
        or participant.get("combat_role")
        or participant.get("role")
        or "neutral"
    ).lower()
    return "party" if value in {"player", "party", "companion", "ally"} else value


def _actor_hp(simulation_state: dict[str, Any], actor_id: str) -> int:
    for key in ("actor_states", "npc_states"):
        for actor in simulation_state.get(key, []) or []:
            if str(actor.get("id") or "") == actor_id:
                return int(dict(actor.get("resources") or {}).get("hp", 0) or 0)
    return _actor_hp_from_combat(
        normalize_combat_state(simulation_state.get("combat_state")),
        actor_id,
    )


def _actor_hp_from_combat(combat_state: dict[str, Any], actor_id: str) -> int:
    participant = dict(dict(combat_state.get("participants") or {}).get(actor_id) or {})
    return _actor_hp_from_participant(participant)


def _actor_hp_from_participant(participant: dict[str, Any]) -> int:
    resources = dict(participant.get("resources") or {})
    return int(resources.get("hp", participant.get("hp", 1)) or 0)


def _apply_cover_modifier(
    combat_state: dict[str, Any],
    cover: CoverAssessment,
) -> None:
    if cover.defense_bonus <= 0:
        return
    modifiers = dict(combat_state.get("defense_modifiers") or {})
    existing = dict(modifiers.get(cover.target_id) or {})
    existing_bonus = int(existing.get("bonus", 0) or 0)
    modifiers[cover.target_id] = {
        "bonus": max(existing_bonus, cover.defense_bonus),
        "duration": "next_incoming_attack",
        "source": f"tactical_cover:{cover.level}",
    }
    combat_state["defense_modifiers"] = modifiers


def _set_participant_cover(
    combat_state: dict[str, Any],
    actor_id: str,
    cover_level: str,
) -> None:
    participants = dict(combat_state.get("participants") or {})
    participant = dict(participants.get(actor_id) or {})
    if not participant:
        return
    position = dict(participant.get("position") or {})
    position["cover"] = cover_level
    participant["position"] = position
    participants[actor_id] = participant
    combat_state["participants"] = participants


def _snapshot_with_actor_anchor(
    snapshot: CampaignMapInstanceSnapshot,
    actor_id: str,
    anchor: GridPoint,
) -> CampaignMapInstanceSnapshot:
    return snapshot.model_copy(
        update={
            "actors": tuple(
                actor.model_copy(update={"cell": anchor})
                if actor.actor_id == actor_id
                else actor
                for actor in snapshot.actors
            )
        }
    )


def _adjacent_at(
    mover: GridActorPlacement,
    mover_anchor: GridPoint,
    reactor: GridActorPlacement,
) -> bool:
    moved = mover.model_copy(update={"cell": mover_anchor})
    return footprint_distance(moved, reactor) <= 1


def _direction_toward(
    attacker_cells: tuple[GridPoint, ...],
    target_cells: tuple[GridPoint, ...],
) -> GridPoint:
    attacker_x = sum(cell[0] for cell in attacker_cells) / len(attacker_cells)
    attacker_y = sum(cell[1] for cell in attacker_cells) / len(attacker_cells)
    target_x = sum(cell[0] for cell in target_cells) / len(target_cells)
    target_y = sum(cell[1] for cell in target_cells) / len(target_cells)
    dx = _sign(attacker_x - target_x)
    dy = _sign(attacker_y - target_y)
    if abs(attacker_x - target_x) > abs(attacker_y - target_y):
        dy = 0
    elif abs(attacker_y - target_y) > abs(attacker_x - target_x):
        dx = 0
    return dx, dy


def _sign(value: float) -> int:
    return 1 if value > 0 else -1 if value < 0 else 0


def _inside(definition: GridMapDefinition, cell: GridPoint) -> bool:
    return 0 <= cell[0] < definition.width and 0 <= cell[1] < definition.height
