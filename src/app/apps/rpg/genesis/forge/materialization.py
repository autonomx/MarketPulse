"""Materialize approved Campaign Genesis into PostgreSQL and playable session state."""
from __future__ import annotations
from app.config.env import env_str as _env_str

from typing import Any, Mapping

from app.apps.rpg.genesis.forge.contract import CampaignGenesisContract
from app.apps.rpg.genesis.forge.world_forge_commit import (
    require_world_forge_commit_ready,
)
from app.apps.rpg.genesis.forge.world_forge_pipeline import CampaignWorldForgeResult


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _progress_payload(result: CampaignWorldForgeResult) -> dict[str, Any]:
    jobs = [job.as_dict() for job in result.generation.jobs]
    completed = sum(1 for job in jobs if job.get("status") == "completed")
    total = len(jobs)
    return {
        "status": "ready" if result.launch_ready else "failed",
        "stage": "launch_ready" if result.launch_ready else "consistency_failed",
        "completed_jobs": completed,
        "total_jobs": total,
        "percent": 100 if total == 0 else round(completed / total * 100),
        "launch_ready": result.launch_ready,
        "failed_topic_ids": list(result.generation.failed_topic_ids),
        "missing_requirements": list(result.compilation.missing_requirements),
    }


def materialize_world_forge_into_session(
    session: dict[str, Any],
    contract: CampaignGenesisContract,
    world_forge: CampaignWorldForgeResult,
) -> dict[str, Any]:
    """Attach portable projections only after generated canon passes certification."""

    certification = require_world_forge_commit_ready(world_forge)
    bible = dict(world_forge.compilation.document)
    entities = _mapping(bible.get("entities"))
    state = _mapping(session.get("state"))
    runtime = _mapping(session.get("runtime_state"))
    setup = _mapping(session.get("setup_payload"))
    manifest = _mapping(session.get("manifest"))
    player_lore = {
        document_id: document
        for document_id, document in (
            (str(row.get("document_id") or ""), dict(row))
            for row in bible.get("documents") or ()
            if isinstance(row, Mapping)
        )
        if document_id
        and str(document.get("visibility") or "")
        in {"public", "player_known", "learned", "partially_known", "disputed"}
    }
    npc_dossiers = {
        entity_id: dict(entity)
        for entity_id, entity in entities.items()
        if isinstance(entity, Mapping) and entity.get("kind") == "npc"
    }
    location_dossiers = {
        entity_id: dict(entity)
        for entity_id, entity in entities.items()
        if isinstance(entity, Mapping) and entity.get("kind") == "location"
    }
    faction_dossiers = {
        entity_id: dict(entity)
        for entity_id, entity in entities.items()
        if isinstance(entity, Mapping) and entity.get("kind") == "faction"
    }
    state["campaign_bible"] = {
        "schema_version": bible.get("schema_version"),
        "canon_revision": bible.get("canon_revision"),
        "content_hash": certification.content_hash,
        "manifest": dict(bible.get("manifest") or {}),
        "completeness": dict(bible.get("completeness") or {}),
        "discovery_state": dict(bible.get("discovery_state") or {}),
        "lore_pages": player_lore,
        "commit_certification": certification.as_dict(),
    }
    state["npc_dossiers"] = npc_dossiers
    state["location_dossiers"] = location_dossiers
    state["faction_dossiers"] = faction_dossiers
    state["opening_story_threads"] = list(bible.get("story_threads") or ())
    runtime["campaign_generation"] = _progress_payload(world_forge)
    runtime["active_job_id"] = None
    runtime["last_error"] = None
    runtime["campaign_bible_revision"] = bible.get("canon_revision")
    runtime["campaign_bible_content_hash"] = certification.content_hash
    runtime["campaign_bible_commit_certification"] = certification.as_dict()
    runtime["campaign_launch_gate"] = {
        "ready": True,
        "required_before_first_turn": True,
        "missing_requirements": [],
        "content_hash": certification.content_hash,
    }
    setup["world_forge"] = {
        "depth": contract.world_forge.depth,
        "topic_graph": world_forge.graph.as_dict(),
        "generation_jobs": [job.as_dict() for job in world_forge.generation.jobs],
        "audit": world_forge.audit.as_dict(),
        "commit_certification": certification.as_dict(),
        "compilation": {
            "launch_ready": True,
            "completeness": dict(world_forge.compilation.completeness),
            "content_hash": certification.content_hash,
        },
    }
    manifest["campaign_bible_revision"] = bible.get("canon_revision")
    manifest["campaign_bible_content_hash"] = certification.content_hash
    manifest["campaign_generation_status"] = "ready"
    manifest["creation_status"] = "completed"
    manifest["campaign_bible_commit_certified"] = True
    session["state"] = state
    session["runtime_state"] = runtime
    session["setup_payload"] = setup
    session["manifest"] = manifest
    session["campaign_bible_projection"] = {
        "documents": list(bible.get("documents") or ()),
        "entities": dict(bible.get("entities") or {}),
        "facts": list(bible.get("facts") or ()),
        "retrieval_cards": list(bible.get("retrieval_cards") or ()),
        "relationships": list(bible.get("relationships") or ()),
        "knowledge_rules": list(bible.get("knowledge_rules") or ()),
        "story_threads": list(bible.get("story_threads") or ()),
        "indexes": dict(bible.get("indexes") or {}),
        "discovery_state": dict(bible.get("discovery_state") or {}),
        "content_hash": certification.content_hash,
        "canon_revision": bible.get("canon_revision"),
        "commit_certification": certification.as_dict(),
    }
    return session


