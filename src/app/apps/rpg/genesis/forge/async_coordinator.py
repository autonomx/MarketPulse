"""Durable asynchronous Campaign Genesis orchestration.

The HTTP path creates and saves a deterministic blocked campaign shell, then records
one PostgreSQL job. A lease-backed worker performs World Forge generation outside the
request, commits certified canon, materializes the session, and opens the first-turn
gate. Expired leases are reclaimed by the shared job repository after restart.
"""
from __future__ import annotations
from app.config.env import environment as _environment

import threading
from contextlib import nullcontext
from contextvars import copy_context
from copy import deepcopy
from typing import TYPE_CHECKING, Any, Mapping

from app.jobs.models import ResourceClass

from .contract import CampaignGenesisContract
from .materialization import (
    materialize_world_forge_into_session,
    persist_campaign_expansion,
    persist_campaign_genesis,
)
from .world_forge_commit import require_world_forge_commit_ready
from .world_forge_generation import GeneratedTopic, WorldForgeTopicGenerator
from .world_forge_pipeline import run_campaign_world_forge

if TYPE_CHECKING:
    from app.apps.rpg.genesis.contracts import CampaignSessions

CAMPAIGN_GENESIS_JOB_TYPE = "rpg.campaign_genesis.generate"
CAMPAIGN_EXPANSION_JOB_TYPE = "rpg.campaign_world.expand"
CAMPAIGN_GENESIS_RESOURCE_CLASS = ResourceClass.RPG_CAMPAIGN_GENESIS.value
CAMPAIGN_GENESIS_ASYNC_CONTRACT = "rpg_campaign_genesis_async_v1"
_DEFAULT_LEASE_SECONDS = 3600

_worker_lock = threading.Lock()
_worker_active = False
_worker_thread = None
_worker_stop = threading.Event()
_background_owner = None


def configure_campaign_genesis_owner(owner):
    global _background_owner
    from app.runtime.capabilities import RuntimeCapability
    owner.capabilities.require(RuntimeCapability.RUN_JOB_WORKERS)
    _background_owner = owner


