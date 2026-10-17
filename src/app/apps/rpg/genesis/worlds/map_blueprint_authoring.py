"""Revisioned semantic map-blueprint authoring and scenario reconciliation."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.security.tenant_context import current_tenant
from app.persistence.unit_of_work import unit_of_work

from .contracts import ScenarioRevisionDocument, canonical_content_hash
from .lifecycle_service import require_world_writable

MapLevel = Literal["settlement", "dungeon", "interior", "encounter"]


class FrozenBlueprintModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MapBlueprintDocument(FrozenBlueprintModel):
    schema_version: Literal["rpg_map_blueprint_v1"] = "rpg_map_blueprint_v1"
    map_id: str = Field(min_length=1)
    location_id: str = Field(min_length=1)
    level: MapLevel
    navigation_kind: Literal["square_grid"] = "square_grid"
    required_portal_ids: tuple[str, ...] = ()
    required_route_ids: tuple[str, ...] = ()
    required_spawn_point_ids: tuple[str, ...] = ()
    required_zone_ids: tuple[str, ...] = ()
    required_object_ids: tuple[str, ...] = ()
    required_hazard_ids: tuple[str, ...] = ()
    size_profile: str = "medium"
    directives: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def unique_semantic_ids(self) -> "MapBlueprintDocument":
        fields = (
            "required_portal_ids",
            "required_route_ids",
            "required_spawn_point_ids",
            "required_zone_ids",
            "required_object_ids",
            "required_hazard_ids",
        )
        for field in fields:
            values = getattr(self, field)
            if len(values) != len(set(values)):
                raise ValueError(f"duplicate_map_blueprint_id:{field}")
            if any(not value.strip() for value in values):
                raise ValueError(f"blank_map_blueprint_id:{field}")
        return self

    def semantic_interface(self) -> dict[str, Any]:
        return {
            "map_id": self.map_id,
            "location_id": self.location_id,
            "level": self.level,
            "navigation_kind": self.navigation_kind,
            "portal_ids": sorted(self.required_portal_ids),
            "route_ids": sorted(self.required_route_ids),
            "spawn_point_ids": sorted(self.required_spawn_point_ids),
            "zone_ids": sorted(self.required_zone_ids),
            "object_ids": sorted(self.required_object_ids),
            "hazard_ids": sorted(self.required_hazard_ids),
        }

    def requirement(
        self,
        *,
        blueprint_revision: int,
        content_hash: str,
        semantic_interface_hash: str,
    ) -> dict[str, Any]:
        return {
            "map_id": self.map_id,
            "location_id": self.location_id,
            "level": self.level,
            "navigation_kind": self.navigation_kind,
            "blueprint_revision": int(blueprint_revision),
            "blueprint_hash": content_hash,
            "semantic_interface_hash": semantic_interface_hash,
            "required_portal_ids": list(self.required_portal_ids),
            "required_route_ids": list(self.required_route_ids),
            "required_spawn_point_ids": list(self.required_spawn_point_ids),
            "required_zone_ids": list(self.required_zone_ids),
            "required_object_ids": list(self.required_object_ids),
            "required_hazard_ids": list(self.required_hazard_ids),
            "size_profile": self.size_profile,
            "directives": dict(self.directives),
            "metadata": dict(self.metadata),
            "simulation_readiness": "semantic",
            "presentation_readiness": "placeholder",
        }


def _row(row: Any) -> dict[str, Any]:
    return {
        "world_id": str(row[0]),
        "map_id": str(row[1]),
        "blueprint_revision": int(row[2]),
        "document": dict(row[3]),
        "content_hash": str(row[4]),
        "semantic_interface_hash": str(row[5]),
        "status": str(row[6]),
        "findings": list(row[7]),
        "created_at": row[8].isoformat(),
    }


def _latest_scenarios(work: Any, context: Any, world_id: str) -> list[ScenarioRevisionDocument]:
    rows = work.connection.execute(
        "SELECT DISTINCT ON (revisions.scenario_id) revisions.document_jsonb "
        "FROM omnix_rpg_scenario_revisions AS revisions "
        "JOIN omnix_rpg_scenarios AS scenarios "
        "ON scenarios.workspace_id = revisions.workspace_id "
        "AND scenarios.id = revisions.scenario_id "
        "WHERE revisions.workspace_id = %s AND revisions.world_id = %s "
        "AND scenarios.status <> 'archived' "
        "ORDER BY revisions.scenario_id, revisions.revision DESC",
        (context.workspace_id, world_id),
    ).fetchall()
    return [ScenarioRevisionDocument.model_validate(row[0]) for row in rows]


def _finding(
    *,
    code: str,
    scenario: ScenarioRevisionDocument,
    target_id: str,
    operation_id: str = "",
) -> dict[str, Any]:
    return {
        "code": code,
        "scenario_id": scenario.scenario_id,
        "scenario_revision": scenario.revision,
        "operation_id": operation_id,
        "target_id": target_id,
    }


def reconcile_blueprint_scenarios(
    document: MapBlueprintDocument,
    scenarios: Sequence[ScenarioRevisionDocument],
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    spawn_ids = set(document.required_spawn_point_ids)
    route_ids = set(document.required_route_ids) | set(document.required_portal_ids)
    object_ids = set(document.required_object_ids)
    hazard_ids = set(document.required_hazard_ids)
    for scenario in scenarios:
        if scenario.starting_location_id == document.location_id and not spawn_ids:
            findings.append(
                _finding(
                    code="starting_location_spawn_required",
                    scenario=scenario,
                    target_id=document.location_id,
                )
            )
        for operation in scenario.map_initialization:
            if operation.map_id != document.map_id:
                continue
            if operation.type == "place_actor":
                spawn_id = str(operation.payload.get("spawn_point_id") or "")
                if spawn_id and spawn_id not in spawn_ids:
                    findings.append(
                        _finding(
                            code="scenario_spawn_missing",
                            scenario=scenario,
                            operation_id=operation.operation_id,
                            target_id=spawn_id,
                        )
                    )
            elif operation.type == "set_route_state" and operation.target_id not in route_ids:
                findings.append(
                    _finding(
                        code="scenario_route_missing",
                        scenario=scenario,
                        operation_id=operation.operation_id,
                        target_id=operation.target_id,
                    )
                )
            elif operation.type == "set_object_state" and operation.target_id not in object_ids:
                findings.append(
                    _finding(
                        code="scenario_object_missing",
                        scenario=scenario,
                        operation_id=operation.operation_id,
                        target_id=operation.target_id,
                    )
                )
            elif operation.type == "set_hazard_state" and operation.target_id not in hazard_ids:
                findings.append(
                    _finding(
                        code="scenario_hazard_missing",
                        scenario=scenario,
                        operation_id=operation.operation_id,
                        target_id=operation.target_id,
                    )
                )
    return sorted(
        findings,
        key=lambda item: (
            str(item["scenario_id"]),
            int(item["scenario_revision"]),
            str(item["operation_id"]),
            str(item["code"]),
            str(item["target_id"]),
        ),
    )


def generated_location_blueprint_documents(
    locations: Mapping[str, Mapping[str, Any]],
) -> tuple[MapBlueprintDocument, ...]:
    """Build the safe semantic baseline for generated world locations."""

    documents: list[MapBlueprintDocument] = []
    for location_id, entity in sorted(locations.items()):
        identifier = str(location_id).strip()
        if not identifier:
            continue
        description = " ".join(
            str(entity.get(key) or "")
            for key in ("name", "title", "description", "summary", "kind")
        ).casefold()
        level: MapLevel = (
            "dungeon"
            if any(token in description for token in ("dungeon", "vault", "catacomb"))
            else "interior"
            if any(token in description for token in ("interior", "building", "facility"))
            else "encounter"
            if any(token in description for token in ("battlefield", "encounter", "ambush"))
            else "settlement"
        )
        documents.append(
            MapBlueprintDocument(
                map_id=f"map:{identifier}",
                location_id=identifier,
                level=level,
                required_spawn_point_ids=("spawn:arrival",),
                required_zone_ids=("zone:main",),
                directives={"generation": "baseline_location_blueprint"},
                metadata={
                    "source": "generated_location_blueprint_v1",
                    "entity_name": str(entity.get("name") or entity.get("title") or identifier),
                },
            )
        )
    return tuple(documents)


def _insert_blueprint(
    work: Any,
    context: Any,
    world_id: str,
    document: MapBlueprintDocument,
    *,
    revision: int,
) -> dict[str, Any]:
    payload = document.model_dump(mode="json")
    content_hash = canonical_content_hash(payload)
    semantic_hash = canonical_content_hash(document.semantic_interface())
    findings = reconcile_blueprint_scenarios(
        document,
        _latest_scenarios(work, context, world_id),
    )
    row = work.connection.execute(
        "INSERT INTO omnix_rpg_map_blueprint_revisions ("
        "workspace_id, world_id, map_id, blueprint_revision, document_jsonb, "
        "content_hash, semantic_interface_hash, status, findings_jsonb) "
        "VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s::jsonb) "
        "RETURNING world_id, map_id, blueprint_revision, document_jsonb, "
        "content_hash, semantic_interface_hash, status, findings_jsonb, created_at",
        (
            context.workspace_id,
            world_id,
            document.map_id,
            revision,
            _json(payload),
            content_hash,
            semantic_hash,
            "invalid" if findings else "ready",
            _json(findings),
        ),
    ).fetchone()
    return _row(row)


def materialize_generated_location_blueprints(
    work: Any,
    context: Any,
    world_id: str,
    locations: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Insert one baseline blueprint per generated location, without overwriting edits."""

    documents = generated_location_blueprint_documents(locations)
    rows = work.connection.execute(
        "SELECT map_id FROM omnix_rpg_map_blueprint_revisions "
        "WHERE workspace_id = %s AND world_id = %s",
        (context.workspace_id, world_id),
    ).fetchall()
    existing_map_ids = {str(row[0]) for row in rows}
    created = []
    for document in documents:
        if document.map_id in existing_map_ids:
            continue
        created.append(
            _insert_blueprint(
                work,
                context,
                world_id,
                document,
                revision=1,
            )
        )
    return created


