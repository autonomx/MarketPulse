"""Phase 13.2 — Creator Pack Authoring / Validation / Preview.

Provides data-only pack draft workflow:
- validate draft shape
- preview draft application
- export draft as pack payload

No live-state mutation required.
"""
from __future__ import annotations

from typing import Any, Dict, List

from app.apps.rpg.genesis.modding.content_packs import build_pack_application_preview
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _first_non_empty(*values: Any) -> str:
    for value in values:
        text = _safe_str(value).strip()
        if text:
            return text
    return ""


def validate_pack_draft(draft: Dict[str, Any]) -> Dict[str, Any]:
    """Validate a creator pack draft without mutating live state.

    Returns dict with ok, errors, warnings keys.
    """
    draft = _safe_dict(draft)
    manifest = _safe_dict(draft.get("manifest"))
    characters = _safe_list(draft.get("characters"))
    scenario = _safe_dict(draft.get("scenario"))
    world_seed = _safe_dict(draft.get("world_seed"))
    visual_defaults = _safe_dict(draft.get("visual_defaults"))

    errors: List[str] = []
    warnings: List[str] = []

    if not _safe_str(manifest.get("id")).strip():
        errors.append("manifest.id_required")
    if not _safe_str(manifest.get("title")).strip():
        errors.append("manifest.title_required")
    if len(characters) > 64:
        errors.append("characters.too_many")
    if not scenario and not world_seed and not characters:
        warnings.append("pack.empty_content")
    if visual_defaults and not isinstance(visual_defaults, dict):
        errors.append("visual_defaults.invalid")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
    }


def build_pack_draft_export(draft: Dict[str, Any]) -> Dict[str, Any]:
    """Export validated pack draft as data-only content pack payload."""
    draft = _safe_dict(draft)
    validation = validate_pack_draft(draft)
    return {
        "manifest": _safe_dict(draft.get("manifest")),
        "characters": [item for item in _safe_list(draft.get("characters")) if isinstance(item, dict)],
        "scenario": _safe_dict(draft.get("scenario")),
        "world_seed": _safe_dict(draft.get("world_seed")),
        "visual_defaults": _safe_dict(draft.get("visual_defaults")),
        "validation": validation,
    }


def build_pack_draft_preview(draft: Dict[str, Any]) -> Dict[str, Any]:
    """Preview creator pack draft application/export."""
    draft = build_pack_draft_export(draft)
    preview = build_pack_application_preview(draft)
    return {
        "validation": _safe_dict(draft.get("validation")),
        "preview": preview,
    }