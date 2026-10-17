"""Promote retained World Forge candidates without relabelling their authorship."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from app.security.tenant_context import current_tenant
from app.apps.rpg.foundation.persistence.rpg_repository import canonical_json
from app.persistence.unit_of_work import unit_of_work
from app.apps.rpg.genesis.forge.world_forge_generation import (
    GeneratedTopic,
    validate_generated_topic_for_publication,
)
from app.apps.rpg.genesis.forge.world_forge_dossiers import (
    dossier_prompt_contract,
    validate_entity_dossier,
)

from .generation_authorship_policy_signing import (
    bind_signed_authorship_policy,
    require_policy_bound_authorship,
    signed_authorship_policy,
)
from .generation_authorship_runtime import generation_artifact
from .generation_authorship_signing import attach_signed_human_authorship, sign_record
from .generation_coordinator import (
    _graph_from_payload,
    _settings_from_payload,
    reconcile_world_generation,
)
from .generation_jobs import (
    canonical_generation_directives,
    canonical_hash,
    topic_generation_fingerprint,
)
from .generation_review_state import accepted_review_report
from .generation_contract_receipt import (
    canonical_candidate_content_hash,
    contract_descriptor_from_candidate,
    require_authoritative_contract_receipt,
)
from .lifecycle_service import require_world_writable

_ACCEPTED_DECISIONS = {"accept", "replace"}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _require_reviewable_entity_dossiers(
    candidate: dict[str, Any],
    *,
    topic_id: str,
) -> None:
    dossier_contract = dict(
        dict(dossier_prompt_contract(topic_id).get("entity_fields") or {}).get(
            "dossier"
        )
        or {}
    )
    expected_section_ids = tuple(
        str(section.get("id") or "")
        for section in dossier_contract.get("sections") or ()
        if isinstance(section, Mapping) and str(section.get("id") or "")
    )
    entities = candidate.get("entities")
    if not isinstance(entities, Sequence) or isinstance(entities, (str, bytes)):
        return
    issues: list[str] = []
    for index, value in enumerate(entities):
        if not isinstance(value, Mapping):
            continue
        entity_id = str(value.get("id") or value.get("entity_id") or index)
        if not str(value.get("short_summary") or "").strip():
            issues.append(f"{entity_id}:short_summary_required")
        for issue in validate_entity_dossier(value.get("dossier")):
            # The dossier projection supplies a stable fallback title (for
            # example, "Section 1") when a provider leaves a section title
            # blank. This is presentation metadata, unlike a missing section
            # or prose, and should not block accepting otherwise usable canon.
            if issue.startswith("section_title_required:"):
                continue
            issues.append(f"{entity_id}:{issue}")
        dossier = _mapping(value.get("dossier"))
        actual_section_ids = tuple(
            str(section.get("id") or "")
            for section in dossier.get("sections") or ()
            if isinstance(section, Mapping)
        )
        if actual_section_ids != expected_section_ids:
            issues.append(
                f"{entity_id}:dossier_section_contract_mismatch:"
                + ",".join(actual_section_ids)
            )
    if issues:
        raise ValueError(
            f"world_generation_accept_dossiers_required:{topic_id}:"
            + ",".join(issues)
        )


def _accepted_candidate(
    candidate: Mapping[str, Any],
    *,
    original_candidate: Mapping[str, Any],
    run_id: str,
    topic_id: str,
    accepted_at: str,
    original_candidate_hash: str,
    edited: bool,
) -> tuple[dict[str, Any], str]:
    payload = dict(candidate)
    if str(payload.get("topic_id") or "") != topic_id:
        raise ValueError(
            f"world_generation_accept_topic_mismatch:{payload.get('topic_id')}:{topic_id}"
        )
    validate_generated_topic_for_publication(
        GeneratedTopic.from_dict(payload),
        expected_topic_id=topic_id,
    )
    _require_reviewable_entity_dossiers(payload, topic_id=topic_id)
    require_authoritative_contract_receipt(
        original_candidate,
        expected_topic_id=topic_id,
        require_signature=True,
    )
    if not edited:
        require_authoritative_contract_receipt(
            payload,
            expected_topic_id=topic_id,
            require_signature=True,
        )

    policy = signed_authorship_policy(original_candidate)
    artifact = generation_artifact(original_candidate)
    if edited:
        event_id = f"humanedit:{run_id}:{topic_id}:{accepted_at}"
        payload = attach_signed_human_authorship(
            payload,
            event_id=event_id,
            prior_candidate=original_candidate,
            edited_llm=bool(artifact),
            policy=policy,
        )
        payload = bind_signed_authorship_policy(payload, policy)
        edited_receipt = sign_record(
            {
                **require_authoritative_contract_receipt(
                    original_candidate,
                    expected_topic_id=topic_id,
                    require_signature=True,
                ),
                "canonical_content_hash": canonical_candidate_content_hash(payload),
                "human_edited": True,
                "human_edit_event_id": event_id,
            }
        )
        edited_provenance = _mapping(payload.get("provenance"))
        edited_provenance["authoritative_contract_receipt"] = edited_receipt
        payload["provenance"] = edited_provenance
        source = "manual"
    else:
        require_policy_bound_authorship(payload, policy=policy)
        source = "ai"

    provenance = _mapping(payload.get("provenance"))
    provenance.pop("generation_review", None)
    authoring = _mapping(provenance.get("authoring"))
    authoring.update(
        {
            "approved_at": accepted_at,
            "approved_by": "local-game-master",
            "edit_state": (
                "review_candidate_human_edited" if edited else "review_candidate_accepted"
            ),
            "authorship_preserved": True,
        }
    )
    provenance.update(
        {
            "authoring": authoring,
            "generation_status": "accepted",
            "generation_result_status": "accepted",
            "review_acceptance": {
                "run_id": run_id,
                "accepted_at": accepted_at,
                "original_candidate_hash": original_candidate_hash,
                "edited_before_acceptance": edited,
                "source_after_acceptance": source,
            },
        }
    )
    payload["provenance"] = provenance
    require_policy_bound_authorship(payload, policy=policy)
    return payload, source


def _promotion_inputs(
    run: Mapping[str, Any],
    result: Mapping[str, Any],
    *,
    topic_id: str,
    candidate: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], str, str, str]:
    graph = _graph_from_payload(_mapping(run.get("graph")))
    node = graph.node_map().get(topic_id)
    if node is None:
        raise ValueError(f"world_generation_accept_unknown_topic:{topic_id}")
    run_context = _mapping(run.get("context"))
    generation_context = _mapping(run_context.get("generation_context"))
    directives = canonical_generation_directives(
        _mapping(_mapping(run_context.get("topic_directives")).get(topic_id))
    )
    settings = _settings_from_payload(_mapping(run.get("settings")))
    dependency_hashes = {
        str(key): str(value)
        for key, value in _mapping(result.get("dependency_hashes")).items()
    }
    dependency_trust = _mapping(result.get("dependency_trust"))
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
        contract_descriptor=contract_descriptor_from_candidate(candidate),
    )
    provenance = _mapping(candidate.get("provenance"))
    provenance.update(
        {
            "generation_fingerprint": fingerprint,
            "directive_hash": directive_hash,
            "run_id": str(run.get("run_id") or ""),
            "generation_result_status": "accepted",
        }
    )
    candidate["provenance"] = provenance
    return directives, dependency_hashes, input_hash, directive_hash, fingerprint


def _mark_result_accepted(
    work: Any,
    context: Any,
    *,
    run_id: str,
    topic_id: str,
    candidate: Mapping[str, Any],
    candidate_hash: str,
    previous_validation: Mapping[str, Any],
    accepted_at: str,
    waiver_reason: str = "",
) -> dict[str, Any]:
    validation = accepted_review_report(
        previous_validation,
        accepted_at=accepted_at,
        waiver_reason=waiver_reason,
    )
    cursor = work.connection.execute(
        "UPDATE omnix_rpg_world_generation_topic_results "
        "SET status = 'accepted', candidate_jsonb = %s::jsonb, candidate_hash = %s, "
        "validation_jsonb = %s::jsonb, updated_at = CURRENT_TIMESTAMP "
        "WHERE workspace_id = %s AND run_id = %s AND topic_id = %s",
        (
            canonical_json(dict(candidate)),
            candidate_hash,
            canonical_json(validation),
            context.workspace_id,
            run_id,
            topic_id,
        ),
    )
    if int(getattr(cursor, "rowcount", 0) or 0) != 1:
        raise KeyError(f"world_generation_topic_result_not_found:{run_id}:{topic_id}")
    return validation


def _promote(
    work: Any,
    context: Any,
    *,
    run: Mapping[str, Any],
    result: Mapping[str, Any],
    topic_id: str,
    candidate_override: Mapping[str, Any] | None,
    expected_candidate_hash: str,
    accepted_at: str,
    waiver_reason: str = "",
) -> dict[str, Any]:
    if str(result.get("status") or "") != "needs_review":
        raise ValueError(f"world_generation_candidate_not_reviewable:{topic_id}")
    original_candidate = result.get("candidate")
    if not isinstance(original_candidate, Mapping):
        raise ValueError(f"world_generation_review_candidate_missing:{topic_id}")
    original_hash = str(result.get("candidate_hash") or "")
    if expected_candidate_hash and expected_candidate_hash != original_hash:
        raise ValueError(
            "world_generation_candidate_hash_conflict:"
            f"expected={expected_candidate_hash}:current={original_hash}"
        )
    source_candidate = candidate_override if candidate_override is not None else original_candidate
    edited = candidate_override is not None and dict(source_candidate) != dict(original_candidate)
    candidate, source = _accepted_candidate(
        source_candidate,
        original_candidate=original_candidate,
        run_id=str(run["run_id"]),
        topic_id=topic_id,
        accepted_at=accepted_at,
        original_candidate_hash=original_hash,
        edited=edited,
    )
    directives, dependency_hashes, input_hash, _directive_hash, _fingerprint = (
        _promotion_inputs(run, result, topic_id=topic_id, candidate=candidate)
    )
    promoted_hash = canonical_hash(candidate)
    work.world_scenarios.put_topic(
        context,
        world_id=str(run["world_id"]),
        topic_id=topic_id,
        draft_revision=int(run["draft_revision"]),
        source=source,
        status="ready",
        content=candidate,
        directives=directives,
        dependency_hashes=dependency_hashes,
        input_hash=input_hash,
        content_hash=promoted_hash,
        provenance=_mapping(candidate.get("provenance")),
    )
    validation = _mark_result_accepted(
        work,
        context,
        run_id=str(run["run_id"]),
        topic_id=topic_id,
        candidate=candidate,
        candidate_hash=promoted_hash,
        previous_validation=_mapping(result.get("validation")),
        accepted_at=accepted_at,
        waiver_reason=waiver_reason,
    )
    return {
        "decision": "accept",
        "candidate_hash": original_hash,
        "promoted_hash": promoted_hash,
        "decided_at": accepted_at,
        "edited": edited,
        "source": source,
        "review_decision": validation["review_decision"],
        "validation_status": validation["validation_status"],
        "waiver_status": validation["waiver_status"],
        "outstanding_finding_count": len(validation["outstanding_findings"]),
    }


def accept_world_generation_candidates(
    run_id: str,
    *,
    topic_ids: Sequence[str] = (),
    candidate_overrides: Mapping[str, Mapping[str, Any]] | None = None,
    expected_candidate_hashes: Mapping[str, str] | None = None,
    waiver_reasons: Mapping[str, str] | None = None,
    default_waiver_reason: str = "",
    database: Any | None = None,
) -> dict[str, Any]:
    """Accept candidates while keeping review decisions separate from validation evidence."""

    context = current_tenant()
    overrides = dict(candidate_overrides or {})
    expected_hashes = dict(expected_candidate_hashes or {})
    reasons = {str(key): str(value) for key, value in dict(waiver_reasons or {}).items()}
    accepted_at = datetime.now(timezone.utc).isoformat()
    with unit_of_work(database) as work:
        run = work.world_generation.get(context, run_id)
        if run is None:
            work.rollback()
            raise KeyError(f"world_generation_run_not_found:{run_id}")
        if str(run.get("status") or "") not in {"review", "failed"}:
            work.rollback()
            raise ValueError("world_generation_accept_requires_completed_review")
        world = require_world_writable(work, context, str(run["world_id"]))
        if int(world["draft_revision"]) != int(run["draft_revision"]):
            work.rollback()
            raise ValueError(
                "world_generation_accept_revision_conflict:"
                f"run={run['draft_revision']}:current={world['draft_revision']}"
            )
        rows = work.world_generation.list_topic_results(context, run_id=run_id)
        by_topic = {str(row.get("topic_id") or ""): row for row in rows}
        requested = tuple(dict.fromkeys(str(value) for value in topic_ids if str(value)))
        selected = requested or tuple(
            topic_id
            for topic_id, row in by_topic.items()
            if str(row.get("status") or "") == "needs_review"
            and isinstance(row.get("candidate"), Mapping)
        )
        if not selected:
            work.rollback()
            raise ValueError("world_generation_accept_scope_empty")
        unknown = sorted(set(selected) - set(by_topic))
        if unknown:
            work.rollback()
            raise KeyError("world_generation_topic_result_not_found:" + ",".join(unknown))
        plan = _mapping(run.get("plan"))
        decisions = {
            str(key): dict(value)
            for key, value in _mapping(plan.get("review_decisions")).items()
            if isinstance(value, Mapping)
        }
        already_decided = sorted(
            topic_id
            for topic_id in selected
            if str(_mapping(decisions.get(topic_id)).get("decision") or "")
            in _ACCEPTED_DECISIONS | {"keep"}
        )
        if already_decided:
            work.rollback()
            raise ValueError(
                "world_generation_candidate_already_decided:" + ",".join(already_decided)
            )
        graph = _graph_from_payload(_mapping(run.get("graph")))
        selected_set = set(selected)
        ordered = [
            node.topic_id
            for node in graph.topological_order()
            if node.topic_id in selected_set
        ]
        decision_rows: dict[str, dict[str, Any]] = {}
        try:
            for topic_id in ordered:
                decision_rows[topic_id] = _promote(
                    work,
                    context,
                    run=run,
                    result=by_topic[topic_id],
                    topic_id=topic_id,
                    candidate_override=overrides.get(topic_id),
                    expected_candidate_hash=str(expected_hashes.get(topic_id) or ""),
                    accepted_at=accepted_at,
                    waiver_reason=reasons.get(topic_id, default_waiver_reason),
                )
        except Exception:
            work.rollback()
            raise
        decisions.update(decision_rows)
        plan["review_decisions"] = decisions
        work.world_generation.update(context, run_id=run_id, plan=plan)
        work.commit()
    reconciled = reconcile_world_generation(run_id, database=database)
    return {
        "ok": True,
        "run_id": run_id,
        "accepted_topic_ids": ordered,
        "decisions": decision_rows,
        "run": reconciled,
    }


def accept_world_generation_candidate(
    run_id: str,
    topic_id: str,
    *,
    candidate: Mapping[str, Any] | None = None,
    expected_candidate_hash: str = "",
    waiver_reason: str = "",
    database: Any | None = None,
) -> dict[str, Any]:
    return accept_world_generation_candidates(
        run_id,
        topic_ids=(topic_id,),
        candidate_overrides={topic_id: candidate} if candidate is not None else {},
        expected_candidate_hashes={topic_id: expected_candidate_hash},
        waiver_reasons={topic_id: waiver_reason},
        database=database,
    )


__all__ = [
    "accept_world_generation_candidate",
    "accept_world_generation_candidates",
]
