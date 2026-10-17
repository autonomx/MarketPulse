"""Persist encountered NPC biographies into PostgreSQL Campaign Bible canon."""
from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from app.persistence.database import default_database
from app.security.tenant_context import current_tenant
from app.apps.rpg.foundation.persistence.rpg_campaign_bible_repository import campaign_bible_hash
from app.persistence.unit_of_work import unit_of_work

from .campaign_lore_store import (
    _campaign_id,
    _campaign_title,
    _hydrate_session,
    _mapping,
    _portable_bible,
    _save_portable_projection,
    _text,
)
from .npc_lore_projection import encountered_npc_ids, ensure_encountered_npc_lore

if TYPE_CHECKING:
    from app.apps.rpg.genesis.contracts import CampaignSessions


def sync_encountered_npc_lore(
    session_id: str,
    session: Mapping[str, Any],
    *,
    sessions: CampaignSessions,
    explicit_npc_ids: Sequence[str] = (),
    database: Any | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Persist newly encountered NPC bios once and return the hydrated session."""

    npc_ids = encountered_npc_ids(session, explicit_npc_ids=explicit_npc_ids)
    if not npc_ids:
        return dict(session), {
            "mode": "no_encountered_npcs",
            "persisted": False,
            "encountered_npc_ids": [],
            "created_npc_ids": [],
            "changed": False,
        }
    campaign_id = _campaign_id(session_id, session)
    portable = _portable_bible(session)
    projection = _mapping(session.get("campaign_bible_projection"))
    _preview, ensured, _created, preview_changed = ensure_encountered_npc_lore(
        portable,
        session,
        explicit_npc_ids=npc_ids,
    )
    if projection.get("content_hash") and not preview_changed:
        return dict(session), {
            "mode": "portable_projection_already_synced",
            "persisted": True,
            "campaign_id": campaign_id,
            "revision": int(projection.get("canon_revision") or 0),
            "content_hash": _text(projection.get("content_hash")),
            "encountered_npc_ids": list(ensured),
            "created_npc_ids": [],
            "changed": False,
        }
    try:
        db = database or default_database()
        context = current_tenant()
        with unit_of_work(db) as work:
            campaign = work.rpg.get_campaign(context, campaign_id, for_update=True)
            if campaign is None:
                manifest = _mapping(session.get("manifest"))
                setup = _mapping(session.get("setup_payload"))
                work.rpg.create_campaign(
                    context,
                    campaign_id=campaign_id,
                    title=_campaign_title(session, campaign_id),
                    state=deepcopy(_mapping(session.get("state"))),
                    engine_version="rpg-npc-lore-sync-v1",
                    schema_version=_text(manifest.get("schema_version")) or "rpg-session-v1",
                    seed=_text(manifest.get("seed") or setup.get("seed")) or "0",
                    metadata={"source": "encountered_npc_profile_sync_v1"},
                )
            current = work.campaign_bibles.get(context, campaign_id, for_update=True)
            bible = deepcopy(current["document"] if current is not None else portable)
            next_revision = int(current["revision"]) + 1 if current is not None else 1
            bible, ensured, created, changed = ensure_encountered_npc_lore(
                bible,
                session,
                explicit_npc_ids=npc_ids,
                canon_revision=next_revision,
            )
            stored = current
            if changed or current is None:
                bible["canon_revision"] = max(
                    int(bible.get("canon_revision") or 0),
                    next_revision,
                )
                stored = work.campaign_bibles.put(
                    context,
                    campaign_id=campaign_id,
                    document=bible,
                    expected_revision=(int(current["revision"]) if current is not None else 0),
                    provenance={
                        **(_mapping(current.get("provenance")) if current is not None else {}),
                        "last_source": "encountered_npc_profile_sync_v1",
                        "encountered_npc_ids": list(ensured),
                        "created_npc_ids": list(created),
                    },
                    consistency_report=(
                        _mapping(current.get("consistency_report"))
                        if current is not None
                        else {}
                    ),
                    completeness=(
                        _mapping(current.get("completeness"))
                        if current is not None
                        else {}
                    ),
                )
            work.commit()
        assert stored is not None
        hydrated = _hydrate_session(
            session,
            stored["document"],
            revision=int(stored["revision"]),
            content_hash=str(stored["content_hash"]),
        )
        if changed:
            hydrated = _save_portable_projection(hydrated, sessions=sessions)
        return hydrated, {
            "mode": "postgresql_authority",
            "persisted": True,
            "campaign_id": campaign_id,
            "revision": int(stored["revision"]),
            "content_hash": str(stored["content_hash"]),
            "encountered_npc_ids": list(ensured),
            "created_npc_ids": list(created),
            "changed": changed,
        }
    except Exception as exc:
        bible, ensured, created, changed = ensure_encountered_npc_lore(
            portable,
            session,
            explicit_npc_ids=npc_ids,
        )
        digest = campaign_bible_hash(bible)
        fallback = _hydrate_session(
            session,
            bible,
            revision=int(bible.get("canon_revision") or 0),
            content_hash=digest,
        )
        if changed:
            fallback = _save_portable_projection(fallback, sessions=sessions)
        return fallback, {
            "mode": "portable_projection_fallback",
            "persisted": False,
            "campaign_id": campaign_id,
            "content_hash": digest,
            "encountered_npc_ids": list(ensured),
            "created_npc_ids": list(created),
            "changed": changed,
            "error": type(exc).__name__,
        }
