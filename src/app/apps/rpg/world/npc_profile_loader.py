from __future__ import annotations

import json
from copy import deepcopy
from app.caching.bounded_cache import bounded_lru_cache
from pathlib import Path
from typing import Any, Dict
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

REPO_ROOT = Path(__file__).resolve().parents[5]
NPC_PROFILE_DIR = REPO_ROOT / "resources" / "data" / "rpg" / "npcs"


def _validate_npc_profile(profile: Dict[str, Any], *, source_path: Path | None = None) -> Dict[str, Any]:
    profile = _safe_dict(profile)
    npc_id = _safe_str(profile.get("npc_id"))
    if not npc_id.startswith("npc:"):
        raise ValueError(f"npc_profile_invalid_id:{source_path or ''}:{npc_id!r}")

    name = _safe_str(profile.get("name"))
    if not name:
        raise ValueError(f"npc_profile_missing_name:{source_path or ''}:{npc_id}")

    personality = _safe_dict(profile.get("personality"))
    knowledge = _safe_dict(profile.get("knowledge_boundaries"))

    normalized = {
        "schema_version": _safe_str(profile.get("schema_version") or "npc_profile_v1"),
        "npc_id": npc_id,
        "name": name,
        "starting_role": _safe_str(profile.get("starting_role") or profile.get("role")),
        "role": _safe_str(profile.get("starting_role") or profile.get("role")),
        "current_role_hint": _safe_str(profile.get("current_role_hint")),
        "biography": _safe_str(profile.get("biography") or profile.get("short_bio")),
        "short_bio": _safe_str(profile.get("biography") or profile.get("short_bio")),
        "personality": {
            "core_traits": _safe_list(personality.get("core_traits") or profile.get("personality_traits")),
            "social_style": _safe_str(personality.get("social_style") or profile.get("speaking_style")),
            "values": _safe_list(personality.get("values")),
            "fears": _safe_list(personality.get("fears")),
        },
        "personality_traits": _safe_list(personality.get("core_traits") or profile.get("personality_traits")),
        "speaking_style": _safe_str(profile.get("speaking_style") or personality.get("social_style")),
        "knowledge_boundaries": {
            "knows_about": _safe_list(knowledge.get("knows_about")),
            "does_not_know_about": _safe_list(knowledge.get("does_not_know_about")),
            "must_not_claim": _safe_list(knowledge.get("must_not_claim")),
        },
        "relationships": _safe_dict(profile.get("relationships")),
        "starting_goals": _safe_list(profile.get("starting_goals")),
        "home_location_id": _safe_str(profile.get("home_location_id")),
        "work_location_id": _safe_str(profile.get("work_location_id")),
        "source": "file_npc_profile",
    }

    return normalized


@bounded_lru_cache(max_entries=1, ttl_seconds=3600.0)
def load_all_file_npc_profiles() -> Dict[str, Dict[str, Any]]:
    profiles: Dict[str, Dict[str, Any]] = {}
    if not NPC_PROFILE_DIR.exists():
        return profiles

    for path in sorted(NPC_PROFILE_DIR.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            profile = _validate_npc_profile(raw, source_path=path)
            profiles[_safe_str(profile.get("npc_id"))] = profile
        except Exception as exc:
            # Fail soft at runtime; tests can assert stricter validation.
            profiles[f"__error__:{path.name}"] = {
                "npc_id": f"__error__:{path.name}",
                "name": path.name,
                "error": f"{type(exc).__name__}: {exc}",
                "source": "file_npc_profile_error",
            }

    return profiles


def get_file_npc_profile(npc_id: str) -> Dict[str, Any]:
    profile = _safe_dict(load_all_file_npc_profiles().get(_safe_str(npc_id)))
    if profile and not profile.get("error"):
        return deepcopy(profile)
    return {}