def persist_campaign_genesis(
    session: Mapping[str, Any],
    contract: CampaignGenesisContract,
    world_forge: CampaignWorldForgeResult,
    *,
    database: Any | None = None,
    required: bool | None = None,
    genesis_run_started: bool = False,
) -> dict[str, Any]:
    """Commit certified canon idempotently in one PostgreSQL transaction.

    ``genesis_run_started`` is used by the asynchronous coordinator after it has
    already persisted the queued/running lifecycle. Replaying the same completed
    job reuses an identical Campaign Bible revision; different canon is rejected.
    """

    required = (
        _env_str("OMNIX_REQUIRE_POSTGRESQL_GENESIS", "").strip().casefold()
        in {"1", "true", "yes", "on"}
        if required is None
        else required
    )
    manifest = _mapping(session.get("manifest"))
    state = _mapping(session.get("state"))
    campaign_id = str(manifest.get("session_id") or manifest.get("id") or "")
    bible = dict(world_forge.compilation.document)
    if not campaign_id:
        raise ValueError("campaign genesis persistence requires a session id")
    certification = require_world_forge_commit_ready(world_forge)
    try:
        from app.security.tenant_context import current_tenant
        from app.apps.rpg.foundation.persistence.rpg_campaign_bible_repository import (
            CampaignBibleRevisionConflict,
            campaign_bible_hash,
        )
        from app.apps.rpg.foundation.persistence.rpg_repository import canonical_json, state_hash
        from app.persistence.unit_of_work import unit_of_work

        context = current_tenant()
        with unit_of_work(database) as work:
            campaign = work.rpg.get_campaign(context, campaign_id, for_update=True)
            if campaign is None:
                campaign = work.rpg.create_campaign(
                    context,
                    campaign_id=campaign_id,
                    title=str(state.get("title") or manifest.get("title") or campaign_id),
                    state=state,
                    engine_version="rne-campaign-genesis-v1",
                    schema_version=str(
                        manifest.get("schema_version") or "rpg-session-v1"
                    ),
                    seed=str(contract.world_options.seed or 0),
                    metadata={
                        "campaign_template": contract.campaign_template,
                        "world_forge_depth": contract.world_forge.depth,
                        "campaign_bible_content_hash": certification.content_hash,
                        "campaign_bible_commit_certified": True,
                    },
                )
            else:
                digest = state_hash(state)
                updated = work.connection.execute(
                    """
                    UPDATE omnix_rpg_campaigns
                       SET state_jsonb = %s::jsonb,
                           state_hash = %s,
                           engine_version = %s,
                           metadata = metadata || %s::jsonb,
                           updated_at = CURRENT_TIMESTAMP
                     WHERE id = %s AND workspace_id = %s AND revision = %s
                    RETURNING id
                    """,
                    (
                        canonical_json(state),
                        digest,
                        "rne-campaign-genesis-v1",
                        canonical_json(
                            {
                                "campaign_bible_content_hash": certification.content_hash,
                                "campaign_bible_commit_certified": True,
                            }
                        ),
                        campaign_id,
                        context.workspace_id,
                        campaign["revision"],
                    ),
                ).fetchone()
                if updated is None:
                    raise RuntimeError(
                        f"campaign changed while materializing genesis: {campaign_id}"
                    )
            progress = _progress_payload(world_forge)
            materializing_progress = {
                **progress,
                "status": "materializing",
                "stage": "materializing",
                "commit_certification": certification.as_dict(),
            }
            if genesis_run_started:
                work.campaign_genesis.update(
                    context,
                    campaign_id=campaign_id,
                    status="running",
                    progress=materializing_progress,
                    audit=world_forge.audit.as_dict(),
                    error={},
                )
            else:
                work.campaign_genesis.start(
                    context,
                    genesis_run_id=f"genesis:{campaign_id}:1",
                    campaign_id=campaign_id,
                    depth=contract.world_forge.depth,
                    topic_graph=world_forge.graph.as_dict(),
                    progress=materializing_progress,
                )
            candidate_hash = campaign_bible_hash(bible)
            stored_bible = work.campaign_bibles.get(
                context,
                campaign_id,
                for_update=True,
            )
            if stored_bible is not None:
                if str(stored_bible["content_hash"]) != candidate_hash:
                    raise CampaignBibleRevisionConflict(
                        "campaign genesis retry produced different canon for "
                        f"{campaign_id}"
                    )
            else:
                stored_bible = work.campaign_bibles.put(
                    context,
                    campaign_id=campaign_id,
                    document=bible,
                    expected_revision=0,
                    provenance={
                        "source": "campaign_genesis_world_forge",
                        "contract_version": contract.contract_version,
                        "depth": contract.world_forge.depth,
                        "compiled_content_hash": certification.content_hash,
                        "commit_certification": certification.as_dict(),
                    },
                    consistency_report=world_forge.audit.as_dict(),
                    completeness=world_forge.compilation.completeness,
                )
            genesis = work.campaign_genesis.update(
                context,
                campaign_id=campaign_id,
                status="ready",
                jobs=[job.as_dict() for job in world_forge.generation.jobs],
                progress={
                    **progress,
                    "commit_certification": certification.as_dict(),
                },
                audit=world_forge.audit.as_dict(),
                bible_revision=int(stored_bible["revision"]),
                bible_content_hash=str(stored_bible["content_hash"]),
                error={},
            )
            work.commit()
        return {
            "persisted": True,
            "mode": "postgresql_authority",
            "campaign_id": campaign_id,
            "campaign_bible": stored_bible,
            "genesis": genesis,
            "compiled_content_hash": certification.content_hash,
            "commit_certification": certification.as_dict(),
        }
    except Exception as exc:
        if required:
            raise
        return {
            "persisted": False,
            "mode": "portable_projection_only",
            "campaign_id": campaign_id,
            "compiled_content_hash": certification.content_hash,
            "commit_certification": certification.as_dict(),
            "error": type(exc).__name__,
            "detail": str(exc),
        }


