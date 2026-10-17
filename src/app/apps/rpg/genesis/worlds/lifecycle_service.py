"""Lifecycle operations for reusable RPG world and scenario projects."""
from __future__ import annotations

from typing import Any

from app.security.tenant_context import current_tenant
from app.persistence.unit_of_work import unit_of_work

from .generation_jobs import WORLD_TOPIC_JOB_TYPE


def require_world_writable(work: Any, context: Any, world_id: str) -> dict[str, Any]:
    world = work.world_scenarios.get_world(context, world_id, for_update=True)
    if world is None:
        raise KeyError(f"world_not_found:{world_id}")
    if world["status"] == "archived":
        raise ValueError(f"world_archived:{world_id}")
    return world


def _scenario_row(row: Any) -> dict[str, Any]:
    return {
        "workspace_id": str(row[0]),
        "id": str(row[1]),
        "world_id": str(row[2]),
        "title": str(row[3]),
        "description": str(row[4]),
        "status": str(row[5]),
        "metadata": dict(row[6]),
        "created_at": row[7].isoformat(),
        "updated_at": row[8].isoformat(),
    }


def _get_scenario(
    work: Any,
    context: Any,
    scenario_id: str,
    *,
    for_update: bool = False,
) -> dict[str, Any] | None:
    suffix = " FOR UPDATE" if for_update else ""
    row = work.connection.execute(
        "SELECT workspace_id, id, world_id, title, description, status, "
        "metadata_jsonb, created_at, updated_at FROM omnix_rpg_scenarios "
        "WHERE workspace_id = %s AND id = %s" + suffix,
        (context.workspace_id, scenario_id),
    ).fetchone()
    return _scenario_row(row) if row is not None else None


def require_scenario_writable(
    work: Any,
    context: Any,
    scenario_id: str,
) -> dict[str, Any]:
    scenario = _get_scenario(work, context, scenario_id, for_update=True)
    if scenario is None:
        raise KeyError(f"scenario_not_found:{scenario_id}")
    if scenario["status"] == "archived":
        raise ValueError(f"scenario_archived:{scenario_id}")
    require_world_writable(work, context, str(scenario["world_id"]))
    return scenario


def _count(work: Any, query: str, params: tuple[Any, ...]) -> int:
    row = work.connection.execute(query, params).fetchone()
    return int(row[0]) if row is not None else 0


