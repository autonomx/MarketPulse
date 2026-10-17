from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List

from app.apps.rpg.foundation.dynamic_npc_profiles import (
    load_npc_profile,
    normalize_npc_profile,
    npc_profile_path,
    save_npc_profile,
)
from app.apps.rpg.genesis.profiles.llm_profile_drafter import (
    merge_profile_draft,
    validate_profile_draft,
)
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

DRAFT_VERSION = 1
MAX_DRAFT_WARNINGS = 12


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def profile_draft_path(npc_id: str) -> Path:
    profile_path = npc_profile_path(npc_id)
    return profile_path.with_suffix(".draft.json")


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    tmp.replace(path)


def load_profile_draft(npc_id: str) -> Dict[str, Any]:
    path = profile_draft_path(npc_id)
    if not path.exists():
        return {}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

    return _safe_dict(data)


def save_profile_draft(draft_state: Dict[str, Any]) -> Dict[str, Any]:
    draft_state = normalize_profile_draft_state(draft_state)
    path = profile_draft_path(_safe_str(draft_state.get("npc_id")))
    _atomic_write_json(path, draft_state)
    return {
        "saved": True,
        "path": str(path),
        "npc_id": _safe_str(draft_state.get("npc_id")),
        "draft_state": deepcopy(draft_state),
        "source": "deterministic_profile_draft_store",
    }


def normalize_profile_draft_state(draft_state: Dict[str, Any]) -> Dict[str, Any]:
    draft_state = deepcopy(_safe_dict(draft_state))

    npc_id = _safe_str(draft_state.get("npc_id"))
    status = _safe_str(draft_state.get("status") or "pending_approval")
    if status not in {"pending_approval", "approved", "rejected"}:
        status = "pending_approval"

    draft = validate_profile_draft(_safe_dict(draft_state.get("draft")))

    return {
        "draft_version": _safe_int(draft_state.get("draft_version"), DRAFT_VERSION),
        "draft_id": _safe_str(draft_state.get("draft_id")) or f"profile_draft:{npc_id}:0",
        "npc_id": npc_id,
        "status": status,
        "created_tick": _safe_int(draft_state.get("created_tick"), 0),
        "updated_tick": _safe_int(draft_state.get("updated_tick"), 0),
        "created_by": _safe_str(draft_state.get("created_by") or "deterministic_fallback_drafter"),
        "warnings": _safe_list(draft_state.get("warnings"))[:MAX_DRAFT_WARNINGS],
        "draft": draft,
        "source_profile_revision": _safe_int(draft_state.get("source_profile_revision"), 0),
        "source": "deterministic_profile_draft_store",
    }


def _contains_forbidden_claims(text: str) -> bool:
    text = _safe_str(text).lower()
    forbidden_markers = [
        "king's secret",
        "secret murder plot",
        "owns the bandit camp",
        "world-ending",
        "ancient god",
        "knows the location of the final boss",
        "can resurrect anyone",
        "controls the kingdom",
    ]
    return any(marker in text for marker in forbidden_markers)


def validate_draft_for_approval(draft: Dict[str, Any]) -> Dict[str, Any]:
    draft = validate_profile_draft(draft)
    warnings: List[Dict[str, Any]] = []

    searchable_fields = [
        _safe_str(_safe_dict(draft.get("biography")).get("short_summary")),
        _safe_str(_safe_dict(draft.get("biography")).get("full_biography")),
        _safe_str(_safe_dict(draft.get("biography")).get("public_reputation")),
        _safe_str(_safe_dict(draft.get("biography")).get("private_notes")),
        _safe_str(_safe_dict(draft.get("history")).get("background")),
    ]

    for field_text in searchable_fields:
        if _contains_forbidden_claims(field_text):
            warnings.append({
                "kind": "possible_unbacked_world_fact",
                "message": "Draft appears to introduce major unbacked world facts.",
                "severity": "warning",
                "source": "deterministic_profile_draft_validator",
            })
            break

    return {
        "valid": not any(_safe_str(w.get("severity")) == "error" for w in warnings),
        "warnings": warnings[:MAX_DRAFT_WARNINGS],
        "draft": draft,
        "source": "deterministic_profile_draft_validator",
    }


def build_deterministic_profile_draft(profile: Dict[str, Any]) -> Dict[str, Any]:
    profile = normalize_npc_profile(profile)
    name = _safe_str(profile.get("name"))
    biography = _safe_dict(profile.get("biography"))
    personality = _safe_dict(profile.get("personality"))
    history = _safe_dict(profile.get("history"))
    evolution = _safe_dict(profile.get("evolution"))

    traits = _safe_list(personality.get("traits"))
    role = _safe_str(evolution.get("current_role")) or "newly introduced companion"
    temperament = _safe_str(personality.get("temperament")) or "measured"
    speech_style = _safe_str(personality.get("speech_style")) or "plain and direct"

    short_summary = _safe_str(biography.get("short_summary"))
    if not short_summary:
        short_summary = f"{name} is a {role} whose deeper story is still being uncovered."

    full_biography = _safe_str(biography.get("full_biography"))
    if not full_biography:
        full_biography = (
            f"{name} carries themself as a {role}. "
            f"Their manner is {temperament}, and they tend to speak in a style that is {speech_style}. "
            "Their past is not fully known yet, leaving room for future discoveries through play."
        )

    background = _safe_str(history.get("background"))
    if not background:
        background = (
            f"{name}'s background is currently known only through first impressions, "
            "their role, and the circumstances under which they entered the story."
        )

    return validate_profile_draft({
        "biography": {
            "short_summary": short_summary,
            "full_biography": full_biography,
            "public_reputation": _safe_str(biography.get("public_reputation")) or f"{name} is known mainly by their current role: {role}.",
            "private_notes": _safe_str(biography.get("private_notes")),
        },
        "history": {
            "background": background,
            "major_life_events": _safe_list(history.get("major_life_events")),
            "recent_events": _safe_list(history.get("recent_events")),
        },
        "personality": {
            "traits": traits or ["observant"],
            "temperament": temperament,
            "speech_style": speech_style,
            "risk_tolerance": _safe_str(personality.get("risk_tolerance")) or "medium",
            "conflict_style": _safe_str(personality.get("conflict_style")) or "context-dependent",
        },
    })