def persist_campaign_expansion(
    session: Mapping[str, Any],
    contract: CampaignGenesisContract,
    world_forge: CampaignWorldForgeResult,
    *,
    database: Any,
) -> dict[str, Any]:
    """Append certified expanded canon without replacing live player state."""

    manifest = _mapping(session.get("manifest"))
    campaign_id = str(manifest.get("session_id") or manifest.get("id") or "")
    if not campaign_id:
        raise ValueError("campaign expansion persistence requires a session id")
    certification = require_world_forge_commit_ready(world_forge)
    bible = dict(world_forge.compilation.document)

    from app.security.tenant_context import current_tenant
    from app.apps.rpg.foundation.persistence.rpg_repository import canonical_json
    from app.persistence.unit_of_work import unit_of_work

    context = current_tenant()
    with unit_of_work(database) as work:
        campaign = work.rpg.get_campaign(context, campaign_id, for_update=True)
        if campaign is None:
            raise RuntimeError(f"campaign is missing during expansion: {campaign_id}")
        current = work.campaign_bibles.get(context, campaign_id, for_update=True)
        if current is None:
            raise RuntimeError(
                f"launch canon is missing during expansion: {campaign_id}"
            )
        stored = work.campaign_bibles.put(
            context,
            campaign_id=campaign_id,
            document=bible,
            expected_revision=int(current["revision"]),
            provenance={
                "source": "campaign_genesis_background_expansion",
                "contract_version": contract.contract_version,
                "depth": contract.world_forge.depth,
                "compiled_content_hash": certification.content_hash,
                "foreground_preemptible": True,
            },
            consistency_report=world_forge.audit.as_dict(),
            completeness=world_forge.compilation.completeness,
        )
        work.connection.execute(
            """
            UPDATE omnix_rpg_campaigns
               SET metadata = metadata || %s::jsonb,
                   updated_at = CURRENT_TIMESTAMP
             WHERE workspace_id = %s AND id = %s
            """,
            (
                canonical_json(
                    {
                        "campaign_bible_content_hash": certification.content_hash,
                        "campaign_bible_revision": int(stored["revision"]),
                        "campaign_expansion_status": "completed",
                    }
                ),
                context.workspace_id,
                campaign_id,
            ),
        )
        work.commit()
    return {
        "persisted": True,
        "mode": "postgresql_background_revision",
        "campaign_id": campaign_id,
        "revision": int(stored["revision"]),
        "content_hash": str(stored["content_hash"]),
    }
