"""Atomic persistence services for tactical spatial commands."""
from __future__ import annotations

from typing import Any

from app.security.tenant_context import current_tenant
from app.persistence.unit_of_work import unit_of_work

from app.apps.rpg.world.map_grid_contracts import GridMapDefinition
from app.apps.rpg.world.map_instance_runtime import CampaignMapInstanceSnapshot
from app.apps.rpg.world.tactical_spatial import (
    TacticalAttackCommand,
    TacticalMoveCommand,
    TacticalSpatialError,
    TacticalSpatialPolicy,
    resolve_tactical_attack,
    resolve_tactical_move,
)

_TACTICAL_ENGINE_VERSION = "tactical-spatial-v1"
_TACTICAL_SCHEMA_VERSION = "tactical-spatial-v1"


def move_actor_tactically(
    map_instance_id: str,
    command: TacticalMoveCommand,
    *,
    policy: TacticalSpatialPolicy | None = None,
    database: Any | None = None,
) -> dict[str, Any]:
    context = current_tenant()
    with unit_of_work(database) as work:
        instance_hint = work.map_instances.get_instance(context, map_instance_id)
        if instance_hint is None:
            raise KeyError(f"map_instance_not_found:{map_instance_id}")
        campaign_id = str(instance_hint["campaign_id"])
        campaign = work.rpg.get_campaign(context, campaign_id, for_update=True)
        if campaign is None:
            raise KeyError(f"campaign_not_found:{campaign_id}")
        existing = work.rpg.get_turn_by_submission(
            context,
            campaign_id,
            command.submission_id,
        )
        if existing is not None:
            work.rollback()
            return _idempotent_response(existing)
        _require_campaign_revision(campaign, command.expected_campaign_revision)

        stored_instance = work.map_instances.get_instance(
            context,
            map_instance_id,
            for_update=True,
        )
        if stored_instance is None:
            raise KeyError(f"map_instance_not_found:{map_instance_id}")
        if str(stored_instance["campaign_id"]) != campaign_id:
            raise TacticalSpatialError("tactical_map_campaign_mismatch")
        definition, snapshot = _load_map(work, context, stored_instance)
        turn_id = _turn_id(campaign_id, command.submission_id)
        event, updated_snapshot, next_state, tactical = resolve_tactical_move(
            definition,
            snapshot,
            dict(campaign["state"]),
            command,
            policy=policy,
            turn_id=turn_id,
        )
        work.map_instances.append_event(
            context,
            map_instance_id=map_instance_id,
            command_id=event.command_id,
            event_id=event.event_id,
            event_type=event.event_type,
            event_sequence=event.event_sequence,
            revision_before=event.map_state_revision_before,
            revision_after=event.map_state_revision_after,
            event=event.model_dump(mode="json"),
            snapshot=updated_snapshot.model_dump(mode="json"),
        )
        compact = {
            "ok": True,
            "kind": "tactical_move",
            "campaign_id": campaign_id,
            "map_instance_id": map_instance_id,
            "map_event_id": event.event_id,
            "map_state_revision": updated_snapshot.map_state_revision,
            "actor_id": command.actor_id,
            "actor_cell": list(updated_snapshot.actor(command.actor_id).cell),
            "tactical": tactical,
        }
        committed = work.rpg.commit_turn(
            context,
            campaign_id=campaign_id,
            turn_id=turn_id,
            submission_id=command.submission_id,
            interaction_id=_interaction_id(campaign_id, command.submission_id),
            expected_revision=command.expected_campaign_revision,
            command={
                "type": "tactical_move",
                **command.model_dump(mode="json"),
                "map_instance_id": map_instance_id,
            },
            next_state=next_state,
            canonical_effects={
                "map_event": event.model_dump(mode="json"),
                "tactical": tactical,
            },
            interaction_event={
                "type": "tactical_move_committed",
                "map_instance_id": map_instance_id,
                "map_event_id": event.event_id,
                "actor_id": command.actor_id,
                "tactical": tactical,
            },
            compact_response=compact,
            engine_version=str(campaign.get("engine_version") or _TACTICAL_ENGINE_VERSION),
            schema_version=str(campaign.get("schema_version") or _TACTICAL_SCHEMA_VERSION),
        )
        work.commit()
    return {
        **compact,
        "idempotent_replay": False,
        "campaign_revision": committed["campaign"]["revision"],
        "campaign_state_hash": committed["campaign"]["state_hash"],
        "map_event": event.model_dump(mode="json"),
    }


