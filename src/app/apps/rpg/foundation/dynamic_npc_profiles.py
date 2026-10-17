from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List

from app.runtime.paths import rpg_npc_profiles_root
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

PROFILE_VERSION = 1
DEFAULT_PROFILE_ROOT: Path | None = None


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _profile_root() -> Path:
    return DEFAULT_PROFILE_ROOT or rpg_npc_profiles_root()


def safe_npc_profile_filename(npc_id: str) -> str:
    npc_id = _safe_str(npc_id).strip() or "npc_unknown"
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", npc_id)
    safe = safe.strip("._") or "npc_unknown"
    return f"{safe}.json"


def npc_profile_path(npc_id: str) -> Path:
    return _profile_root() / safe_npc_profile_filename(npc_id)


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    tmp.replace(path)


def load_npc_profile(npc_id: str) -> Dict[str, Any]:
    path = npc_profile_path(npc_id)
    if not path.exists():
        return {}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

    return _safe_dict(data)


def save_npc_profile(profile: Dict[str, Any]) -> Dict[str, Any]:
    profile = normalize_npc_profile(profile)
    path = npc_profile_path(_safe_str(profile.get("npc_id")))
    _atomic_write_json(path, profile)
    return {
        "saved": True,
        "path": str(path),
        "npc_id": _safe_str(profile.get("npc_id")),
        "profile": deepcopy(profile),
        "source": "deterministic_dynamic_npc_profile_store",
    }


def _archetype_from_identity_arc(identity_arc: str, current_role: str) -> str:
    identity_arc = _safe_str(identity_arc)
    current_role_l = _safe_str(current_role).lower()

    if identity_arc == "revenge_after_losing_tavern":
        return "displaced_tavern_keeper"
    if identity_arc == "cautious_mediator":
        return "cautious_mediator"
    if identity_arc in {"lawful_guard", "guard"} or "guard" in current_role_l:
        return "guard"
    if identity_arc in {"thief", "opportunistic_thief"} or "thief" in current_role_l:
        return "thief"

    return "unknown"


def _defaults_for_archetype(archetype: str) -> Dict[str, Any]:
    if archetype == "displaced_tavern_keeper":
        return {
            "traits": ["grieving", "practical", "protective"],
            "temperament": "wounded but steady",
            "speech_style": "plainspoken and emotionally guarded",
            "risk_tolerance": "medium",
            "conflict_style": "direct when loss is involved",
            "morality": {
                "lawfulness": 0,
                "compassion": 2,
                "honor": 1,
                "greed": 0,
                "opportunism": 0,
                "vengefulness": 3,
            },
            "short_summary": "A displaced tavern keeper shaped by loss and a need for justice.",
        }

    if archetype == "cautious_mediator":
        return {
            "traits": ["cautious", "observant", "diplomatic"],
            "temperament": "measured",
            "speech_style": "calm and precise",
            "risk_tolerance": "low",
            "conflict_style": "de-escalates first",
            "morality": {
                "lawfulness": 1,
                "compassion": 2,
                "honor": 1,
                "greed": 0,
                "opportunism": -1,
                "vengefulness": 0,
            },
            "short_summary": "A cautious mediator who tries to keep dangerous people talking before blades are drawn.",
        }

    if archetype == "guard":
        return {
            "traits": ["lawful", "disciplined", "protective"],
            "temperament": "controlled",
            "speech_style": "firm and direct",
            "risk_tolerance": "medium",
            "conflict_style": "asserts order and clear boundaries",
            "morality": {
                "lawfulness": 3,
                "compassion": 1,
                "honor": 2,
                "greed": -1,
                "opportunism": -2,
                "vengefulness": 0,
            },
            "short_summary": "A disciplined guard who values order, duty, and restraint.",
        }

    if archetype == "thief":
        return {
            "traits": ["opportunistic", "irreverent", "risk_tolerant"],
            "temperament": "quick-witted",
            "speech_style": "wry and evasive",
            "risk_tolerance": "high",
            "conflict_style": "avoids fair fights and favors leverage",
            "morality": {
                "lawfulness": -3,
                "compassion": 0,
                "honor": -1,
                "greed": 3,
                "opportunism": 3,
                "vengefulness": 0,
            },
            "short_summary": "A risk-tolerant opportunist who respects cleverness and profit.",
        }

    return {
        "traits": ["observant"],
        "temperament": "reserved",
        "speech_style": "plain and direct",
        "risk_tolerance": "medium",
        "conflict_style": "context-dependent",
        "morality": {
            "lawfulness": 0,
            "compassion": 0,
            "honor": 0,
            "greed": 0,
            "opportunism": 0,
            "vengefulness": 0,
        },
        "short_summary": "A newly introduced figure whose deeper history is still being established.",
    }


