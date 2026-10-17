"""Explicit Game Master review and retry for World Forge candidates."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from app.security.tenant_context import current_tenant
from app.persistence.unit_of_work import unit_of_work
from app.apps.rpg.genesis.forge.world_forge_generation import GeneratedTopic

from .generation_coordinator import (
    _graph_from_payload,
    _settings_from_payload,
    reconcile_world_generation,
    start_world_generation,
)
from .generation_dependency_invalidation import (
    apply_stale_progress,
    build_retry_invalidation_records,
)
from .generation_diagnostics import log_world_generation_event
from .generation_jobs import (
    canonical_generation_directives,
    canonical_hash,
    topic_generation_fingerprint,
)
from .generation_contract_receipt import contract_descriptor_from_candidate
from .generation_worker import kick_world_generation_worker
from .lifecycle_service import require_world_writable
from .profile_authoring import require_approved_profile

_NON_GENERATION_CATEGORIES = {"compiler", "audit", "index", "bootstrap"}


def _execution_summary(run: Mapping[str, Any]) -> dict[str, Any]:
    plan = dict(run.get("plan") or {})
    progress = dict(run.get("progress") or {})
    return {
        "queued_topic_ids": list(plan.get("new_topic_ids") or ()),
        "reused_topic_ids": list(plan.get("reusable_topic_ids") or ()),
        "protected_topic_ids": list(plan.get("protected_topic_ids") or ()),
        "target_topic_ids": list(plan.get("topic_ids") or ()),
        "queued_count": len(plan.get("new_job_ids") or ()),
        "reused_count": len(plan.get("reusable_topic_ids") or ()),
        "protected_count": len(plan.get("protected_topic_ids") or ()),
        "active_count": len(progress.get("active_topic_ids") or ()),
        "flagged_count": len(progress.get("flagged_topic_ids") or ()),
        "failed_count": len(progress.get("failed_topic_ids") or ()),
        "blocked_count": len(progress.get("blocked_topic_ids") or ()),
        "stale_count": len(progress.get("stale_topic_ids") or ()),
    }


def _require_run_profile_matches_approval(
    run: Mapping[str, Any],
    graph: Any,
    approval: Mapping[str, Any],
) -> dict[str, Any]:
    run_context = dict(run.get("context") or {})
    generation_context = dict(run_context.get("generation_context") or {})
    graph_metadata = dict(getattr(graph, "metadata", {}) or {})
    run_profile_hash = str(
        generation_context.get("approved_profile_hash")
        or generation_context.get("resolved_profile_hash")
        or graph_metadata.get("resolved_profile_hash")
        or ""
    )
    approved_hash = str(approval.get("approved_profile_hash") or "")
    if not run_profile_hash or run_profile_hash != approved_hash:
        raise ValueError("world_profile_approval_hash_mismatch")
    return generation_context


def _reviewable_topic_ids(run: Mapping[str, Any]) -> tuple[str, ...]:
    progress = dict(run.get("progress") or {})
    return tuple(
        dict.fromkeys(
            str(topic_id)
            for key in (
                "flagged_topic_ids",
                "failed_topic_ids",
                "blocked_topic_ids",
            )
            for topic_id in progress.get(key) or ()
            if str(topic_id)
        )
    )


def _generation_ids(graph: Any) -> tuple[str, ...]:
    return tuple(
        node.topic_id
        for node in graph.topological_order()
        if node.category not in _NON_GENERATION_CATEGORIES
    )


def _retry_closure(
    graph: Any,
    selected: Sequence[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return selected retry topics and only the dependencies they require.

    Downstream consumers are not silently regenerated. They are recorded as
    potentially stale and must be revalidated, invalidated, or explicitly
    regenerated according to the changed contract.
    """

    node_map = graph.node_map()
    affected = {
        str(topic_id)
        for topic_id in selected
        if node_map.get(str(topic_id)) is not None
        and node_map[str(topic_id)].category not in _NON_GENERATION_CATEGORIES
    }
    targets = set(affected)
    pending = list(affected)
    while pending:
        topic_id = pending.pop()
        node = node_map[topic_id]
        for dependency in node.dependencies:
            dependency_node = node_map.get(dependency)
            if dependency_node is None or dependency_node.category in _NON_GENERATION_CATEGORIES:
                continue
            if dependency not in targets:
                targets.add(dependency)
                pending.append(dependency)
    order = _generation_ids(graph)
    return (
        tuple(topic_id for topic_id in order if topic_id in affected),
        tuple(topic_id for topic_id in order if topic_id in targets),
    )


