"""Runtime artifact verification built on immutable World Forge topic results."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from .generation_authorship import (
    AuthorshipClass,
    AuthorshipValidationError,
    attach_human_authorship as _attach_human_authorship,
    attach_llm_authorship,
    content_hash,
    lore_string_leaves as _base_lore_string_leaves,
    prove_structural_repair_non_authoring,
    validate_publishable_authorship as _validate_origins,
)

_MACHINE_FACT_FIELDS = {
    "id",
    "subject",
    "predicate",
    "object",
    "authority",
    "approved_authority",
    "visibility",
    "entity_refs",
    "topic_id",
    "field_id",
    "value_type",
    "semantic_role",
    "source",
    "authorship_class",
    "lookup",
    "lookup_schema",
}
_MACHINE_ORIGIN_BLOCKER_CODES = {
    "authorship_class_not_publishable",
    "server_generation_artifact_missing",
    "origin_artifact_mismatch",
    "human_authorship_event_missing",
}


def _machine_structured_fact_paths(candidate: Mapping[str, Any]) -> set[str]:
    paths: set[str] = set()
    facts = candidate.get("facts")
    if not isinstance(facts, (list, tuple)):
        return paths
    for index, value in enumerate(facts):
        if not isinstance(value, Mapping):
            continue
        row = dict(value)
        machine = (
            str(row.get("authorship_class") or "")
            == AuthorshipClass.MACHINE_STRUCTURED.value
            or str(row.get("source") or "").startswith(
                "profile_structured_fact_compiler_"
            )
        )
        if not machine:
            continue
        prefix = f"/facts/{index}/"
        for leaf in _base_lore_string_leaves({"facts": [row]}):
            path = str(leaf["path"])
            parts = [part for part in path.split("/") if part]
            if len(parts) >= 3 and parts[2] in _MACHINE_FACT_FIELDS:
                suffix = "/".join(parts[2:])
                paths.add(prefix + suffix)
    return paths


def lore_string_leaves(candidate: Mapping[str, Any]) -> tuple[dict[str, str], ...]:
    machine_paths = _machine_structured_fact_paths(candidate)
    return tuple(
        row
        for row in _base_lore_string_leaves(candidate)
        if str(row["path"]) not in machine_paths
    )


def _authorship(candidate: Mapping[str, Any]) -> dict[str, Any]:
    provenance = candidate.get("provenance")
    provenance = dict(provenance) if isinstance(provenance, Mapping) else {}
    value = provenance.get("authorship")
    return dict(value) if isinstance(value, Mapping) else {}


def _origin_ledger(candidate: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in _authorship(candidate).get("origin_ledger") or ()
        if isinstance(row, Mapping)
    ]


def _reclassify_machine_origins(candidate: Mapping[str, Any]) -> dict[str, Any]:
    payload = deepcopy(dict(candidate))
    machine_paths = _machine_structured_fact_paths(payload)
    provenance = dict(payload.get("provenance") or {})
    authorship = dict(provenance.get("authorship") or {})
    ledger: list[dict[str, Any]] = []
    for value in authorship.get("origin_ledger") or ():
        if not isinstance(value, Mapping):
            continue
        row = dict(value)
        if str(row.get("path") or "") in machine_paths:
            row.update(
                {
                    "authorship_class": AuthorshipClass.MACHINE_STRUCTURED.value,
                    "generation_artifact_id": "",
                    "human_edit_event_id": "",
                }
            )
            row.pop("parent_origin", None)
        ledger.append(row)
    authorship["origin_ledger"] = ledger
    provenance["authorship"] = authorship
    payload["provenance"] = provenance
    return payload


def _authored_payload_rows(
    candidate: Mapping[str, Any],
    *,
    authored_paths: Iterable[str] | None = None,
) -> list[dict[str, str]]:
    allowed = None if authored_paths is None else {str(path) for path in authored_paths}
    return sorted(
        [
            {"path": str(row["path"]), "content_hash": str(row["content_hash"])}
            for row in lore_string_leaves(candidate)
            if allowed is None or str(row["path"]) in allowed
        ],
        key=lambda row: (row["path"], row["content_hash"]),
    )


def _artifact_without_hash(value: Mapping[str, Any]) -> dict[str, Any]:
    artifact = deepcopy(dict(value))
    artifact.pop("artifact_hash", None)
    return artifact


def build_generation_artifact(
    candidate: Mapping[str, Any],
    *,
    run_id: str,
    job_id: str,
    topic_id: str,
    provider: Mapping[str, Any],
    settings: Mapping[str, Any] | None = None,
    attempt: int = 1,
    authored_paths: Iterable[str] | None = None,
    parent_artifact_ids: Iterable[str] = (),
) -> dict[str, Any]:
    """Build a server-owned artifact for all or a declared subset of lore paths."""

    provider_row = dict(provider)
    settings_row = dict(settings or {})
    recovery = provider_row.get("structured_recovery")
    recovery = dict(recovery) if isinstance(recovery, Mapping) else {}
    authored_strings = _authored_payload_rows(
        candidate,
        authored_paths=authored_paths,
    )
    parsed_hash = content_hash(authored_strings)
    raw_hash = str(
        provider_row.get("raw_response_hash")
        or provider_row.get("provider_response_hash")
        or recovery.get("accepted_response_hash")
        or recovery.get("original_candidate_hash")
        or ""
    )
    raw_hash_kind = str(
        provider_row.get("raw_response_hash_kind") or "provider_response"
    )
    if not raw_hash:
        raw_hash = parsed_hash
        raw_hash_kind = "authored_payload_fallback"
    transformations: list[str] = []
    method = str(recovery.get("method") or "")
    if method:
        transformations.append(method)
    transformations.extend(str(value) for value in recovery.get("repair_codes") or ())
    transformations.extend(str(value) for value in provider_row.get("transformations") or ())
    parents = tuple(dict.fromkeys(str(value) for value in parent_artifact_ids if str(value)))
    identity = {
        "run_id": run_id,
        "job_id": job_id,
        "topic_id": topic_id,
        "authored_payload_hash": parsed_hash,
        "provider": str(provider_row.get("provider") or ""),
        "model": str(provider_row.get("model") or ""),
        "parents": list(parents),
    }
    artifact = {
        "schema_version": "rpg_world_generation_artifact_v2",
        "generation_artifact_id": "genart:" + content_hash(identity)[:32],
        "generation_run_id": run_id,
        "job_id": job_id,
        "topic_id": topic_id,
        "provider": str(provider_row.get("provider") or ""),
        "model": str(provider_row.get("model") or ""),
        "generator": str(provider_row.get("generator") or ""),
        "generator_version": str(settings_row.get("generator_version") or ""),
        "prompt_version": str(settings_row.get("prompt_version") or ""),
        "raw_response_hash": raw_hash,
        "raw_response_hash_kind": raw_hash_kind,
        "parsed_payload_hash": parsed_hash,
        "authored_strings": authored_strings,
        "parent_generation_artifact_ids": list(parents),
        "attempt": max(1, int(attempt or 1)),
        "authorship_class": AuthorshipClass.LLM_AUTHORED.value,
        "transformations": list(dict.fromkeys(transformations)),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    artifact["artifact_hash"] = content_hash(_artifact_without_hash(artifact))
    return artifact


def generation_artifacts(candidate: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    authorship = _authorship(candidate)
    result: dict[str, dict[str, Any]] = {}
    values = authorship.get("generation_artifacts")
    if isinstance(values, Mapping):
        for key, value in values.items():
            if isinstance(value, Mapping):
                artifact = dict(value)
                artifact_id = str(artifact.get("generation_artifact_id") or key)
                if artifact_id:
                    result[artifact_id] = artifact
    elif isinstance(values, Sequence) and not isinstance(values, (str, bytes, bytearray)):
        for value in values:
            if not isinstance(value, Mapping):
                continue
            artifact = dict(value)
            artifact_id = str(artifact.get("generation_artifact_id") or "")
            if artifact_id:
                result[artifact_id] = artifact
    legacy = authorship.get("generation_artifact")
    if isinstance(legacy, Mapping):
        artifact = dict(legacy)
        artifact_id = str(artifact.get("generation_artifact_id") or "")
        if artifact_id:
            result.setdefault(artifact_id, artifact)
    return result


def generation_artifact(candidate: Mapping[str, Any]) -> dict[str, Any]:
    authorship = _authorship(candidate)
    primary_id = str(authorship.get("generation_artifact_id") or "")
    artifacts = generation_artifacts(candidate)
    if primary_id and primary_id in artifacts:
        return artifacts[primary_id]
    legacy = authorship.get("generation_artifact")
    if isinstance(legacy, Mapping):
        return dict(legacy)
    return next(reversed(artifacts.values()), {}) if artifacts else {}


def _store_generation_artifacts(
    candidate: Mapping[str, Any],
    artifacts: Mapping[str, Mapping[str, Any]],
    *,
    primary_artifact_id: str,
) -> dict[str, Any]:
    payload = deepcopy(dict(candidate))
    provenance = dict(payload.get("provenance") or {})
    authorship = dict(provenance.get("authorship") or {})
    normalized = {
        str(artifact_id): dict(artifact)
        for artifact_id, artifact in artifacts.items()
        if str(artifact_id) and isinstance(artifact, Mapping)
    }
    authorship["generation_artifacts"] = normalized
    authorship["generation_artifact_id"] = primary_artifact_id
    if primary_artifact_id in normalized:
        authorship["generation_artifact"] = normalized[primary_artifact_id]
    provenance["authorship"] = authorship
    payload["provenance"] = provenance
    return payload


def attach_server_llm_authorship(
    candidate: Mapping[str, Any],
    artifact: Mapping[str, Any],
) -> dict[str, Any]:
    payload = _reclassify_machine_origins(attach_llm_authorship(candidate, artifact))
    artifact_id = str(artifact.get("generation_artifact_id") or "")
    return _store_generation_artifacts(
        payload,
        {artifact_id: dict(artifact)},
        primary_artifact_id=artifact_id,
    )


def _parent_origin(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "authorship_class": str(value.get("authorship_class") or ""),
        "generation_artifact_id": str(value.get("generation_artifact_id") or ""),
        "human_edit_event_id": str(value.get("human_edit_event_id") or ""),
        "source_json_pointer": str(value.get("source_json_pointer") or value.get("path") or ""),
        "content_hash": str(value.get("content_hash") or ""),
    }


def attach_partial_llm_authorship(
    candidate: Mapping[str, Any],
    artifact: Mapping[str, Any],
    *,
    llm_paths: Iterable[str],
    prior_candidate: Mapping[str, Any],
) -> dict[str, Any]:
    """Attach a new LLM artifact only to changed paths and preserve all other origins."""

    selected = {str(path) for path in llm_paths}
    seeded = _reclassify_machine_origins(attach_llm_authorship(candidate, artifact))
    new_rows = {
        str(row.get("path") or ""): dict(row)
        for row in _origin_ledger(seeded)
    }
    prior_rows = {
        str(row.get("path") or ""): dict(row)
        for row in _origin_ledger(prior_candidate)
    }
    blockers: list[dict[str, str]] = []
    ledger: list[dict[str, Any]] = []
    for path, row in sorted(new_rows.items()):
        if str(row.get("authorship_class") or "") == AuthorshipClass.MACHINE_STRUCTURED.value:
            ledger.append(row)
            continue
        previous = prior_rows.get(path)
        if path in selected:
            if previous:
                row["parent_origin"] = _parent_origin(previous)
            ledger.append(row)
            continue
        if previous and str(previous.get("content_hash") or "") == str(row.get("content_hash") or ""):
            ledger.append(previous)
            continue
        blockers.append({"path": path, "code": "partial_authorship_uncovered_change"})
    if blockers:
        raise AuthorshipValidationError(
            {
                "schema_version": "rpg_world_publishable_authorship_report_v1",
                "publishable": False,
                "blocked_paths": blockers,
            }
        )

    payload = deepcopy(dict(seeded))
    provenance = dict(payload.get("provenance") or {})
    authorship = dict(provenance.get("authorship") or {})
    authorship["schema_version"] = "rpg_world_field_origin_ledger_v2"
    authorship["authorship_class"] = "mixed_authored"
    authorship["origin_ledger"] = ledger
    provenance["authorship"] = authorship
    payload["provenance"] = provenance

    artifacts = generation_artifacts(prior_candidate)
    artifact_id = str(artifact.get("generation_artifact_id") or "")
    artifacts[artifact_id] = dict(artifact)
    return _store_generation_artifacts(
        payload,
        artifacts,
        primary_artifact_id=artifact_id,
    )


def attach_human_authorship(
    candidate: Mapping[str, Any],
    *,
    event_id: str,
    prior_candidate: Mapping[str, Any] | None = None,
    edited_llm: bool = False,
) -> dict[str, Any]:
    """Attach human origins and retain all immutable provider artifacts for lineage."""

    prior = dict(prior_candidate or {})
    payload = _reclassify_machine_origins(
        _attach_human_authorship(
            candidate,
            event_id=event_id,
            prior_candidate=prior,
            edited_llm=edited_llm,
        )
    )
    artifacts = generation_artifacts(prior)
    if artifacts:
        primary = str(_authorship(prior).get("generation_artifact_id") or "")
        if not primary:
            primary = next(reversed(artifacts))
        payload = _store_generation_artifacts(
            payload,
            artifacts,
            primary_artifact_id=primary,
        )
    return payload


def _artifact_claims(artifact: Mapping[str, Any]) -> set[tuple[str, str]] | None:
    values = artifact.get("authored_strings")
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
        return None
    return {
        (str(row.get("path") or ""), str(row.get("content_hash") or ""))
        for row in values
        if isinstance(row, Mapping)
        and str(row.get("path") or "")
        and str(row.get("content_hash") or "")
    }


def _artifact_origin_rows(
    candidate: Mapping[str, Any],
    artifact_id: str,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for row in _origin_ledger(candidate):
        path = str(row.get("path") or "")
        if (
            str(row.get("authorship_class") or "")
            == AuthorshipClass.LLM_AUTHORED.value
            and str(row.get("generation_artifact_id") or "") == artifact_id
        ):
            rows.append({"path": path, "content_hash": str(row.get("content_hash") or "")})
    return sorted(rows, key=lambda value: (value["path"], value["content_hash"]))


def _verify_origin_claim(
    origin: Mapping[str, Any],
    artifacts: Mapping[str, Mapping[str, Any]],
    blockers: list[dict[str, str]],
    *,
    parent: bool = False,
) -> None:
    source = dict(origin.get("parent_origin") or {}) if parent else dict(origin)
    if str(source.get("authorship_class") or "") != AuthorshipClass.LLM_AUTHORED.value:
        return
    artifact_id = str(source.get("generation_artifact_id") or "")
    path = str(source.get("source_json_pointer") or origin.get("path") or "/")
    value_hash = str(source.get("content_hash") or "")
    artifact = artifacts.get(artifact_id)
    if artifact is None:
        blockers.append({"path": path, "code": "server_generation_artifact_missing"})
        return
    claims = _artifact_claims(artifact)
    if claims is not None and (path, value_hash) not in claims:
        blockers.append({"path": path, "code": "origin_artifact_content_mismatch"})


def validate_publishable_authorship(
    candidate: Mapping[str, Any],
    *,
    server_artifact: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Verify every lore origin against one or more immutable server artifacts."""

    machine_paths = _machine_structured_fact_paths(candidate)
    origin_report = _validate_origins(candidate, server_artifact=None)
    blockers = [
        dict(row)
        for row in origin_report.get("blocked_paths") or ()
        if str(dict(row).get("code") or "") != "server_generation_artifact_missing"
        and not (
            str(dict(row).get("path") or "") in machine_paths
            and str(dict(row).get("code") or "") in _MACHINE_ORIGIN_BLOCKER_CODES
        )
    ]
    artifacts = generation_artifacts(candidate)
    if server_artifact is not None:
        supplied = dict(server_artifact)
        supplied_id = str(supplied.get("generation_artifact_id") or "")
        if supplied_id:
            artifacts[supplied_id] = supplied

    ledger = _origin_ledger(candidate)
    required_artifact_ids: set[str] = set()
    for origin in ledger:
        if str(origin.get("authorship_class") or "") == AuthorshipClass.LLM_AUTHORED.value:
            required_artifact_ids.add(str(origin.get("generation_artifact_id") or ""))
        parent = dict(origin.get("parent_origin") or {})
        if str(parent.get("authorship_class") or "") == AuthorshipClass.LLM_AUTHORED.value:
            required_artifact_ids.add(str(parent.get("generation_artifact_id") or ""))

    for artifact_id in sorted(value for value in required_artifact_ids if value):
        artifact = artifacts.get(artifact_id)
        if artifact is None:
            continue
        expected_artifact_hash = content_hash(_artifact_without_hash(artifact))
        if str(artifact.get("artifact_hash") or "") != expected_artifact_hash:
            blockers.append(
                {"path": "/provenance/authorship", "code": "generation_artifact_hash_mismatch"}
            )
        claims = _artifact_claims(artifact)
        if claims is not None:
            expected_payload_hash = content_hash(
                sorted(
                    [
                        {"path": path, "content_hash": value_hash}
                        for path, value_hash in claims
                    ],
                    key=lambda row: (row["path"], row["content_hash"]),
                )
            )
        else:
            expected_payload_hash = content_hash(_artifact_origin_rows(candidate, artifact_id))
        if str(artifact.get("parsed_payload_hash") or "") != expected_payload_hash:
            blockers.append(
                {"path": "/", "code": "generation_artifact_payload_hash_mismatch"}
            )
        if not str(artifact.get("provider") or "") or not str(artifact.get("model") or ""):
            blockers.append(
                {
                    "path": "/provenance/authorship",
                    "code": "generation_artifact_provider_or_model_missing",
                }
            )
        if not str(artifact.get("raw_response_hash") or ""):
            blockers.append(
                {
                    "path": "/provenance/authorship",
                    "code": "generation_artifact_raw_response_hash_missing",
                }
            )
        if str(artifact.get("authorship_class") or "") != AuthorshipClass.LLM_AUTHORED.value:
            blockers.append(
                {
                    "path": "/provenance/authorship",
                    "code": "generation_artifact_not_llm_authored",
                }
            )

    for origin in ledger:
        _verify_origin_claim(origin, artifacts, blockers)
        _verify_origin_claim(origin, artifacts, blockers, parent=True)

    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for blocker in blockers:
        unique[(str(blocker.get("path") or ""), str(blocker.get("code") or ""))] = blocker
    ordered = [unique[key] for key in sorted(unique)]
    authored_origin_count = sum(
        1
        for row in ledger
        if str(row.get("authorship_class") or "")
        != AuthorshipClass.MACHINE_STRUCTURED.value
    )
    primary = generation_artifact(candidate)
    leaves = lore_string_leaves(candidate)
    return {
        "schema_version": "rpg_world_publishable_authorship_report_v2",
        "publishable": not ordered,
        "lore_string_count": len(leaves),
        "origin_count": authored_origin_count,
        "machine_structured_string_count": len(machine_paths),
        "generation_artifact_id": str(primary.get("generation_artifact_id") or ""),
        "generation_artifact_count": len(artifacts),
        "blocked_paths": ordered,
    }


def require_publishable_authorship(
    candidate: Mapping[str, Any],
    *,
    server_artifact: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    report = validate_publishable_authorship(candidate, server_artifact=server_artifact)
    if not report["publishable"]:
        raise AuthorshipValidationError(report)
    return report


__all__ = [
    "AuthorshipClass",
    "AuthorshipValidationError",
    "attach_human_authorship",
    "attach_partial_llm_authorship",
    "attach_server_llm_authorship",
    "build_generation_artifact",
    "generation_artifact",
    "generation_artifacts",
    "lore_string_leaves",
    "prove_structural_repair_non_authoring",
    "require_publishable_authorship",
    "validate_publishable_authorship",
]
