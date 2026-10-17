"""PostgreSQL-backed narrative and NPC evolution compatibility stores."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from app.apps.rpg.foundation.narrative_event import NarrativeEvent
from app.apps.rpg.world.npc_evolution.profile_store import (
    PROFILE_VERSION,
    _bounded_extend_unique,
    _profile_arc_projection,
    _profile_projection_from_arc,
    _safe_str,
)
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

from app.persistence.document_store import PostgresDocumentStore
from app.persistence.document_schemas import register_document_schema


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class PostgresNarrativeEventStore:
    def __init__(self, db_path: str | None = None, session_id: str | None = None) -> None:
        if db_path not in (None, "", "postgresql"):
            raise RuntimeError("SQLite narrative event authority is retired")
        self.session_id = session_id or f"session:{int(datetime.now().timestamp())}"
        self.documents = PostgresDocumentStore()

    def get_history(
        self,
        limit: int = 100,
        offset: int = 0,
        event_type: str | None = None,
        min_importance: float = 0.0,
    ) -> list[NarrativeEvent]:
        rows = [
            payload
            for _, payload, _ in self.documents.list(
                module="rpg", record_type="narrative-event", limit=5000
            )
            if isinstance(payload, dict)
            and (event_type is None or payload.get("event_type") == event_type)
            and float(payload.get("importance") or 0.0) >= float(min_importance)
        ]
        rows.sort(
            key=lambda item: (float(item.get("timestamp") or 0.0), str(item.get("id") or "")),
            reverse=True,
        )
        return [self._event(item) for item in rows[max(0, offset) : max(0, offset) + max(0, limit)]]

    def delete_session(self, session_id: str) -> int:
        deleted = 0
        for record_id, payload, _ in self.documents.list(
            module="rpg", record_type="narrative-event", limit=5000
        ):
            if isinstance(payload, dict) and payload.get("session_id") == session_id:
                deleted += int(
                    self.documents.delete(
                        module="rpg",
                        record_type="narrative-event",
                        record_id=record_id,
                    )
                )
        return deleted

    @staticmethod
    def _event(payload: dict[str, Any]) -> NarrativeEvent:
        return NarrativeEvent(
            id=str(payload.get("id") or ""),
            type=str(payload.get("event_type") or "unknown"),
            description=str(payload.get("description") or ""),
            actors=list(payload.get("actors") or []),
            location=payload.get("location"),
            importance=float(payload.get("importance") or 0.5),
            emotional_weight=float(payload.get("emotional_weight") or 0.0),
            tags=list(payload.get("tags") or []),
            raw_event=dict(payload.get("raw_event") or {}),
        )


def _new_npc_profile(npc_id: str) -> dict[str, Any]:
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


def load_npc_profile_postgres(npc_id: str, *, root: Any = None) -> dict[str, Any]:
    if root is not None:
        raise RuntimeError("file-backed NPC profile authority is retired")
    value = PostgresDocumentStore().read(
        module="rpg",
        record_type="npc-evolution-profile",
        record_id=str(npc_id),
        default=None,
    )
    return dict(value) if isinstance(value, dict) else _new_npc_profile(str(npc_id))


def persist_npc_evolution_profiles_postgres(
    *,
    runtime_state: dict[str, Any],
    root: Any = None,
) -> dict[str, Any]:
    if root is not None:
        raise RuntimeError("file-backed NPC profile authority is retired")
    runtime_state = _safe_dict(runtime_state)
    evolution_root = _safe_dict(runtime_state.get("npc_evolution"))
    arcs = _safe_dict(evolution_root.get("arcs"))
    signals = _safe_list(evolution_root.get("signals"))
    signals_by_npc: dict[str, list[dict[str, Any]]] = {}
    for signal in signals:
        value = _safe_dict(signal)
        npc_id = _safe_str(value.get("npc_id"))
        if npc_id:
            signals_by_npc.setdefault(npc_id, []).append(value)
    store = PostgresDocumentStore()
    written: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for npc_id, arc_value in arcs.items():
        try:
            profile = load_npc_profile_postgres(str(npc_id))
            profile = deepcopy(profile)
            evolution = profile.setdefault("evolution", {})
            projected = _profile_projection_from_arc(_safe_dict(arc_value))
            evolution["arc_stage"] = projected["arc_stage"]
            evolution["axes"] = projected["axes"]
            for key, limit in (
                ("memories", 30),
                ("world_signals", 20),
                ("future_hooks", 20),
                ("semantic_intents", 20),
                ("milestones", 20),
            ):
                evolution[key] = _bounded_extend_unique(
                    _safe_list(evolution.get(key)),
                    projected[key],
                    id_key="signal_id",
                    limit=limit,
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
            store.write(
                profile,
                module="rpg",
                record_type="npc-evolution-profile",
                record_id=str(npc_id),
            )
            written.append(
                {
                    "npc_id": str(npc_id),
                    "record": f"postgresql://npc-evolution-profile/{npc_id}",
                    "arc_stage": evolution.get("arc_stage"),
                    "memory_count": len(_safe_list(evolution.get("memories"))),
                    "future_hook_count": len(_safe_list(evolution.get("future_hooks"))),
                    "signal_count": len(_safe_list(evolution.get("signals_applied"))),
                }
            )
        except Exception as exc:
            errors.append({"npc_id": str(npc_id), "error": f"{type(exc).__name__}: {exc}"})
    return {
        "ok": not errors,
        "root": "postgresql://omnix_module_records/rpg/npc-evolution-profile",
        "written_count": len(written),
        "written": written,
        "errors": errors,
    }


def load_npc_evolution_profiles_for_runtime_postgres(
    *,
    npc_ids: list[str],
    root: Any = None,
) -> dict[str, Any]:
    if root is not None:
        raise RuntimeError("file-backed NPC profile authority is retired")
    loaded: dict[str, Any] = {}
    missing: list[str] = []
    errors: list[dict[str, Any]] = []
    store = PostgresDocumentStore()
    for raw_id in npc_ids if isinstance(npc_ids, list) else []:
        npc_id = _safe_str(raw_id)
        if not npc_id:
            continue
        try:
            profile = store.read(
                module="rpg",
                record_type="npc-evolution-profile",
                record_id=npc_id,
                default=None,
            )
            if not isinstance(profile, dict):
                missing.append(npc_id)
                continue
            loaded[npc_id] = {
                "record": f"postgresql://npc-evolution-profile/{npc_id}",
                "profile": _profile_arc_projection(profile),
            }
        except Exception as exc:
            errors.append({"npc_id": npc_id, "error": f"{type(exc).__name__}: {exc}"})
    return {
        "ok": not errors,
        "root": "postgresql://omnix_module_records/rpg/npc-evolution-profile",
        "loaded_count": len(loaded),
        "missing_count": len(missing),
        "loaded": loaded,
        "missing": missing,
        "errors": errors,
    }


# Document shapes (WP-5.9). RPG is being retired; its two kinds are JSON objects.
register_document_schema("rpg", "narrative-event", dict[str, Any])
register_document_schema("rpg", "npc-evolution-profile", dict[str, Any])