def _authoring_locked(row: Mapping[str, Any]) -> bool:
    provenance = row.get("provenance")
    provenance = provenance if isinstance(provenance, Mapping) else {}
    authoring = provenance.get("authoring")
    authoring = authoring if isinstance(authoring, Mapping) else {}
    return str(row.get("source") or "") == "manual" or bool(authoring.get("generation_lock"))


def _previous_result_rows(
    result_rows: Sequence[Mapping[str, Any]],
    topic_rows: Sequence[Mapping[str, Any]],
    fallback_result_rows: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Mapping[str, Any]]:
    previous = {str(row.get("topic_id") or ""): row for row in result_rows}
    for row in fallback_result_rows:
        topic_id = str(row.get("topic_id") or "")
        candidate = row.get("candidate")
        current_candidate = previous.get(topic_id, {}).get("candidate")
        if (
            topic_id
            and isinstance(candidate, Mapping)
            and not isinstance(current_candidate, Mapping)
            and str(row.get("status") or "") in {"accepted", "needs_review"}
        ):
            previous[topic_id] = row
    for row in topic_rows:
        topic_id = str(row.get("topic_id") or "")
        content = row.get("content")
        if (
            not topic_id
            or str(row.get("status") or "") != "ready"
            or not isinstance(content, Mapping)
        ):
            continue
        # Durable authoring canon is newer and more authoritative than the
        # result that originally produced it. In particular, a Game Master may
        # have accepted a candidate after that result was recorded.
        previous[topic_id] = {
            "topic_id": topic_id,
            "status": "accepted",
            "candidate": dict(content),
            "candidate_hash": str(row.get("content_hash") or ""),
            "validation": {"reason_codes": [], "issues": []},
        }
    return previous


def _manual_retry_directives(
    original: Mapping[str, Mapping[str, Any]],
    *,
    selected_topic_ids: Sequence[str],
    requested_topic_ids: Sequence[str],
    previous_results: Mapping[str, Mapping[str, Any]],
    retry_scopes: Mapping[str, Mapping[str, Any]],
    parent_run_id: str,
) -> dict[str, dict[str, Any]]:
    directives = {
        str(topic_id): dict(value)
        for topic_id, value in original.items()
        if isinstance(value, Mapping)
    }
    requested_set = set(requested_topic_ids)
    for topic_id in selected_topic_ids:
        previous = dict(previous_results.get(topic_id) or {})
        validation = dict(previous.get("validation") or {})
        requested = dict(retry_scopes.get(topic_id) or {})
        prior_candidate = previous.get("candidate")
        is_requested = topic_id in requested_set
        directives[topic_id] = {
            **directives.get(topic_id, {}),
            "manual_retry": {
                "parent_run_id": parent_run_id,
                "prior_status": str(previous.get("status") or ""),
                "prior_candidate_hash": str(previous.get("candidate_hash") or ""),
                "prior_candidate": (
                    dict(prior_candidate)
                    if isinstance(prior_candidate, Mapping)
                    else None
                ),
                "reason_codes": list(validation.get("reason_codes") or ()),
                "issues": list(validation.get("issues") or ()),
                "scope": str(requested.get("scope") or "topic"),
                "entity_ids": list(requested.get("entity_ids") or ()),
                "fields": list(requested.get("fields") or ()),
                "instructions": list(requested.get("instructions") or ()),
                "requested_by_game_master": is_requested,
                "affected_by_upstream_retry": not is_requested,
                "required_behavior": (
                    "Create a complete candidate for this retry decision group. Preserve stable "
                    "IDs and valid unaffected canon. Resolve listed validation issues and all "
                    "effects of corrected upstream dependencies without unresolved references."
                ),
            },
        }
    return directives


