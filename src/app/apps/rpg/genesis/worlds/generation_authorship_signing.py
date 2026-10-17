"""Cryptographically trusted authorship evidence for World Forge content.

The topic payload may carry origin ledgers and generation artifacts for portability, but
those records are trusted only when signed by this application. Provider- or
client-supplied provenance is stripped before a new server attestation is created.
"""
from __future__ import annotations
from app.config.env import env_str as _env_str

import hashlib
import hmac
import json
import os
import secrets
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from app.apps.rpg.genesis.forge.world_forge_authorship_policy import (
    MACHINE_ALLOWED,
    STRUCTURAL_ONLY,
    policy_for_path,
)

from .generation_authorship import (
    AuthorshipClass,
    AuthorshipValidationError,
    content_hash,
    string_hash,
)
from .generation_test_mode import deterministic_world_forge_test_mode

_SIGNATURE_SCHEMA = "rpg_world_authorship_hmac_sha256_v1"
_LEDGER_SCHEMA = "rpg_world_field_origin_ledger_v3"
_ARTIFACT_SCHEMA = "rpg_world_generation_artifact_v3"
_ALLOWED_RAW_HASH_KINDS = {
    "provider_response",
    "provider_response_set",
    "retained_original_provider_response",
}
_ALLOWED_LORE_CLASSES = {
    AuthorshipClass.LLM_AUTHORED.value,
    AuthorshipClass.HUMAN_AUTHORED.value,
    AuthorshipClass.HUMAN_EDITED_LLM.value,
}
_BLOCKED_MARKERS = {
    "deterministic_fallback",
    "deterministic_profile_fixture_v1",
    "deterministic_world_forge_v1",
}
_STRUCTURAL_KEYS = {
    "id",
    "entity_id",
    "topic_id",
    "fact_id",
    "field_id",
    "source_id",
    "target_id",
    "document_id",
    "relationship_id",
    "schema_version",
    "visibility",
    "status",
    "kind",
    "type",
    "source",
    "authority",
    "approved_authority",
    "value_type",
    "semantic_role",
    "generator",
    "provider",
    "model",
    "prompt_version",
    "generator_version",
    "response_format",
    "finish_reason",
}
_PROVENANCE_CONTAINERS = {
    "provenance",
    "authorship",
    "origin_ledger",
    "generation_artifact",
    "generation_artifacts",
    "validation",
    "dependency_hashes",
    "dependency_trust",
    "lookup",
    "lookup_schema",
}
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
}
_SAFE_WRAPPER_KEYS = {"response", "result", "payload", "data"}


