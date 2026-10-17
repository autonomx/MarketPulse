"""Persist on-demand current-scene canon before narrative retrieval."""
from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from app.persistence.database import default_database
from app.security.tenant_context import current_tenant
from app.apps.rpg.foundation.persistence.rpg_campaign_bible_repository import campaign_bible_hash
from app.persistence.unit_of_work import unit_of_work
from app.apps.rpg.genesis.worlds.published_canon_projection import project_published_canon

from .campaign_lore_store import (
    _campaign_id,
    _campaign_title,
    _hydrate_session,
    _mapping,
    _merge_published_world_canon,
    _portable_bible,
    _save_portable_projection,
    _text,
)
from .runtime_lore_materialization import (
    materialize_scene_lore,
    scene_lore_entity_is_rich,
    scene_lore_targets,
)

if TYPE_CHECKING:
    from app.apps.rpg.genesis.contracts import CampaignSessions


def _campaign_exists(
    work: Any,
    context: Any,
    campaign_id: str,
    session: Mapping[str, Any],
) -> None:
    # The lock is what matters here; a turn's campaign row is the whole saved game (WP-5.6).
    if work.rpg.lock_campaign(context, campaign_id):
        return
    manifest = _mapping(session.get("manifest"))
    setup = _mapping(session.get("setup_payload"))
    work.rpg.create_campaign(
        context,
        campaign_id=campaign_id,
        title=_campaign_title(session, campaign_id),
        state=deepcopy(_mapping(session.get("state"))),
        engine_version="rpg-runtime-lore-materialization-v1",
        schema_version=_text(manifest.get("schema_version")) or "rpg-session-v1",
        seed=_text(manifest.get("seed") or setup.get("seed")) or "0",
        metadata={"source": "runtime_scene_materialization_v1"},
    )


def _pinned_world_canon(
    work: Any,
    context: Any,
    campaign_id: str,
) -> dict[str, Any]:
    binding = work.world_scenarios.get_campaign_binding(context, campaign_id)
    if binding is None:
        return {}
    revision = work.world_scenarios.get_world_revision(
        context,
        _text(binding.get("world_id")),
        int(binding.get("world_revision") or 0),
    )
    document = _mapping((revision or {}).get("document"))
    canon = _mapping(document.get("canon"))
    if not canon:
        return {}
    return project_published_canon(
        canon,
        campaign_id=campaign_id,
        canon_revision=int(binding.get("world_revision") or 1),
    )


def _has_dossier_and_document(
    bible: Mapping[str, Any],
    entity_id: str,
) -> bool:
    entity = _mapping(_mapping(bible.get("entities")).get(entity_id))
    rich = scene_lore_entity_is_rich(entity)
    target = entity_id.casefold()
    documented = any(
        target
        in {
            _text(value).casefold()
            for value in (
                *list(row.get("entity_refs") or ()),
                *list(row.get("entities") or ()),
            )
        }
        for row in bible.get("documents") or ()
        if isinstance(row, Mapping)
    )
    return rich and documented


def _assert_materialized(
    bible: Mapping[str, Any],
    session: Mapping[str, Any],
    result: Mapping[str, Any],
    explicit_entity_ids: Sequence[str],
) -> tuple[str, ...]:
    targets = scene_lore_targets(
        result,
        session,
        explicit_entity_ids=explicit_entity_ids,
    )
    missing = tuple(
        target.entity_id
        for target in targets
        if not _has_dossier_and_document(bible, target.entity_id)
    )
    if missing:
        raise RuntimeError(
            "runtime_scene_lore_materialization_incomplete:" + ",".join(missing)
        )
    return tuple(target.entity_id for target in targets)


def _persist_portable_without_replacing(
    hydrated: dict[str, Any],
    *,
    sessions: CampaignSessions,
) -> dict[str, Any]:
    """Save as a side effect while preserving the exact current-turn projection.

    Some compact session-store adapters return a compatibility projection that can
    omit newly attached top-level fields. Grounding must continue with the exact
    hydrated Campaign Bible that was just validated, not that lossy return value.
    """

    _save_portable_projection(hydrated, sessions=sessions)
    return hydrated


def _pregenerate_scene_lore(
    db: Any,
    context: Any,
    campaign_id: str,
    session: Mapping[str, Any],
    result: Mapping[str, Any],
    portable: Mapping[str, Any],
    *,
    explicit_entity_ids: Sequence[str],
    llm_gateway: Any | None,
) -> dict[str, Mapping[str, Any]]:
    """The scene's missing-canon bundles, generated from an unlocked read of the bible."""
    with unit_of_work(db) as work:
        stored = work.campaign_bibles.get(context, campaign_id)
        bible = deepcopy(stored["document"] if stored is not None else portable)
        world_canon = _pinned_world_canon(work, context, campaign_id)
        if world_canon:
            bible = _merge_published_world_canon(world_canon, bible, campaign_id=campaign_id)
        next_revision = int(stored["revision"]) + 1 if stored is not None else 1
    bundles: dict[str, Mapping[str, Any]] = {}
    materialize_scene_lore(
        bible,
        session,
        result,
        campaign_id=campaign_id,
        explicit_entity_ids=explicit_entity_ids,
        canon_revision=next_revision,
        llm_gateway=llm_gateway,
        generated=bundles,
    )
    return bundles


