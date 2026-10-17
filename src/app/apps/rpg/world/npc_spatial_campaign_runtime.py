"""Transactional campaign tick runtime for living NPC spatial goals."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from app.security.tenant_context import current_tenant
from app.persistence.unit_of_work import unit_of_work

from app.apps.rpg.world.map_grid_contracts import GridMapDefinition
from app.apps.rpg.world.map_instance_runtime import (
    ActorMovedEvent,
    CampaignMapInstanceSnapshot,
    MapMovementError,
    MoveActorCommand,
    reduce_map_event,
    resolve_move_command,
)
from app.apps.rpg.world.npc_spatial_campaign_contracts import (
    CampaignNpcSpatialGoal,
    CampaignNpcSpatialPolicy,
    CampaignNpcSpatialRoutine,
    CampaignSpatialTickRequest,
)
from app.apps.rpg.world.npc_spatial_simulation import (
    NpcSpatialSimulationContext,
    advance_npc_spatial_tick,
)
from app.apps.rpg.world.npc_spatial_transition import resolve_portal_transition


def _goal(row: dict[str, Any]) -> CampaignNpcSpatialGoal:
    return CampaignNpcSpatialGoal.model_validate(row)


def _routine(row: dict[str, Any]) -> CampaignNpcSpatialRoutine:
    return CampaignNpcSpatialRoutine.model_validate(row)


def _selected_goals(
    goals: list[CampaignNpcSpatialGoal],
    world_tick: int,
) -> tuple[CampaignNpcSpatialGoal, ...]:
    by_actor: dict[str, list[CampaignNpcSpatialGoal]] = defaultdict(list)
    for goal in goals:
        if goal.is_available(world_tick):
            by_actor[goal.actor_id].append(goal)
    selected = []
    for actor_id in sorted(by_actor):
        selected.append(
            sorted(
                by_actor[actor_id],
                key=lambda row: (-row.priority, row.issued_tick, row.goal_id),
            )[0]
        )
    return tuple(selected)


def _persist_movement_event(
    work: Any,
    context: Any,
    event: ActorMovedEvent,
    before: CampaignMapInstanceSnapshot,
) -> CampaignMapInstanceSnapshot:
    after = reduce_map_event(before, event)
    work.map_instances.append_event(
        context,
        map_instance_id=event.map_instance_id,
        command_id=event.command_id,
        event_id=event.event_id,
        event_type=event.event_type,
        event_sequence=event.event_sequence,
        revision_before=event.map_state_revision_before,
        revision_after=event.map_state_revision_after,
        event=event.model_dump(mode="json"),
        snapshot=after.model_dump(mode="json"),
    )
    return after


def _record_decision(
    work: Any,
    context: Any,
    goal: CampaignNpcSpatialGoal,
    decision: dict[str, Any],
    *,
    policy: CampaignNpcSpatialPolicy,
    completed: bool = False,
    blocked: bool = False,
) -> None:
    status = "completed" if completed else None
    if blocked and goal.blocked_attempts + 1 >= policy.max_blocked_attempts:
        status = "blocked"
    work.npc_spatial.record_goal_decision(
        context,
        campaign_id=goal.campaign_id,
        goal_id=goal.goal_id,
        decision=decision,
        status=status,
        increment_blocked=blocked,
    )


def _emit_due_routines(
    work: Any,
    context: Any,
    *,
    campaign_id: str,
    world_tick: int,
) -> list[CampaignNpcSpatialGoal]:
    emitted: list[CampaignNpcSpatialGoal] = []
    for row in work.npc_spatial.list_due_routines(context, campaign_id, world_tick):
        routine = _routine(row)
        goal = routine.emitted_goal(world_tick)
        work.npc_spatial.put_goal(context, goal, expected_revision=0)
        next_index = (routine.next_step_index + 1) % len(routine.steps)
        work.npc_spatial.advance_routine(
            context,
            campaign_id=campaign_id,
            routine_id=routine.routine_id,
            next_step_index=next_index,
            emission_count=routine.emission_count + 1,
            next_due_tick=world_tick + routine.interval_ticks,
            world_tick=world_tick,
        )
        emitted.append(goal)
    return emitted


def _load_maps(
    work: Any,
    context: Any,
    campaign_id: str,
) -> tuple[
    dict[str, CampaignMapInstanceSnapshot],
    dict[str, GridMapDefinition],
]:
    snapshots: dict[str, CampaignMapInstanceSnapshot] = {}
    definitions: dict[str, GridMapDefinition] = {}
    for row in work.npc_spatial.list_campaign_instances(context, campaign_id):
        snapshot = CampaignMapInstanceSnapshot.model_validate(row["snapshot"])
        definition_row = work.map_instances.get_definition(
            context,
            row["map_id"],
            row["definition_revision"],
        )
        if definition_row is None:
            raise KeyError(
                f"map_definition_not_found:{row['map_id']}:{row['definition_revision']}"
            )
        snapshots[snapshot.map_instance_id] = snapshot
        definitions[snapshot.map_instance_id] = GridMapDefinition.model_validate(
            definition_row["document"]
        )
    return snapshots, definitions


def _simulation_context(
    campaign: dict[str, Any],
    snapshots: dict[str, CampaignMapInstanceSnapshot],
    request: CampaignSpatialTickRequest,
) -> NpcSpatialSimulationContext:
    known = set(snapshots)
    requested = set(request.active_map_instance_ids) | set(
        request.coarse_map_instance_ids
    )
    unknown = requested - known
    if unknown:
        raise ValueError(
            "npc_spatial_unknown_map_instances:" + ",".join(sorted(unknown))
        )
    if request.active_map_instance_ids:
        active = tuple(sorted(request.active_map_instance_ids))
    else:
        current = str(dict(campaign.get("state") or {}).get("current_map_instance_id") or "")
        active = (current,) if current in known else ()
    if request.coarse_map_instance_ids:
        coarse = tuple(sorted(request.coarse_map_instance_ids))
    else:
        coarse = tuple(sorted(known - set(active)))
    return NpcSpatialSimulationContext(
        active_map_instance_ids=active,
        coarse_map_instance_ids=coarse,
    )


def _movement_decisions(
    work: Any,
    context: Any,
    *,
    goals: tuple[CampaignNpcSpatialGoal, ...],
    world_tick: int,
    simulation_context: NpcSpatialSimulationContext,
    policy: CampaignNpcSpatialPolicy,
    snapshots: dict[str, CampaignMapInstanceSnapshot],
    definitions: dict[str, GridMapDefinition],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    decisions: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    by_map: dict[str, list[CampaignNpcSpatialGoal]] = defaultdict(list)
    for goal in goals:
        by_map[goal.map_instance_id].append(goal)
    for map_instance_id in sorted(by_map):
        snapshot = snapshots.get(map_instance_id)
        definition = definitions.get(map_instance_id)
        if snapshot is None or definition is None:
            for goal in by_map[map_instance_id]:
                decision = {
                    "actor_id": goal.actor_id,
                    "goal_id": goal.goal_id,
                    "tier": "dormant",
                    "status": "blocked",
                    "error_code": "map_instance_not_found",
                }
                _record_decision(
                    work,
                    context,
                    goal,
                    decision,
                    policy=policy,
                    blocked=True,
                )
                decisions.append(decision)
            continue
        result = advance_npc_spatial_tick(
            definition,
            snapshot,
            [goal.movement_goal() for goal in by_map[map_instance_id]],
            world_tick=world_tick,
            context=simulation_context,
            policy=policy.movement_policy(),
        )
        current = snapshot
        for event in result.events:
            current = _persist_movement_event(work, context, event, current)
            events.append(
                {
                    "event_id": event.event_id,
                    "event_type": event.event_type,
                    "map_instance_id": event.map_instance_id,
                    "actor_id": event.actor_id,
                }
            )
        snapshots[map_instance_id] = current
        goals_by_id = {goal.goal_id: goal for goal in by_map[map_instance_id]}
        for row in result.decisions:
            goal = goals_by_id[row.goal_id]
            decision = row.model_dump(mode="json")
            completed = row.status in {"moved", "completed", "already_applied"}
            blocked = row.status == "blocked"
            _record_decision(
                work,
                context,
                goal,
                decision,
                policy=policy,
                completed=completed,
                blocked=blocked,
            )
            decisions.append(decision)
    return decisions, events


def _transition_decisions(
    work: Any,
    context: Any,
    *,
    goals: tuple[CampaignNpcSpatialGoal, ...],
    world_tick: int,
    simulation_context: NpcSpatialSimulationContext,
    policy: CampaignNpcSpatialPolicy,
    snapshots: dict[str, CampaignMapInstanceSnapshot],
    definitions: dict[str, GridMapDefinition],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    decisions: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    transitioned = 0
    for goal in sorted(goals, key=lambda row: (row.actor_id, row.goal_id)):
        tier = simulation_context.tier_for(goal.map_instance_id)
        if tier == "dormant":
            decision = {
                "actor_id": goal.actor_id,
                "goal_id": goal.goal_id,
                "tier": tier,
                "status": "dormant",
            }
            _record_decision(work, context, goal, decision, policy=policy)
            decisions.append(decision)
            continue
        if tier == "coarse" and world_tick % policy.coarse_tick_interval:
            decision = {
                "actor_id": goal.actor_id,
                "goal_id": goal.goal_id,
                "tier": tier,
                "status": "deferred_cadence",
            }
            _record_decision(work, context, goal, decision, policy=policy)
            decisions.append(decision)
            continue
        if transitioned >= policy.transition_actor_budget:
            decision = {
                "actor_id": goal.actor_id,
                "goal_id": goal.goal_id,
                "tier": tier,
                "status": "deferred_budget",
            }
            _record_decision(work, context, goal, decision, policy=policy)
            decisions.append(decision)
            continue
        source = snapshots.get(goal.map_instance_id)
        target = snapshots.get(str(goal.target_map_instance_id))
        source_definition = definitions.get(goal.map_instance_id)
        target_definition = definitions.get(str(goal.target_map_instance_id))
        if not all((source, target, source_definition, target_definition)):
            decision = {
                "actor_id": goal.actor_id,
                "goal_id": goal.goal_id,
                "tier": tier,
                "status": "blocked",
                "error_code": "portal_map_instance_not_found",
            }
            _record_decision(
                work,
                context,
                goal,
                decision,
                policy=policy,
                blocked=True,
            )
            decisions.append(decision)
            continue
        assert source is not None and target is not None
        assert source_definition is not None and target_definition is not None
        portal = next(
            (
                row
                for row in source_definition.portals
                if row.portal_id == goal.portal_id
            ),
            None,
        )
        if portal is None:
            decision = {
                "actor_id": goal.actor_id,
                "goal_id": goal.goal_id,
                "tier": tier,
                "status": "blocked",
                "error_code": "portal_not_found",
            }
            _record_decision(
                work,
                context,
                goal,
                decision,
                policy=policy,
                blocked=True,
            )
            decisions.append(decision)
            continue
        actor = source.actor(goal.actor_id)
        if actor.cell != portal.source.cell:
            command_id = (
                f"npc-spatial:{source.map_instance_id}:{world_tick}:"
                f"{goal.actor_id}:{goal.goal_id}:r{goal.goal_revision}:portal-approach"
            )
            try:
                event, _ = resolve_move_command(
                    source_definition,
                    source,
                    MoveActorCommand(
                        command_id=command_id,
                        actor_id=goal.actor_id,
                        destination=portal.source.cell,
                        expected_map_state_revision=source.map_state_revision,
                    ),
                )
                after = _persist_movement_event(work, context, event, source)
                snapshots[source.map_instance_id] = after
                decision = {
                    "actor_id": goal.actor_id,
                    "goal_id": goal.goal_id,
                    "tier": tier,
                    "status": "moved",
                    "phase": "portal_approach",
                    "command_id": command_id,
                    "event_id": event.event_id,
                }
                events.append(
                    {
                        "event_id": event.event_id,
                        "event_type": event.event_type,
                        "map_instance_id": event.map_instance_id,
                        "actor_id": event.actor_id,
                    }
                )
                _record_decision(work, context, goal, decision, policy=policy)
            except MapMovementError as exc:
                decision = {
                    "actor_id": goal.actor_id,
                    "goal_id": goal.goal_id,
                    "tier": tier,
                    "status": "blocked",
                    "phase": "portal_approach",
                    "command_id": command_id,
                    "error_code": exc.code,
                }
                _record_decision(
                    work,
                    context,
                    goal,
                    decision,
                    policy=policy,
                    blocked=True,
                )
            decisions.append(decision)
            continue
        transition_id = (
            f"npc-spatial-transition:{goal.campaign_id}:{world_tick}:"
            f"{goal.actor_id}:{goal.goal_id}:r{goal.goal_revision}"
        )
        try:
            exit_event, enter_event, source_after, target_after = resolve_portal_transition(
                source_definition,
                source,
                target_definition,
                target,
                actor_id=goal.actor_id,
                portal_id=str(goal.portal_id),
                transition_id=transition_id,
            )
            work.map_instances.append_event(
                context,
                map_instance_id=source.map_instance_id,
                command_id=exit_event.command_id,
                event_id=exit_event.event_id,
                event_type=exit_event.event_type,
                event_sequence=exit_event.event_sequence,
                revision_before=exit_event.map_state_revision_before,
                revision_after=exit_event.map_state_revision_after,
                event=exit_event.model_dump(mode="json"),
                snapshot=source_after.model_dump(mode="json"),
            )
            work.map_instances.append_event(
                context,
                map_instance_id=target.map_instance_id,
                command_id=enter_event.command_id,
                event_id=enter_event.event_id,
                event_type=enter_event.event_type,
                event_sequence=enter_event.event_sequence,
                revision_before=enter_event.map_state_revision_before,
                revision_after=enter_event.map_state_revision_after,
                event=enter_event.model_dump(mode="json"),
                snapshot=target_after.model_dump(mode="json"),
            )
            snapshots[source.map_instance_id] = source_after
            snapshots[target.map_instance_id] = target_after
            work.npc_spatial.record_transition(
                context,
                campaign_id=goal.campaign_id,
                transition_id=transition_id,
                world_tick=world_tick,
                actor_id=goal.actor_id,
                portal_id=str(goal.portal_id),
                source_map_instance_id=source.map_instance_id,
                target_map_instance_id=target.map_instance_id,
                source_event_id=exit_event.event_id,
                target_event_id=enter_event.event_id,
                payload={
                    "source_event": exit_event.model_dump(mode="json"),
                    "target_event": enter_event.model_dump(mode="json"),
                },
            )
            decision = {
                "actor_id": goal.actor_id,
                "goal_id": goal.goal_id,
                "tier": tier,
                "status": "completed",
                "phase": "portal_transition",
                "transition_id": transition_id,
                "source_event_id": exit_event.event_id,
                "target_event_id": enter_event.event_id,
            }
            events.extend(
                (
                    {
                        "event_id": exit_event.event_id,
                        "event_type": exit_event.event_type,
                        "map_instance_id": exit_event.map_instance_id,
                        "actor_id": goal.actor_id,
                    },
                    {
                        "event_id": enter_event.event_id,
                        "event_type": enter_event.event_type,
                        "map_instance_id": enter_event.map_instance_id,
                        "actor_id": goal.actor_id,
                    },
                )
            )
            _record_decision(
                work,
                context,
                goal,
                decision,
                policy=policy,
                completed=True,
            )
            transitioned += 1
        except MapMovementError as exc:
            decision = {
                "actor_id": goal.actor_id,
                "goal_id": goal.goal_id,
                "tier": tier,
                "status": "blocked",
                "phase": "portal_transition",
                "transition_id": transition_id,
                "error_code": exc.code,
            }
            _record_decision(
                work,
                context,
                goal,
                decision,
                policy=policy,
                blocked=True,
            )
        decisions.append(decision)
    return decisions, events


def _tick_metrics(
    *,
    world_tick: int,
    policy: CampaignNpcSpatialPolicy,
    simulation_context: NpcSpatialSimulationContext,
    snapshots: dict[str, CampaignMapInstanceSnapshot],
    decisions: list[dict[str, Any]],
    events: list[dict[str, Any]],
    routine_goals_emitted: int,
) -> dict[str, Any]:
    statuses = Counter(str(row.get("status") or "unknown") for row in decisions)
    tiers = Counter(str(row.get("tier") or "unknown") for row in decisions)
    active_actions = sum(
        1
        for row in decisions
        if row.get("tier") == "active"
        and row.get("status") in {"moved", "completed", "blocked"}
    )
    coarse_actions = sum(
        1
        for row in decisions
        if row.get("tier") == "coarse"
        and row.get("status") in {"moved", "completed", "blocked"}
    )
    transitions = sum(
        1 for row in decisions if row.get("phase") == "portal_transition"
    )
    return {
        "world_tick": world_tick,
        "policy": policy.model_dump(mode="json"),
        "map_tiers": {
            "active": len(simulation_context.active_map_instance_ids),
            "coarse": len(simulation_context.coarse_map_instance_ids),
            "dormant": len(snapshots)
            - len(simulation_context.active_map_instance_ids)
            - len(simulation_context.coarse_map_instance_ids),
        },
        "selected_actors": len(decisions),
        "decision_counts": dict(sorted(statuses.items())),
        "tier_decision_counts": dict(sorted(tiers.items())),
        "events": len(events),
        "portal_transitions": transitions,
        "routine_goals_emitted": routine_goals_emitted,
        "active_budget_used": active_actions,
        "active_budget_capacity": policy.active_actor_budget,
        "active_budget_utilization": round(
            active_actions / policy.active_actor_budget,
            4,
        ),
        "coarse_budget_used": coarse_actions,
        "coarse_budget_capacity": policy.coarse_actor_budget,
        "coarse_budget_utilization": round(
            coarse_actions / policy.coarse_actor_budget,
            4,
        ),
        "transition_budget_used": transitions,
        "transition_budget_capacity": policy.transition_actor_budget,
        "transition_budget_utilization": round(
            transitions / policy.transition_actor_budget,
            4,
        ),
    }


def _aggregate_metrics(
    current: dict[str, Any],
    tick: dict[str, Any],
) -> dict[str, Any]:
    decisions = dict(tick.get("decision_counts") or {})
    aggregate_decisions = dict(current.get("decision_counts") or {})
    for key, value in decisions.items():
        aggregate_decisions[key] = int(aggregate_decisions.get(key) or 0) + int(value)
    return {
        "total_ticks": int(current.get("total_ticks") or 0) + 1,
        "total_events": int(current.get("total_events") or 0)
        + int(tick.get("events") or 0),
        "total_portal_transitions": int(
            current.get("total_portal_transitions") or 0
        )
        + int(tick.get("portal_transitions") or 0),
        "total_routine_goals_emitted": int(
            current.get("total_routine_goals_emitted") or 0
        )
        + int(tick.get("routine_goals_emitted") or 0),
        "decision_counts": dict(sorted(aggregate_decisions.items())),
        "max_active_budget_utilization": max(
            float(current.get("max_active_budget_utilization") or 0.0),
            float(tick.get("active_budget_utilization") or 0.0),
        ),
        "max_coarse_budget_utilization": max(
            float(current.get("max_coarse_budget_utilization") or 0.0),
            float(tick.get("coarse_budget_utilization") or 0.0),
        ),
        "max_transition_budget_utilization": max(
            float(current.get("max_transition_budget_utilization") or 0.0),
            float(tick.get("transition_budget_utilization") or 0.0),
        ),
        "last_world_tick": int(tick["world_tick"]),
    }


def advance_campaign_spatial_tick(
    campaign_id: str,
    request: CampaignSpatialTickRequest,
    *,
    database: Any | None = None,
) -> dict[str, Any]:
    """Advance one serialized campaign-owned spatial simulation tick."""

    context = current_tenant()
    default_policy = CampaignNpcSpatialPolicy()
    with unit_of_work(database) as work:
        campaign = work.rpg.get_campaign(context, campaign_id, for_update=True)
        if campaign is None:
            raise KeyError(f"campaign_not_found:{campaign_id}")
        clock = work.npc_spatial.clock_for_update(
            context,
            campaign_id,
            default_policy=default_policy,
        )
        if int(clock["world_tick"]) != request.expected_world_tick:
            raise ValueError(
                f"campaign_spatial_tick_conflict:{campaign_id}:"
                f"{request.expected_world_tick}:{clock['world_tick']}"
            )
        policy = CampaignNpcSpatialPolicy.model_validate(
            clock["policy"] or default_policy.model_dump(mode="json")
        )
        world_tick = request.expected_world_tick + 1
        emitted = _emit_due_routines(
            work,
            context,
            campaign_id=campaign_id,
            world_tick=world_tick,
        )
        rows = work.npc_spatial.list_active_goals(context, campaign_id)
        goals = [_goal(row) for row in rows]
        expired = [
            goal
            for goal in goals
            if goal.expires_after_tick is not None
            and world_tick > goal.expires_after_tick
        ]
        for goal in expired:
            work.npc_spatial.record_goal_decision(
                context,
                campaign_id=campaign_id,
                goal_id=goal.goal_id,
                decision={
                    "actor_id": goal.actor_id,
                    "goal_id": goal.goal_id,
                    "status": "expired",
                    "world_tick": world_tick,
                },
                status="expired",
            )
        active_goals = [goal for goal in goals if goal not in expired]
        selected = _selected_goals(active_goals, world_tick)
        snapshots, definitions = _load_maps(work, context, campaign_id)
        simulation_context = _simulation_context(
            campaign,
            snapshots,
            request,
        )
        movement = tuple(goal for goal in selected if goal.goal_type == "move_to_cell")
        transitions = tuple(
            goal for goal in selected if goal.goal_type == "transition_via_portal"
        )
        movement_decisions, movement_events = _movement_decisions(
            work,
            context,
            goals=movement,
            world_tick=world_tick,
            simulation_context=simulation_context,
            policy=policy,
            snapshots=snapshots,
            definitions=definitions,
        )
        transition_decisions, transition_events = _transition_decisions(
            work,
            context,
            goals=transitions,
            world_tick=world_tick,
            simulation_context=simulation_context,
            policy=policy,
            snapshots=snapshots,
            definitions=definitions,
        )
        decisions = [*movement_decisions, *transition_decisions]
        events = [*movement_events, *transition_events]
        metrics = _tick_metrics(
            world_tick=world_tick,
            policy=policy,
            simulation_context=simulation_context,
            snapshots=snapshots,
            decisions=decisions,
            events=events,
            routine_goals_emitted=len(emitted),
        )
        result = {
            "campaign_id": campaign_id,
            "world_tick": world_tick,
            "routine_goal_ids": [goal.goal_id for goal in emitted],
            "decisions": decisions,
            "events": events,
            "map_state_revisions": {
                key: value.map_state_revision
                for key, value in sorted(snapshots.items())
            },
        }
        aggregate = _aggregate_metrics(clock["aggregate_metrics"], metrics)
        stored_clock = work.npc_spatial.update_clock(
            context,
            campaign_id=campaign_id,
            expected_world_tick=request.expected_world_tick,
            world_tick=world_tick,
            policy=policy.model_dump(mode="json"),
            aggregate_metrics=aggregate,
        )
        work.npc_spatial.record_tick(
            context,
            campaign_id=campaign_id,
            world_tick=world_tick,
            result=result,
            metrics=metrics,
        )
        work.commit()
    return {
        "ok": True,
        "clock": stored_clock,
        "result": result,
        "metrics": metrics,
    }
