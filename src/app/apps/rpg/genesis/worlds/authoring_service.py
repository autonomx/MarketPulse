"""User-facing projections for reusable-world authoring.

The authoring API intentionally projects durable topic JSON into stable document
and collection page contracts. The browser does not need to understand provider-
specific payload shapes, and player-facing session lore remains a separate API.
"""
from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from app.security.tenant_context import current_tenant
from app.persistence.unit_of_work import unit_of_work
from app.apps.rpg.genesis.forge.world_forge_contract import build_campaign_topic_graph

from .authoring_presentations import (
    PIPELINE_CATEGORIES as _PIPELINE_CATEGORIES,
    SYSTEM_SECTIONS as _SYSTEM_SECTIONS,
    entity_card,
    rows as _rows,
    section_group,
    section_label,
    section_page_kind,
    text as _text,
)
from .generation_jobs import WORLD_TOPIC_JOB_TYPE
from .library_service import read_world_detail
from .lifecycle_service import require_world_writable

_ENTITY_KEYS = (
    "entities",
    "regions",
    "areas",
    "points_of_interest",
    "locations",
    "characters",
    "npcs",
    "races",
    "classes",
    "factions",
    "monsters",
    "items",
    "spells",
    "feats",
    "quests",
    "encounter_seeds",
    "one_shots",
    "opening_scenarios",
)