def ensure_turn_scene_lore(
    session_id: str,
    session: Mapping[str, Any],
    result: Mapping[str, Any],
    *,
    sessions: CampaignSessions,
    explicit_entity_ids: Sequence[str] = (),
    database: Any | None = None,
    llm_gateway: Any | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Materialize missing location and encountered-entity canon for this turn.

    PostgreSQL remains preferred authority. If it is unavailable, deterministic
    canon is written to the portable session projection and used immediately. The
    function fails closed only when neither authority can provide complete dossiers.
    """

    campaign_id = _campaign_id(session_id, session)
    portable = _portable_bible(session)
    targets = scene_lore_targets(
        result,
        session,
        explicit_entity_ids=explicit_entity_ids,
    )
    if not targets:
        return dict(session), {
            "mode": "no_scene_targets",
            "persisted": False,
            "changed": False,
            "target_entity_ids": [],
            "created_entity_ids": [],
            "created_document_ids": [],
        }

    try:
        db = database or default_database()
        context = current_tenant()
        # The model calls run before the campaign and bible rows are locked, so
        # other turns of this campaign do not wait on them (WP-8.6). The locked
        # pass merges those bundles into the current bible without a model call.
        bundles = _pregenerate_scene_lore(
            db,
            context,
            campaign_id,
            session,
            result,
            portable,
            explicit_entity_ids=explicit_entity_ids,
            llm_gateway=llm_gateway,
        )
        with unit_of_work(db) as work:
            _campaign_exists(work, context, campaign_id, session)
            stored = work.campaign_bibles.get(context, campaign_id, for_update=True)
            bible = deepcopy(stored["document"] if stored is not None else portable)
            world_canon = _pinned_world_canon(work, context, campaign_id)
            if world_canon:
                bible = _merge_published_world_canon(
                    world_canon,
                    bible,
                    campaign_id=campaign_id,
                )
            next_revision = int(stored["revision"]) + 1 if stored is not None else 1
            bible, report = materialize_scene_lore(
                bible,
                session,
                result,
                campaign_id=campaign_id,
                explicit_entity_ids=explicit_entity_ids,
                canon_revision=next_revision,
                llm_gateway=False,
                pregenerated=bundles,
            )
            _assert_materialized(bible, session, result, explicit_entity_ids)
            should_write = (
                stored is None
                or campaign_bible_hash(bible)
                != str(stored.get("content_hash") or "")
            )
            if should_write:
                bible["canon_revision"] = max(
                    int(bible.get("canon_revision") or 0),
                    next_revision,
                )
                stored = work.campaign_bibles.put(
                    context,
                    campaign_id=campaign_id,
                    document=bible,
                    expected_revision=(
                        int(stored["revision"]) if stored is not None else 0
                    ),
                    provenance={
                        **(
                            _mapping(stored.get("provenance"))
                            if stored is not None
                            else {}
                        ),
                        "last_source": "runtime_scene_materialization_v1",
                        "target_entity_ids": [
                            target.entity_id for target in targets
                        ],
                        "created_entity_ids": list(
                            report.get("created_entity_ids") or ()
                        ),
                        "created_document_ids": list(
                            report.get("created_document_ids") or ()
                        ),
                    },
                    consistency_report=(
                        _mapping(stored.get("consistency_report"))
                        if stored is not None
                        else {}
                    ),
                    completeness=(
                        _mapping(stored.get("completeness"))
                        if stored is not None
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
        previous_hash = _text(
            _mapping(session.get("campaign_bible_projection")).get("content_hash")
        )
        if (
            report.get("changed") is True
            or previous_hash != str(stored["content_hash"])
        ):
            hydrated = _persist_portable_without_replacing(hydrated, sessions=sessions)
        return hydrated, {
            **report,
            "mode": "postgresql_authority",
            "persisted": True,
            "campaign_id": campaign_id,
            "revision": int(stored["revision"]),
            "content_hash": str(stored["content_hash"]),
            "target_entity_ids": [target.entity_id for target in targets],
        }
    except Exception as database_error:
        fallback_gateway = llm_gateway if llm_gateway is not None else False
        bible, report = materialize_scene_lore(
            portable,
            session,
            result,
            campaign_id=campaign_id,
            explicit_entity_ids=explicit_entity_ids,
            llm_gateway=fallback_gateway,
        )
        target_ids = _assert_materialized(
            bible,
            session,
            result,
            explicit_entity_ids,
        )
        digest = campaign_bible_hash(bible)
        fallback = _hydrate_session(
            session,
            bible,
            revision=int(bible.get("canon_revision") or 0),
            content_hash=digest,
        )
        fallback = _persist_portable_without_replacing(fallback, sessions=sessions)
        return fallback, {
            **report,
            "mode": "portable_projection_authority",
            "persisted": True,
            "postgresql_persisted": False,
            "campaign_id": campaign_id,
            "content_hash": digest,
            "target_entity_ids": list(target_ids),
            "database_error": type(database_error).__name__,
        }
