"""Image FeatureModule declaration."""
from __future__ import annotations

from app.jobs.handlers import JobExecutionContext, JobHandlerSpec
from app.jobs.models import CreateJobRequest, ResourceClass
from app.settings.effective_defaults import apply_job_defaults
from app.assets.store import LEGACY_IMAGE_MANIFEST
from app.providers.facade import PROVIDER_CATALOGS
from app.runtime.features import FeatureContext, FeatureModule
from app.runtime.ports import ContributionSpec

from .jobs import execute_image_job
from .contracts import ImageGenerateInput
from .routes.assets import create_image_asset_file_router
from .routes.models import create_image_model_router
from .routes.references import create_image_reference_router
from .routes.workspace import create_image_workspace_router


def _execute(context: JobExecutionContext, job):
    return execute_image_job(context.job_store, job)


def _submission_defaults(request: CreateJobRequest) -> CreateJobRequest:
    return CreateJobRequest.model_validate(
        apply_job_defaults(request.model_dump(mode="python"))
    )


def _model_router(_context: FeatureContext):
    return create_image_model_router()


def _asset_file_router(context: FeatureContext):
    assets = context.services.assets
    if assets is None:
        raise RuntimeError("image feature requires the asset service")
    return create_image_asset_file_router(assets)


def _reference_router(context: FeatureContext):
    assets = context.services.assets
    if assets is None:
        raise RuntimeError("image feature requires the asset service")
    return create_image_reference_router(assets)


def _workspace_router(context: FeatureContext):
    assets = context.services.assets
    jobs = context.services.jobs
    if assets is None or jobs is None:
        raise RuntimeError("image feature requires asset and job services")
    return create_image_workspace_router(jobs, assets)


class _ImageProviderCatalog:
    family = "image"

    def list_providers(self):
        from .providers.registry import list_image_providers

        return list_image_providers()


def _legacy_image_manifest():
    from .asset_store import get_image_asset_manifest

    return get_image_asset_manifest()


FEATURE = FeatureModule(
    id="image",
    title="Images",
    tier="platform",
    routers=(_model_router, _asset_file_router, _reference_router, _workspace_router),
    job_handlers=(
        JobHandlerSpec(
            type="image.generate",
            handler=_execute,
            input_model=ImageGenerateInput,
            resource_class=ResourceClass.GPU_IMAGE,
            timeout_seconds=600,
            max_attempts=3,
            submission_policy=_submission_defaults,
        ),
    ),
    contributions=(
        ContributionSpec(PROVIDER_CATALOGS, lambda _context: _ImageProviderCatalog()),
        ContributionSpec(LEGACY_IMAGE_MANIFEST, lambda _context: _legacy_image_manifest),
    ),
)