def stop_campaign_genesis_worker(timeout=5):
    _worker_stop.set()
    thread = _worker_thread
    if thread is not None:
        thread.join(timeout)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def campaign_genesis_async_enabled(
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Use asynchronous Genesis in production while keeping deterministic CI explicit."""

    env = environ or _environment()
    configured = str(env.get("OMNIX_RPG_CAMPAIGN_GENESIS_MODE") or "").strip().casefold()
    if configured:
        return configured not in {"sync", "synchronous", "disabled", "off", "test"}
    return str(env.get("RPG_TEST_MODE") or "").strip().casefold() != "deterministic"


def campaign_genesis_sync_fallback_allowed(
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Allow portable generation unless the operator explicitly requires async."""

    env = environ or _environment()
    configured = str(env.get("OMNIX_RPG_CAMPAIGN_GENESIS_MODE") or "").strip().casefold()
    return configured not in {"async", "asynchronous", "required", "durable"}


def campaign_genesis_job_id(campaign_id: str) -> str:
    return f"rpg-genesis:{campaign_id}"


def campaign_expansion_job_id(campaign_id: str) -> str:
    return f"rpg-world-expansion:{campaign_id}"


class _BackgroundPriorityGenerator:
    def __init__(self, generator: WorldForgeTopicGenerator) -> None:
        self.generator = generator

    def generate(self, node: Any, **kwargs: Any) -> GeneratedTopic:
        from app.apps.rpg.foundation.llm_priority import background_rpg_llm_priority

        with background_rpg_llm_priority():
            return self.generator.generate(node, **kwargs)


def _progress(
    *,
    status: str,
    stage: str,
    percent: int,
    job_id: str,
    error: str = "",
) -> dict[str, Any]:
    bounded_percent = max(0, min(int(percent), 100))
    return {
        "contract_version": CAMPAIGN_GENESIS_ASYNC_CONTRACT,
        "job_id": job_id,
        "status": status,
        "stage": stage,
        "percent": bounded_percent,
        "progress": bounded_percent,
        "launch_ready": status == "ready",
        "error": error,
    }


def _write_session_progress(
    campaign_id: str,
    *,
    status: str,
    stage: str,
    percent: int,
    job_id: str,
    sessions: CampaignSessions,
    error: str = "",
) -> dict[str, Any] | None:
    session = sessions.load(campaign_id)
    if not session:
        return None
    runtime = _mapping(session.get("runtime_state"))
    manifest = _mapping(session.get("manifest"))
    snapshot = _progress(
        status=status,
        stage=stage,
        percent=percent,
        job_id=job_id,
        error=error,
    )
    runtime["campaign_generation"] = snapshot
    runtime["active_job_id"] = None if status in {"ready", "failed"} else job_id
    runtime["last_error"] = error or None
    runtime["campaign_launch_gate"] = {
        "ready": status == "ready",
        "required_before_first_turn": True,
        "missing_requirements": (
            [] if status == "ready" else [f"campaign_genesis_{stage}"]
        ),
    }
    manifest["campaign_generation_status"] = status
    manifest["creation_status"] = "completed" if status == "ready" else status
    session["runtime_state"] = runtime
    session["manifest"] = manifest
    return sessions.save(session, compact=True)


def _database(value: Any | None) -> Any:
    if value is not None:
        return value
    from app.persistence.database import default_database

    return default_database()


def enqueue_campaign_genesis(
    result: dict[str, Any],
    *,
    contract: CampaignGenesisContract,
    compiled: Mapping[str, Any],
    bootstrap: Mapping[str, Any],
    legacy: Mapping[str, Any],
    sessions: CampaignSessions,
    database: Any | None = None,
    kick_worker: bool = True,
) -> dict[str, Any]:
    """Persist a blocked shell and one durable World Forge job, then return immediately."""

    session = raw_session if isinstance(raw_session := result.get("session"), dict) else None
    campaign_id = str(result.get("session_id") or "")
    if session is None or not campaign_id:
        return {
            **result,
            "ok": False,
            "status": "failed",
            "error": "campaign_genesis_session_missing",
        }
    if not contract.world_forge.enabled:
        return result

    job_id = campaign_genesis_job_id(campaign_id)
    runtime = _mapping(session.get("runtime_state"))
    setup = _mapping(session.get("setup_payload"))
    manifest = _mapping(session.get("manifest"))
    queued = _progress(
        status="queued",
        stage="queued",
        percent=0,
        job_id=job_id,
    )
    runtime["campaign_generation"] = queued
    runtime["active_job_id"] = job_id
    runtime["campaign_launch_gate"] = {
        "ready": False,
        "required_before_first_turn": True,
        "missing_requirements": ["campaign_genesis_queued"],
    }
    setup["world_forge"] = {
        "depth": contract.world_forge.depth,
        "topic_graph": dict(
            _mapping(compiled.get("compiled_world_forge")).get("topic_graph") or {}
        ),
        "async_job_id": job_id,
        "status": "queued",
    }
    manifest["campaign_generation_status"] = "queued"
    manifest["creation_job_id"] = job_id
    manifest["creation_status"] = "queued"
    session["runtime_state"] = runtime
    session["setup_payload"] = setup
    session["manifest"] = manifest

    saved = sessions.save(session, compact=True)
    try:
        db = _database(database)
        from app.security.tenant_context import current_tenant
        from app.persistence.unit_of_work import unit_of_work

        context = current_tenant()
        with unit_of_work(db) as work:
            campaign = work.rpg.get_campaign(context, campaign_id, for_update=True)
            state = _mapping(saved.get("state"))
            if campaign is None:
                work.rpg.create_campaign(
                    context,
                    campaign_id=campaign_id,
                    title=str(state.get("title") or campaign_id),
                    state=state,
                    engine_version="rne-campaign-genesis-async-v1",
                    schema_version=str(manifest.get("schema_version") or "rpg-session-v1"),
                    seed=str(contract.world_options.seed or 0),
                    metadata={
                        "campaign_template": contract.campaign_template,
                        "world_forge_depth": contract.world_forge.depth,
                        "campaign_genesis_async": True,
                    },
                )
            existing = work.jobs.get_job(context, job_id)
            work.campaign_genesis.start(
                context,
                genesis_run_id=f"genesis:{campaign_id}:1",
                campaign_id=campaign_id,
                depth=contract.world_forge.depth,
                topic_graph=setup["world_forge"]["topic_graph"],
                progress=queued,
            )
            job = existing or work.jobs.create_job(
                context,
                {
                    "id": job_id,
                    "module": "rpg",
                    "job_type": CAMPAIGN_GENESIS_JOB_TYPE,
                    "resource_class": CAMPAIGN_GENESIS_RESOURCE_CLASS,
                    "priority": 20,
                    "max_attempts": 3,
                    "input_payload": {
                        "campaign_id": campaign_id,
                        "contract": contract.model_dump(mode="json"),
                        "compiled": dict(compiled),
                        "bootstrap": dict(bootstrap),
                        "legacy": dict(legacy),
                    },
                    "metadata": {
                        "contract_version": CAMPAIGN_GENESIS_ASYNC_CONTRACT,
                        "campaign_id": campaign_id,
                        "world_forge_depth": contract.world_forge.depth,
                    },
                },
            )
            work.commit()
    except Exception as exc:
        failed = _write_session_progress(
            campaign_id,
            status="failed",
            stage="enqueue_failed",
            percent=0,
            job_id=job_id,
            sessions=sessions,
            error=str(exc),
        )
        failed_progress = _mapping(_mapping(failed or saved).get("runtime_state")).get(
            "campaign_generation"
        )
        if not isinstance(failed_progress, Mapping):
            failed_progress = _progress(
                status="failed",
                stage="enqueue_failed",
                percent=0,
                job_id=job_id,
                error=str(exc),
            )
        return {
            **result,
            "ok": False,
            "status": "failed",
            "session": failed or saved,
            "error": "campaign_genesis_enqueue_failed",
            "detail": str(exc),
            "creation_job": dict(failed_progress),
            "creation_progress": dict(failed_progress),
        }

    response = {
        **result,
        "ok": True,
        "status": "generating_world",
        "session": saved,
        "game": saved.get("state", {}),
        "creation_job": {**queued, "id": job["id"], "type": job["job_type"]},
        "creation_progress": queued,
    }
    if kick_worker:
        kick_campaign_genesis_worker(sessions=sessions, database=db)
    return response


def _enqueue_campaign_expansion(
    work: Any,
    context: Any,
    *,
    campaign_id: str,
    contract: CampaignGenesisContract,
    compiled: Mapping[str, Any],
    launch_topics: tuple[GeneratedTopic, ...],
) -> dict[str, Any]:
    job_id = campaign_expansion_job_id(campaign_id)
    existing = work.jobs.get_job(context, job_id)
    return existing or work.jobs.create_job(
        context,
        {
            "id": job_id,
            "module": "rpg",
            "job_type": CAMPAIGN_EXPANSION_JOB_TYPE,
            "resource_class": CAMPAIGN_GENESIS_RESOURCE_CLASS,
            "priority": -20,
            "max_attempts": 2,
            "input_payload": {
                "campaign_id": campaign_id,
                "contract": contract.model_dump(mode="json"),
                "compiled": dict(compiled),
                "launch_topics": [topic.as_dict() for topic in launch_topics],
            },
            "metadata": {
                "contract_version": CAMPAIGN_GENESIS_ASYNC_CONTRACT,
                "campaign_id": campaign_id,
                "execution_tier": "background_expansion",
                "foreground_preemptible": True,
            },
        },
    )


def _write_expansion_progress(
    campaign_id: str,
    *,
    status: str,
    job_id: str,
    sessions: CampaignSessions,
    error: str = "",
) -> dict[str, Any] | None:
    session = sessions.load(campaign_id)
    if not session:
        return None
    runtime = _mapping(session.get("runtime_state"))
    runtime["campaign_expansion"] = {
        "job_id": job_id,
        "status": status,
        "stage": "background_world_forge",
        "foreground_preemptible": True,
        "error": error,
    }
    session["runtime_state"] = runtime
    return sessions.save(session, compact=True)


def _preserve_live_progress_during_expansion(
    before: Mapping[str, Any],
    materialized: dict[str, Any],
) -> None:
    """Keep discoveries and thread progress made while lore was generating."""

    before_projection = _mapping(before.get("campaign_bible_projection"))
    before_discovery = _mapping(before_projection.get("discovery_state"))
    projection = _mapping(materialized.get("campaign_bible_projection"))
    expanded_discovery = _mapping(projection.get("discovery_state"))
    for key in ("pages", "entities"):
        expanded_discovery[key] = {
            **_mapping(expanded_discovery.get(key)),
            **_mapping(before_discovery.get(key)),
        }
    discoveries: list[Any] = []
    seen_discoveries: set[str] = set()
    for item in [
        *(expanded_discovery.get("discoveries") or ()),
        *(before_discovery.get("discoveries") or ()),
    ]:
        marker = repr(item)
        if marker in seen_discoveries:
            continue
        seen_discoveries.add(marker)
        discoveries.append(item)
    expanded_discovery["discoveries"] = discoveries
    projection["discovery_state"] = expanded_discovery
    materialized["campaign_bible_projection"] = projection

    state = _mapping(materialized.get("state"))
    bible = _mapping(state.get("campaign_bible"))
    bible["discovery_state"] = expanded_discovery
    state["campaign_bible"] = bible
    before_state = _mapping(before.get("state"))
    previous_threads = {
        str(row.get("id") or ""): dict(row)
        for row in before_state.get("opening_story_threads") or ()
        if isinstance(row, Mapping) and row.get("id")
    }
    merged_threads: list[dict[str, Any]] = []
    for row in state.get("opening_story_threads") or ():
        if not isinstance(row, Mapping):
            continue
        merged = dict(row)
        previous = previous_threads.get(str(merged.get("id") or ""), {})
        for key in ("status", "progress", "resolved", "completed_at"):
            if key in previous:
                merged[key] = previous[key]
        merged_threads.append(merged)
    state["opening_story_threads"] = merged_threads
    materialized["state"] = state


def _run_campaign_expansion_job(
    *,
    db: Any,
    context: Any,
    job: Mapping[str, Any],
    payload: Mapping[str, Any],
    generator: WorldForgeTopicGenerator | None,
    worker_id: str,
    sessions: CampaignSessions,
) -> dict[str, Any]:
    campaign_id = str(payload.get("campaign_id") or "")
    job_id = str(job.get("id") or "")
    _write_expansion_progress(
        campaign_id,
        status="running",
        job_id=job_id,
        sessions=sessions,
    )
    try:
        contract = CampaignGenesisContract.model_validate(payload.get("contract") or {})
        launch_topics = {
            topic.topic_id: topic
            for row in payload.get("launch_topics") or ()
            if isinstance(row, Mapping)
            for topic in (GeneratedTopic.from_dict(row),)
            if topic.topic_id
        }
        selected_generator = generator
        if selected_generator is None:
            from app.apps.rpg.genesis.worlds.providers.world_forge import (
                build_production_world_forge_generator,
            )

            selected_generator = build_production_world_forge_generator()
        world_forge = run_campaign_world_forge(
            contract,
            campaign_id=campaign_id,
            compiled_genesis=_mapping(payload.get("compiled")),
            generator=_BackgroundPriorityGenerator(selected_generator),
            existing_topics=launch_topics,
            canon_revision=2,
        )
        certification = require_world_forge_commit_ready(world_forge)
        from app.apps.rpg.foundation.llm_priority import background_rpg_llm_priority

        with background_rpg_llm_priority():
            session = sessions.load(campaign_id)
            if not session:
                raise RuntimeError(f"campaign is missing during expansion: {campaign_id}")
            live_session = deepcopy(session)
            materialized = materialize_world_forge_into_session(
                session,
                contract,
                world_forge,
            )
            _preserve_live_progress_during_expansion(live_session, materialized)
            runtime = _mapping(materialized.get("runtime_state"))
            runtime["campaign_expansion"] = {
                "job_id": job_id,
                "status": "completed",
                "stage": "background_world_forge",
                "foreground_preemptible": True,
                "canon_revision": 2,
                "content_hash": certification.content_hash,
                "error": "",
            }
            materialized["runtime_state"] = runtime
            persistence = persist_campaign_expansion(
                materialized,
                contract,
                world_forge,
                database=db,
            )
            saved = sessions.save(materialized, compact=True)
        from app.persistence.unit_of_work import unit_of_work

        with unit_of_work(db) as work:
            completed = work.jobs.complete(
                context,
                job_id=job_id,
                worker_id=worker_id,
                lease_token=str(job["lease_token"]),
                output_refs=[
                    {
                        "campaign_id": campaign_id,
                        "campaign_bible_content_hash": certification.content_hash,
                        "canon_revision": 2,
                    }
                ],
                progress={
                    "status": "completed",
                    "stage": "background_world_forge",
                    "percent": 100,
                },
            )
            work.commit()
        return {
            "ok": True,
            "status": "completed",
            "campaign_id": campaign_id,
            "job": completed,
            "session": saved,
            "persistence": persistence,
        }
    except Exception as exc:
        from app.persistence.unit_of_work import unit_of_work

        with unit_of_work(db) as work:
            failed = work.jobs.fail(
                context,
                job_id=job_id,
                worker_id=worker_id,
                lease_token=str(job["lease_token"]),
                error={"code": "campaign_expansion_failed", "message": str(exc)},
                retry_delay_seconds=1,
            )
            work.commit()
        _write_expansion_progress(
            campaign_id,
            status=str(failed["status"]),
            job_id=job_id,
            sessions=sessions,
            error=str(exc),
        )
        return {
            "ok": False,
            "status": failed["status"],
            "campaign_id": campaign_id,
            "job": failed,
            "error": "campaign_expansion_failed",
            "detail": str(exc),
        }


def run_campaign_genesis_worker_once(
    *,
    sessions: CampaignSessions,
    worker_id: str = "rpg-genesis:local",
    database: Any | None = None,
    generator: WorldForgeTopicGenerator | None = None,
) -> dict[str, Any] | None:
    """Claim and execute one durable Genesis job. Returns ``None`` when idle."""

    db = _database(database)
    from app.security.tenant_context import current_tenant
    from app.persistence.unit_of_work import unit_of_work

    context = current_tenant()
    with unit_of_work(db) as work:
        job = work.jobs.claim_next(
            context,
            worker_id=worker_id,
            resource_classes=[CAMPAIGN_GENESIS_RESOURCE_CLASS],
            lease_seconds=_DEFAULT_LEASE_SECONDS,
        )
        if job is None:
            work.rollback()
            return None
        job = work.jobs.mark_running(
            context,
            job_id=job["id"],
            worker_id=worker_id,
            lease_token=str(job["lease_token"]),
        )
        payload = _mapping(job.get("input_payload"))
        campaign_id = str(payload.get("campaign_id") or "")
        is_expansion = (
            str(job.get("job_type") or job.get("type") or "")
            == CAMPAIGN_EXPANSION_JOB_TYPE
        )
        if not is_expansion:
            work.campaign_genesis.update(
                context,
                campaign_id=campaign_id,
                status="generating",
                progress=_progress(
                    status="running",
                    stage="world_forge",
                    percent=10,
                    job_id=job["id"],
                ),
                error={},
            )
        work.commit()

    if is_expansion:
        return _run_campaign_expansion_job(
            db=db,
            context=context,
            job=job,
            payload=payload,
            generator=generator,
            worker_id=worker_id,
            sessions=sessions,
        )
    _write_session_progress(
        campaign_id,
        status="running",
        stage="world_forge",
        percent=10,
        job_id=job["id"],
        sessions=sessions,
    )
    try:
        contract = CampaignGenesisContract.model_validate(payload.get("contract") or {})
        world_forge = run_campaign_world_forge(
            contract,
            campaign_id=campaign_id,
            compiled_genesis=_mapping(payload.get("compiled")),
            generator=generator,
            launch_only=True,
        )
        certification = require_world_forge_commit_ready(world_forge)
        session = sessions.load(campaign_id)
        if not session:
            raise RuntimeError(f"campaign shell is missing: {campaign_id}")
        materialized = materialize_world_forge_into_session(session, contract, world_forge)
        persistence = persist_campaign_genesis(
            materialized,
            contract,
            world_forge,
            database=db,
            required=True,
            genesis_run_started=True,
        )
        expansion_id = campaign_expansion_job_id(campaign_id)
        runtime = _mapping(materialized.get("runtime_state"))
        runtime["campaign_expansion"] = {
            "job_id": expansion_id,
            "status": "queued",
            "stage": "background_world_forge",
            "foreground_preemptible": True,
            "error": "",
        }
        materialized["runtime_state"] = runtime
        saved = sessions.save(materialized, compact=True)
        with unit_of_work(db) as work:
            completed = work.jobs.complete(
                context,
                job_id=job["id"],
                worker_id=worker_id,
                lease_token=str(job["lease_token"]),
                output_refs=[
                    {
                        "campaign_id": campaign_id,
                        "campaign_bible_content_hash": certification.content_hash,
                    }
                ],
                progress=_progress(
                    status="ready",
                    stage="launch_ready",
                    percent=100,
                    job_id=job["id"],
                ),
            )
            work.commit()
        try:
            with unit_of_work(db) as work:
                expansion = _enqueue_campaign_expansion(
                    work,
                    context,
                    campaign_id=campaign_id,
                    contract=contract,
                    compiled=_mapping(payload.get("compiled")),
                    launch_topics=world_forge.generation.topics,
                )
                work.commit()
        except Exception as expansion_error:
            expansion = {
                "id": expansion_id,
                "status": "failed",
                "error": str(expansion_error),
            }
            saved = _write_expansion_progress(
                campaign_id,
                status="failed",
                job_id=expansion_id,
                sessions=sessions,
                error=str(expansion_error),
            ) or saved
        return {
            "ok": True,
            "status": "ready",
            "campaign_id": campaign_id,
            "job": completed,
            "session": saved,
            "persistence": persistence,
            "commit_certification": certification.as_dict(),
            "expansion_job": expansion,
        }
    except Exception as exc:
        with unit_of_work(db) as work:
            failed = work.jobs.fail(
                context,
                job_id=job["id"],
                worker_id=worker_id,
                lease_token=str(job["lease_token"]),
                error={"code": "campaign_genesis_failed", "message": str(exc)},
                retry_delay_seconds=1,
            )
            retrying = failed["status"] == "retrying"
            work.campaign_genesis.update(
                context,
                campaign_id=campaign_id,
                status="generating" if retrying else "failed",
                progress=_progress(
                    status="retrying" if retrying else "failed",
                    stage="retry_wait" if retrying else "failed",
                    percent=10,
                    job_id=job["id"],
                    error=str(exc),
                ),
                error={"code": "campaign_genesis_failed", "message": str(exc)},
            )
            work.commit()
        _write_session_progress(
            campaign_id,
            status="retrying" if retrying else "failed",
            stage="retry_wait" if retrying else "failed",
            percent=10,
            job_id=job["id"],
            sessions=sessions,
            error=str(exc),
        )
        return {
            "ok": False,
            "status": failed["status"],
            "campaign_id": campaign_id,
            "job": failed,
            "error": "campaign_genesis_failed",
            "detail": str(exc),
        }


def _worker_loop(database: Any | None, sessions: CampaignSessions) -> None:
    global _worker_active
    try:
        while not _worker_stop.is_set():
            if _background_owner is not None:
                _background_owner.require_live()
            result = run_campaign_genesis_worker_once(sessions=sessions, database=database)
            if result is None:
                break
            if result.get("status") == "retrying":
                # fail() makes the retry eligible one second later. Keep this
                # process-local recovery worker alive until that durable retry
                # can be claimed; otherwise polling /api/jobs cannot re-kick it.
                _worker_stop.wait(1.05)
    finally:
        with _worker_lock:
            _worker_active = False


def kick_campaign_genesis_worker(*, sessions: CampaignSessions, database: Any | None = None) -> bool:
    """Start one process-local recovery worker without creating duplicate consumers."""

    global _worker_active, _worker_thread
    from app.runtime.config import get_runtime_config
    if not get_runtime_config().runs_job_workers:
        return False
    if not campaign_genesis_async_enabled():
        return False
    with _worker_lock:
        if _worker_active:
            return False
        _worker_active = True
        _worker_stop.clear()
    from app.persistence.background_authority import background_execution
    owner = _background_owner
    context = copy_context()

    def run():
        global _worker_active
        from app.runtime.background import BackgroundOwnershipUnavailable
        try:
            with background_execution(owner) if owner is not None else nullcontext():
                _worker_loop(database, sessions)
        except BackgroundOwnershipUnavailable:
            return
        finally:
            with _worker_lock:
                _worker_active = False

    thread = threading.Thread(
        target=context.run,
        args=(run,),
        name="omnix-rpg-campaign-genesis",
        daemon=True,
    )
    _worker_thread = thread
    thread.start()
    return True