def _world_deletion_snapshot(
    work: Any,
    context: Any,
    world: dict[str, Any],
) -> dict[str, Any]:
    workspace_id = context.workspace_id
    world_id = str(world["id"])
    params = (workspace_id, world_id)
    counts = {
        "world_revisions": _count(
            work,
            "SELECT COUNT(*) FROM omnix_rpg_world_revisions "
            "WHERE workspace_id = %s AND world_id = %s",
            params,
        ),
        "world_releases": _count(
            work,
            "SELECT COUNT(*) FROM omnix_rpg_world_releases "
            "WHERE workspace_id = %s AND world_id = %s",
            params,
        ),
        "scenario_projects": _count(
            work,
            "SELECT COUNT(*) FROM omnix_rpg_scenarios "
            "WHERE workspace_id = %s AND world_id = %s",
            params,
        ),
        "scenario_revisions": _count(
            work,
            "SELECT COUNT(*) FROM omnix_rpg_scenario_revisions AS revision "
            "JOIN omnix_rpg_scenarios AS scenario "
            "ON scenario.workspace_id = revision.workspace_id "
            "AND scenario.id = revision.scenario_id "
            "WHERE scenario.workspace_id = %s AND scenario.world_id = %s",
            params,
        ),
        "campaign_bindings": _count(
            work,
            "SELECT COUNT(*) FROM omnix_rpg_campaign_world_bindings "
            "WHERE workspace_id = %s AND world_id = %s",
            params,
        ),
        "map_definitions": _count(
            work,
            "SELECT COUNT(*) FROM omnix_rpg_map_definitions "
            "WHERE workspace_id = %s AND world_id = %s",
            params,
        ),
        "active_generation_runs": _count(
            work,
            "SELECT COUNT(*) FROM omnix_rpg_world_generation_runs "
            "WHERE workspace_id = %s AND world_id = %s "
            "AND status IN ('planned', 'running')",
            params,
        ),
        "active_image_targets": _count(
            work,
            "SELECT COUNT(*) FROM omnix_rpg_world_image_targets "
            "WHERE workspace_id = %s AND world_id = %s "
            "AND status IN ('queued', 'generating')",
            params,
        ),
        "topics": _count(
            work,
            "SELECT COUNT(*) FROM omnix_rpg_world_topics "
            "WHERE workspace_id = %s AND world_id = %s",
            params,
        ),
        "generation_runs": _count(
            work,
            "SELECT COUNT(*) FROM omnix_rpg_world_generation_runs "
            "WHERE workspace_id = %s AND world_id = %s",
            params,
        ),
        "topic_history": _count(
            work,
            "SELECT COUNT(*) FROM omnix_rpg_world_topic_history "
            "WHERE workspace_id = %s AND world_id = %s",
            params,
        ),
        "entity_history": _count(
            work,
            "SELECT COUNT(*) FROM omnix_rpg_world_entity_history "
            "WHERE workspace_id = %s AND world_id = %s",
            params,
        ),
        "map_blueprints": _count(
            work,
            "SELECT COUNT(*) FROM omnix_rpg_map_blueprint_revisions "
            "WHERE workspace_id = %s AND world_id = %s",
            params,
        ),
        "image_targets": _count(
            work,
            "SELECT COUNT(*) FROM omnix_rpg_world_image_targets "
            "WHERE workspace_id = %s AND world_id = %s",
            params,
        ),
        "image_attempts": _count(
            work,
            "SELECT COUNT(*) FROM omnix_rpg_world_image_attempts "
            "WHERE workspace_id = %s AND world_id = %s",
            params,
        ),
    }
    deleted_counts = {
        key: counts[key]
        for key in (
            "world_revisions",
            "world_releases",
            "scenario_projects",
            "scenario_revisions",
            "campaign_bindings",
            "map_definitions",
            "topics",
            "generation_runs",
            "topic_history",
            "entity_history",
            "map_blueprints",
            "image_targets",
            "image_attempts",
        )
    }
    return {
        "can_delete": True,
        "world_id": world_id,
        "world_title": str(world["title"]),
        "world_status": str(world["status"]),
        "blockers": [],
        "deleted_counts": deleted_counts,
    }


def world_deletion_eligibility(
    world_id: str,
    *,
    database: Any | None = None,
) -> dict[str, Any]:
    context = current_tenant()
    with unit_of_work(database) as work:
        world = work.world_scenarios.get_world(context, world_id)
        if world is None:
            raise KeyError(f"world_not_found:{world_id}")
        eligibility = _world_deletion_snapshot(work, context, world)
        work.rollback()
    return {"ok": True, "eligibility": eligibility}