def _pin_parent_run(
    child_run_id: str,
    parent_run: Mapping[str, Any],
    *,
    database: Any | None,
) -> None:
    context = current_tenant()
    parent_run_id = str(parent_run["run_id"])
    parent_lineage = dict(parent_run.get("lineage") or {})
    root_run_id = str(parent_lineage.get("root_run_id") or parent_run_id)
    lineage = {
        "root_run_id": root_run_id,
        "parent_run_id": parent_run_id,
        "parent_draft_revision": int(parent_run["draft_revision"]),
        "draft_revision": int(parent_run["draft_revision"]),
        "manual_retry": True,
    }
    with unit_of_work(database) as work:
        connection = getattr(work, "connection", None)
        if connection is None:
            rollback = getattr(work, "rollback", None)
            if callable(rollback):
                rollback()
            return
        connection.execute(
            "UPDATE omnix_rpg_world_generation_runs "
            "SET parent_run_id = %s, lineage_jsonb = %s::jsonb, "
            "updated_at = CURRENT_TIMESTAMP "
            "WHERE workspace_id = %s AND run_id = %s",
            (
                parent_run_id,
                json.dumps(lineage, sort_keys=True),
                context.workspace_id,
                child_run_id,
            ),
        )
        work.commit()


def _record_stale_dependants(
    child_run_id: str,
    records: Mapping[str, Mapping[str, Any]],
    *,
    database: Any | None,
) -> dict[str, Any]:
    context = current_tenant()
    with unit_of_work(database) as work:
        child = work.world_generation.get(context, child_run_id)
        if child is None:
            work.rollback()
            raise KeyError(f"world_generation_run_not_found:{child_run_id}")
        progress = apply_stale_progress(child.get("progress"), records)
        work.world_generation.update(
            context,
            run_id=child_run_id,
            progress=progress,
        )
        work.commit()
    return progress