def build_dynamic_npc_profile_scaffold(
    *,
    npc_id: str,
    name: str = "",
    identity_arc: str = "",
    current_role: str = "",
    active_motivations: List[Dict[str, Any]] | None = None,
    location_id: str = "",
    source_event: str = "",
    context_summary: str = "",
    tick: int = 0,
) -> Dict[str, Any]:
    npc_id = _safe_str(npc_id)
    name = _safe_str(name) or npc_id.replace("npc:", "") or "Unknown NPC"
    identity_arc = _safe_str(identity_arc)
    current_role = _safe_str(current_role)
    archetype = _archetype_from_identity_arc(identity_arc, current_role)
    defaults = _defaults_for_archetype(archetype)

    return normalize_npc_profile({
        "npc_id": npc_id,
        "name": name,
        "profile_version": PROFILE_VERSION,
        "origin": "dynamic_scaffold",
        "created_tick": int(tick or 0),
        "updated_tick": int(tick or 0),
        "first_seen": {
            "location_id": _safe_str(location_id),
            "source_event": _safe_str(source_event),
            "context_summary": _safe_str(context_summary),
        },
        "biography": {
            "short_summary": _safe_str(defaults.get("short_summary")),
            "full_biography": "",
            "public_reputation": "",
            "private_notes": "",
        },
        "history": {
            "background": "",
            "major_life_events": [],
            "recent_events": [],
        },
        "personality": {
            "traits": _safe_list(defaults.get("traits")),
            "temperament": _safe_str(defaults.get("temperament")),
            "speech_style": _safe_str(defaults.get("speech_style")),
            "risk_tolerance": _safe_str(defaults.get("risk_tolerance")),
            "conflict_style": _safe_str(defaults.get("conflict_style")),
        },
        "morality": deepcopy(_safe_dict(defaults.get("morality"))),
        "motivations": deepcopy(_safe_list(active_motivations)),
        "relationships": {},
        "evolution": {
            "identity_arc": identity_arc,
            "current_role": current_role,
            "arc_stage": "",
        },
        "card_edit_state": {
            "editable": True,
            "revision": 1,
            "last_edited_by": "system",
        },
        "source": "deterministic_dynamic_npc_profile_store",
    })


def normalize_npc_profile(profile: Dict[str, Any]) -> Dict[str, Any]:
    profile = deepcopy(_safe_dict(profile))

    profile["npc_id"] = _safe_str(profile.get("npc_id"))
    profile["name"] = _safe_str(profile.get("name") or profile["npc_id"].replace("npc:", "") or "Unknown NPC")
    profile["profile_version"] = _safe_int(profile.get("profile_version"), PROFILE_VERSION)
    profile["origin"] = _safe_str(profile.get("origin") or "dynamic_scaffold")
    profile["created_tick"] = _safe_int(profile.get("created_tick"), 0)
    profile["updated_tick"] = _safe_int(profile.get("updated_tick"), profile["created_tick"])

    first_seen = _safe_dict(profile.get("first_seen"))
    profile["first_seen"] = {
        "location_id": _safe_str(first_seen.get("location_id")),
        "source_event": _safe_str(first_seen.get("source_event")),
        "context_summary": _safe_str(first_seen.get("context_summary")),
    }

    biography = _safe_dict(profile.get("biography"))
    profile["biography"] = {
        "short_summary": _safe_str(biography.get("short_summary")),
        "full_biography": _safe_str(biography.get("full_biography")),
        "public_reputation": _safe_str(biography.get("public_reputation")),
        "private_notes": _safe_str(biography.get("private_notes")),
    }

    history = _safe_dict(profile.get("history"))
    profile["history"] = {
        "background": _safe_str(history.get("background")),
        "major_life_events": _safe_list(history.get("major_life_events")),
        "recent_events": _safe_list(history.get("recent_events")),
    }

    personality = _safe_dict(profile.get("personality"))
    profile["personality"] = {
        "traits": _safe_list(personality.get("traits")),
        "temperament": _safe_str(personality.get("temperament")),
        "speech_style": _safe_str(personality.get("speech_style")),
        "risk_tolerance": _safe_str(personality.get("risk_tolerance")),
        "conflict_style": _safe_str(personality.get("conflict_style")),
    }

    morality = _safe_dict(profile.get("morality"))
    profile["morality"] = {
        "lawfulness": _safe_int(morality.get("lawfulness"), 0),
        "compassion": _safe_int(morality.get("compassion"), 0),
        "honor": _safe_int(morality.get("honor"), 0),
        "greed": _safe_int(morality.get("greed"), 0),
        "opportunism": _safe_int(morality.get("opportunism"), 0),
        "vengefulness": _safe_int(morality.get("vengefulness"), 0),
    }

    profile["motivations"] = _safe_list(profile.get("motivations"))
    profile["relationships"] = _safe_dict(profile.get("relationships"))

    evolution = _safe_dict(profile.get("evolution"))
    profile["evolution"] = {
        "identity_arc": _safe_str(evolution.get("identity_arc")),
        "current_role": _safe_str(evolution.get("current_role")),
        "arc_stage": _safe_str(evolution.get("arc_stage")),
    }

    edit_state = _safe_dict(profile.get("card_edit_state"))
    profile["card_edit_state"] = {
        "editable": bool(edit_state.get("editable", True)),
        "revision": _safe_int(edit_state.get("revision"), 1),
        "last_edited_by": _safe_str(edit_state.get("last_edited_by") or "system"),
    }

    profile["source"] = _safe_str(profile.get("source") or "deterministic_dynamic_npc_profile_store")
    return profile