def attack_tactically(
    map_instance_id: str,
    command: TacticalAttackCommand,
    *,
    expected_map_state_revision: int,
    policy: TacticalSpatialPolicy | None = None,
    database: Any | None = None,
) -> dict[str, Any]:
    context = current_tenant()
    with unit_of_work(database) as work:
        instance_hint = work.map_instances.get_instance(context, map_instance_id)
        if instance_hint is None:
            raise KeyError(f"map_instance_not_found:{map_instance_id}")
        campaign_id = str(instance_hint["campaign_id"])
        campaign = work.rpg.get_campaign(context, campaign_id, for_update=True)
        if campaign is None:
            raise KeyError(f"campaign_not_found:{campaign_id}")
        existing = work.rpg.get_turn_by_submission(
            context,
            campaign_id,
            command.submission_id,
        )
        if existing is not None:
            work.rollback()
            return _idempotent_response(existing)
        _require_campaign_revision(campaign, command.expected_campaign_revision)

        stored_instance = work.map_instances.get_instance(
            context,
            map_instance_id,
            for_update=True,
        )
        if stored_instance is None:
            raise KeyError(f"map_instance_not_found:{map_instance_id}")
        definition, snapshot = _load_map(work, context, stored_instance)
        if snapshot.map_state_revision != int(expected_map_state_revision):
            raise TacticalSpatialError(
                "stale_tactical_map_state_revision",
                f"expected={expected_map_state_revision}:current={snapshot.map_state_revision}",
            )
        turn_id = _turn_id(campaign_id, command.submission_id)
        next_state, tactical = resolve_tactical_attack(
            definition,
            snapshot,
            dict(campaign["state"]),
            command,
            policy=policy,
            turn_id=turn_id,
        )
        compact = {
            "ok": True,
            "kind": "tactical_attack",
            "campaign_id": campaign_id,
            "map_instance_id": map_instance_id,
            "map_state_revision": snapshot.map_state_revision,
            "actor_id": command.actor_id,
            "target_id": command.target_id,
            "tactical": tactical,
        }
        committed = work.rpg.commit_turn(
            context,
            campaign_id=campaign_id,
            turn_id=turn_id,
            submission_id=command.submission_id,
            interaction_id=_interaction_id(campaign_id, command.submission_id),
            expected_revision=command.expected_campaign_revision,
            command={
                "type": "tactical_attack",
                **command.model_dump(mode="json"),
                "map_instance_id": map_instance_id,
                "expected_map_state_revision": expected_map_state_revision,
            },
            next_state=next_state,
            canonical_effects={"tactical": tactical},
            interaction_event={
                "type": "tactical_attack_committed",
                "map_instance_id": map_instance_id,
                "actor_id": command.actor_id,
                "target_id": command.target_id,
                "tactical": tactical,
            },
            compact_response=compact,
            engine_version=str(campaign.get("engine_version") or _TACTICAL_ENGINE_VERSION),
            schema_version=str(campaign.get("schema_version") or _TACTICAL_SCHEMA_VERSION),
        )
        work.commit()
    return {
        **compact,
        "idempotent_replay": False,
        "campaign_revision": committed["campaign"]["revision"],
        "campaign_state_hash": committed["campaign"]["state_hash"],
    }


def _load_map(
    work: Any,
    context: Any,
    stored_instance: dict[str, Any],
) -> tuple[GridMapDefinition, CampaignMapInstanceSnapshot]:
    stored_definition = work.map_instances.get_definition(
        context,
        stored_instance["map_id"],
        stored_instance["definition_revision"],
    )
    if stored_definition is None:
        raise KeyError(
            "map_definition_not_found:"
            f"{stored_instance['map_id']}:{stored_instance['definition_revision']}"
        )
    return (
        GridMapDefinition.model_validate(stored_definition["document"]),
        CampaignMapInstanceSnapshot.model_validate(stored_instance["snapshot"]),
    )


def _require_campaign_revision(
    campaign: dict[str, Any],
    expected_revision: int,
) -> None:
    current = int(campaign["revision"])
    if current != int(expected_revision):
        raise TacticalSpatialError(
            "stale_tactical_campaign_revision",
            f"expected={expected_revision}:current={current}",
        )


def _turn_id(campaign_id: str, submission_id: str) -> str:
    return f"turn:{campaign_id}:tactical:{submission_id}"


def _interaction_id(campaign_id: str, submission_id: str) -> str:
    return f"interaction:{campaign_id}:tactical:{submission_id}"


def _idempotent_response(turn: dict[str, Any]) -> dict[str, Any]:
    compact = dict(turn.get("compact_response") or {})
    return {
        **compact,
        "ok": True,
        "idempotent_replay": True,
        "campaign_revision": int(turn["resulting_revision"]),
        "turn_id": str(turn["id"]),
    }