def retry_failed_world_generation(
    run_id: str,
    *,
    selected_topic_ids: Sequence[str] = (),
    retry_scopes: Mapping[str, Mapping[str, Any]] | None = None,
    database: Any | None = None,
    kick_worker: bool = True,
    diagnostic_id: str | None = None,
) -> dict[str, Any]:
    """Retry selected review outcomes plus required upstream dependencies."""

    context = current_tenant()
    with unit_of_work(database) as work:
        parent_run = work.world_generation.get(context, run_id)
        if parent_run is None:
            work.rollback()
            raise KeyError(f"world_generation_run_not_found:{run_id}")
        world_id = str(parent_run["world_id"])
        world = require_world_writable(work, context, world_id)
        topics = work.world_library.list_topics(context, world_id)
        result_rows = work.world_generation.list_topic_results(context, run_id=run_id)
        ancestor_result_rows: list[Mapping[str, Any]] = []
        ancestor_run_id = str(parent_run.get("parent_run_id") or "")
        if ancestor_run_id:
            ancestor_result_rows = work.world_generation.list_topic_results(
                context,
                run_id=ancestor_run_id,
            )
        work.rollback()

    if str(parent_run.get("status") or "") not in {"review", "failed"}:
        raise ValueError("world_generation_retry_requires_completed_review")
    profile_approval = require_approved_profile(world)
    draft_revision = int(parent_run["draft_revision"])
    if draft_revision != int(world["draft_revision"]):
        raise ValueError(
            "world_generation_retry_revision_conflict:"
            f"run={draft_revision}:current={world['draft_revision']}"
        )

    reviewable = _reviewable_topic_ids(parent_run)
    requested = tuple(
        dict.fromkeys(str(value) for value in selected_topic_ids if str(value))
    )
    if requested:
        invalid = sorted(set(requested) - set(reviewable))
        if invalid:
            raise ValueError("generation_topics_not_reviewable:" + ",".join(invalid))
    selected = requested or reviewable
    if not selected:
        raise ValueError("generation_scope_empty:review")

    graph = _graph_from_payload(dict(parent_run.get("graph") or {}))
    generation_context = _require_run_profile_matches_approval(
        parent_run,
        graph,
        profile_approval,
    )
    affected, targets = _retry_closure(graph, selected)
    resolved_scopes = {
        str(key): dict(value)
        for key, value in dict(retry_scopes or {}).items()
        if isinstance(value, Mapping)
    }
    stale_dependants = build_retry_invalidation_records(
        graph,
        affected,
        resolved_scopes,
    )
    topic_map = {str(row.get("topic_id") or ""): row for row in topics}
    locked = sorted(topic_id for topic_id in affected if _authoring_locked(topic_map.get(topic_id, {})))
    if locked:
        raise ValueError("generation_topics_locked:" + ",".join(locked))
    normalized_scope = {
        "mode": "review",
        "topic_ids": list(selected),
        "affected_topic_ids": list(affected),
        "resolved_topic_ids": list(targets),
        "include_dependencies": True,
        "include_dependents": False,
        "stale_topic_ids": sorted(stale_dependants),
        "stale_topics": stale_dependants,
        "replace_locked": False,
    }
    previous_results = _previous_result_rows(
        result_rows,
        topics,
        ancestor_result_rows,
    )
    run_context = dict(parent_run.get("context") or {})
    topic_directives = _manual_retry_directives(
        {
            str(topic_id): dict(value)
            for topic_id, value in dict(run_context.get("topic_directives") or {}).items()
            if isinstance(value, Mapping)
        },
        selected_topic_ids=affected,
        requested_topic_ids=selected,
        previous_results=previous_results,
        retry_scopes=resolved_scopes,
        parent_run_id=run_id,
    )
    settings = _settings_from_payload(dict(parent_run.get("settings") or {}))
    retry_scope = {
        **normalized_scope,
        "retry_of_run_id": run_id,
        "selected_topic_ids": list(selected),
        "decision_topic_ids": list(affected),
        "previous_results": {
            topic_id: {
                "status": str(previous_results.get(topic_id, {}).get("status") or ""),
                "candidate_hash": str(previous_results.get(topic_id, {}).get("candidate_hash") or ""),
            }
            for topic_id in affected
            if topic_id in previous_results
        },
    }

    log_world_generation_event(
        "world_generation.manual_retry_requested",
        diagnostic_id=diagnostic_id,
        world_id=world_id,
        run_id=run_id,
        fields={
            "draft_revision": draft_revision,
            "selected_topic_ids": selected,
            "affected_topic_ids": affected,
            "resolved_topic_ids": targets,
            "stale_topic_ids": tuple(sorted(stale_dependants)),
            "provider_route": settings.provider_route,
            "model": settings.model,
            "approved_profile_hash": profile_approval["approved_profile_hash"],
        },
    )
    child_run = start_world_generation(
        world_id=world_id,
        draft_revision=draft_revision,
        graph=graph,
        generation_context=generation_context,
        topic_directives=topic_directives,
        entity_manifest_hash=str(run_context.get("entity_manifest_hash") or ""),
        settings=settings,
        target_topic_ids=targets,
        forced_topic_ids=affected,
        pinned_topic_ids=tuple(
            topic_id for topic_id in targets if topic_id not in set(affected)
        ),
        scope=retry_scope,
        strategy="force",
        database=database,
        tenant_context=context,
    )
    child_run_id = str(child_run["run_id"])
    _pin_parent_run(child_run_id, parent_run, database=database)
    stale_progress = _record_stale_dependants(
        child_run_id,
        stale_dependants,
        database=database,
    )
    child_run = {**dict(child_run), "progress": stale_progress}
    worker_started = (
        kick_world_generation_worker(database=database, provider_route=settings.provider_route)
        if kick_worker
        else False
    )
    return {
        "ok": True,
        "run": child_run,
        "worker_started": worker_started,
        "scope": retry_scope,
        "retry_of_run_id": run_id,
        "diagnostic_id": diagnostic_id,
        "execution_summary": _execution_summary(child_run),
        "resolved_route": {
            "provider": settings.provider_route,
            "model": settings.model,
            "source": "manual_retry_durable_run",
        },
    }