class AuthorshipSigningKeyUnavailable(RuntimeError):
    pass


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _key_path() -> Path:
    configured = str(_env_str("OMNIX_RPG_AUTHORSHIP_KEY_FILE") or "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".omnix" / "world_forge_authorship.key"


def _signing_key() -> bytes:
    configured = str(_env_str("OMNIX_RPG_AUTHORSHIP_SIGNING_KEY") or "")
    if configured:
        return configured.encode("utf-8")
    if deterministic_world_forge_test_mode():
        return b"omnix-world-forge-deterministic-test-signing-key-v1"

    path = _key_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            value = secrets.token_urlsafe(48)
            try:
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                pass
            else:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(value)
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise AuthorshipSigningKeyUnavailable(
            "world_forge_authorship_signing_key_unavailable"
        ) from exc
    if len(value) < 32:
        raise AuthorshipSigningKeyUnavailable(
            "world_forge_authorship_signing_key_too_short"
        )
    return value.encode("utf-8")


def _signature_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    payload = deepcopy(dict(value))
    payload.pop("server_signature", None)
    return payload


def sign_record(value: Mapping[str, Any]) -> dict[str, Any]:
    payload = _signature_payload(value)
    digest = hmac.new(
        _signing_key(),
        _canonical_json(payload).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {
        **payload,
        "server_signature": {
            "schema_version": _SIGNATURE_SCHEMA,
            "algorithm": "hmac-sha256",
            "digest": digest,
        },
    }


def verify_record_signature(value: Mapping[str, Any]) -> bool:
    row = dict(value)
    signature = row.get("server_signature")
    if not isinstance(signature, Mapping):
        return False
    signature_row = dict(signature)
    if (
        str(signature_row.get("schema_version") or "") != _SIGNATURE_SCHEMA
        or str(signature_row.get("algorithm") or "") != "hmac-sha256"
    ):
        return False
    supplied = str(signature_row.get("digest") or "")
    expected = hmac.new(
        _signing_key(),
        _canonical_json(_signature_payload(row)).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return bool(supplied) and hmac.compare_digest(supplied, expected)


def _pointer_segment(value: Any) -> str:
    return str(value).replace("~", "~0").replace("/", "~1")


def _walk_strings(
    value: Any,
    *,
    segments: tuple[str, ...] = (),
    parent: Mapping[str, Any] | None = None,
) -> Iterable[tuple[str, str, str, Mapping[str, Any] | None, tuple[str, ...]]]:
    if isinstance(value, Mapping):
        current = dict(value)
        for key, item in current.items():
            yield from _walk_strings(
                item,
                segments=(*segments, str(key)),
                parent=current,
            )
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            yield from _walk_strings(
                item,
                segments=(*segments, str(index)),
                parent=parent,
            )
        return
    if isinstance(value, str) and value.strip():
        path = "/" + "/".join(_pointer_segment(segment) for segment in segments)
        yield path, segments[-1] if segments else "", value, parent, segments


def _looks_like_identifier(value: str) -> bool:
    text = value.strip()
    if not text or any(character.isspace() for character in text):
        return False
    return ":" in text or text.startswith(("sha256:", "ent_", "fact_", "topic_"))


def _machine_fact_path(segments: tuple[str, ...], key: str, parent: Mapping[str, Any] | None) -> bool:
    if len(segments) < 3 or segments[0] != "facts" or key not in _MACHINE_FACT_FIELDS:
        return False
    if not isinstance(parent, Mapping):
        return False
    return (
        str(parent.get("authorship_class") or "")
        == AuthorshipClass.MACHINE_STRUCTURED.value
        or str(parent.get("source") or "").startswith("profile_structured_fact_compiler_")
    )


def _machine_presentation_path(segments: tuple[str, ...], key: str) -> bool:
    if "dossier" in segments and "sections" in segments and key in {"id", "title"}:
        return True
    if "quick_facts" in segments and key == "label":
        return True
    return False


def strict_lore_string_leaves(
    value: Mapping[str, Any],
    *,
    policy: Mapping[str, Any] | None = None,
) -> tuple[dict[str, str], ...]:
    rows: list[dict[str, str]] = []
    for path, key, text, parent, segments in _walk_strings(value):
        if any(segment in _PROVENANCE_CONTAINERS for segment in segments[:-1]):
            continue
        declared = policy_for_path(policy, path) if policy else ""
        if declared in {MACHINE_ALLOWED, STRUCTURAL_ONLY}:
            continue
        if key in _STRUCTURAL_KEYS or key.endswith(("_id", "_ids", "_hash", "_version")):
            continue
        if _machine_fact_path(segments, key, parent):
            continue
        if _machine_presentation_path(segments, key):
            continue
        if _looks_like_identifier(text):
            continue
        rows.append({"path": path, "value": text, "content_hash": string_hash(text)})
    return tuple(rows)


def sanitize_untrusted_candidate(candidate: Mapping[str, Any]) -> dict[str, Any]:
    payload = deepcopy(dict(candidate))
    provenance = payload.get("provenance")
    if isinstance(provenance, Mapping):
        row = deepcopy(dict(provenance))
        for key in (
            "authorship",
            "test_authorship_exemption",
            "authorship_validation",
            "validation_receipt",
            "authoritative_contract_receipt",
        ):
            row.pop(key, None)
        payload["provenance"] = row
    return payload


def _artifact_without_hash_or_signature(value: Mapping[str, Any]) -> dict[str, Any]:
    row = _signature_payload(value)
    row.pop("artifact_hash", None)
    return row


def harden_and_sign_generation_artifact(
    candidate: Mapping[str, Any],
    artifact: Mapping[str, Any],
    *,
    policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    row = deepcopy(dict(artifact))
    raw_kind = str(row.get("raw_response_hash_kind") or "")
    if raw_kind not in _ALLOWED_RAW_HASH_KINDS or not str(row.get("raw_response_hash") or ""):
        raise AuthorshipValidationError(
            {
                "schema_version": "rpg_world_publishable_authorship_report_v3",
                "publishable": False,
                "blocked_paths": [
                    {
                        "path": "/provenance/authorship",
                        "code": "genuine_provider_response_hash_required",
                    }
                ],
            }
        )
    authored = sorted(
        (
            {"path": item["path"], "content_hash": item["content_hash"]}
            for item in strict_lore_string_leaves(candidate, policy=policy)
        ),
        key=lambda item: (item["path"], item["content_hash"]),
    )
    row.update(
        {
            "schema_version": _ARTIFACT_SCHEMA,
            "authored_strings": authored,
            "parsed_payload_hash": content_hash(authored),
        }
    )
    row["artifact_hash"] = content_hash(_artifact_without_hash_or_signature(row))
    return sign_record(row)


def _origin_entry(
    item: Mapping[str, str],
    *,
    authorship_class: str,
    artifact_id: str = "",
    event_id: str = "",
    parent_origin: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "path": str(item["path"]),
        "authorship_class": authorship_class,
        "generation_artifact_id": artifact_id,
        "human_edit_event_id": event_id,
        "source_json_pointer": str(item["path"]),
        "content_hash": str(item["content_hash"]),
    }
    if parent_origin:
        row["parent_origin"] = {
            "authorship_class": str(parent_origin.get("authorship_class") or ""),
            "generation_artifact_id": str(parent_origin.get("generation_artifact_id") or ""),
            "human_edit_event_id": str(parent_origin.get("human_edit_event_id") or ""),
            "source_json_pointer": str(
                parent_origin.get("source_json_pointer") or parent_origin.get("path") or ""
            ),
            "content_hash": str(parent_origin.get("content_hash") or ""),
        }
    return row


def _embedded_artifacts(candidate: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    authorship = dict(dict(candidate.get("provenance") or {}).get("authorship") or {})
    values = authorship.get("generation_artifacts")
    if not isinstance(values, Mapping):
        return {}
    return {
        str(key): dict(value)
        for key, value in values.items()
        if str(key) and isinstance(value, Mapping)
    }


def attach_signed_llm_authorship(
    candidate: Mapping[str, Any],
    artifact: Mapping[str, Any],
    *,
    policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload = sanitize_untrusted_candidate(candidate)
    artifact_row = dict(artifact)
    artifact_id = str(artifact_row.get("generation_artifact_id") or "")
    ledger = [
        _origin_entry(
            item,
            authorship_class=AuthorshipClass.LLM_AUTHORED.value,
            artifact_id=artifact_id,
        )
        for item in strict_lore_string_leaves(payload, policy=policy)
    ]
    authorship = sign_record(
        {
            "schema_version": _LEDGER_SCHEMA,
            "authorship_class": AuthorshipClass.LLM_AUTHORED.value,
            "generation_artifact_id": artifact_id,
            "origin_ledger": ledger,
            "generation_artifacts": {artifact_id: artifact_row},
        }
    )
    provenance = dict(payload.get("provenance") or {})
    provenance["authorship"] = authorship
    payload["provenance"] = provenance
    return payload


def attach_signed_human_authorship(
    candidate: Mapping[str, Any],
    *,
    event_id: str,
    prior_candidate: Mapping[str, Any] | None = None,
    edited_llm: bool = False,
    policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload = sanitize_untrusted_candidate(candidate)
    prior = dict(prior_candidate or {})
    prior_authorship = dict(dict(prior.get("provenance") or {}).get("authorship") or {})
    prior_rows = {
        str(row.get("path") or ""): dict(row)
        for row in prior_authorship.get("origin_ledger") or ()
        if isinstance(row, Mapping)
    }
    human_class = (
        AuthorshipClass.HUMAN_EDITED_LLM.value
        if edited_llm
        else AuthorshipClass.HUMAN_AUTHORED.value
    )
    ledger: list[dict[str, Any]] = []
    for item in strict_lore_string_leaves(payload, policy=policy):
        previous = prior_rows.get(str(item["path"]))
        if previous and str(previous.get("content_hash") or "") == str(item["content_hash"]):
            ledger.append(previous)
        else:
            ledger.append(
                _origin_entry(
                    item,
                    authorship_class=human_class,
                    event_id=event_id,
                    parent_origin=previous,
                )
            )
    artifacts = _embedded_artifacts(prior)
    authorship = sign_record(
        {
            "schema_version": _LEDGER_SCHEMA,
            "authorship_class": human_class,
            "human_edit_event_id": event_id,
            "origin_ledger": ledger,
            "generation_artifacts": artifacts,
        }
    )
    provenance = dict(payload.get("provenance") or {})
    provenance["authorship"] = authorship
    payload["provenance"] = provenance
    return payload


def attach_signed_partial_llm_authorship(
    candidate: Mapping[str, Any],
    artifact: Mapping[str, Any],
    *,
    llm_paths: Iterable[str],
    prior_candidate: Mapping[str, Any],
    policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload = sanitize_untrusted_candidate(candidate)
    selected = {str(path) for path in llm_paths}
    prior_authorship = dict(
        dict(prior_candidate.get("provenance") or {}).get("authorship") or {}
    )
    prior_rows = {
        str(row.get("path") or ""): dict(row)
        for row in prior_authorship.get("origin_ledger") or ()
        if isinstance(row, Mapping)
    }
    artifact_row = dict(artifact)
    artifact_id = str(artifact_row.get("generation_artifact_id") or "")
    ledger: list[dict[str, Any]] = []
    blockers: list[dict[str, str]] = []
    for item in strict_lore_string_leaves(payload, policy=policy):
        path = str(item["path"])
        previous = prior_rows.get(path)
        if path in selected:
            ledger.append(
                _origin_entry(
                    item,
                    authorship_class=AuthorshipClass.LLM_AUTHORED.value,
                    artifact_id=artifact_id,
                    parent_origin=previous,
                )
            )
        elif previous and str(previous.get("content_hash") or "") == str(item["content_hash"]):
            ledger.append(previous)
        else:
            blockers.append({"path": path, "code": "partial_authorship_uncovered_change"})
    if blockers:
        raise AuthorshipValidationError(
            {
                "schema_version": "rpg_world_publishable_authorship_report_v3",
                "publishable": False,
                "blocked_paths": blockers,
            }
        )
    artifacts = _embedded_artifacts(prior_candidate)
    artifacts[artifact_id] = artifact_row
    authorship = sign_record(
        {
            "schema_version": _LEDGER_SCHEMA,
            "authorship_class": "mixed_authored",
            "generation_artifact_id": artifact_id,
            "origin_ledger": ledger,
            "generation_artifacts": artifacts,
        }
    )
    provenance = dict(payload.get("provenance") or {})
    provenance["authorship"] = authorship
    payload["provenance"] = provenance
    return payload


def _blocked_marker_rows(value: Any, path: str = "") -> list[dict[str, str]]:
    blocked: list[dict[str, str]] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            child = f"{path}/{_pointer_segment(key)}"
            key_text = str(key)
            if key_text in {
                "quality_enriched",
                "generated_from_legacy",
                "presentation_derived_from_structured_facts",
            } and item is True:
                blocked.append({"path": child, "code": key_text})
            elif key_text == "used_llm" and item is False:
                blocked.append({"path": child, "code": "used_llm_false"})
            elif key_text == "generator" and str(item) in _BLOCKED_MARKERS:
                blocked.append({"path": child, "code": str(item)})
            blocked.extend(_blocked_marker_rows(item, child))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            blocked.extend(_blocked_marker_rows(item, f"{path}/{index}"))
    return blocked


def validate_signed_authorship(
    candidate: Mapping[str, Any],
    *,
    policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload = dict(candidate)
    provenance = dict(payload.get("provenance") or {})
    authorship = dict(provenance.get("authorship") or {})
    blockers: list[dict[str, Any]] = list(_blocked_marker_rows(payload))
    if not verify_record_signature(authorship):
        blockers.append(
            {"path": "/provenance/authorship", "code": "server_authorship_signature_invalid"}
        )

    ledger = {
        str(row.get("path") or ""): dict(row)
        for row in authorship.get("origin_ledger") or ()
        if isinstance(row, Mapping) and str(row.get("path") or "")
    }
    artifacts = _embedded_artifacts(payload)
    for artifact_id, artifact in artifacts.items():
        if not verify_record_signature(artifact):
            blockers.append(
                {
                    "path": "/provenance/authorship",
                    "code": "generation_artifact_signature_invalid",
                }
            )
            continue
        expected_hash = content_hash(_artifact_without_hash_or_signature(artifact))
        if str(artifact.get("artifact_hash") or "") != expected_hash:
            blockers.append(
                {
                    "path": "/provenance/authorship",
                    "code": "generation_artifact_hash_mismatch",
                }
            )
        if str(artifact.get("generation_artifact_id") or "") != artifact_id:
            blockers.append(
                {
                    "path": "/provenance/authorship",
                    "code": "generation_artifact_id_mismatch",
                }
            )
        if not str(artifact.get("provider") or "") or not str(artifact.get("model") or ""):
            blockers.append(
                {
                    "path": "/provenance/authorship",
                    "code": "generation_artifact_provider_or_model_missing",
                }
            )
        if str(artifact.get("raw_response_hash_kind") or "") not in _ALLOWED_RAW_HASH_KINDS:
            blockers.append(
                {
                    "path": "/provenance/authorship",
                    "code": "genuine_provider_response_hash_required",
                }
            )
        claims = sorted(
            (
                {
                    "path": str(row.get("path") or ""),
                    "content_hash": str(row.get("content_hash") or ""),
                }
                for row in artifact.get("authored_strings") or ()
                if isinstance(row, Mapping)
            ),
            key=lambda row: (row["path"], row["content_hash"]),
        )
        if str(artifact.get("parsed_payload_hash") or "") != content_hash(claims):
            blockers.append(
                {"path": "/", "code": "generation_artifact_payload_hash_mismatch"}
            )

    leaves = strict_lore_string_leaves(payload, policy=policy)
    current_paths = {str(row["path"]) for row in leaves}
    for item in leaves:
        path = str(item["path"])
        origin = ledger.get(path)
        if origin is None:
            blockers.append({"path": path, "code": "trusted_authorship_missing"})
            continue
        if str(origin.get("content_hash") or "") != str(item["content_hash"]):
            blockers.append({"path": path, "code": "origin_content_hash_mismatch"})
            continue
        authorship_class = str(origin.get("authorship_class") or "")
        if authorship_class not in _ALLOWED_LORE_CLASSES:
            blockers.append(
                {
                    "path": path,
                    "code": "authorship_class_not_publishable",
                    "authorship_class": authorship_class,
                }
            )
            continue
        if authorship_class == AuthorshipClass.LLM_AUTHORED.value:
            artifact_id = str(origin.get("generation_artifact_id") or "")
            llm_artifact = artifacts.get(artifact_id)
            artifact_claims = {
                (str(row.get("path") or ""), str(row.get("content_hash") or ""))
                for row in (llm_artifact or {}).get("authored_strings") or ()
                if isinstance(row, Mapping)
            }
            source_path = str(origin.get("source_json_pointer") or path)
            if llm_artifact is None:
                blockers.append({"path": path, "code": "server_generation_artifact_missing"})
            elif (source_path, str(item["content_hash"])) not in artifact_claims:
                blockers.append({"path": path, "code": "origin_artifact_content_mismatch"})
        elif not str(origin.get("human_edit_event_id") or ""):
            blockers.append({"path": path, "code": "human_authorship_event_missing"})
    for stale_path in sorted(set(ledger) - current_paths):
        blockers.append({"path": stale_path, "code": "origin_stale"})

    unique = {
        (str(row.get("path") or ""), str(row.get("code") or "")): row
        for row in blockers
    }
    ordered = [unique[key] for key in sorted(unique)]
    return {
        "schema_version": "rpg_world_publishable_authorship_report_v3",
        "publishable": not ordered,
        "lore_string_count": len(leaves),
        "origin_count": len(ledger),
        "generation_artifact_count": len(artifacts),
        "blocked_paths": ordered,
    }


def require_signed_authorship(
    candidate: Mapping[str, Any],
    *,
    policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    report = validate_signed_authorship(candidate, policy=policy)
    if not report["publishable"]:
        raise AuthorshipValidationError(report)
    return report


def _unwrap_safe_wrapper(value: Mapping[str, Any]) -> Mapping[str, Any]:
    current: Mapping[str, Any] = value
    while len(current) == 1:
        key, child = next(iter(current.items()))
        if str(key) not in _SAFE_WRAPPER_KEYS or not isinstance(child, Mapping):
            break
        current = child
    return current


def prove_path_aware_structural_repair(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    before_rows = {
        str(row["path"]): str(row["content_hash"])
        for row in strict_lore_string_leaves(_unwrap_safe_wrapper(before), policy=policy)
    }
    after_rows = {
        str(row["path"]): str(row["content_hash"])
        for row in strict_lore_string_leaves(_unwrap_safe_wrapper(after), policy=policy)
    }
    changed = sorted(
        path
        for path in set(before_rows) | set(after_rows)
        if before_rows.get(path) != after_rows.get(path)
    )
    report = {
        "schema_version": "rpg_world_structural_repair_proof_v2",
        "non_authoring": not changed,
        "changed_paths": changed,
    }
    if changed:
        raise AuthorshipValidationError(
            {
                "schema_version": "rpg_world_publishable_authorship_report_v3",
                "publishable": False,
                "blocked_paths": [
                    {"path": path, "code": "structural_repair_changed_lore_path"}
                    for path in changed
                ],
                "structural_repair": report,
            }
        )
    return report


__all__ = [
    "AuthorshipSigningKeyUnavailable",
    "attach_signed_human_authorship",
    "attach_signed_llm_authorship",
    "attach_signed_partial_llm_authorship",
    "harden_and_sign_generation_artifact",
    "prove_path_aware_structural_repair",
    "require_signed_authorship",
    "sanitize_untrusted_candidate",
    "sign_record",
    "strict_lore_string_leaves",
    "validate_signed_authorship",
    "verify_record_signature",
]
