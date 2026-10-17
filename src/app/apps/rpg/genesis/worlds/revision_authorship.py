"""Trusted-authorship boundary for immutable world revisions and releases."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from .contracts import (
    WorldReleaseDocument,
    WorldRevisionDocument,
    canonical_content_hash,
)
from .generation_authorship import AuthorshipValidationError
from .generation_authorship_runtime import lore_string_leaves
from .generation_authorship_signing import verify_record_signature
from .generation_test_mode import deterministic_world_forge_test_mode

_REVISION_LEDGER_SCHEMA = "rpg_world_revision_origin_ledger_v1"
_GENERATION_RECEIPT_SCHEMA = "rpg_world_revision_generation_receipt_v2"


def _revision_origin_ledger(
    canon: Mapping[str, Any],
    *,
    event_id: str,
) -> dict[str, Any]:
    return {
        "schema_version": _REVISION_LEDGER_SCHEMA,
        "authorship_class": "human_authored",
        "human_edit_event_id": event_id,
        "origin_ledger": [
            {
                "path": str(row["path"]),
                "content_hash": str(row["content_hash"]),
                "authorship_class": "human_authored",
                "human_edit_event_id": event_id,
                "source_json_pointer": str(row["path"]),
                "generation_artifact_id": "",
            }
            for row in lore_string_leaves(canon)
        ],
    }


def attach_revision_human_authorship(
    canon: Mapping[str, Any],
    provenance: Mapping[str, Any] | None,
    *,
    event_id: str,
) -> dict[str, Any]:
    """Attach origin evidence to revision provenance without changing canon."""

    payload = dict(provenance or {})
    payload["source"] = "manual_world_authoring"
    payload["human_authorship_event_id"] = event_id
    payload["authorship"] = _revision_origin_ledger(canon, event_id=event_id)
    payload["authorship_validation"] = validate_revision_origin_ledger(
        canon,
        payload["authorship"],
    )
    return payload


def validate_revision_origin_ledger(
    canon: Mapping[str, Any],
    authorship: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Verify exact canon string hashes against a server-created human ledger."""

    row = dict(authorship or {})
    event_id = str(row.get("human_edit_event_id") or "")
    blockers: list[dict[str, str]] = []
    if str(row.get("schema_version") or "") != _REVISION_LEDGER_SCHEMA:
        blockers.append(
            {"path": "/provenance/authorship", "code": "revision_authorship_schema_invalid"}
        )
    if str(row.get("authorship_class") or "") != "human_authored":
        blockers.append(
            {"path": "/provenance/authorship", "code": "revision_authorship_class_invalid"}
        )
    if not event_id:
        blockers.append(
            {"path": "/provenance/authorship", "code": "revision_human_event_missing"}
        )

    current = {
        str(value["path"]): str(value["content_hash"])
        for value in lore_string_leaves(canon)
    }
    ledger_values = row.get("origin_ledger")
    ledger_values = (
        ledger_values
        if isinstance(ledger_values, Sequence)
        and not isinstance(ledger_values, (str, bytes, bytearray))
        else ()
    )
    ledger: dict[str, dict[str, Any]] = {}
    for value in ledger_values:
        if not isinstance(value, Mapping):
            continue
        origin = dict(value)
        path = str(origin.get("path") or "")
        if path:
            ledger[path] = origin
    for path, value_hash in current.items():
        ledger_origin = ledger.get(path)
        if ledger_origin is None:
            blockers.append({"path": path, "code": "revision_origin_missing"})
            continue
        if str(ledger_origin.get("content_hash") or "") != value_hash:
            blockers.append({"path": path, "code": "revision_origin_hash_mismatch"})
        if str(ledger_origin.get("authorship_class") or "") != "human_authored":
            blockers.append({"path": path, "code": "revision_origin_class_invalid"})
        if str(ledger_origin.get("human_edit_event_id") or "") != event_id:
            blockers.append({"path": path, "code": "revision_origin_event_mismatch"})
    for path in sorted(set(ledger) - set(current)):
        blockers.append({"path": path, "code": "revision_origin_stale"})

    unique = {
        (str(blocker["path"]), str(blocker["code"])): blocker
        for blocker in blockers
    }
    ordered = [unique[key] for key in sorted(unique)]
    report = {
        "schema_version": "rpg_world_revision_authorship_report_v1",
        "publishable": not ordered,
        "authorship_class": "human_authored",
        "human_edit_event_id": event_id,
        "lore_string_count": len(current),
        "origin_count": len(ledger),
        "blocked_paths": ordered,
    }
    if ordered:
        raise AuthorshipValidationError(report)
    return report


def prepare_direct_world_revision(
    world: Mapping[str, Any],
    document: WorldRevisionDocument,
) -> WorldRevisionDocument:
    """Permit direct publication only for manual authorship in production."""

    provenance = dict(document.provenance)
    if str(provenance.get("source") or "") == "manual_world_authoring":
        validate_revision_origin_ledger(
            document.canon,
            raw_authorship if isinstance(raw_authorship := provenance.get("authorship"), Mapping) else None,
        )
        return document

    source_mode = str(world.get("source_mode") or "manual")
    if source_mode != "manual" and not deterministic_world_forge_test_mode():
        raise ValueError(
            f"world_revision_requires_guarded_generation:{document.world_id}:{source_mode}"
        )
    event_id = (
        f"humanrevision:{document.world_id}:{document.revision}:"
        f"{datetime.now(timezone.utc).isoformat()}"
    )
    payload = document.model_dump(mode="json")
    payload["provenance"] = {
        **attach_revision_human_authorship(
            document.canon,
            provenance,
            event_id=event_id,
        ),
        "deterministic_test_mode_fixture": (
            source_mode != "manual" and deterministic_world_forge_test_mode()
        ),
    }
    payload["content_hash"] = ""
    payload["content_hash"] = canonical_content_hash(payload)
    return WorldRevisionDocument.model_validate(payload)


