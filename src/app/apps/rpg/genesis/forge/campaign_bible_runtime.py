"""Load the authoritative Campaign Bible for turn-time narrative grounding."""
from __future__ import annotations

import logging

from typing import TYPE_CHECKING, Any, Mapping, cast

from app.apps.rpg.narration.narrative_engine import CampaignBibleSnapshot

if TYPE_CHECKING:
    from app.apps.rpg.genesis.contracts import CampaignSessions

logger = logging.getLogger(__name__)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _portable_snapshot(
    session: Mapping[str, Any],
    campaign_id: str,
) -> CampaignBibleSnapshot | None:
    projection = _mapping(session.get("campaign_bible_projection"))
    state = _mapping(session.get("state"))
    summary = _mapping(state.get("campaign_bible"))
    if not projection and not summary:
        return None
    document = {
        "schema_version": str(summary.get("schema_version") or "rpg_campaign_bible_v2"),
        "campaign_id": campaign_id,
        "canon_revision": int(
            projection.get("canon_revision")
            or summary.get("canon_revision")
            or 1
        ),
        "documents": list(projection.get("documents") or ()),
        "entities": dict(projection.get("entities") or {}),
        "facts": list(projection.get("facts") or ()),
        "relationships": list(projection.get("relationships") or ()),
        "knowledge_rules": list(projection.get("knowledge_rules") or ()),
        "retrieval_cards": list(projection.get("retrieval_cards") or ()),
        "indexes": dict(projection.get("indexes") or {}),
        "discovery_state": dict(projection.get("discovery_state") or {}),
        "story_threads": list(projection.get("story_threads") or ()),
        "mechanics_catalog": dict(projection.get("mechanics_catalog") or {}),
        "completeness": dict(summary.get("completeness") or {}),
    }
    return CampaignBibleSnapshot(
        campaign_id=campaign_id,
        revision=int(cast(Any, document["canon_revision"])),
        content_hash=str(
            projection.get("content_hash")
            or summary.get("content_hash")
            or ""
        ),
        document=document,
        provenance={"source": "portable_session_projection"},
        consistency_report={},
        completeness=dict(summary.get("completeness") or {}),
    )


def _postgres_snapshot(campaign_id: str) -> CampaignBibleSnapshot | None:
    try:
        from app.security.tenant_context import current_tenant
        from app.persistence.unit_of_work import unit_of_work

        with unit_of_work() as work:
            record = work.campaign_bibles.get(current_tenant(), campaign_id)
            work.rollback()
        if record is not None:
            snapshot = CampaignBibleSnapshot.from_record(record)
            return CampaignBibleSnapshot(
                campaign_id=snapshot.campaign_id,
                revision=snapshot.revision,
                content_hash=snapshot.content_hash,
                document=snapshot.document,
                provenance={**dict(snapshot.provenance), "runtime_source": "postgresql"},
                consistency_report=snapshot.consistency_report,
                completeness=snapshot.completeness,
            )
    except Exception:
        logger.debug("suppressed error in %s", "_postgres_snapshot", exc_info=True)
        return None
    return None


def load_campaign_bible_snapshot(
    campaign_id: str,
    *,
    sessions: CampaignSessions,
    session: Mapping[str, Any] | None = None,
    prefer_postgresql: bool = True,
) -> CampaignBibleSnapshot | None:
    """Load PostgreSQL authority first, then the portable save projection."""

    campaign_id = str(campaign_id or "").strip()
    if not campaign_id:
        return None
    if prefer_postgresql:
        stored = _postgres_snapshot(campaign_id)
        if stored is not None:
            return stored
    if session is None:
        try:
            session = sessions.load(campaign_id)
        except Exception:
            session = None
    portable = _portable_snapshot(session or {}, campaign_id)
    if portable is not None:
        return portable
    if not prefer_postgresql:
        return _postgres_snapshot(campaign_id)
    return None