def materialize_missing_location_blueprints(
    world_id: str,
    *,
    database: Any | None = None,
) -> dict[str, Any]:
    """Backfill baseline blueprints for an already-generated world in one action."""

    from .library_service import read_world_detail

    detail = read_world_detail(world_id, database=database)
    locations: dict[str, Mapping[str, Any]] = {}
    for topic in detail.get("topics") or ():
        if not isinstance(topic, Mapping) or str(topic.get("topic_id") or "") != "locations":
            continue
        content = topic.get("content")
        if not isinstance(content, Mapping):
            continue
        entities = content.get("entities")
        if not isinstance(entities, Sequence) or isinstance(entities, (str, bytes)):
            continue
        for entity in entities:
            if not isinstance(entity, Mapping):
                continue
            identifier = str(entity.get("id") or entity.get("entity_id") or "").strip()
            if identifier:
                locations[identifier] = entity
    context = current_tenant()
    with unit_of_work(database) as work:
        require_world_writable(work, context, world_id)
        created = materialize_generated_location_blueprints(
            work,
            context,
            world_id,
            locations,
        )
        work.commit()
    return {"ok": True, "created": created, "created_count": len(created)}


def save_map_blueprint(
    world_id: str,
    document: MapBlueprintDocument,
    *,
    expected_revision: int,
    database: Any | None = None,
) -> dict[str, Any]:
    context = current_tenant()
    with unit_of_work(database) as work:
        require_world_writable(work, context, world_id)
        current = work.connection.execute(
            "SELECT COALESCE(MAX(blueprint_revision), 0) "
            "FROM omnix_rpg_map_blueprint_revisions "
            "WHERE workspace_id = %s AND world_id = %s AND map_id = %s",
            (context.workspace_id, world_id, document.map_id),
        ).fetchone()
        current_revision = int(current[0])
        if current_revision != int(expected_revision):
            raise ValueError(
                "map_blueprint_revision_conflict:"
                f"expected={expected_revision}:current={current_revision}"
            )
        row = _insert_blueprint(
            work,
            context,
            world_id,
            document,
            revision=current_revision + 1,
        )
        work.commit()
    return {"ok": True, "map_blueprint": row}


