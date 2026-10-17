"""RPG feature declaration."""
from __future__ import annotations

from app.jobs.handlers import JobExecutionContext, JobHandlerSpec
from app.jobs.models import CreateJobRequest, ResourceClass
from app.settings.effective_defaults import apply_job_defaults
from app.runtime.background import BackgroundWorker
from app.runtime.capabilities import RuntimeCapability
from app.providers.facade import PROVIDER_CATALOGS
from app.runtime.features import FeatureModule
from app.runtime.ports import ContributionSpec

from app.apps.rpg.session.persistence.feature_repositories import RPG_REPOSITORY_SPECS
from app.apps.rpg.edge.api.compat_router import create_rpg_compatibility_router
from app.apps.rpg.edge.api.feature_routes import create_rpg_routes_router
from app.apps.rpg.edge.api.feature_routes.rpg_campaign_lore_routes import _kick_genesis_recovery

from app.apps.rpg.session.jobs.turn_job_guard import rpg_turn_submission_policy
from app.apps.rpg.session.jobs.handlers import (
    RpgNarrationJobInput,
    RpgReportJobInput,
    RpgTurnJobInput,
    execute_rpg_narration_job,
    execute_rpg_report_job,
    execute_rpg_turn_job,
)
from app.apps.rpg.session.jobs.debug_observer import rpg_debug_job_observer


def _turn(context: JobExecutionContext, job):
    return execute_rpg_turn_job(context.job_store, job)


def _report(context: JobExecutionContext, job):
    return execute_rpg_report_job(context.job_store, job)


def _narration(context: JobExecutionContext, job):
    return execute_rpg_narration_job(context.job_store, job)


def _rpg_submission_policy(request: CreateJobRequest) -> CreateJobRequest:
    routed = CreateJobRequest.model_validate(
        apply_job_defaults(request.model_dump(mode="python"))
    )
    return rpg_turn_submission_policy(routed)


def _compatibility_router(_context):
    return create_rpg_compatibility_router()


def _rpg_routes_router(context):
    return create_rpg_routes_router(context)


def _campaign_genesis_worker(context):
    async def recover() -> None:
        from app.apps.rpg.genesis.forge.async_coordinator import configure_campaign_genesis_owner

        owner = getattr(context.runtime_state, "background_runtime", None)
        if owner is not None:
            configure_campaign_genesis_owner(owner)
        _kick_genesis_recovery()

    async def stop() -> None:
        import asyncio

        from app.apps.rpg.genesis.forge.async_coordinator import stop_campaign_genesis_worker

        await asyncio.to_thread(stop_campaign_genesis_worker)

    return BackgroundWorker(
        name="rpg_campaign_genesis",
        monitor=object(),
        startup=(recover,),
        shutdown=(stop,),
        requires=frozenset({RuntimeCapability.RUN_JOB_WORKERS}),
    )

class _VisualProviderCatalog:
    family = "rpg_visual"

    def list_providers(self):
        from app.apps.rpg.narration.visual.providers.registry import list_visual_provider_options

        return list_visual_provider_options()


FEATURE = FeatureModule(
    id="rpg",
    title="RPG",
    tier="app",
    # The Hermes routes in app/apps/rpg/edge/hermes read assist mode through app.platform.chat.contracts.
    depends_on=("chat",),
    # World images use image providers; RPG runs without the image feature.
    uses=("image",),
    routers=(_rpg_routes_router, _compatibility_router),
    repositories=RPG_REPOSITORY_SPECS,
    job_observers=(rpg_debug_job_observer,),
    background_workers=(_campaign_genesis_worker,),
    job_handlers=(
        JobHandlerSpec(
            type="rpg.turn",
            handler=_turn,
            input_model=RpgTurnJobInput,
            resource_class=ResourceClass.GPU_LLM,
            timeout_seconds=900,
            submission_policy=_rpg_submission_policy,
        ),
        JobHandlerSpec(
            # Deferred narration (WP-5.6); the jobs system owns its claims and retries.
            type="rpg.narration",
            handler=_narration,
            input_model=RpgNarrationJobInput,
            resource_class=ResourceClass.GPU_LLM,
            timeout_seconds=300,
            max_attempts=3,
        ),
        JobHandlerSpec(
            type="rpg.report.last10",
            handler=_report,
            input_model=RpgReportJobInput,
            resource_class=ResourceClass.CPU,
            timeout_seconds=120,
        ),
    ),
    contributions=(ContributionSpec(PROVIDER_CATALOGS, lambda _context: _VisualProviderCatalog()),),
)
