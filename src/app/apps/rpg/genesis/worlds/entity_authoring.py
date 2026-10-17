"""Entity-level editing and regeneration for reusable RPG world topics."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
from typing import Any, Mapping

from app.security.tenant_context import current_tenant
from app.persistence.unit_of_work import unit_of_work
from app.apps.rpg.genesis.forge.world_forge_contract import CampaignTopicNode
from app.apps.rpg.genesis.forge.world_forge_generation import GeneratedTopic, WorldForgeTopicGenerator

from .generation_jobs import canonical_hash
from .targeted_generation_authorship import (
    attach_targeted_regeneration_authorship,
    trusted_targeted_generator,
)
from .topic_authoring import (
    _assert_writable_topic,
    _authoring,
    _graph_nodes,
    _latest_run,
    _mark_downstream_stale,
    _record,
)

_COLLECTIONS = (
    "documents",
    "facts",
    "relationships",
    "knowledge_rules",
    "story_threads",
)
_REFERENCE_EXCLUSIONS = {
    "id",
    "entity_id",
    "world_id",
    "topic_id",
    "document_id",
    "fact_id",
    "relationship_id",
    "schema_version",
    "voice_id",
    "model_id",
    "provider_id",
    "asset_id",
    "active_asset_id",
    "avatar_asset_id",
    "image_target_id",
}


def _entities(content: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [dict(row) for row in content.get("entities") or () if isinstance(row, Mapping)]


def _entity(content: Mapping[str, Any], entity_id: str) -> dict[str, Any]:
    for row in _entities(content):
        if str(row.get("id") or row.get("entity_id") or "") == entity_id:
            return row
    raise KeyError(f"world_entity_not_found:{entity_id}")


def _walk_ids(value: Any, output: set[str]) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in {"id", "entity_id", "document_id", "fact_id", "relationship_id"}:
                candidate = str(item or "").strip()
                if candidate:
                    output.add(candidate)
            _walk_ids(item, output)
    elif isinstance(value, list):
        for item in value:
            _walk_ids(item, output)


def _known_ids(topic_rows: list[Mapping[str, Any]], world_id: str) -> set[str]:
    result = {world_id}
    for row in topic_rows:
        _walk_ids(_record(row.get("content")), result)
    return result


def _reference_pairs(value: Any, path: str = "") -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    if not isinstance(value, Mapping):
        return pairs
    for key, item in value.items():
        field = str(key)
        field_path = f"{path}.{field}" if path else field
        is_reference = (
            field not in _REFERENCE_EXCLUSIONS
            and not field.endswith("asset_id")
            and not field.endswith("asset_ids")
            and (
                field.endswith("_id")
                or field.endswith("_ids")
                or field in {"known_facts", "entity_refs"}
            )
        )
        if is_reference:
            values = item if isinstance(item, list) else [item]
            pairs.extend((field_path, str(candidate)) for candidate in values if str(candidate or ""))
        if isinstance(item, Mapping):
            pairs.extend(_reference_pairs(item, field_path))
    return pairs


def validate_entity_references(entity: Mapping[str, Any], known_ids: set[str]) -> None:
    for field, reference_id in _reference_pairs(entity):
        if ":" not in reference_id or reference_id.endswith(":pending"):
            continue
        if reference_id not in known_ids:
            raise ValueError(
                f"world_entity_dangling_reference:{field}:{reference_id}"
            )


def _references(value: Any, entity_id: str) -> bool:
    if isinstance(value, Mapping):
        return any(_references(item, entity_id) for item in value.values())
    if isinstance(value, list):
        return any(_references(item, entity_id) for item in value)
    return str(value or "") == entity_id


def _rewrite(value: Any, source_id: str, target_id: str) -> Any:
    if isinstance(value, Mapping):
        return {key: _rewrite(item, source_id, target_id) for key, item in value.items()}
    if isinstance(value, list):
        return [_rewrite(item, source_id, target_id) for item in value]
    return target_id if str(value or "") == source_id else value


def replace_entity_content(
    content: Mapping[str, Any],
    entity_id: str,
    replacement: Mapping[str, Any],
    *,
    generated: GeneratedTopic | None = None,
) -> dict[str, Any]:
    payload = dict(content)
    rows = _entities(content)
    found = False
    updated: list[dict[str, Any]] = []
    for row in rows:
        row_id = str(row.get("id") or row.get("entity_id") or "")
        if row_id == entity_id:
            updated.append(dict(replacement))
            found = True
        else:
            updated.append(row)
    if not found:
        raise KeyError(f"world_entity_not_found:{entity_id}")
    payload["entities"] = updated
    if generated is None:
        return payload

    generated_id = str((generated.entities[0] if generated.entities else {}).get("id") or entity_id)
    generated_payload = generated.as_dict()
    for collection in _COLLECTIONS:
        current = [dict(row) for row in payload.get(collection) or () if isinstance(row, Mapping)]
        current = [row for row in current if not _references(row, entity_id)]
        additions = [
            _rewrite(dict(row), generated_id, entity_id)
            for row in generated_payload.get(collection) or ()
            if isinstance(row, Mapping)
        ]
        payload[collection] = [*current, *additions]
    return payload


def _mark_sibling_entities_stale(
    content: Mapping[str, Any],
    changed_entity_id: str,
) -> tuple[dict[str, Any], list[str]]:
    payload = dict(content)
    stale: list[str] = []
    entities: list[dict[str, Any]] = []
    for row in _entities(content):
        row_id = str(row.get("id") or "")
        if row_id != changed_entity_id and _references(row, changed_entity_id):
            row["authoring_status"] = "stale"
            row["stale_reason"] = {"dependency_entity_id": changed_entity_id}
            stale.append(row_id)
        entities.append(row)
    payload["entities"] = entities
    return payload, stale


def _mark_external_entity_dependents_stale(
    work: Any,
    context: Any,
    *,
    world: Mapping[str, Any],
    topic_id: str,
    entity_id: str,
) -> tuple[list[str], list[str]]:
    stale_topics: list[str] = []
    stale_entities: list[str] = []
    rows = work.world_generation.list_topics(
        context,
        world_id=str(world["id"]),
        draft_revision=int(world["draft_revision"]),
    )
    for row in rows:
        candidate_topic_id = str(row.get("topic_id") or "")
        if candidate_topic_id == topic_id or bool(_authoring(row).get("generation_lock")):
            continue
        content, entity_ids = _mark_sibling_entities_stale(
            _record(row.get("content")), entity_id
        )
        if not entity_ids:
            continue
        provenance = _record(row.get("provenance"))
        provenance["authoring"] = {
            **_authoring(row),
            "stale_entity_ids": sorted(
                set(_authoring(row).get("stale_entity_ids") or ()).union(entity_ids)
            ),
            "stale_reason": {"dependency_entity_id": entity_id},
        }
        work.world_scenarios.put_topic(
            context,
            world_id=str(world["id"]),
            topic_id=candidate_topic_id,
            draft_revision=int(world["draft_revision"]),
            source=str(row.get("source") or "ai"),
            status="stale",
            content=content,
            directives=_record(row.get("directives")),
            dependency_hashes=_record(row.get("dependency_hashes")),
            input_hash=str(row.get("input_hash") or ""),
            content_hash=canonical_hash(content),
            provenance=provenance,
        )
        stale_topics.append(candidate_topic_id)
        stale_entities.extend(entity_ids)
    return stale_topics, stale_entities


def _record_history(
    work: Any,
    context: Any,
    *,
    world_id: str,
    topic_id: str,
    entity_id: str,
    operation: str,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    topic_content_hash: str,
    metadata: Mapping[str, Any],
) -> None:
    work.connection.execute(
        "INSERT INTO omnix_rpg_world_entity_history (workspace_id, world_id, "
        "topic_id, entity_id, operation, before_jsonb, after_jsonb, "
        "topic_content_hash, metadata_jsonb) VALUES "
        "(%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s::jsonb)",
        (
            context.workspace_id,
            world_id,
            topic_id,
            entity_id,
            operation,
            json.dumps(dict(before), sort_keys=True),
            json.dumps(dict(after), sort_keys=True),
            topic_content_hash,
            json.dumps(dict(metadata), sort_keys=True),
        ),
    )


def _history(work: Any, context: Any, world_id: str, topic_id: str, entity_id: str) -> list[dict[str, Any]]:
    rows = work.connection.execute(
        "SELECT history_sequence, operation, before_jsonb, after_jsonb, "
        "topic_content_hash, metadata_jsonb, created_at FROM "
        "omnix_rpg_world_entity_history WHERE workspace_id = %s AND world_id = %s "
        "AND topic_id = %s AND entity_id = %s ORDER BY history_sequence DESC",
        (context.workspace_id, world_id, topic_id, entity_id),
    ).fetchall()
    return [
        {
            "history_sequence": int(row[0]),
            "operation": str(row[1]),
            "before": dict(row[2]),
            "after": dict(row[3]),
            "topic_content_hash": str(row[4]),
            "metadata": dict(row[5]),
            "created_at": row[6].isoformat(),
        }
        for row in rows
    ]


def read_world_entity(world_id: str, topic_id: str, entity_id: str, *, database: Any | None = None) -> dict[str, Any]:
    context = current_tenant()
    with unit_of_work(database) as work:
        world = work.world_scenarios.get_world(context, world_id)
        topic = work.world_generation.get_topic(context, world_id=world_id, topic_id=topic_id)
        if world is None:
            raise KeyError(f"world_not_found:{world_id}")
        if topic is None:
            raise KeyError(f"world_topic_not_found:{world_id}:{topic_id}")
        entity = _entity(_record(topic.get("content")), entity_id)
        history = _history(work, context, world_id, topic_id, entity_id)
        work.rollback()
    return {"ok": True, "world": world, "topic": topic, "entity": entity, "history": history}


def _store_replacement(
    world_id: str,
    topic_id: str,
    entity_id: str,
    *,
    expected_draft_revision: int,
    expected_content_hash: str,
    replacement: Mapping[str, Any],
    operation: str,
    generated: GeneratedTopic | None = None,
    metadata: Mapping[str, Any] | None = None,
    database: Any | None = None,
) -> dict[str, Any]:
    context = current_tenant()
    with unit_of_work(database) as work:
        world, current = _assert_writable_topic(
            work,
            context,
            world_id=world_id,
            topic_id=topic_id,
            expected_draft_revision=expected_draft_revision,
            expected_content_hash=expected_content_hash,
        )
        prior_payload = _record(current.get("content"))
        before = _entity(prior_payload, entity_id)
        after = {**before, **dict(replacement), "id": entity_id}
        topic_rows = work.world_generation.list_topics(
            context,
            world_id=world_id,
            draft_revision=int(world["draft_revision"]),
        )
        validate_entity_references(after, _known_ids(topic_rows, world_id).union({entity_id}))
        payload = replace_entity_content(
            prior_payload, entity_id, after, generated=generated
        )
        payload, stale_siblings = _mark_sibling_entities_stale(payload, entity_id)
        changed_lore_paths: tuple[str, ...] = ()
        if generated is not None:
            payload, changed_lore_paths = attach_targeted_regeneration_authorship(
                prior_payload,
                payload,
                generated,
                topic_id=topic_id,
                operation=operation,
            )
            source = "ai"
            edit_state = "llm_regenerated"
            provenance = _record(payload.get("provenance"))
        else:
            source = "manual"
            edit_state = "manually_edited"
            provenance = _record(current.get("provenance"))
        provenance["authoring"] = {
            **_authoring(current),
            "edit_state": edit_state,
            "generation_lock": True,
            "last_entity_edit": {
                "entity_id": entity_id,
                "operation": operation,
                "edited_at": datetime.now(timezone.utc).isoformat(),
                "llm_changed_paths": list(changed_lore_paths),
            },
            "stale_entity_ids": sorted(stale_siblings),
        }
        stored = work.world_scenarios.put_topic(
            context,
            world_id=world_id,
            topic_id=topic_id,
            draft_revision=int(world["draft_revision"]),
            source=source,
            status="ready",
            content=payload,
            directives=_record(current.get("directives")),
            dependency_hashes=_record(current.get("dependency_hashes")),
            input_hash=canonical_hash({"source": operation, "content": payload}),
            content_hash=canonical_hash(payload),
            provenance=provenance,
        )
        stored_hash = str(stored.get("content_hash") or canonical_hash(payload))
        _record_history(
            work,
            context,
            world_id=world_id,
            topic_id=topic_id,
            entity_id=entity_id,
            operation=operation,
            before=before,
            after=after,
            topic_content_hash=stored_hash,
            metadata={
                **dict(metadata or {}),
                "llm_changed_paths": list(changed_lore_paths),
            },
        )
        direct_topics, direct_entities = _mark_external_entity_dependents_stale(
            work,
            context,
            world=world,
            topic_id=topic_id,
            entity_id=entity_id,
        )
        downstream = _mark_downstream_stale(
            work,
            context,
            world=world,
            changed_topic_id=topic_id,
            changed_content_hash=stored_hash,
        )
        work.commit()
    return {
        "ok": True,
        "topic": stored,
        "entity": after,
        "stale_topic_ids": sorted(set(direct_topics).union(downstream)),
        "stale_entity_ids": sorted(set(stale_siblings).union(direct_entities)),
    }


def update_world_entity(
    world_id: str,
    topic_id: str,
    entity_id: str,
    *,
    expected_draft_revision: int,
    expected_content_hash: str,
    changes: Mapping[str, Any],
    database: Any | None = None,
) -> dict[str, Any]:
    return _store_replacement(
        world_id,
        topic_id,
        entity_id,
        expected_draft_revision=expected_draft_revision,
        expected_content_hash=expected_content_hash,
        replacement=changes,
        operation="manual_edit",
        database=database,
    )


def _topic_node(world: Mapping[str, Any], run: Mapping[str, Any] | None, topic_id: str) -> CampaignTopicNode:
    row = next((item for item in _graph_nodes(world, run) if str(item.get("topic_id") or "") == topic_id), None)
    if row is None:
        raise KeyError(f"world_topic_graph_node_not_found:{topic_id}")
    return CampaignTopicNode(
        topic_id=topic_id,
        title=str(row.get("title") or topic_id.replace("_", " ").title()),
        category=str(row.get("category") or "lore"),
        dependencies=tuple(str(value) for value in row.get("dependencies") or ()),
        generator_role=str(row.get("generator_role") or "world_forge"),
        required_before_launch=bool(row.get("required_before_launch", True)),
        visibility=str(row.get("visibility") or "game_master_canon"),
        target_count=1,
        metadata=dict(row.get("metadata") or {}),
    )


def regenerate_world_entity(
    world_id: str,
    topic_id: str,
    entity_id: str,
    *,
    expected_draft_revision: int,
    expected_content_hash: str,
    directives: Mapping[str, Any] | None = None,
    generator: WorldForgeTopicGenerator | None = None,
    database: Any | None = None,
) -> dict[str, Any]:
    context = current_tenant()
    with unit_of_work(database) as work:
        world, current = _assert_writable_topic(
            work,
            context,
            world_id=world_id,
            topic_id=topic_id,
            expected_draft_revision=expected_draft_revision,
            expected_content_hash=expected_content_hash,
        )
        before = _entity(_record(current.get("content")), entity_id)
        run = _latest_run(work, context, world_id)
        node = _topic_node(world, run, topic_id)
        rows = work.world_generation.list_topics(
            context,
            world_id=world_id,
            draft_revision=int(world["draft_revision"]),
        )
        row_map = {str(row.get("topic_id") or ""): row for row in rows}
        dependencies = {
            dependency_id: GeneratedTopic.from_dict(_record(row_map[dependency_id].get("content")))
            for dependency_id in node.dependencies
            if dependency_id in row_map
        }
        work.rollback()

    selected_generator = generator
    if selected_generator is None:
        from .generation_routing import build_world_forge_generator_for_run

        selected_generator = build_world_forge_generator_for_run(run)
    selected_generator = trusted_targeted_generator(
        selected_generator,
        world_id=world_id,
        topic_id=topic_id,
        entity_id=entity_id,
        operation="entity_regeneration",
    )
    node = replace(
        node,
        metadata={
            **dict(node.metadata),
            "entity_regeneration": {
                "entity_id": entity_id,
                "entity_name": str(before.get("name") or ""),
                "directives": dict(directives or {}),
            },
        },
    )
    generated = selected_generator.generate(
        node,
        seed=int(world.get("seed") or 0),
        campaign_context={
            "campaign_template": _record(world.get("metadata")).get("campaign_template") or "classic_fantasy",
            "genre": str(world.get("genre") or "classic_fantasy"),
            "tone": str(world.get("tone") or "heroic adventure"),
            "custom_directives": [json.dumps(dict(directives or {}), sort_keys=True)],
            "entity_regeneration": {"entity_id": entity_id, "current": before},
        },
        dependency_topics=dependencies,
    )
    if not generated.entities:
        raise ValueError(f"world_entity_regeneration_empty:{topic_id}:{entity_id}")
    candidate = dict(generated.entities[0])
    candidate["id"] = entity_id
    candidate["name"] = str(before.get("name") or candidate.get("name") or entity_id)
    candidate["kind"] = str(before.get("kind") or candidate.get("kind") or "entity")
    return _store_replacement(
        world_id,
        topic_id,
        entity_id,
        expected_draft_revision=expected_draft_revision,
        expected_content_hash=expected_content_hash,
        replacement=candidate,
        operation="regenerate",
        generated=generated,
        metadata={"directives": dict(directives or {}), "generation": dict(generated.provenance)},
        database=database,
    )