def create_pending_profile_draft(
    npc_id: str,
    *,
    tick: int = 0,
    draft: Dict[str, Any] | None = None,
    created_by: str = "deterministic_fallback_drafter",
) -> Dict[str, Any]:
    profile = load_npc_profile(npc_id)
    if not profile:
        return {
            "drafted": False,
            "reason": "profile_not_found",
            "npc_id": _safe_str(npc_id),
            "source": "deterministic_profile_draft_store",
        }

    profile = normalize_npc_profile(profile)
    clean_draft = validate_profile_draft(_safe_dict(draft) if draft else build_deterministic_profile_draft(profile))
    validation = validate_draft_for_approval(clean_draft)

    revision = _safe_int(_safe_dict(profile.get("card_edit_state")).get("revision"), 1)
    draft_state = normalize_profile_draft_state({
        "draft_version": DRAFT_VERSION,
        "draft_id": f"profile_draft:{_safe_str(npc_id)}:{int(tick or 0)}",
        "npc_id": _safe_str(npc_id),
        "status": "pending_approval",
        "created_tick": int(tick or 0),
        "updated_tick": int(tick or 0),
        "created_by": created_by,
        "warnings": validation.get("warnings", []),
        "draft": validation.get("draft", clean_draft),
        "source_profile_revision": revision,
    })

    saved = save_profile_draft(draft_state)
    return {
        "drafted": True,
        "status": "pending_approval",
        "npc_id": _safe_str(npc_id),
        "draft_state": deepcopy(saved["draft_state"]),
        "path": saved["path"],
        "validation": validation,
        "source": "deterministic_profile_draft_store",
    }


def approve_profile_draft(
    npc_id: str,
    *,
    tick: int = 0,
    approved_by: str = "llm_draft_approved",
) -> Dict[str, Any]:
    profile = load_npc_profile(npc_id)
    if not profile:
        return {
            "approved": False,
            "reason": "profile_not_found",
            "npc_id": _safe_str(npc_id),
            "source": "deterministic_profile_draft_store",
        }

    draft_state = load_profile_draft(npc_id)
    if not draft_state:
        return {
            "approved": False,
            "reason": "draft_not_found",
            "npc_id": _safe_str(npc_id),
            "source": "deterministic_profile_draft_store",
        }

    draft_state = normalize_profile_draft_state(draft_state)
    if _safe_str(draft_state.get("status")) != "pending_approval":
        return {
            "approved": False,
            "reason": "draft_not_pending_approval",
            "draft_state": deepcopy(draft_state),
            "source": "deterministic_profile_draft_store",
        }

    validation = validate_draft_for_approval(_safe_dict(draft_state.get("draft")))
    if not validation.get("valid"):
        return {
            "approved": False,
            "reason": "draft_validation_failed",
            "validation": validation,
            "draft_state": deepcopy(draft_state),
            "source": "deterministic_profile_draft_store",
        }

    merged = merge_profile_draft(normalize_npc_profile(profile), _safe_dict(draft_state.get("draft")))

    edit_state = _safe_dict(merged.get("card_edit_state"))
    edit_state["revision"] = _safe_int(edit_state.get("revision"), 1) + 1
    edit_state["last_edited_by"] = _safe_str(approved_by)
    merged["card_edit_state"] = edit_state
    merged["updated_tick"] = int(tick or 0)
    merged["origin"] = "llm_drafted_from_scaffold"

    saved = save_npc_profile(merged)

    draft_state["status"] = "approved"
    draft_state["updated_tick"] = int(tick or 0)
    save_profile_draft(draft_state)

    return {
        "approved": True,
        "profile": deepcopy(saved["profile"]),
        "draft_state": deepcopy(draft_state),
        "profile_path": saved["path"],
        "draft_path": str(profile_draft_path(npc_id)),
        "validation": validation,
        "source": "deterministic_profile_draft_store",
    }


def reject_profile_draft(
    npc_id: str,
    *,
    tick: int = 0,
) -> Dict[str, Any]:
    draft_state = load_profile_draft(npc_id)
    if not draft_state:
        return {
            "rejected": False,
            "reason": "draft_not_found",
            "npc_id": _safe_str(npc_id),
            "source": "deterministic_profile_draft_store",
        }

    draft_state = normalize_profile_draft_state(draft_state)
    draft_state["status"] = "rejected"
    draft_state["updated_tick"] = int(tick or 0)
    saved = save_profile_draft(draft_state)

    return {
        "rejected": True,
        "draft_state": deepcopy(saved["draft_state"]),
        "path": saved["path"],
        "source": "deterministic_profile_draft_store",
    }


def profile_draft_summary(npc_id: str) -> Dict[str, Any]:
    draft_state = load_profile_draft(npc_id)
    if not draft_state:
        return {
            "npc_id": _safe_str(npc_id),
            "has_draft": False,
            "source": "deterministic_profile_draft_store",
        }

    draft_state = normalize_profile_draft_state(draft_state)
    return {
        "npc_id": _safe_str(npc_id),
        "has_draft": True,
        "draft_state": deepcopy(draft_state),
        "source": "deterministic_profile_draft_store",
    }
