from __future__ import annotations

import logging
from app.config.env import env_str as _env_str

import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

logger = logging.getLogger(__name__)

PROFILE_VERSION = "npc_evolution_profile_v1"


def _safe_str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(value: str) -> str:
    value = _safe_str(value).strip().lower()
    if value.startswith("npc:"):
        value = value.split("npc:", 1)[1]
    value = re.sub(r"[^a-z0-9_.-]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or "unknown_npc"


def default_profile_root() -> Path:
    return Path(
        _env_str(
            "RPG_NPC_PROFILE_ROOT",
            "resources/data/rpg_npc_profiles",
        )
    )


def profile_path_for_npc(npc_id: str, *, root: Path | None = None) -> Path:
    root = root or default_profile_root()
    return root / f"{_slug(npc_id)}.json"


def legacy_profile_paths_for_npc(npc_id: str, *, root: Path | None = None) -> List[Path]:
    root = root or default_profile_root()
    raw = _safe_str(npc_id).strip().lower()
    paths: List[Path] = []
    if raw.startswith("npc:"):
        legacy_slug = re.sub(r"[^a-z0-9_.-]+", "_", raw).strip("_")
        paths.append(root / f"{legacy_slug}.json")
    else:
        paths.append(root / f"npc_{_slug(npc_id)}.json")
    canonical = profile_path_for_npc(npc_id, root=root)
    return [path for path in paths if path != canonical]


def load_npc_profile(npc_id: str, *, root: Path | None = None) -> Dict[str, Any]:
    from app.persistence.runtime import uses_postgresql_runtime
    if uses_postgresql_runtime():
        from app.apps.rpg.world.persistence.narrative_npc_stores import load_npc_profile_postgres
        return load_npc_profile_postgres(npc_id, root=root)
    path = profile_path_for_npc(npc_id, root=root)
    if not path.exists():
        return {
            "format_version": PROFILE_VERSION,
            "npc_id": npc_id,
            "created_at": _now_iso(),
            "updated_at": "",
            "evolution": {
                "arc_stage": "stable",
                "axes": {},
                "memories": [],
                "world_signals": [],
                "future_hooks": [],
                "semantic_intents": [],
                "milestones": [],
                "signals_applied": [],
            },
            "audit": [],
        }
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            data.setdefault("format_version", PROFILE_VERSION)
            data.setdefault("npc_id", npc_id)
            data.setdefault("evolution", {})
            data.setdefault("audit", [])
            return data
    except Exception:
        logger.debug("suppressed error in %s", "load_npc_profile", exc_info=True)
    return {
        "format_version": PROFILE_VERSION,
        "npc_id": npc_id,
        "created_at": _now_iso(),
        "updated_at": "",
        "evolution": {},
        "audit": [{"kind": "profile_load_failed_reset", "at": _now_iso()}],
    }


def _bounded_extend_unique(
    existing: List[Dict[str, Any]],
    incoming: List[Dict[str, Any]],
    *,
    id_key: str,
    limit: int,
) -> List[Dict[str, Any]]:
    out = [deepcopy(item) for item in existing if isinstance(item, dict)]
    seen = {_safe_str(item.get(id_key)) or json.dumps(item, sort_keys=True, default=str) for item in out}
    for item in incoming:
        if not isinstance(item, dict):
            continue
        marker = _safe_str(item.get(id_key)) or json.dumps(item, sort_keys=True, default=str)
        if marker in seen:
            continue
        seen.add(marker)
        out.append(deepcopy(item))
    if len(out) > limit:
        out = out[-limit:]
    return out


def _profile_projection_from_arc(arc: Dict[str, Any]) -> Dict[str, Any]:
    arc = _safe_dict(arc)
    return {
        "arc_stage": _safe_str(arc.get("arc_stage")) or "stable",
        "axes": _safe_dict(arc.get("axes")),
        "memories": _safe_list(arc.get("memories"))[-30:],
        "world_signals": _safe_list(arc.get("world_signals"))[-20:],
        "future_hooks": _safe_list(arc.get("future_hooks"))[-20:],
        "semantic_intents": _safe_list(arc.get("semantic_intents"))[-20:],
        "milestones": _safe_list(arc.get("milestones"))[-20:],
    }


def persist_npc_evolution_profiles(
    *,
    runtime_state: Dict[str, Any],
    root: Path | None = None,
) -> Dict[str, Any]:
    """Persist runtime_state.npc_evolution.arcs into file-based NPC profiles."""
    from app.persistence.runtime import uses_postgresql_runtime
    if uses_postgresql_runtime():
        from app.apps.rpg.world.persistence.narrative_npc_stores import persist_npc_evolution_profiles_postgres
        return persist_npc_evolution_profiles_postgres(runtime_state=runtime_state, root=root)
    runtime_state = _safe_dict(runtime_state)
    evo = _safe_dict(runtime_state.get("npc_evolution"))
    arcs = _safe_dict(evo.get("arcs"))
    signals = _safe_list(evo.get("signals"))
    root = root or default_profile_root()
    root.mkdir(parents=True, exist_ok=True)

    written: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    signals_by_npc: Dict[str, List[Dict[str, Any]]] = {}
    for signal in signals:
        signal_dict = _safe_dict(signal)
        npc_id = _safe_str(signal_dict.get("npc_id"))
        if npc_id:
            signals_by_npc.setdefault(npc_id, []).append(signal_dict)

    for npc_id, arc_any in arcs.items():
        try:
            arc = _safe_dict(arc_any)
            profile = load_npc_profile(str(npc_id), root=root)
            evolution = profile.setdefault("evolution", {})
            projected = _profile_projection_from_arc(arc)

            evolution["arc_stage"] = projected["arc_stage"]
            evolution["axes"] = projected["axes"]
            evolution["memories"] = _bounded_extend_unique(
                _safe_list(evolution.get("memories")),
                projected["memories"],
                id_key="signal_id",
                limit=30,
            )
            evolution["world_signals"] = _bounded_extend_unique(
                _safe_list(evolution.get("world_signals")),
                projected["world_signals"],
                id_key="signal_id",
                limit=20,
            )
            evolution["future_hooks"] = _bounded_extend_unique(
                _safe_list(evolution.get("future_hooks")),
                projected["future_hooks"],
                id_key="signal_id",
                limit=20,
            )
            evolution["semantic_intents"] = _bounded_extend_unique(
                _safe_list(evolution.get("semantic_intents")),
                projected["semantic_intents"],
                id_key="signal_id",
                limit=20,
            )
            evolution["milestones"] = _bounded_extend_unique(
                _safe_list(evolution.get("milestones")),
                projected["milestones"],
                id_key="signal_id",
                limit=20,
            )
            evolution["signals_applied"] = _bounded_extend_unique(
                _safe_list(evolution.get("signals_applied")),
                [
                    {
                        "signal_id": signal.get("signal_id"),
                        "kind": signal.get("kind"),
                        "turn_index": signal.get("turn_index"),
                        "summary": signal.get("summary"),
                        "source": signal.get("source"),
                    }
                    for signal in signals_by_npc.get(str(npc_id), [])
                    if signal.get("consumed")
                ],
                id_key="signal_id",
                limit=100,
            )

            profile["updated_at"] = _now_iso()
            profile.setdefault("audit", []).append(
                {
                    "kind": "npc_evolution_profile_persisted",
                    "at": profile["updated_at"],
                    "arc_stage": evolution.get("arc_stage"),
                    "signal_count": len(signals_by_npc.get(str(npc_id), [])),
                }
            )
            profile["audit"] = _safe_list(profile.get("audit"))[-50:]

            path = profile_path_for_npc(str(npc_id), root=root)
            with path.open("w", encoding="utf-8") as fh:
                json.dump(profile, fh, indent=2, sort_keys=True, ensure_ascii=False, default=str)

            removed_legacy_paths = []
            for legacy_path in legacy_profile_paths_for_npc(str(npc_id), root=root):
                if legacy_path.exists() and legacy_path != path:
                    try:
                        legacy_path.unlink()
                        removed_legacy_paths.append(str(legacy_path))
                    except Exception:
                        logger.debug("suppressed error in %s", "persist_npc_evolution_profiles", exc_info=True)

            written.append(
                {
                    "npc_id": str(npc_id),
                    "path": str(path),
                    "arc_stage": evolution.get("arc_stage"),
                    "memory_count": len(_safe_list(evolution.get("memories"))),
                    "future_hook_count": len(_safe_list(evolution.get("future_hooks"))),
                    "signal_count": len(_safe_list(evolution.get("signals_applied"))),
                    "removed_legacy_paths": removed_legacy_paths,
                }
            )
        except Exception as exc:
            errors.append({"npc_id": str(npc_id), "error": f"{type(exc).__name__}: {exc}"})

    return {
        "ok": not errors,
        "root": str(root),
        "written_count": len(written),
        "written": written,
        "errors": errors,
    }


def _profile_arc_projection(profile: Dict[str, Any]) -> Dict[str, Any]:
    """Bounded runtime projection of persistent NPC profile evolution.

    This is context only. It intentionally excludes any authoritative fields.
    """
    profile = _safe_dict(profile)
    evolution = _safe_dict(profile.get("evolution"))
    return {
        "npc_id": _safe_str(profile.get("npc_id")),
        "arc_stage": _safe_str(evolution.get("arc_stage")) or "stable",
        "axes": _safe_dict(evolution.get("axes")),
        "memories": _safe_list(evolution.get("memories"))[-8:],
        "future_hooks": _safe_list(evolution.get("future_hooks"))[-8:],
        "world_signals": _safe_list(evolution.get("world_signals"))[-6:],
        "semantic_intents": _safe_list(evolution.get("semantic_intents"))[-6:],
        "milestones": _safe_list(evolution.get("milestones"))[-5:],
        "signals_applied_count": len(_safe_list(evolution.get("signals_applied"))),
    }


def load_npc_evolution_profiles_for_runtime(
    *,
    npc_ids: List[str],
    root: Path | None = None,
) -> Dict[str, Any]:
    """Load file-based NPC evolution profiles into a bounded runtime shape."""
    from app.persistence.runtime import uses_postgresql_runtime
    if uses_postgresql_runtime():
        from app.apps.rpg.world.persistence.narrative_npc_stores import load_npc_evolution_profiles_for_runtime_postgres
        return load_npc_evolution_profiles_for_runtime_postgres(npc_ids=npc_ids, root=root)
    root = root or default_profile_root()
    loaded: Dict[str, Any] = {}
    missing: List[str] = []
    errors: List[Dict[str, Any]] = []

    for npc_id in npc_ids if isinstance(npc_ids, list) else []:
        npc_id = _safe_str(npc_id)
        if not npc_id:
            continue
        try:
            path = profile_path_for_npc(npc_id, root=root)
            if not path.exists():
                missing.append(npc_id)
                continue
            profile = load_npc_profile(npc_id, root=root)
            projection = _profile_arc_projection(profile)
            loaded[npc_id] = {
                "path": str(path),
                "profile": projection,
            }
        except Exception as exc:
            errors.append({"npc_id": npc_id, "error": f"{type(exc).__name__}: {exc}"})

    return {
        "ok": not errors,
        "root": str(root),
        "loaded_count": len(loaded),
        "missing_count": len(missing),
        "loaded": loaded,
        "missing": missing,
        "errors": errors,
    }


