"""Story and podcast feature declaration."""
from __future__ import annotations

from fastapi import APIRouter

from app.jobs.handlers import JobExecutionContext, JobHandlerSpec
from app.jobs.models import CreateJobRequest, ResourceClass
from app.settings.effective_defaults import apply_job_defaults
from app.runtime.features import FeatureContext, FeatureModule

from .jobs import PodcastGenerateInput, StoryGenerateInput, execute_story_job
from .asset_save import (
    SaveStoryAssetRequest,
    SavedStoryAssetResponse,
    save_story_asset,
)


def _story_asset_router(context: FeatureContext) -> APIRouter:
    router = APIRouter()
    assets = context.services.assets
    if assets is None:
        raise RuntimeError("story feature requires the asset service")

    @router.post("/api/assets/story", response_model=SavedStoryAssetResponse)
    def save_story_asset_endpoint(
        request: SaveStoryAssetRequest,
    ) -> SavedStoryAssetResponse:
        return save_story_asset(assets, request)

    return router


def _execute(context: JobExecutionContext, job):
    return execute_story_job(context.job_store, job)


def _submission_defaults(request: CreateJobRequest) -> CreateJobRequest:
    return CreateJobRequest.model_validate(
        apply_job_defaults(request.model_dump(mode="python"))
    )


FEATURE = FeatureModule(
    id="story",
    title="Storyteller and Podcast",
    tier="app",
    routers=(_story_asset_router,),
    job_handlers=(
        JobHandlerSpec(
            type="story.generate",
            handler=_execute,
            input_model=StoryGenerateInput,
            resource_class=ResourceClass.GPU_LLM,
            timeout_seconds=900,
            submission_policy=_submission_defaults,
        ),
        JobHandlerSpec(
            type="podcast.generate",
            handler=_execute,
            input_model=PodcastGenerateInput,
            resource_class=ResourceClass.GPU_LLM,
            timeout_seconds=900,
            submission_policy=_submission_defaults,
        ),
    ),
)
