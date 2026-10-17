"""Durable single-pass World Forge topic generation and review orchestration."""
from __future__ import annotations

import logging
import threading
import uuid
from collections import Counter
from typing import Any, Mapping, Sequence

from app.persistence.database import DatabaseUnavailableError
from app.apps.rpg.genesis.forge.world_forge_contract import (
    CampaignTopicGraph,
    CampaignTopicNode,
)
from app.apps.rpg.genesis.forge.world_forge_generation import (
    GeneratedTopic,
    WorldForgeTopicGenerator,
)
from app.apps.rpg.genesis.forge.world_forge_review import (
    failure_report,
    result_status,
    review_report,
)

from .generation_candidate_spool import (
    delete_candidate_spool,
    read_candidate_spool,
    write_candidate_spool,
)
from .generation_jobs import (
    WORLD_TOPIC_JOB_TYPE,
    WorldTopicGenerationSettings,
    canonical_hash,
    generation_progress,
    generation_topic_ids,
    plan_ready_topic_jobs,
    topic_generation_fingerprint,
    world_generation_run_id,
)
from .generation_contract_receipt import contract_descriptor_from_candidate
from .generation_payloads import (
    graph_from_payload as _graph_from_payload,
    settings_from_payload as _settings_from_payload,
    protected as _protected,
    available_result_row as _available_result_row,
)

_TERMINAL_JOB_STATUSES = {"completed", "failed", "canceled", "stale"}
_ACTIVE_JOB_STATUSES = {"queued", "leased", "running", "waiting", "retrying"}
_NON_GENERATION_CATEGORIES = {"compiler", "audit", "index", "bootstrap"}
_reconcile_lock = threading.Lock()
_LOGGER = logging.getLogger(__name__)


def _database(value: Any | None) -> Any:
    if value is not None:
        return value
    from app.persistence.database import default_database

    return default_database()