def _unsigned_test_receipt(
    document: WorldRevisionDocument,
    provenance: Mapping[str, Any],
) -> dict[str, Any] | None:
    if not deterministic_world_forge_test_mode():
        return None
    run_id = str(provenance.get("generation_run_id") or "")
    topic_hashes = {
        str(key): str(value)
        for key, value in dict(provenance.get("topic_hashes") or {}).items()
    }
    if not run_id or not topic_hashes:
        return None
    return {
        "schema_version": _GENERATION_RECEIPT_SCHEMA,
        "world_id": document.world_id,
        "revision": document.revision,
        "generation_run_id": run_id,
        "topic_hashes": topic_hashes,
        "canon_hash": canonical_content_hash(dict(document.canon)),
        "deterministic_test_mode_fixture": True,
    }


def _require_generation_receipt(
    document: WorldRevisionDocument,
    provenance: Mapping[str, Any],
) -> dict[str, Any]:
    value = provenance.get("authorship_receipt")
    receipt = dict(value) if isinstance(value, Mapping) else {}
    signature_verified = bool(receipt) and verify_record_signature(receipt)
    if not signature_verified:
        test_receipt = _unsigned_test_receipt(document, provenance)
        if test_receipt is None:
            raise ValueError(
                f"world_revision_generation_receipt_signature_invalid:"
                f"{document.world_id}:{document.revision}"
            )
        receipt = test_receipt
    if str(receipt.get("schema_version") or "") != _GENERATION_RECEIPT_SCHEMA:
        raise ValueError(
            f"world_revision_generation_receipt_schema_invalid:"
            f"{document.world_id}:{document.revision}"
        )
    run_id = str(receipt.get("generation_run_id") or "")
    topic_hashes = {
        str(key): str(value)
        for key, value in dict(receipt.get("topic_hashes") or {}).items()
    }
    if (
        str(receipt.get("world_id") or "") != document.world_id
        or int(receipt.get("revision") or 0) != document.revision
        or not run_id
        or not topic_hashes
        or run_id != str(provenance.get("generation_run_id") or "")
        or topic_hashes
        != {
            str(key): str(value)
            for key, value in dict(provenance.get("topic_hashes") or {}).items()
        }
        or str(receipt.get("canon_hash") or "")
        != canonical_content_hash(dict(document.canon))
    ):
        raise ValueError(
            f"world_revision_generation_receipt_content_invalid:"
            f"{document.world_id}:{document.revision}"
        )
    return {**receipt, "server_signature_verified": signature_verified}


def require_revision_authorship(document: WorldRevisionDocument) -> dict[str, Any]:
    """Reject immutable revisions not produced by a trusted server path."""

    provenance = dict(document.provenance)
    source = str(provenance.get("source") or "")
    if source == "durable_world_generation":
        receipt = _require_generation_receipt(document, provenance)
        return {
            "schema_version": "rpg_world_revision_authorship_receipt_v2",
            "publishable": True,
            "source": source,
            "generation_run_id": str(receipt["generation_run_id"]),
            "topic_hashes": dict(receipt["topic_hashes"]),
            "canon_hash": str(receipt["canon_hash"]),
            "server_signature_verified": bool(receipt["server_signature_verified"]),
            "deterministic_test_mode_fixture": bool(
                receipt.get("deterministic_test_mode_fixture")
            ),
        }
    if source == "manual_world_authoring":
        return validate_revision_origin_ledger(
            document.canon,
            raw_authorship if isinstance(raw_authorship := provenance.get("authorship"), Mapping) else None,
        )
    raise ValueError(
        f"world_revision_authorship_untrusted:{document.world_id}:"
        f"{document.revision}:{source or 'unknown'}"
    )


def require_release_authorship(
    revision: WorldRevisionDocument,
    release: WorldReleaseDocument,
) -> dict[str, Any]:
    """Require either explicit certification or the guarded generation compiler chain."""

    receipt = require_revision_authorship(revision)
    certification = dict(release.certification)
    if bool(certification.get("authorship_validated")):
        return {
            **receipt,
            "release_authorship_validated": True,
            "release_authorship_source": str(
                certification.get("authorship_source") or receipt.get("source") or ""
            ),
        }

    revision_provenance = dict(revision.provenance)
    compiler = dict(release.compiler_provenance)
    revision_run_id = str(revision_provenance.get("generation_run_id") or "")
    release_run_id = str(compiler.get("generation_run_id") or "")
    if (
        str(revision_provenance.get("source") or "") == "durable_world_generation"
        and str(compiler.get("compiler") or "")
        == "rpg_world_generation_publication_v2"
        and revision_run_id
        and release_run_id == revision_run_id
    ):
        return {
            **receipt,
            "release_authorship_validated": True,
            "release_authorship_source": "guarded_generation_compiler",
        }
    raise ValueError(
        f"world_release_authorship_uncertified:{revision.world_id}:"
        f"{revision.revision}:{release.release}"
    )


__all__ = [
    "attach_revision_human_authorship",
    "prepare_direct_world_revision",
    "require_release_authorship",
    "require_revision_authorship",
    "validate_revision_origin_ledger",
]