def _clean_promoted_candidate(candidate: Mapping[str, Any], *, decision: str) -> dict[str, Any]:
    payload = dict(candidate)
    provenance = dict(payload.get("provenance") or {})
    provenance.pop("generation_review", None)
    provenance["generation_status"] = "accepted"
    provenance["manual_retry_pending_decision"] = False
    provenance["manual_retry_decision"] = decision
    payload["provenance"] = provenance
    return payload


def decide_world_generation_retry(
    run_id: str,
    topic_id: str,
    *,
    decision: str,
    database: Any | None = None,
) -> dict[str, Any]:
    """Explicitly keep the prior authoring topic or promote one valid retry candidate."""

    if decision not in {"keep", "replace"}:
        raise ValueError(f"invalid_world_generation_retry_decision:{decision}")
    context = current_tenant()
    with unit_of_work(database) as work:
        run = work.world_generation.get(context, run_id)
        if run is None:
            work.rollback()
            raise KeyError(f"world_generation_run_not_found:{run_id}")
        if str(run.get("status") or "") not in {"review", "failed"}:
            work.rollback()
            raise ValueError("world_generation_decision_requires_completed_review")
        scope = dict(dict(run.get("context") or {}).get("scope") or {})
        if not scope.get("retry_of_run_id"):
            work.rollback()
            raise ValueError("world_generation_decision_requires_manual_retry_run")
        result = work.world_generation.get_topic_result(
            context,
            run_id=run_id,
            topic_id=topic_id,
        )
        if result is None:
            work.rollback()
            raise KeyError(f"world_generation_topic_result_not_found:{run_id}:{topic_id}")
        reason_codes = set(dict(result.get("validation") or {}).get("reason_codes") or ())
        if "manual_retry_decision_required" not in reason_codes:
            work.rollback()
            raise ValueError("world_generation_candidate_not_ready_for_decision")

        plan = dict(run.get("plan") or {})
        decisions = dict(plan.get("review_decisions") or {})
        decision_row: dict[str, Any] = {
            "decision": decision,
            "candidate_hash": str(result.get("candidate_hash") or ""),
            "decided_at": datetime.now(timezone.utc).isoformat(),
        }
        if decision == "replace":
            candidate_value = result.get("candidate")
            if not isinstance(candidate_value, Mapping):
                work.rollback()
                raise ValueError("world_generation_retry_candidate_missing")
            graph = _graph_from_payload(dict(run.get("graph") or {}))
            node = graph.node_map()[topic_id]
            run_context = dict(run.get("context") or {})
            generation_context = dict(run_context.get("generation_context") or {})
            directives = canonical_generation_directives(
                dict(dict(run_context.get("topic_directives") or {}).get(topic_id) or {})
            )
            settings = _settings_from_payload(dict(run.get("settings") or {}))
            dependency_hashes = {
                str(key): str(value)
                for key, value in dict(result.get("dependency_hashes") or {}).items()
            }
            dependency_trust = dict(result.get("dependency_trust") or {})
            fingerprint, input_hash, directive_hash = topic_generation_fingerprint(
                node,
                normalized_topic_input={
                    "generation_context": generation_context,
                    "target_count": node.target_count,
                    "visibility": node.visibility,
                    "dependency_trust": dependency_trust,
                },
                dependency_hashes=dependency_hashes,
                directives=directives,
                entity_manifest_hash=str(run_context.get("entity_manifest_hash") or ""),
                settings=settings,
                contract_descriptor=contract_descriptor_from_candidate(candidate_value),
            )
            candidate = _clean_promoted_candidate(candidate_value, decision=decision)
            GeneratedTopic.from_dict(candidate)
            provenance = dict(candidate.get("provenance") or {})
            provenance.update(
                {
                    "generation_fingerprint": fingerprint,
                    "directive_hash": directive_hash,
                    "run_id": run_id,
                    "generation_result_status": "accepted",
                }
            )
            candidate["provenance"] = provenance
            promoted_hash = canonical_hash(candidate)
            work.world_scenarios.put_topic(
                context,
                world_id=str(run["world_id"]),
                topic_id=topic_id,
                draft_revision=int(run["draft_revision"]),
                source="ai",
                status="ready",
                content=candidate,
                directives=directives,
                dependency_hashes=dependency_hashes,
                input_hash=input_hash,
                content_hash=promoted_hash,
                provenance=provenance,
            )
            decision_row["promoted_hash"] = promoted_hash

        decisions[topic_id] = decision_row
        plan["review_decisions"] = decisions
        progress = dict(run.get("progress") or {})
        pending = set(str(value) for value in scope.get("decision_topic_ids") or ())
        replaced = {
            key for key, value in decisions.items()
            if isinstance(value, Mapping) and str(value.get("decision") or "") == "replace"
        }
        kept = {
            key for key, value in decisions.items()
            if isinstance(value, Mapping) and str(value.get("decision") or "") == "keep"
        }
        pending.difference_update(replaced | kept)
        progress["pending_decision_topic_ids"] = sorted(pending)
        progress["kept_previous_topic_ids"] = sorted(kept)
        progress["publication_blocked"] = bool(
            pending
            or kept
            or progress.get("stale_topic_ids")
            or progress.get("failed_topic_ids")
            or progress.get("blocked_topic_ids")
            or [
                value
                for value in progress.get("flagged_topic_ids") or ()
                if str(value) not in replaced
            ]
        )
        work.world_generation.update(
            context,
            run_id=run_id,
            plan=plan,
            progress=progress,
        )
        work.commit()
    reconciled = reconcile_world_generation(run_id, database=database)
    return {
        "ok": True,
        "run_id": run_id,
        "topic_id": topic_id,
        "decision": decision,
        "decision_record": decision_row,
        "run": reconciled,
    }