def list_map_blueprints(
    world_id: str,
    *,
    latest_only: bool = True,
    database: Any | None = None,
) -> list[dict[str, Any]]:
    context = current_tenant()
    with unit_of_work(database) as work:
        if work.world_scenarios.get_world(context, world_id) is None:
            raise KeyError(f"world_not_found:{world_id}")
        if latest_only:
            sql = (
                "SELECT DISTINCT ON (map_id) world_id, map_id, blueprint_revision, "
                "document_jsonb, content_hash, semantic_interface_hash, status, "
                "findings_jsonb, created_at FROM omnix_rpg_map_blueprint_revisions "
                "WHERE workspace_id = %s AND world_id = %s "
                "ORDER BY map_id, blueprint_revision DESC"
            )
        else:
            sql = (
                "SELECT world_id, map_id, blueprint_revision, document_jsonb, "
                "content_hash, semantic_interface_hash, status, findings_jsonb, "
                "created_at FROM omnix_rpg_map_blueprint_revisions "
                "WHERE workspace_id = %s AND world_id = %s "
                "ORDER BY map_id, blueprint_revision DESC"
            )
        rows = work.connection.execute(
            sql,
            (context.workspace_id, world_id),
        ).fetchall()
        work.rollback()
    return [_row(row) for row in rows]


def latest_ready_blueprint_requirements(
    work: Any,
    context: Any,
    world_id: str,
) -> tuple[dict[str, Any], ...]:
    rows = work.connection.execute(
        "SELECT DISTINCT ON (map_id) map_id, blueprint_revision, document_jsonb, "
        "content_hash, semantic_interface_hash FROM omnix_rpg_map_blueprint_revisions "
        "WHERE workspace_id = %s AND world_id = %s AND status = 'ready' "
        "ORDER BY map_id, blueprint_revision DESC",
        (context.workspace_id, world_id),
    ).fetchall()
    requirements = []
    for row in rows:
        document = MapBlueprintDocument.model_validate(row[2])
        requirements.append(
            document.requirement(
                blueprint_revision=int(row[1]),
                content_hash=str(row[3]),
                semantic_interface_hash=str(row[4]),
            )
        )
    return tuple(sorted(requirements, key=lambda item: str(item["map_id"])))


def _json(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