def delete_world_project(
    world_id: str,
    *,
    confirmation_title: str,
    acknowledge_permanent: bool,
    database: Any | None = None,
) -> dict[str, Any]:
    if not acknowledge_permanent:
        raise ValueError("world_delete_acknowledgement_required")
    context = current_tenant()
    with unit_of_work(database) as work:
        world = work.world_scenarios.get_world(context, world_id, for_update=True)
        if world is None:
            raise KeyError(f"world_not_found:{world_id}")
        if confirmation_title != str(world["title"]):
            raise ValueError("world_delete_confirmation_mismatch")
        eligibility = _world_deletion_snapshot(work, context, world)

        work.connection.execute(
            "UPDATE omnix_rpg_world_generation_runs SET parent_run_id = NULL "
            "WHERE workspace_id = %s AND parent_run_id IN ("
            "SELECT run_id FROM omnix_rpg_world_generation_runs "
            "WHERE workspace_id = %s AND world_id = %s)",
            (context.workspace_id, context.workspace_id, world_id),
        )
        work.connection.execute(
            "UPDATE omnix_rpg_world_generation_runs SET parent_run_id = NULL "
            "WHERE workspace_id = %s AND world_id = %s",
            (context.workspace_id, world_id),
        )
        work.jobs.delete_jobs(
            context,
            job_type=WORLD_TOPIC_JOB_TYPE,
            input_fields=(("world_id", world_id),),
        )
        work.jobs.delete_jobs(
            context,
            job_type=WORLD_TOPIC_JOB_TYPE,
            metadata_fields=(("world_id", world_id),),
        )
        work.connection.execute(
            "DELETE FROM omnix_rpg_campaigns WHERE workspace_id = %s AND id IN ("
            "SELECT campaign_id FROM omnix_rpg_campaign_world_bindings "
            "WHERE workspace_id = %s AND world_id = %s)",
            (context.workspace_id, context.workspace_id, world_id),
        )
        work.connection.execute(
            "DELETE FROM omnix_rpg_campaign_map_instances WHERE workspace_id = %s "
            "AND (map_id, map_definition_revision) IN ("
            "SELECT map_id, definition_revision FROM omnix_rpg_map_definitions "
            "WHERE workspace_id = %s AND world_id = %s)",
            (context.workspace_id, context.workspace_id, world_id),
        )
        work.connection.execute(
            "DELETE FROM omnix_rpg_map_definitions "
            "WHERE workspace_id = %s AND world_id = %s",
            (context.workspace_id, world_id),
        )
        work.connection.execute(
            "DELETE FROM omnix_rpg_scenario_revisions "
            "WHERE workspace_id = %s AND world_id = %s",
            (context.workspace_id, world_id),
        )
        work.connection.execute(
            "DELETE FROM omnix_rpg_scenarios "
            "WHERE workspace_id = %s AND world_id = %s",
            (context.workspace_id, world_id),
        )
        work.connection.execute(
            "DELETE FROM omnix_rpg_world_releases "
            "WHERE workspace_id = %s AND world_id = %s",
            (context.workspace_id, world_id),
        )
        work.connection.execute(
            "DELETE FROM omnix_rpg_world_revisions "
            "WHERE workspace_id = %s AND world_id = %s",
            (context.workspace_id, world_id),
        )
        audit_event_id = work.audit.append(
            context,
            aggregate_type="rpg_world_project",
            aggregate_id=world_id,
            action="permanently_deleted",
            payload={
                "title": str(world["title"]),
                "status": str(world["status"]),
                "draft_revision": int(world["draft_revision"]),
                "deleted_counts": eligibility["deleted_counts"],
                "decision": "explicit_typed_confirmation",
            },
        )
        deleted = work.connection.execute(
            "DELETE FROM omnix_rpg_worlds "
            "WHERE workspace_id = %s AND id = %s RETURNING id",
            (context.workspace_id, world_id),
        ).fetchone()
        if deleted is None:
            raise KeyError(f"world_not_found:{world_id}")
        work.commit()
    return {
        "ok": True,
        "deleted": True,
        "world_id": world_id,
        "world_title": str(world["title"]),
        "deleted_counts": eligibility["deleted_counts"],
        "audit_event_id": audit_event_id,
    }