def continue_world_generation(
    run_id: str,
    *,
    database: Any | None = None,
    kick_worker: bool = True,
    diagnostic_id: str | None = None,
) -> dict[str, Any]:
    context = current_tenant()
    with unit_of_work(database) as work:
        existing = work.world_generation.get(context, run_id)
        if existing is None:
            work.rollback()
            raise KeyError(f"world_generation_run_not_found:{run_id}")
        work.rollback()
    if str(existing.get("status") or "") == "running":
        reconciled = reconcile_world_generation(run_id, database=database)
        progress = dict(reconciled.get("progress") or {})
        settings = _settings_from_payload(dict(reconciled.get("settings") or {}))
        worker_started = bool(
            kick_worker
            and not progress.get("generation_complete")
            and kick_world_generation_worker(
                database=database,
                provider_route=settings.provider_route,
            )
        )
        log_world_generation_event(
            "world_generation.running_run_resumed",
            diagnostic_id=diagnostic_id,
            world_id=str(reconciled.get("world_id") or ""),
            run_id=run_id,
            fields={
                "provider_route": settings.provider_route,
                "model": settings.model,
                "worker_started": worker_started,
                "active_topic_ids": tuple(progress.get("active_topic_ids") or ()),
                "flagged_topic_ids": tuple(progress.get("flagged_topic_ids") or ()),
            },
        )
        return {
            "ok": True,
            "run": reconciled,
            "worker_started": worker_started,
            "continue_of_run_id": run_id,
            "execution_summary": _execution_summary(reconciled),
            "resolved_route": {
                "provider": settings.provider_route,
                "model": settings.model,
                "source": "existing_running_run",
            },
        }
    result = retry_failed_world_generation(
        run_id,
        database=database,
        kick_worker=kick_worker,
        diagnostic_id=diagnostic_id,
    )
    return {**result, "continue_of_run_id": run_id}