def available_completed_topics(
    graph: CampaignTopicGraph,
    *,
    rows: Mapping[str, Mapping[str, Any]],
    generation_context: Mapping[str, Any],
    topic_directives: Mapping[str, Mapping[str, Any]],
    entity_manifest_hash: str,
    settings: WorldTopicGenerationSettings,
    forced_topic_ids: Sequence[str] = (),
    pinned_topic_ids: Sequence[str] = (),
    current_run_id: str = "",
    run_results: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[
    dict[str, Mapping[str, Any]],
    tuple[str, ...],
    tuple[str, ...],
]:
    """Return same-run candidates plus reusable/protected approved authoring rows."""

    available: dict[str, Mapping[str, Any]] = {}
    reusable: list[str] = []
    protected: list[str] = []
    forced = set(forced_topic_ids)
    pinned = set(pinned_topic_ids)
    results = dict(run_results or {})
    for node in graph.topological_order():
        if node.category in _NON_GENERATION_CATEGORIES:
            continue
        if not set(node.dependencies).issubset(available):
            continue

        result_row = _available_result_row(results.get(node.topic_id) or {})
        if result_row is not None:
            available[node.topic_id] = result_row
            continue

        row = rows.get(node.topic_id)
        if row is None or str(row.get("status") or "") != "ready":
            continue
        provenance = row.get("provenance")
        provenance = provenance if isinstance(provenance, Mapping) else {}
        if (
            node.topic_id in forced
            and str(provenance.get("run_id") or "") != current_run_id
        ):
            continue
        if node.topic_id in pinned or _protected(row):
            available[node.topic_id] = {**dict(row), "dependency_trust": "accepted"}
            protected.append(node.topic_id)
            continue
        dependency_hashes = {
            dependency_id: str(available[dependency_id]["content_hash"])
            for dependency_id in node.dependencies
        }
        dependency_trust = {
            dependency_id: str(
                available[dependency_id].get("dependency_trust") or "accepted"
            )
            for dependency_id in node.dependencies
        }
        fingerprint, input_hash, directive_hash = topic_generation_fingerprint(
            node,
            normalized_topic_input={
                "generation_context": dict(generation_context),
                "target_count": node.target_count,
                "visibility": node.visibility,
                "dependency_trust": dependency_trust,
            },
            dependency_hashes=dependency_hashes,
            directives=dict(topic_directives.get(node.topic_id) or {}),
            entity_manifest_hash=entity_manifest_hash,
            settings=settings,
            contract_descriptor=contract_descriptor_from_candidate(
                dict(row.get("content") or {})
            ),
        )
        if str(provenance.get("generation_fingerprint") or "") != fingerprint:
            continue
        if str(row.get("input_hash") or "") != input_hash:
            continue
        if str(provenance.get("directive_hash") or "") != directive_hash:
            continue
        if dict(row.get("dependency_hashes") or {}) != dependency_hashes:
            continue
        available[node.topic_id] = {**dict(row), "dependency_trust": "accepted"}
        reusable.append(node.topic_id)
    return available, tuple(reusable), tuple(protected)


def start_world_generation(
    *,
    world_id: str,
    draft_revision: int,
    graph: CampaignTopicGraph,
    generation_context: Mapping[str, Any],
    topic_directives: Mapping[str, Mapping[str, Any]],
    entity_manifest_hash: str,
    settings: WorldTopicGenerationSettings,
    target_topic_ids: Sequence[str] | None = None,
    forced_topic_ids: Sequence[str] = (),
    pinned_topic_ids: Sequence[str] = (),
    scope: Mapping[str, Any] | None = None,
    strategy: str = "reuse_unchanged",
    database: Any | None = None,
    tenant_context: Any | None = None,
) -> dict[str, Any]:
    issues = graph.validate()
    if issues:
        raise ValueError("invalid_world_generation_graph:" + ",".join(issues))
    db = _database(database)
    from app.security.tenant_context import current_tenant
    from app.persistence.unit_of_work import unit_of_work

    context = tenant_context or current_tenant()
    targets = generation_topic_ids(graph, target_topic_ids)
    scope_payload = {"topic_ids": list(targets), **dict(scope or {})}
    scope_hash = canonical_hash(
        {
            "scope": scope_payload,
            "strategy": strategy,
            "forced_topic_ids": sorted(set(forced_topic_ids)),
            "pinned_topic_ids": sorted(set(pinned_topic_ids)),
            "directives": {
                key: dict(value) for key, value in sorted(topic_directives.items())
            },
        }
    )
    run_id = world_generation_run_id(
        world_id=world_id,
        draft_revision=draft_revision,
        scope_hash=scope_hash,
        run_nonce=uuid.uuid4().hex[:12],
    )
    run_context = {
        "generation_context": dict(generation_context),
        "topic_directives": {
            topic_id: dict(value)
            for topic_id, value in sorted(topic_directives.items())
        },
        "entity_manifest_hash": entity_manifest_hash,
        "scope": scope_payload,
        "scope_hash": scope_hash,
        "strategy": strategy,
        "target_topic_ids": list(targets),
        "forced_topic_ids": sorted(set(forced_topic_ids)),
        "pinned_topic_ids": sorted(set(pinned_topic_ids)),
        "content_generation_policy": "single_pass_manual_retry",
    }
    with unit_of_work(db) as work:
        if work.world_scenarios.get_world(context, world_id) is None:
            raise KeyError(f"world_not_found:{world_id}")
        run = work.world_generation.start(
            context,
            run_id=run_id,
            world_id=world_id,
            draft_revision=draft_revision,
            graph=graph.as_dict(),
            generation_context=run_context,
            settings=settings.as_dict(),
            plan={"job_ids": [], "topic_ids": list(targets)},
            progress=generation_progress(
                graph,
                active_topic_ids=(),
                target_topic_ids=targets,
            ),
        )
        work.commit()
    return reconcile_world_generation(run["run_id"], database=db)


def reconcile_world_generation(
    run_id: str,
    *,
    database: Any | None = None,
) -> dict[str, Any]:
    """Reconcile one DAG at a time to avoid duplicate downstream job creation."""

    with _reconcile_lock:
        return _reconcile_world_generation_unlocked(run_id, database=database)


def _result_map(rows: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    return {str(row.get("topic_id") or ""): row for row in rows}


def _record_reused_results(
    work: Any,
    context: Any,
    *,
    run: Mapping[str, Any],
    available: Mapping[str, Mapping[str, Any]],
    reusable_ids: Sequence[str],
    protected_ids: Sequence[str],
    existing_results: Mapping[str, Mapping[str, Any]],
) -> None:
    for topic_id in (*reusable_ids, *protected_ids):
        if topic_id in existing_results:
            continue
        row = available[topic_id]
        content = dict(row.get("content") or {})
        work.world_generation.put_topic_result(
            context,
            run_id=str(run["run_id"]),
            world_id=str(run["world_id"]),
            draft_revision=int(run["draft_revision"]),
            topic_id=topic_id,
            status="accepted",
            candidate=content,
            candidate_hash=str(row.get("content_hash") or canonical_hash(content)),
            validation={
                "schema_version": "rpg_world_generation_review_v1",
                "status": "accepted",
                "blocking": False,
                "reason_codes": [],
                "issues": [],
                "summary": "Reused approved authoring topic.",
            },
            provider={
                "source": "protected_authoring" if topic_id in protected_ids else "reused_authoring",
            },
            dependency_hashes=dict(row.get("dependency_hashes") or {}),
            dependency_trust={
                dependency_id: str(
                    available[dependency_id].get("dependency_trust") or "accepted"
                )
                for dependency_id in dict(row.get("dependency_hashes") or {})
                if dependency_id in available
            },
            job_id="",
        )


def _job_list(work: Any, context: Any, run_id: str) -> list[Mapping[str, Any]]:
    return [
        job
        for job in work.jobs.iter_jobs(context, job_types=(WORLD_TOPIC_JOB_TYPE,))
        if str(job["metadata"].get("run_id") or "") == run_id
    ]


def _terminal_job_failure_result(
    work: Any,
    context: Any,
    *,
    run: Mapping[str, Any],
    job: Mapping[str, Any],
) -> None:
    topic_id = str(dict(job.get("metadata") or {}).get("topic_id") or "")
    if not topic_id or work.world_generation.get_topic_result(
        context, run_id=str(run["run_id"]), topic_id=topic_id
    ) is not None:
        return
    error = dict(job.get("error") or {})
    work.world_generation.put_topic_result(
        context,
        run_id=str(run["run_id"]),
        world_id=str(run["world_id"]),
        draft_revision=int(run["draft_revision"]),
        topic_id=topic_id,
        status="failed",
        candidate=None,
        candidate_hash="",
        validation={
            "schema_version": "rpg_world_generation_review_v1",
            "status": "failed",
            "blocking": True,
            "error_type": str(error.get("type") or "WorldTopicJobFailure"),
            "reason_codes": [str(error.get("code") or "world_topic_job_failed")],
            "issues": [
                {
                    "code": str(error.get("code") or "world_topic_job_failed"),
                    "topic_id": topic_id,
                    "entity_id": "",
                    "field_id": "",
                    "message": str(error.get("message") or error),
                }
            ],
            "summary": str(error.get("message") or error),
        },
        provider={},
        dependency_hashes=dict(dict(job.get("input_payload") or {}).get("dependency_hashes") or {}),
        dependency_trust=dict(dict(job.get("input_payload") or {}).get("dependency_trust") or {}),
        job_id=str(job.get("id") or ""),
    )


def _issue_counts(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_code: Counter[str] = Counter()
    by_field: Counter[str] = Counter()
    by_topic: Counter[str] = Counter()
    for result in results:
        validation = dict(result.get("validation") or {})
        for issue in validation.get("issues") or ():
            if not isinstance(issue, Mapping):
                continue
            code = str(issue.get("code") or "unknown")
            field = str(issue.get("field_id") or "")
            topic = str(issue.get("topic_id") or result.get("topic_id") or "")
            by_code[code] += 1
            if field:
                by_field[field] += 1
            if topic:
                by_topic[topic] += 1
    return {
        "by_code": dict(sorted(by_code.items())),
        "by_field": dict(sorted(by_field.items())),
        "by_topic": dict(sorted(by_topic.items())),
    }


def _mark_blocked_results(
    work: Any,
    context: Any,
    *,
    run: Mapping[str, Any],
    graph: CampaignTopicGraph,
    targets: Sequence[str],
    available: Mapping[str, Mapping[str, Any]],
    results: Mapping[str, Mapping[str, Any]],
) -> None:
    node_map = graph.node_map()
    for topic_id in targets:
        if topic_id in results or topic_id in available:
            continue
        node = node_map[topic_id]
        missing = tuple(
            dependency for dependency in node.dependencies if dependency not in available
        )
        work.world_generation.put_topic_result(
            context,
            run_id=str(run["run_id"]),
            world_id=str(run["world_id"]),
            draft_revision=int(run["draft_revision"]),
            topic_id=topic_id,
            status="blocked",
            candidate=None,
            candidate_hash="",
            validation={
                "schema_version": "rpg_world_generation_review_v1",
                "status": "blocked",
                "blocking": True,
                "error_type": "DependencyUnavailable",
                "reason_codes": ["dependency_no_candidate"],
                "issues": [
                    {
                        "code": "dependency_no_candidate",
                        "topic_id": topic_id,
                        "entity_id": "",
                        "field_id": "dependencies",
                        "message": "No candidate was available for: " + ",".join(missing),
                        "supplied_value": list(missing),
                    }
                ],
                "summary": "Topic could not be scheduled because a dependency has no candidate.",
            },
            provider={},
            dependency_hashes={},
            dependency_trust={dependency: "missing" for dependency in missing},
            job_id="",
        )


def _reconcile_world_generation_unlocked(
    run_id: str,
    *,
    database: Any | None = None,
) -> dict[str, Any]:
    db = _database(database)
    from app.security.tenant_context import current_tenant
    from app.persistence.unit_of_work import unit_of_work

    context = current_tenant()
    with unit_of_work(db) as work:
        run = work.world_generation.get(context, run_id)
        if run is None:
            raise KeyError(f"world_generation_run_not_found:{run_id}")
        graph = _graph_from_payload(run["graph"])
        run_context = dict(run["context"])
        generation_context = dict(run_context.get("generation_context") or {})
        topic_directives = {
            str(key): dict(value)
            for key, value in dict(run_context.get("topic_directives") or {}).items()
            if isinstance(value, Mapping)
        }
        entity_manifest_hash = str(run_context.get("entity_manifest_hash") or "")
        targets = tuple(str(value) for value in run_context.get("target_topic_ids") or ())
        forced = tuple(str(value) for value in run_context.get("forced_topic_ids") or ())
        pinned = tuple(str(value) for value in run_context.get("pinned_topic_ids") or ())
        settings = _settings_from_payload(run["settings"])
        topic_rows = work.world_generation.list_topics(
            context,
            world_id=run["world_id"],
            draft_revision=run["draft_revision"],
        )
        topic_map = {str(row["topic_id"]): row for row in topic_rows}
        result_rows = work.world_generation.list_topic_results(context, run_id=run_id)
        results = _result_map(result_rows)
        available, reusable_ids, protected_ids = available_completed_topics(
            graph,
            rows=topic_map,
            generation_context=generation_context,
            topic_directives=topic_directives,
            entity_manifest_hash=entity_manifest_hash,
            settings=settings,
            forced_topic_ids=forced,
            pinned_topic_ids=pinned,
            current_run_id=run_id,
            run_results=results,
        )
        _record_reused_results(
            work,
            context,
            run=run,
            available=available,
            reusable_ids=reusable_ids,
            protected_ids=protected_ids,
            existing_results=results,
        )
        result_rows = work.world_generation.list_topic_results(context, run_id=run_id)
        results = _result_map(result_rows)
        available, reusable_ids, protected_ids = available_completed_topics(
            graph,
            rows=topic_map,
            generation_context=generation_context,
            topic_directives=topic_directives,
            entity_manifest_hash=entity_manifest_hash,
            settings=settings,
            forced_topic_ids=forced,
            pinned_topic_ids=pinned,
            current_run_id=run_id,
            run_results=results,
        )

        jobs = _job_list(work, context, run_id)
        for job in jobs:
            if str(job.get("status") or "") == "failed":
                _terminal_job_failure_result(work, context, run=run, job=job)
        result_rows = work.world_generation.list_topic_results(context, run_id=run_id)
        results = _result_map(result_rows)
        existing_job_ids = [str(job["id"]) for job in jobs]
        plans = plan_ready_topic_jobs(
            graph,
            run_id=run_id,
            world_id=run["world_id"],
            draft_revision=run["draft_revision"],
            generation_context=generation_context,
            topic_directives=topic_directives,
            completed_topics=available,
            existing_job_ids=existing_job_ids,
            entity_manifest_hash=entity_manifest_hash,
            settings=settings,
            target_topic_ids=targets,
        )
        for plan in plans:
            work.jobs.create_job(context, dict(plan.job_payload))
            existing_job_ids.append(plan.job_id)
        jobs = _job_list(work, context, run_id)
        active_topics = [
            str(dict(job.get("metadata") or {}).get("topic_id") or "")
            for job in jobs
            if str(job.get("status") or "") in _ACTIVE_JOB_STATUSES
        ]

        if not plans and not active_topics:
            _mark_blocked_results(
                work,
                context,
                run=run,
                graph=graph,
                targets=targets,
                available=available,
                results=results,
            )
            result_rows = work.world_generation.list_topic_results(context, run_id=run_id)
            results = _result_map(result_rows)

        accepted = [topic_id for topic_id, row in results.items() if row["status"] == "accepted"]
        flagged = [topic_id for topic_id, row in results.items() if row["status"] == "needs_review"]
        failed = [topic_id for topic_id, row in results.items() if row["status"] == "failed"]
        blocked = [topic_id for topic_id, row in results.items() if row["status"] == "blocked"]
        progress = generation_progress(
            graph,
            accepted_topic_ids=accepted,
            flagged_topic_ids=flagged,
            failed_topic_ids=failed,
            blocked_topic_ids=blocked,
            active_topic_ids=active_topics,
            issue_counts=_issue_counts(result_rows),
            target_topic_ids=targets,
        )
        status = "review" if progress["generation_complete"] else "running"
        plan_payload = {
            "job_ids": sorted(set(existing_job_ids)),
            "new_job_ids": [plan.job_id for plan in plans],
            "recovery_job_ids": [],
            "available_topic_ids": sorted(available),
            "reusable_topic_ids": sorted(reusable_ids),
            "protected_topic_ids": sorted(protected_ids),
            "topic_ids": list(targets),
            "topic_results": {
                topic_id: {
                    "status": str(row.get("status") or ""),
                    "candidate_hash": str(row.get("candidate_hash") or ""),
                    "reason_codes": list(dict(row.get("validation") or {}).get("reason_codes") or ()),
                }
                for topic_id, row in sorted(results.items())
            },
        }
        updated = work.world_generation.update(
            context,
            run_id=run_id,
            status=status,
            plan=plan_payload,
            progress=progress,
            error={},
        )
        work.commit()
    return updated


def _provider_metadata(topic: GeneratedTopic) -> dict[str, Any]:
    provenance = dict(topic.provenance)
    receipt = dict(provenance.get("authoritative_contract_receipt") or {})
    return {
        "provider": provenance.get("provider"),
        "model": provenance.get("model"),
        "prompt_contract": provenance.get("provider_contract"),
        "structured_contract": provenance.get("structured_contract"),
        "schema_hash": provenance.get("schema_hash"),
        "response_format": provenance.get("response_format"),
        "finish_reason": provenance.get("finish_reason"),
        "provider_calls": provenance.get("attempt_count"),
        "latency_ms": provenance.get("latency_ms"),
        "usage": dict(provenance.get("usage") or {}),
        "token_estimate": dict(provenance.get("token_estimate") or {}),
        "contract_descriptor": {
            key: receipt.get(key)
            for key in (
                "contract_id",
                "contract_version",
                "provider_schema_hash",
                "provider_wire_schema_hash",
                "authored_schema_hash",
                "prompt_contract_hash",
                "canonical_contract_hash",
                "dossier_template_hash",
                "collection_policy_hash",
                "payload_limits_hash",
                "materializer_version",
                "semantic_policy_version",
                "schema_projection_version",
            )
            if receipt.get(key) not in (None, "")
        },
        "strategy_identity": provenance.get("strategy_identity"),
    }


def _terminally_fail_job(
    work: Any,
    context: Any,
    *,
    job: Mapping[str, Any],
    worker_id: str,
    lease_token: str,
    error: Mapping[str, Any],
) -> Mapping[str, Any]:
    work.jobs.set_max_attempts_to_current(
        context, job_id=str(job["id"])
    )
    return work.jobs.fail(
        context,
        job_id=str(job["id"]),
        worker_id=worker_id,
        lease_token=lease_token,
        error=dict(error),
        retry_delay_seconds=0,
    )


def _load_dependency_candidate(
    work: Any,
    context: Any,
    *,
    run_id: str,
    world_id: str,
    dependency_id: str,
    expected_hash: str,
) -> GeneratedTopic:
    result = work.world_generation.get_topic_result(
        context,
        run_id=run_id,
        topic_id=dependency_id,
    )
    if result is not None and result.get("candidate") is not None:
        if str(result.get("candidate_hash") or "") != expected_hash:
            raise RuntimeError(
                f"world_topic_dependency_mismatch:{dependency_id}:result_hash"
            )
        return GeneratedTopic.from_dict(dict(result["candidate"]))
    row = work.world_generation.get_topic(
        context,
        world_id=world_id,
        topic_id=dependency_id,
    )
    if row is None or str(row.get("content_hash") or "") != expected_hash:
        raise RuntimeError(
            f"world_topic_dependency_mismatch:{dependency_id}:authoring_hash"
        )
    return GeneratedTopic.from_dict(dict(row["content"]))


def execute_claimed_world_topic_job(
    *,
    job: Mapping[str, Any],
    worker_id: str,
    generator: WorldForgeTopicGenerator | None = None,
    database: Any | None = None,
) -> dict[str, Any]:
    if str(job.get("job_type") or "") != WORLD_TOPIC_JOB_TYPE:
        raise ValueError("not_world_topic_job")
    db = _database(database)
    payload = dict(job.get("input_payload") or {})
    run_id = str(payload.get("run_id") or "")
    world_id = str(payload.get("world_id") or "")
    topic_payload = dict(payload.get("topic") or {})
    topic_id = str(topic_payload.get("topic_id") or "")
    lease_token = str(job.get("lease_token") or "")
    job_id = str(job.get("id") or "")
    from app.security.tenant_context import current_tenant
    from app.persistence.unit_of_work import unit_of_work

    context = current_tenant()
    try:
        existing_result: Mapping[str, Any] | None = None
        completed_existing_job: Mapping[str, Any] | None = None
        with unit_of_work(db) as work:
            run = work.world_generation.get(context, run_id)
            if run is None:
                raise KeyError(f"world_generation_run_not_found:{run_id}")
            existing_result = work.world_generation.get_topic_result(
                context,
                run_id=run_id,
                topic_id=topic_id,
            )
            if existing_result is not None:
                # A process may die after persisting the durable topic result but
                # before completing its leased job. Treat that result as the
                # idempotency receipt: finalize the job without a second model call.
                completed_existing_job = work.jobs.complete(
                    context,
                    job_id=job_id,
                    worker_id=worker_id,
                    lease_token=lease_token,
                    output_refs=[
                        {
                            "run_id": run_id,
                            "world_id": world_id,
                            "draft_revision": int(payload.get("draft_revision") or 1),
                            "topic_id": topic_id,
                            "candidate_hash": str(existing_result.get("candidate_hash") or ""),
                            "result_status": str(existing_result.get("status") or ""),
                            "replayed_existing_result": True,
                        }
                    ],
                    progress={
                        "current": 1,
                        "total": 1,
                        "message": "existing world topic result finalized",
                    },
                )
                work.commit()
            else:
                dependency_hashes = dict(payload.get("dependency_hashes") or {})
                dependency_topics = {
                    dependency_id: _load_dependency_candidate(
                        work,
                        context,
                        run_id=run_id,
                        world_id=world_id,
                        dependency_id=dependency_id,
                        expected_hash=str(expected_hash),
                    )
                    for dependency_id, expected_hash in sorted(dependency_hashes.items())
                }
                work.rollback()

        if existing_result is not None and completed_existing_job is not None:
            run = reconcile_world_generation(run_id, database=db)
            return {
                "ok": True,
                "status": "completed",
                "result_status": str(existing_result.get("status") or ""),
                "job": completed_existing_job,
                "topic_result": dict(existing_result),
                "topic_id": topic_id,
                "content_hash": str(existing_result.get("candidate_hash") or ""),
                "run": run,
                "replayed_existing_result": True,
            }

        node = CampaignTopicNode(
            topic_id=topic_id,
            title=str(topic_payload.get("title") or topic_id),
            category=str(topic_payload.get("category") or "lore"),
            dependencies=tuple(str(item) for item in topic_payload.get("dependencies") or ()),
            generator_role=str(topic_payload.get("generator_role") or "world_forge"),
            required_before_launch=bool(topic_payload.get("required_before_launch", True)),
            visibility=str(topic_payload.get("visibility") or "game_master_canon"),
            target_count=int(topic_payload.get("target_count") or 1),
            metadata=dict(topic_payload.get("metadata") or {}),
        )
        selected_generator = generator
        if selected_generator is None:
            from .generation_routing import build_world_forge_generator_from_settings

            selected_generator = build_world_forge_generator_from_settings(
                dict(payload.get("settings") or {})
            )

        from .generation_contract_bundle import build_topic_contract_bundle

        planned_descriptor = dict(payload.get("contract_descriptor") or {})
        runtime_bundle = build_topic_contract_bundle(
            node,
            allocated_entity_ids=tuple(
                str(item.get("entity_id") or "")
                for item in payload.get("entity_manifest_slots") or ()
                if isinstance(item, Mapping) and str(item.get("entity_id") or "")
            ),
            dependencies=dependency_topics,
        )
        if planned_descriptor and runtime_bundle.descriptor() != planned_descriptor:
            raise RuntimeError(
                f"world_topic_contract_mismatch_before_provider_call:{topic_id}"
            )

        from app.apps.rpg.genesis.worlds.providers.world_forge import attach_world_forge_progress_callback

        def checkpoint_batch_progress(checkpoint: Mapping[str, Any]) -> None:
            token_usage = dict(checkpoint.get("token_usage") or {})
            batch_current = max(0, int(checkpoint.get("batch_current") or 0))
            batch_total = max(1, int(checkpoint.get("batch_total") or 1))
            try:
                with unit_of_work(db) as checkpoint_work:
                    checkpoint_work.jobs.update_progress(
                        context,
                        job_id=job_id,
                        worker_id=worker_id,
                        lease_token=lease_token,
                        progress={
                            "current": batch_current,
                            "total": batch_total,
                            "message": f"world entity batch {batch_current}/{batch_total} generated",
                            "topic_id": topic_id,
                            "token_usage": token_usage,
                        },
                    )
                    checkpoint_work.commit()
            except Exception:
                _LOGGER.warning(
                    "world_generation_progress_checkpoint_failed",
                    exc_info=True,
                    extra={"run_id": run_id, "topic_id": topic_id, "job_id": job_id},
                )

        spooled = read_candidate_spool(job_id)
        if spooled is not None:
            if (
                str(spooled.get("run_id") or "") != run_id
                or str(spooled.get("topic_id") or "") != topic_id
            ):
                raise RuntimeError(f"world_generation_spool_identity_mismatch:{job_id}")
            generated = GeneratedTopic.from_dict(dict(spooled.get("candidate") or {}))
            content = generated.as_dict()
            content_hash = str(spooled.get("candidate_hash") or canonical_hash(content))
            status = str(spooled.get("status") or result_status(generated))
            validation = dict(spooled.get("validation") or review_report(generated))
            provider = dict(spooled.get("provider") or _provider_metadata(generated))
        else:
            detach = attach_world_forge_progress_callback(
                selected_generator,
                checkpoint_batch_progress,
            )
            from app.apps.rpg.foundation.llm_priority import background_rpg_llm_priority

            try:
                with background_rpg_llm_priority():
                    generated = selected_generator.generate(
                        node,
                        seed=int(dict(payload.get("settings") or {}).get("seed") or 0),
                        campaign_context={
                            **dict(payload.get("generation_context") or {}),
                            "world_id": world_id,
                            "draft_revision": int(payload.get("draft_revision") or 1),
                            "topic_directives": dict(payload.get("directives") or {}),
                            "dependency_trust": dict(payload.get("dependency_trust") or {}),
                        },
                        dependency_topics=dependency_topics,
                    )
            finally:
                detach()
            if generated.topic_id != topic_id:
                raise RuntimeError(
                    f"world_topic_generator_returned:{generated.topic_id}:expected:{topic_id}"
                )
            content = generated.as_dict()
            content_hash = canonical_hash(content)
            status = result_status(generated)
            validation = review_report(generated)
            if not validation:
                validation = {
                    "schema_version": "rpg_world_generation_review_v1",
                    "status": "accepted",
                    "blocking": False,
                    "error_type": "",
                    "reason_codes": [],
                    "issues": [],
                    "summary": "Candidate passed blocking validation.",
                }
            provider = _provider_metadata(generated)
            write_candidate_spool(
                job_id,
                {
                    "schema_version": "rpg_world_generation_candidate_spool_v1",
                    "run_id": run_id,
                    "world_id": world_id,
                    "draft_revision": int(payload.get("draft_revision") or 1),
                    "topic_id": topic_id,
                    "candidate": content,
                    "candidate_hash": content_hash,
                    "status": status,
                    "validation": validation,
                    "provider": provider,
                    "dependency_hashes": dict(payload.get("dependency_hashes") or {}),
                    "dependency_trust": dict(payload.get("dependency_trust") or {}),
                    "job_id": job_id,
                },
            )

        provenance = {
            **dict(generated.provenance),
            "generation_fingerprint": str(payload.get("fingerprint") or ""),
            "directive_hash": str(payload.get("directive_hash") or ""),
            "entity_manifest_hash": str(payload.get("entity_manifest_hash") or ""),
            "job_id": job_id,
            "run_id": run_id,
            "generation_result_status": status,
            "contract_descriptor": planned_descriptor,
        }
        content = {**content, "provenance": provenance}
        content_hash = canonical_hash(content)
        with unit_of_work(db) as work:
            stored_result = work.world_generation.put_topic_result(
                context,
                run_id=run_id,
                world_id=world_id,
                draft_revision=int(payload.get("draft_revision") or 1),
                topic_id=topic_id,
                status=status,
                candidate=content,
                candidate_hash=content_hash,
                validation=validation,
                provider=provider,
                dependency_hashes=dict(payload.get("dependency_hashes") or {}),
                dependency_trust=dict(payload.get("dependency_trust") or {}),
                job_id=job_id,
            )
            if status == "accepted":
                work.world_scenarios.put_topic(
                    context,
                    world_id=world_id,
                    topic_id=topic_id,
                    draft_revision=int(payload.get("draft_revision") or 1),
                    source="ai",
                    status="ready",
                    content=content,
                    directives=dict(payload.get("directives") or {}),
                    dependency_hashes=dict(payload.get("dependency_hashes") or {}),
                    input_hash=str(payload.get("input_hash") or ""),
                    content_hash=content_hash,
                    provenance=provenance,
                )
            completed = work.jobs.complete(
                context,
                job_id=job_id,
                worker_id=worker_id,
                lease_token=lease_token,
                output_refs=[
                    {
                        "run_id": run_id,
                        "world_id": world_id,
                        "draft_revision": int(payload.get("draft_revision") or 1),
                        "topic_id": topic_id,
                        "candidate_hash": content_hash,
                        "result_status": status,
                    }
                ],
                progress={
                    "current": 1,
                    "total": 1,
                    "message": (
                        "world topic accepted"
                        if status == "accepted"
                        else "world topic retained for review"
                    ),
                },
            )
            work.commit()
        delete_candidate_spool(job_id)
        run = reconcile_world_generation(run_id, database=db)
        return {
            "ok": True,
            "status": "completed",
            "result_status": status,
            "job": completed,
            "topic_result": stored_result,
            "topic_id": topic_id,
            "content_hash": content_hash,
            "run": run,
        }
    except DatabaseUnavailableError:
        # A second lease may resume this exact spool, but no provider call is repeated.
        raise
    except Exception as exc:
        try:
            with unit_of_work(db) as work:
                report = failure_report(topic_id, exc)
                diagnostics = dict(getattr(exc, "diagnostics", {}) or {})
                artifact = dict(diagnostics.get("failure_artifact") or {})
                if artifact:
                    artifact.update(
                        {
                            "run_id": run_id,
                            "job_id": job_id,
                            "topic_id": topic_id,
                            "attempt": int(job.get("attempt_count") or 1),
                        }
                    )
                    diagnostics["failure_artifact"] = artifact
                directives = dict(payload.get("directives") or {})
                manual_retry = dict(directives.get("manual_retry") or {})
                prior_candidate = manual_retry.get("prior_candidate")
                prior_candidate_source = "retry_directive"
                if manual_retry and not isinstance(prior_candidate, Mapping):
                    authoring_row = work.world_generation.get_topic(
                        context,
                        world_id=world_id,
                        topic_id=topic_id,
                    )
                    authoring_content = (
                        authoring_row.get("content")
                        if isinstance(authoring_row, Mapping)
                        else None
                    )
                    if (
                        isinstance(authoring_row, Mapping)
                        and str(authoring_row.get("status") or "") == "ready"
                        and int(authoring_row.get("draft_revision") or 0)
                        == int(payload.get("draft_revision") or 1)
                        and isinstance(authoring_content, Mapping)
                    ):
                        candidate = GeneratedTopic.from_dict(dict(authoring_content))
                        if candidate.topic_id == topic_id:
                            prior_candidate = authoring_content
                            prior_candidate_source = "durable_authoring"
                if isinstance(prior_candidate, Mapping):
                    retained = dict(prior_candidate)
                    retained_provenance = dict(retained.get("provenance") or {})
                    retained_provenance["manual_retry_failure"] = {
                        "run_id": run_id,
                        "job_id": job_id,
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                        "prior_candidate_retained": True,
                        "prior_candidate_source": prior_candidate_source,
                    }
                    retained["provenance"] = retained_provenance
                    retained_hash = canonical_hash(retained)
                    reason_codes = list(report.get("reason_codes") or ())
                    reason_codes.insert(
                        0, "manual_retry_failed_prior_candidate_retained"
                    )
                    report = {
                        **report,
                        "status": "needs_review",
                        "blocking": True,
                        "reason_codes": list(dict.fromkeys(reason_codes)),
                        "summary": (
                            "Retry failed; the prior approved candidate was retained "
                            "so dependent regeneration can continue."
                        ),
                    }
                    stored_result = work.world_generation.put_topic_result(
                        context,
                        run_id=run_id,
                        world_id=world_id,
                        draft_revision=int(payload.get("draft_revision") or 1),
                        topic_id=topic_id,
                        status="needs_review",
                        candidate=retained,
                        candidate_hash=retained_hash,
                        validation=report,
                        provider=diagnostics,
                        dependency_hashes=dict(
                            payload.get("dependency_hashes") or {}
                        ),
                        dependency_trust=dict(
                            payload.get("dependency_trust") or {}
                        ),
                        job_id=job_id,
                    )
                    completed_job = work.jobs.complete(
                        context,
                        job_id=job_id,
                        worker_id=worker_id,
                        lease_token=lease_token,
                        output_refs=[
                            {
                                "run_id": run_id,
                                "world_id": world_id,
                                "draft_revision": int(
                                    payload.get("draft_revision") or 1
                                ),
                                "topic_id": topic_id,
                                "candidate_hash": retained_hash,
                                "result_status": "needs_review",
                                "prior_candidate_retained": True,
                            }
                        ],
                        progress={
                            "current": 1,
                            "total": 1,
                            "message": (
                                "retry failed; prior approved topic retained"
                            ),
                        },
                    )
                else:
                    work.world_generation.put_topic_result(
                        context,
                        run_id=run_id,
                        world_id=world_id,
                        draft_revision=int(payload.get("draft_revision") or 1),
                        topic_id=topic_id,
                        status="failed",
                        candidate=None,
                        candidate_hash="",
                        validation=report,
                        provider=diagnostics,
                        dependency_hashes=dict(
                            payload.get("dependency_hashes") or {}
                        ),
                        dependency_trust=dict(
                            payload.get("dependency_trust") or {}
                        ),
                        job_id=job_id,
                    )
                    failed_job = _terminally_fail_job(
                        work,
                        context,
                        job=job,
                        worker_id=worker_id,
                        lease_token=lease_token,
                        error={
                            "code": "world_topic_generation_failed",
                            "type": type(exc).__name__,
                            "message": str(exc),
                        },
                    )
                work.commit()
            run = reconcile_world_generation(run_id, database=db)
        except DatabaseUnavailableError:
            raise
        if isinstance(prior_candidate, Mapping):
            return {
                "ok": True,
                "status": "completed",
                "result_status": "needs_review",
                "job": completed_job,
                "topic_result": stored_result,
                "topic_id": topic_id,
                "content_hash": retained_hash,
                "run": run,
                "prior_candidate_retained": True,
                "retry_error": str(exc),
            }
        return {
            "ok": False,
            "status": "failed",
            "job": failed_job,
            "topic_id": topic_id,
            "error": "world_topic_generation_failed",
            "detail": str(exc),
        }