def archive_world_project(
    world_id: str,
    *,
    database: Any | None = None,
) -> dict[str, Any]:
    context = current_tenant()
    with unit_of_work(database) as work:
        world = work.world_scenarios.get_world(context, world_id, for_update=True)
        if world is None:
            raise KeyError(f"world_not_found:{world_id}")
        if world["status"] == "archived":
            work.rollback()
            return {"ok": True, "world": world, "idempotent": True}
        active = work.connection.execute(
            "SELECT run_id FROM omnix_rpg_world_generation_runs "
            "WHERE workspace_id = %s AND world_id = %s "
            "AND status IN ('planned', 'running') ORDER BY created_at LIMIT 1",
            (context.workspace_id, world_id),
        ).fetchone()
        if active is not None:
            raise ValueError(f"world_generation_active:{world_id}:{active[0]}")
        work.connection.execute(
            "UPDATE omnix_rpg_worlds SET status = 'archived', "
            "updated_at = CURRENT_TIMESTAMP WHERE workspace_id = %s AND id = %s",
            (context.workspace_id, world_id),
        )
        archived = work.world_scenarios.get_world(context, world_id)
        work.commit()
    return {"ok": True, "world": archived, "idempotent": False}


def restore_world_project(
    world_id: str,
    *,
    database: Any | None = None,
) -> dict[str, Any]:
    context = current_tenant()
    with unit_of_work(database) as work:
        world = work.world_scenarios.get_world(context, world_id, for_update=True)
        if world is None:
            raise KeyError(f"world_not_found:{world_id}")
        if world["status"] != "archived":
            work.rollback()
            return {"ok": True, "world": world, "idempotent": True}
        published = work.connection.execute(
            "SELECT EXISTS(SELECT 1 FROM omnix_rpg_world_revisions "
            "WHERE workspace_id = %s AND world_id = %s)",
            (context.workspace_id, world_id),
        ).fetchone()[0]
        status = "published" if bool(published) else "draft"
        work.connection.execute(
            "UPDATE omnix_rpg_worlds SET status = %s, updated_at = CURRENT_TIMESTAMP "
            "WHERE workspace_id = %s AND id = %s",
            (status, context.workspace_id, world_id),
        )
        restored = work.world_scenarios.get_world(context, world_id)
        work.commit()
    return {"ok": True, "world": restored, "idempotent": False}


def archive_scenario_project(
    scenario_id: str,
    *,
    database: Any | None = None,
) -> dict[str, Any]:
    context = current_tenant()
    with unit_of_work(database) as work:
        scenario = _get_scenario(work, context, scenario_id, for_update=True)
        if scenario is None:
            raise KeyError(f"scenario_not_found:{scenario_id}")
        if scenario["status"] == "archived":
            work.rollback()
            return {"ok": True, "scenario": scenario, "idempotent": True}
        work.connection.execute(
            "UPDATE omnix_rpg_scenarios SET status = 'archived', "
            "updated_at = CURRENT_TIMESTAMP WHERE workspace_id = %s AND id = %s",
            (context.workspace_id, scenario_id),
        )
        archived = _get_scenario(work, context, scenario_id)
        work.commit()
    return {"ok": True, "scenario": archived, "idempotent": False}


def restore_scenario_project(
    scenario_id: str,
    *,
    database: Any | None = None,
) -> dict[str, Any]:
    context = current_tenant()
    with unit_of_work(database) as work:
        scenario = _get_scenario(work, context, scenario_id, for_update=True)
        if scenario is None:
            raise KeyError(f"scenario_not_found:{scenario_id}")
        if scenario["status"] != "archived":
            work.rollback()
            return {"ok": True, "scenario": scenario, "idempotent": True}
        require_world_writable(work, context, str(scenario["world_id"]))
        published = work.connection.execute(
            "SELECT EXISTS(SELECT 1 FROM omnix_rpg_scenario_revisions "
            "WHERE workspace_id = %s AND scenario_id = %s)",
            (context.workspace_id, scenario_id),
        ).fetchone()[0]
        status = "published" if bool(published) else "draft"
        work.connection.execute(
            "UPDATE omnix_rpg_scenarios SET status = %s, updated_at = CURRENT_TIMESTAMP "
            "WHERE workspace_id = %s AND id = %s",
            (status, context.workspace_id, scenario_id),
        )
        restored = _get_scenario(work, context, scenario_id)
        work.commit()
    return {"ok": True, "scenario": restored, "idempotent": False}
