"""Phase 15.2 — Session/package bridge with validation and normalization."""
from __future__ import annotations

from typing import Any, Dict, List, cast

from app.apps.rpg.session.survival_persistence import normalize_survival_for_persistence
from app.apps.rpg.rules.validation.integrity import validate_package_integrity
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

_PACKAGE_SCHEMA_VERSION = 1


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _normalize_manifest(manifest: Dict[str, Any]) -> Dict[str, Any]:
    manifest = _safe_dict(manifest)
    return {
        "id": _safe_str(manifest.get("id")).strip() or "session:unknown",
        "title": _safe_str(manifest.get("title")).strip(),
        "schema_version": int(manifest.get("schema_version") or 2),
        "archived": bool(manifest.get("archived", False)),
    }


def _normalize_visual_state_for_export(visual_state: Dict[str, Any]) -> Dict[str, Any]:
    visual_state = _safe_dict(visual_state)

    image_requests = []
    for item in _safe_list(visual_state.get("image_requests")):
        item = _safe_dict(item)
        image_requests.append(
            {
                "request_id": _safe_str(item.get("request_id")).strip(),
                "kind": _safe_str(item.get("kind")).strip(),
                "target_id": _safe_str(item.get("target_id")).strip(),
                "status": _safe_str(item.get("status")).strip(),
                "attempts": int(item.get("attempts") or 0),
                "max_attempts": int(item.get("max_attempts") or 0),
                "error": _safe_str(item.get("error")).strip(),
                "updated_at": _safe_str(item.get("updated_at")).strip(),
                "completed_at": _safe_str(item.get("completed_at")).strip(),
            }
        )

    visual_assets = []
    for item in _safe_list(visual_state.get("visual_assets")):
        item = _safe_dict(item)
        visual_assets.append(
            {
                "asset_id": _safe_str(item.get("asset_id")).strip(),
                "kind": _safe_str(item.get("kind")).strip(),
                "target_id": _safe_str(item.get("target_id")).strip(),
                "status": _safe_str(item.get("status")).strip(),
                "model": _safe_str(item.get("model")).strip(),
                "style": _safe_str(item.get("style")).strip(),
                "url": _safe_str(item.get("url")).strip(),
                "local_path": _safe_str(item.get("local_path")).strip(),
                "created_from_request_id": _safe_str(item.get("created_from_request_id")).strip(),
            }
        )

    image_requests.sort(key=lambda x: (x["request_id"], x["kind"], x["target_id"]))
    visual_assets.sort(key=lambda x: (x["asset_id"], x["kind"], x["target_id"]))

    # Intentionally do not export queue/job state here.
    return {
        "image_requests": image_requests[:100],
        "visual_assets": visual_assets[:200],
    }


def _normalize_memory_state_for_export(memory_state: Dict[str, Any]) -> Dict[str, Any]:
    memory_state = _safe_dict(memory_state)

    actor_memory_in = _safe_dict(memory_state.get("actor_memory"))
    actor_memory_out: Dict[str, Any] = {}
    for actor_id in sorted(actor_memory_in.keys()):
        bucket = _safe_dict(actor_memory_in.get(actor_id))
        entries = []
        for item in _safe_list(bucket.get("entries")):
            item = _safe_dict(item)
            entries.append(
                {
                    "text": _safe_str(item.get("text")).strip(),
                    "strength": float(item.get("strength") or 0.0),
                    "updated_at": _safe_str(item.get("updated_at")).strip(),
                }
            )
        entries.sort(key=lambda item: (-cast(Any, item["strength"]), item["updated_at"], item["text"]))
        actor_memory_out[actor_id] = {"entries": entries[:50]}

    world_memory_in = _safe_dict(memory_state.get("world_memory"))
    rumors = []
    for item in _safe_list(world_memory_in.get("rumors")):
        item = _safe_dict(item)
        rumors.append(
            {
                "text": _safe_str(item.get("text")).strip(),
                "strength": float(item.get("strength") or 0.0),
                "reach": int(item.get("reach") or 0),
                "updated_at": _safe_str(item.get("updated_at")).strip(),
            }
        )
    rumors.sort(key=lambda item: (-cast(Any, item["strength"]), -cast(Any, item["reach"]), item["updated_at"], item["text"]))

    return {
        "actor_memory": actor_memory_out,
        "world_memory": {
            "rumors": rumors[:50],
        },
    }