def _record(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _token_count(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _usage_value(usage: Mapping[str, Any], *keys: str) -> int:
    for key in keys:
        value = _token_count(usage.get(key))
        if value:
            return value
    return 0


def _world_token_usage(
    topics: Sequence[Mapping[str, Any]],
    *,
    topic_results: Sequence[Mapping[str, Any]] = (),
    active_job_progresses: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Project all recorded world-generation usage into a clear total.

    Generation-result records are authoritative for the current run because
    they are retained for candidates that were rejected during review as well
    as candidates accepted into durable world topics. Provider usage is
    preferred; the generator records a labelled character-based estimate when
    a local provider omits token usage.
    """

    totals = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "provider_reported_topics": 0,
        "estimated_topics": 0,
        "unavailable_topics": 0,
        "in_flight_topics": 0,
        "generation_duration_ms": 0,
        "timed_topics": 0,
        "repair_count": 0,
        "repair_tokens": 0,
        "provider_reported_repairs": 0,
        "estimated_repairs": 0,
    }
    generated_topic_count = 0
    accounted_topic_ids: set[str] = set()

    def add_usage(
        provenance: Mapping[str, Any],
        fallback: Mapping[str, Any] = {},
        *,
        is_repair: bool = False,
    ) -> None:
        if is_repair:
            totals["repair_count"] += 1
        duration_ms = _token_count(provenance.get("latency_ms")) or _token_count(
            fallback.get("latency_ms")
        )
        if duration_ms:
            totals["generation_duration_ms"] += duration_ms
            totals["timed_topics"] += 1
        usage = _record(provenance.get("usage"))
        if not usage:
            usage = _record(fallback.get("usage"))
        prompt_tokens = _usage_value(usage, "prompt_tokens", "input_tokens")
        completion_tokens = _usage_value(usage, "completion_tokens", "output_tokens")
        total_tokens = _usage_value(usage, "total_tokens", "total")
        if total_tokens:
            totals["prompt_tokens"] += prompt_tokens
            totals["completion_tokens"] += completion_tokens
            totals["total_tokens"] += total_tokens
            totals["provider_reported_topics"] += 1
            if is_repair:
                totals["repair_tokens"] += total_tokens
                totals["provider_reported_repairs"] += 1
            return
        estimate = _record(provenance.get("token_estimate"))
        if not estimate:
            estimate = _record(fallback.get("token_estimate"))
        estimated_total = _token_count(estimate.get("total_tokens"))
        if estimated_total:
            totals["prompt_tokens"] += _token_count(estimate.get("prompt_tokens"))
            totals["completion_tokens"] += _token_count(
                estimate.get("completion_tokens")
            )
            totals["total_tokens"] += estimated_total
            totals["estimated_topics"] += 1
            if is_repair:
                totals["repair_tokens"] += estimated_total
                totals["estimated_repairs"] += 1
            return
        totals["unavailable_topics"] += 1

    for result in topic_results:
        candidate = _record(result.get("candidate"))
        provenance = _record(candidate.get("provenance"))
        if not provenance:
            continue
        generated_topic_count += 1
        accounted_topic_ids.add(_text(result.get("topic_id") or candidate.get("topic_id")))
        add_usage(provenance, _record(result.get("provider")))

    for topic in topics:
        if _text(topic.get("source")) != "ai" or _text(topic.get("topic_id")) in accounted_topic_ids:
            continue
        generated_topic_count += 1
        add_usage(_record(topic.get("provenance")))
    for topic in topics:
        authoring = _record(_record(topic.get("provenance")).get("authoring"))
        repair_usage = authoring.get("dossier_regeneration_usage")
        if not isinstance(repair_usage, list):
            continue
        for usage in repair_usage:
            if isinstance(usage, Mapping):
                add_usage(usage, is_repair=True)
    totals["topic_count"] = generated_topic_count
    for progress in active_job_progresses:
        totals["in_flight_topics"] += 1
        usage = _record(progress.get("token_usage"))
        prompt_tokens = _token_count(usage.get("prompt_tokens"))
        completion_tokens = _token_count(usage.get("completion_tokens"))
        total_tokens = _token_count(usage.get("total_tokens"))
        if total_tokens:
            totals["prompt_tokens"] += prompt_tokens
            totals["completion_tokens"] += completion_tokens
            totals["total_tokens"] += total_tokens
            if _text(usage.get("source")) == "provider_reported":
                totals["provider_reported_topics"] += 1
            else:
                totals["estimated_topics"] += 1
    return totals


def _active_world_topic_progresses(
    run_id: str,
    *,
    database: Any | None = None,
) -> list[dict[str, Any]]:
    """Read optional batch checkpoints without making the authoring view fragile."""

    if not run_id:
        return []
    try:
        context = current_tenant()
        with unit_of_work(database) as work:
            progresses = [
                _record(job.get("progress"))
                for job in work.jobs.iter_jobs(
                    context,
                    job_types=(WORLD_TOPIC_JOB_TYPE,),
                    statuses=("leased", "running", "cancel_requested"),
                )
                if _text(_record(job.get("metadata")).get("run_id")) == run_id
            ]
            work.rollback()
        return progresses
    except Exception:
        # Live usage is an enhancement of the durable topic projection.  A
        # temporary jobs-store read problem must not make authoring unavailable.
        return []


def _topic_entity_rows(content: Mapping[str, Any]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for key in _ENTITY_KEYS:
        for row in _rows(content.get(key)):
            identity = _text(
                row.get("id")
                or row.get("entity_id")
                or row.get("location_id")
                or row.get("npc_id")
                or row.get("faction_id")
                or row.get("name")
                or row.get("title")
            )
            fingerprint = identity or json.dumps(row, sort_keys=True, default=str)
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            rows.append(row)
    return rows


def _entity_count(content: Mapping[str, Any]) -> int:
    return len(_topic_entity_rows(content))


def _operational_status(
    topic_id: str,
    *,
    topic: Mapping[str, Any] | None,
    dependencies: Sequence[str],
    topics: Mapping[str, Mapping[str, Any]],
    active: set[str],
    failed: set[str],
) -> str:
    if topic_id in failed or (topic and _text(topic.get("status")) == "failed"):
        return "failed"
    if topic_id in active:
        return "generating"
    if topic and _text(topic.get("status")) == "stale":
        return "stale"
    if topic and _text(topic.get("status")) == "ready":
        return "complete"
    if any(_text(topics.get(dep, {}).get("status")) != "ready" for dep in dependencies):
        return "waiting"
    return "empty"


def _editorial_status(topic: Mapping[str, Any] | None) -> str:
    if topic is None:
        return "unreviewed"
    authoring = _record(_record(topic.get("provenance")).get("authoring"))
    if bool(authoring.get("generation_lock")):
        return "locked"
    if (
        _text(authoring.get("edit_state")) == "manually_edited"
        or _text(topic.get("source")) == "manual"
    ):
        return "manually_edited"
    if authoring.get("approved_at"):
        return "approved"
    return "needs_review"


def _graph_nodes(detail: Mapping[str, Any]) -> list[dict[str, Any]]:
    runs = list(detail.get("generation_runs") or [])
    latest_run = _record(runs[0]) if runs else {}
    nodes = _rows(_record(latest_run.get("graph")).get("nodes"))
    # Profile-resolution runs are bookkeeping only: before any authoring
    # generation they contain a single bootstrap node (or another
    # pipeline-only graph).  Imported bundles already contain durable topics,
    # and using that placeholder as the authoring graph hides every one of
    # them from the workspace.
    if any(_text(node.get("category")) not in _PIPELINE_CATEGORIES for node in nodes):
        return nodes
    world = _record(detail.get("world"))
    metadata = _record(world.get("metadata"))
    graph = build_campaign_topic_graph(
        campaign_template=_text(metadata.get("campaign_template"), "classic_fantasy"),
        genre=_text(world.get("genre"), "classic_fantasy"),
        tone=_text(world.get("tone"), "heroic adventure"),
        depth="standard",
        background_expansion=True,
    )
    return [node.as_dict() for node in graph.nodes]


def _system_status(section_id: str, detail: Mapping[str, Any]) -> tuple[str, int]:
    if section_id == "overview":
        return "complete", 1
    if section_id == "generation":
        runs = list(detail.get("generation_runs") or [])
        if not runs:
            return "empty", 0
        status = _text(_record(runs[0]).get("status"), "empty")
        return ("generating" if status in {"planned", "running"} else status), 1
    if section_id == "map":
        blueprints = list(detail.get("map_blueprints") or [])
        return ("complete" if blueprints else "empty"), len(blueprints)
    collections = {
        "map_blueprints": list(detail.get("map_blueprints") or []),
        "scenarios": list(detail.get("scenarios") or []),
        "releases": list(detail.get("releases") or []),
        "history_revisions": list(detail.get("revisions") or []),
    }
    if section_id in collections:
        count = len(collections[section_id])
        return ("complete" if count else "empty"), count
    if section_id == "validation":
        releases = list(detail.get("releases") or [])
        return ("complete" if releases else "waiting"), len(releases)
    return "empty", 0


def _image_section_status(
    world_id: str,
    *,
    database: Any | None = None,
) -> tuple[str, int]:
    """Summarize the durable image targets shown on the Images page."""

    context = current_tenant()
    with unit_of_work(database) as work:
        rows = work.connection.execute(
            "SELECT status, COUNT(*) FROM omnix_rpg_world_image_targets "
            "WHERE workspace_id = %s AND world_id = %s GROUP BY status",
            (context.workspace_id, world_id),
        ).fetchall()
        work.rollback()

    counts = {_text(status): int(count) for status, count in rows}
    total = sum(counts.values())
    if not total:
        return "empty", 0
    if counts.get("queued", 0) or counts.get("generating", 0):
        return "generating", total
    if counts.get("ready", 0):
        return "complete", total
    if counts.get("stale", 0):
        return "stale", total
    if counts.get("failed", 0):
        return "failed", total
    return "empty", total


def read_authoring_manifest(
    world_id: str,
    *,
    database: Any | None = None,
) -> dict[str, Any]:
    detail = read_world_detail(world_id, database=database)
    topics = {str(row["topic_id"]): row for row in detail["topics"]}
    latest_run = _record(detail["generation_runs"][0]) if detail["generation_runs"] else {}
    progress = _record(latest_run.get("progress"))
    active = {_text(value) for value in progress.get("active_topic_ids") or []}
    failed = {_text(value) for value in progress.get("failed_topic_ids") or []}
    sections: list[dict[str, Any]] = []
    graph_ids: set[str] = set()
    for node in _graph_nodes(detail):
        topic_id = _text(node.get("topic_id"))
        if not topic_id:
            continue
        category = _text(node.get("category"), "lore")
        if category in _PIPELINE_CATEGORIES:
            continue
        graph_ids.add(topic_id)
        topic = topics.get(topic_id)
        dependencies = [
            _text(value) for value in node.get("dependencies") or [] if _text(value)
        ]
        metadata = _record(node.get("metadata"))
        page_kind = section_page_kind(category)
        sections.append(
            {
                "id": topic_id,
                "label": section_label(topic_id, _text(node.get("title"))),
                "group": "game-master" if category == "story" else section_group(category),
                "page_kind": page_kind,
                "topic_ids": [topic_id],
                "entity_kind": _text(
                    metadata.get("entity_kind"),
                    category[:-1] if category.endswith("s") else category,
                ),
                "dependencies": dependencies,
                "required_before_launch": bool(
                    node.get("required_before_launch", True)
                ),
                "supports_generation": True,
                "supports_images": page_kind == "collection" or topic_id in {
                    "realm",
                    "regions",
                    "locations",
                },
                "supports_entity_editing": page_kind == "collection",
                "operational_status": _operational_status(
                    topic_id,
                    topic=topic,
                    dependencies=dependencies,
                    topics=topics,
                    active=active,
                    failed=failed,
                ),
                "editorial_status": _editorial_status(topic),
                "entity_count": (
                    _entity_count(_record(topic.get("content"))) if topic else 0
                ),
            }
        )
    for section in _SYSTEM_SECTIONS:
        if section["id"] in graph_ids:
            continue
        if section["id"] == "images":
            status, count = _image_section_status(world_id, database=database)
        else:
            status, count = _system_status(str(section["id"]), detail)
        sections.append(
            {
                **section,
                "topic_ids": [],
                "dependencies": [],
                "required_before_launch": False,
                "supports_generation": False,
                "supports_images": section["id"] == "images",
                "supports_entity_editing": False,
                "operational_status": status,
                "editorial_status": "unreviewed",
                "entity_count": count,
            }
        )
    return {
        "ok": True,
        # Keep the overview dashboard on the same durable generation snapshot as
        # the dedicated Generation page.  Without this, the overview falls back
        # to counting every authoring section (including auxiliary sections),
        # which can disagree with the run's topic progress.
        "world": {**detail["world"], "generation": latest_run or None},
        "sections": sections,
        "generation": latest_run,
        "token_usage": _world_token_usage(
            detail["topics"],
            topic_results=_record(detail.get("generation_topic_results")).get(
                _text(latest_run.get("run_id")),
                [],
            ),
            active_job_progresses=_active_world_topic_progresses(
                _text(latest_run.get("run_id")),
                database=database,
            ),
        ),
    }


def _document_blocks(content: Mapping[str, Any]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for document in _rows(content.get("documents")):
        title = _text(document.get("title") or document.get("name"), "Lore")
        body = _text(
            document.get("full_text")
            or document.get("body")
            or document.get("text")
            or document.get("summary")
        )
        if body:
            blocks.append({"kind": "section", "title": title, "body": body})
    facts = _rows(content.get("facts"))
    if facts:
        blocks.append({"kind": "facts", "title": "Canon facts", "items": facts})
    if not blocks:
        blocks.append(
            {"kind": "json", "title": "Structured canon", "value": dict(content)}
        )
    return blocks


def _map_page(detail: Mapping[str, Any]) -> dict[str, Any]:
    blueprints = [_record(row) for row in detail.get("map_blueprints") or ()]
    ready = sum(1 for row in blueprints if _text(row.get("status")) == "ready")
    invalid = sum(1 for row in blueprints if _text(row.get("status")) == "invalid")
    return {
        "ok": True,
        "section_id": "map",
        "page_kind": "document",
        "title": "Map",
        "summary": (
            "World map presentation is backed by authored semantic map blueprints."
            if blueprints
            else "No map blueprints have been authored yet."
        ),
        "body": [
            {
                "kind": "facts",
                "title": "Map readiness",
                "items": [
                    {"label": "Blueprints", "value": len(blueprints)},
                    {"label": "Ready", "value": ready},
                    {"label": "Invalid", "value": invalid},
                ],
            }
        ],
        "related_entities": [],
    }


def _validation_page(detail: Mapping[str, Any]) -> dict[str, Any]:
    releases = [_record(row) for row in detail.get("releases") or ()]
    latest = releases[0] if releases else {}
    document = _record(latest.get("document"))
    certification = _record(document.get("certification"))
    missing = certification.get("missing_requirements") or []
    return {
        "ok": True,
        "section_id": "validation",
        "page_kind": "document",
        "title": "Validation",
        "summary": (
            "Latest published release certification and launch readiness."
            if latest
            else "Publish a world release to produce launch certification."
        ),
        "body": [
            {
                "kind": "facts",
                "title": "Release readiness",
                "items": [
                    {"label": "Launch ready", "value": certification.get("launch_ready", False)},
                    {"label": "Missing requirements", "value": missing},
                    {"label": "World revision", "value": latest.get("world_revision")},
                    {"label": "Release", "value": latest.get("release")},
                ],
            },
            {
                "kind": "json",
                "title": "Certification details",
                "value": certification,
            },
        ],
        "related_entities": [],
    }


def read_authoring_section(
    world_id: str,
    section_id: str,
    *,
    database: Any | None = None,
) -> dict[str, Any]:
    detail = read_world_detail(world_id, database=database)
    graph_node = next(
        (
            row
            for row in _graph_nodes(detail)
            if _text(row.get("topic_id")) == section_id
        ),
        None,
    )
    topic = next(
        (
            row
            for row in detail["topics"]
            if str(row["topic_id"]) == section_id
        ),
        None,
    )
    if topic is not None:
        content = _record(topic.get("content"))
        category = _text(_record(graph_node).get("category"), "lore")
        page_kind = section_page_kind(category)
        title = section_label(
            section_id,
            _text(_record(graph_node).get("title"), section_id.replace("_", " ").title()),
        )
        content_provenance = _record(content.get("provenance"))
        generator = _text(
            content_provenance.get("generator")
            or _record(topic.get("provenance")).get("generator")
        )
        deterministic_lore = generator.startswith("deterministic_")
        if _text(topic.get("status")) == "failed" or deterministic_lore:
            retry_note = _record(_record(content.get("provenance")).get("retry_note"))
            message = _text(
                retry_note.get("message"),
                (
                    "This topic was generated by a retired deterministic fallback and "
                    "is hidden. Retry it with a configured provider; no fallback lore "
                    "will be published."
                    if deterministic_lore
                    else "This topic could not be generated. No fallback lore was published."
                ),
            )
            return {
                "ok": True,
                "section_id": section_id,
                "page_kind": "document",
                "title": title,
                "summary": "Generation needs retry",
                "body": [
                    {
                        "kind": "section",
                        "title": "Generation needs retry",
                        "body": message,
                    }
                ],
                "related_entities": [],
                "topic": topic,
            }
        if page_kind == "collection":
            entities = _topic_entity_rows(content)
            metadata = _record(_record(graph_node).get("metadata"))
            kind = _text(
                metadata.get("entity_kind")
                or (entities[0].get("kind") if entities else None),
                section_id.rstrip("s"),
            )
            return {
                "ok": True,
                "section_id": section_id,
                "page_kind": "collection",
                "title": title,
                "entities": [
                    entity_card(
                        row,
                        card_type=section_id,
                        kind=_text(row.get("kind"), kind),
                        index=index,
                        content=content,
                    )
                    for index, row in enumerate(entities)
                ],
                "filters": [],
                "sort_options": ["name"],
                "topic": topic,
            }
        return {
            "ok": True,
            "section_id": section_id,
            "page_kind": "document",
            "title": title,
            "summary": _text(
                content.get("summary") or content.get("description")
            ),
            "body": _document_blocks(content),
            "related_entities": [
                entity_card(
                    row,
                    card_type=section_id,
                    kind=_text(row.get("kind"), section_id.rstrip("s")),
                    index=index,
                    content=content,
                )
                for index, row in enumerate(_topic_entity_rows(content))
            ],
            "topic": topic,
        }
    if section_id == "overview":
        world = detail["world"]
        return {
            "ok": True,
            "section_id": section_id,
            "page_kind": "document",
            "title": world["title"],
            "summary": world.get("description") or "No description yet.",
            "body": [
                {
                    "kind": "facts",
                    "title": "World settings",
                    "items": [
                        {"label": "Genre", "value": world.get("genre")},
                        {"label": "Tone", "value": world.get("tone")},
                        {
                            "label": "Draft revision",
                            "value": world.get("draft_revision"),
                        },
                    ],
                }
            ],
            "related_entities": [],
        }
    if section_id == "map":
        return _map_page(detail)
    if section_id == "validation":
        return _validation_page(detail)
    collection_map = {
        "scenarios": detail["scenarios"],
        "map_blueprints": detail["map_blueprints"],
        "releases": detail["releases"],
        "history_revisions": detail["revisions"],
    }
    if section_id in collection_map:
        rows = [_record(row) for row in collection_map[section_id]]
        return {
            "ok": True,
            "section_id": section_id,
            "page_kind": "collection",
            "title": section_label(section_id),
            "entities": [
                entity_card(
                    row,
                    card_type=section_id,
                    kind=section_id.rstrip("s"),
                    index=index,
                )
                for index, row in enumerate(rows)
            ],
            "filters": [],
            "sort_options": ["name"],
        }
    return {
        "ok": True,
        "section_id": section_id,
        "page_kind": "document",
        "title": section_label(section_id),
        "summary": "This section has not been generated yet.",
        "body": [],
        "related_entities": [],
    }


def update_world_metadata(
    world_id: str,
    *,
    expected_draft_revision: int,
    changes: Mapping[str, Any],
    database: Any | None = None,
) -> dict[str, Any]:
    context = current_tenant()
    with unit_of_work(database) as work:
        world = require_world_writable(work, context, world_id)
        current_revision = int(world["draft_revision"])
        if current_revision != int(expected_draft_revision):
            raise ValueError(
                "world_draft_revision_conflict:"
                f"expected={expected_draft_revision}:current={current_revision}"
            )
        metadata = {
            **_record(world.get("metadata")),
            **_record(changes.get("metadata")),
        }
        values = {
            "title": _text(changes.get("title"), _text(world.get("title"))),
            "description": _text(
                changes.get("description"), _text(world.get("description"))
            ),
            "genre": _text(
                changes.get("genre"),
                _text(world.get("genre"), "classic_fantasy"),
            ),
            "tone": _text(
                changes.get("tone"),
                _text(world.get("tone"), "heroic adventure"),
            ),
            "seed": int(changes.get("seed", world.get("seed") or 0)),
        }
        updated = work.connection.execute(
            "UPDATE omnix_rpg_worlds SET title = %s, description = %s, "
            "genre = %s, tone = %s, seed = %s, metadata_jsonb = %s::jsonb, "
            "updated_at = CURRENT_TIMESTAMP WHERE workspace_id = %s AND id = %s "
            "AND draft_revision = %s RETURNING id",
            (
                values["title"],
                values["description"],
                values["genre"],
                values["tone"],
                values["seed"],
                json.dumps(metadata, sort_keys=True),
                context.workspace_id,
                world_id,
                current_revision,
            ),
        ).fetchone()
        if updated is None:
            raise ValueError("world_metadata_compare_and_swap_failed")
        stored = work.world_scenarios.get_world(context, world_id)
        work.commit()
    return {"ok": True, "world": stored}