def ensure_dynamic_npc_profile(
    *,
    npc_id: str,
    name: str = "",
    identity_arc: str = "",
    current_role: str = "",
    active_motivations: List[Dict[str, Any]] | None = None,
    location_id: str = "",
    source_event: str = "",
    context_summary: str = "",
    tick: int = 0,
) -> Dict[str, Any]:
    existing = load_npc_profile(npc_id)
    if existing:
        return {
            "created": False,
            "profile": normalize_npc_profile(existing),
            "path": str(npc_profile_path(npc_id)),
            "reason": "profile_already_exists",
            "source": "deterministic_dynamic_npc_profile_store",
        }

    profile = build_dynamic_npc_profile_scaffold(
        npc_id=npc_id,
        name=name,
        identity_arc=identity_arc,
        current_role=current_role,
        active_motivations=active_motivations,
        location_id=location_id,
        source_event=source_event,
        context_summary=context_summary,
        tick=tick,
    )
    saved = save_npc_profile(profile)

    return {
        "created": True,
        "profile": deepcopy(profile),
        "path": saved["path"],
        "reason": "profile_created",
        "source": "deterministic_dynamic_npc_profile_store",
    }


def update_npc_character_card(
    npc_id: str,
    updates: Dict[str, Any],
    *,
    edited_by: str = "user",
    tick: int = 0,
) -> Dict[str, Any]:
    existing = load_npc_profile(npc_id)
    if not existing:
        return {
            "updated": False,
            "reason": "profile_not_found",
            "npc_id": _safe_str(npc_id),
            "source": "deterministic_dynamic_npc_profile_store",
        }

    profile = normalize_npc_profile(existing)
    updates = _safe_dict(updates)

    # Editable prose sections.
    for section in ("biography", "history", "personality", "morality"):
        if section in updates:
            if section == "morality":
                merged = _safe_dict(profile.get(section))
                merged.update(_safe_dict(updates.get(section)))
                profile[section] = merged
            else:
                merged = _safe_dict(profile.get(section))
                merged.update(_safe_dict(updates.get(section)))
                profile[section] = merged

    if "motivations" in updates:
        profile["motivations"] = _safe_list(updates.get("motivations"))

    if "evolution" in updates:
        merged = _safe_dict(profile.get("evolution"))
        merged.update(_safe_dict(updates.get("evolution")))
        profile["evolution"] = merged

    edit_state = _safe_dict(profile.get("card_edit_state"))
    edit_state["revision"] = _safe_int(edit_state.get("revision"), 1) + 1
    edit_state["last_edited_by"] = _safe_str(edited_by)
    profile["card_edit_state"] = edit_state
    profile["updated_tick"] = int(tick or 0)

    saved = save_npc_profile(profile)
    return {
        "updated": True,
        "profile": deepcopy(saved["profile"]),
        "path": saved["path"],
        "source": "deterministic_dynamic_npc_profile_store",
    }