def _normalize_simulation_state_for_export(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    simulation_state = normalize_survival_for_persistence(_safe_dict(simulation_state))
    presentation_state = _safe_dict(simulation_state.get("presentation_state"))
    memory_state = _safe_dict(simulation_state.get("memory_state"))

    presentation_state["visual_state"] = _normalize_visual_state_for_export(
        _safe_dict(presentation_state.get("visual_state"))
    )

    return {
        **simulation_state,
        "presentation_state": presentation_state,
        "memory_state": _normalize_memory_state_for_export(memory_state),
    }


def validate_package_payload(package_payload: Dict[str, Any]) -> Dict[str, Any]:
    package_payload = _safe_dict(package_payload)
    package_manifest = _safe_dict(package_payload.get("package_manifest"))
    session_manifest = _safe_dict(package_payload.get("session_manifest"))
    simulation_state = _safe_dict(package_payload.get("simulation_state"))
    installed_packs = _safe_list(package_payload.get("installed_packs"))

    errors: List[str] = []
    warnings: List[str] = []

    schema_version = int(package_manifest.get("schema_version") or 0)
    if schema_version != _PACKAGE_SCHEMA_VERSION:
        errors.append("unsupported_package_schema_version")

    if not _safe_str(session_manifest.get("id")).strip():
        errors.append("missing_session_manifest_id")

    if not isinstance(simulation_state, dict):
        errors.append("invalid_simulation_state")

    if not isinstance(installed_packs, list):
        errors.append("invalid_installed_packs")

    integrity = validate_package_integrity(package_payload)
    if not integrity["ok"]:
        errors.extend([item.get("code", "package_integrity_error") for item in integrity["errors"]])
    warnings.extend([item.get("code", "package_integrity_warning") for item in integrity["warnings"]])

    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "error_type": "package_validation_failed" if errors else "",
    }


def session_to_package(session: Dict[str, Any]) -> Dict[str, Any]:
    session = _safe_dict(session)
    manifest = _normalize_manifest(_safe_dict(session.get("manifest")))
    simulation_state = _normalize_simulation_state_for_export(_safe_dict(session.get("simulation_state")))
    installed_packs = sorted(_safe_list(session.get("installed_packs")), key=lambda x: _safe_str(x).strip())

    return {
        "package_manifest": {
            "package_kind": "rpg_session_export",
            "schema_version": _PACKAGE_SCHEMA_VERSION,
            "source_session_id": manifest.get("id"),
            "title": manifest.get("title"),
        },
        "session_manifest": manifest,
        "simulation_state": simulation_state,
        "installed_packs": installed_packs,
    }


def package_to_session(package_payload: Dict[str, Any], **kwargs: Any) -> Dict[str, Any]:
    """Convert a portable package dict back to a session dict.

    Accepts optional keyword arguments (session_id, title) for backward
    compatibility with callers that pass them, but uses canonical
    package validation and normalization internally.
    """
    package_payload = _safe_dict(package_payload)
    validation = validate_package_payload(package_payload)
    if not validation["ok"]:
        return {
            "ok": False,
            "errors": validation["errors"],
        }

    session_manifest = _normalize_manifest(_safe_dict(package_payload.get("session_manifest")))
    simulation_state = _normalize_simulation_state_for_export(_safe_dict(package_payload.get("simulation_state")))
    installed_packs = sorted(_safe_list(package_payload.get("installed_packs")), key=lambda x: _safe_str(x).strip())

    # Apply overrides from kwargs for backward compatibility
    if kwargs.get("session_id"):
        session_manifest["id"] = _safe_str(kwargs["session_id"]).strip()
    if kwargs.get("title"):
        session_manifest["title"] = _safe_str(kwargs["title"]).strip()

    return {
        "ok": True,
        "session": {
            "manifest": session_manifest,
            "simulation_state": simulation_state,
            "installed_packs": installed_packs,
            "import_metadata": {
                "package_manifest": _safe_dict(package_payload.get("package_manifest")),
            },
        },
    }
