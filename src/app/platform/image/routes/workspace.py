"""Bounded browser projections and actions for the Image Generation workspace."""
from __future__ import annotations

import logging
from itertools import islice
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.assets.content import asset_available
from app.assets import AssetListResponse, AssetRecord, AssetType, PublicAssetListResponse, iter_assets
from app.jobs import CreateJobRequest, JobListResponse, JobRecord, JobStatus
from app.jobs.models import JobStage
from app.runtime.contracts import AssetService, JobService

from app.jobs.projections import summarize_job

DEFAULT_IMAGE_JOB_LIMIT = 25
MAX_IMAGE_JOB_LIMIT = 100
IMAGE_GALLERY_MODULES = ("image", "image-generation")
DEFAULT_IMAGE_ASSET_LIMIT = 100
MAX_IMAGE_ASSET_LIMIT = 250
RETRYABLE_IMAGE_JOB_STATUSES = {JobStatus.FAILED, JobStatus.CANCELED, JobStatus.STALE}
SUPPORTED_IMAGE_MIME_TYPES = {"image/gif", "image/jpeg", "image/png", "image/webp"}
_LOGGER = logging.getLogger(__name__)


class ImageAssetDeleteResponse(BaseModel):
    ok: bool
    asset_id: str
    deleted: bool
    file_deleted: bool
    file_error: str | None = None


def create_image_workspace_router(
    job_store: JobService,
    asset_store: AssetService,
) -> APIRouter:
    """Build image job and asset routes from composed kernel services."""
    router = APIRouter()

    @router.get("/api/image-generation/jobs", response_model=JobListResponse, tags=["image"])
    def image_jobs(limit: int = Query(default=DEFAULT_IMAGE_JOB_LIMIT, ge=1, le=MAX_IMAGE_JOB_LIMIT)) -> JobListResponse:
        try:
            jobs = _recent_image_jobs(job_store, limit)
            jobs = _prune_deleted_image_asset_jobs(job_store, jobs, asset_store)
        except Exception:
            _LOGGER.exception("Unable to list image workspace jobs")
            jobs = []
        summaries = []
        for job in jobs:
            try:
                summaries.append(summarize_job(job))
            except Exception:
                _LOGGER.exception("Unable to summarize image workspace job job_id=%s", getattr(job, "id", None))
                continue
        return JobListResponse(jobs=summaries)

    @router.post("/api/image-generation/jobs/{job_id}/retry", response_model=JobRecord, tags=["image"])
    def retry_image_job(job_id: str) -> JobRecord:
        source = job_store.get_job(job_id)
        if source is None:
            raise HTTPException(status_code=404, detail="job_not_found")
        if not _is_image_job(source):
            raise HTTPException(status_code=409, detail="job_not_image_generation")
        if source.status not in RETRYABLE_IMAGE_JOB_STATUSES:
            raise HTTPException(status_code=409, detail="job_not_retryable")
        return job_store.create_job(_retry_request(source))

    @router.get("/api/image-generation/assets", response_model=PublicAssetListResponse, tags=["image"])
    def image_assets(limit: int = Query(default=DEFAULT_IMAGE_ASSET_LIMIT, ge=1, le=MAX_IMAGE_ASSET_LIMIT)) -> AssetListResponse:
        rpg_world_asset_ids = _rpg_world_image_asset_ids(job_store)
        # Type and module are filtered in SQL, newest first (WP-5.5); the
        # remaining checks need the job store, so stop once the page is full.
        gallery = (
            asset
            for asset in iter_assets(asset_store, asset_type=AssetType.IMAGE.value, modules=IMAGE_GALLERY_MODULES)
            if _is_usable_image_asset(asset)
            and not _is_character_avatar_asset(asset, job_store)
            and not _is_rpg_world_image_asset(asset, job_store)
            and asset.id not in rpg_world_asset_ids
        )
        return AssetListResponse(assets=list(islice(gallery, limit)))

    @router.delete(
        "/api/image-generation/assets/{asset_id}",
        response_model=ImageAssetDeleteResponse,
        response_model_exclude_none=True,
        tags=["image"],
    )
    @router.post(
        "/api/image-generation/assets/{asset_id}/delete",
        response_model=ImageAssetDeleteResponse,
        response_model_exclude_none=True,
        tags=["image"],
    )
    def delete_image_asset(asset_id: str) -> ImageAssetDeleteResponse:
        store = asset_store
        asset = store.get_asset(asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="asset_not_found")
        if asset.type != AssetType.IMAGE or asset.module not in {"image", "image-generation"}:
            raise HTTPException(status_code=409, detail="asset_not_image_generation")

        shared_result = store.delete_asset(asset_id)
        legacy_result: dict[str, Any] | None = None
        legacy_asset_id = str((asset.compat or {}).get("legacy_asset_id") or "").strip()
        if legacy_asset_id:
            from app.platform.image.asset_store import delete_image_asset as delete_legacy_image_asset

            legacy_result = delete_legacy_image_asset(
                legacy_asset_id,
                delete_file=not bool(shared_result.get("file_deleted")),
            )

        deleted = bool(shared_result.get("deleted")) or bool((legacy_result or {}).get("deleted"))
        if not deleted:
            raise HTTPException(status_code=404, detail="asset_not_deletable")

        _delete_jobs_for_image_asset(asset, job_store)

        file_error = str(shared_result.get("file_error") or (legacy_result or {}).get("file_error") or "").strip()
        return ImageAssetDeleteResponse(
            ok=True,
            asset_id=asset_id,
            deleted=True,
            file_deleted=bool(shared_result.get("file_deleted"))
            or bool((legacy_result or {}).get("file_deleted")),
            file_error=file_error or None,
        )

    return router


def _is_usable_image_asset(asset: AssetRecord) -> bool:
    if str(asset.mime_type or "").lower() not in SUPPORTED_IMAGE_MIME_TYPES:
        return False
    try:
        return asset_available(asset)
    except OSError:
        return False


def _is_character_avatar_asset(asset: AssetRecord, job_store: Any) -> bool:
    if asset.module == "character-avatar":
        return True
    if str((asset.metadata or {}).get("source_module") or "").strip() == "character-avatar":
        return True
    source_job_id = str(asset.source_job_id or "").strip()
    if not source_job_id:
        return False
    source_job = job_store.get_job(source_job_id)
    return bool(source_job and source_job.module == "character-avatar")


def _is_rpg_world_image_asset(asset: AssetRecord, job_store: Any) -> bool:
    if _has_rpg_world_target_metadata(asset.metadata):
        return True
    source_job_id = str(asset.source_job_id or "").strip()
    if not source_job_id:
        return False
    source_job = job_store.get_job(source_job_id)
    payload = source_job.input_payload if source_job else None
    return bool(
        isinstance(payload, dict)
        and _has_rpg_world_target_metadata(payload.get("metadata"))
    )


def _has_rpg_world_target_metadata(metadata: Any) -> bool:
    if not isinstance(metadata, dict):
        return False
    return bool(
        metadata.get("rpg_world_image")
        or (
            str(metadata.get("world_id") or "").strip()
            and str(metadata.get("target_id") or "").strip()
        )
    )


def _rpg_world_image_asset_ids(job_store: Any) -> set[str]:
    try:
        jobs = list(job_store.iter_jobs(modules=IMAGE_GALLERY_MODULES))
    except Exception:
        _LOGGER.exception("Unable to classify RPG world image jobs")
        return set()
    return {
        asset_id
        for job in jobs
        if _has_rpg_world_target_metadata(
            (job.input_payload or {}).get("metadata")
            if isinstance(getattr(job, "input_payload", None), dict)
            else None
        )
        for asset_id in _job_image_asset_ids(job)
    }


def _delete_jobs_for_image_asset(asset: AssetRecord, store: JobService) -> None:
    job_ids = {str(asset.source_job_id or "").strip()}
    legacy_asset_id = str((asset.compat or {}).get("legacy_asset_id") or "").strip()
    asset_ids = {asset.id}
    if legacy_asset_id:
        asset_ids.add(legacy_asset_id)

    try:
        for job in store.iter_jobs(modules=IMAGE_GALLERY_MODULES):
            if not _is_image_job(job):
                continue
            if _job_references_image_asset(job, asset_ids):
                job_ids.add(job.id)
        delete_job = getattr(store, "delete_job", None)
        if not callable(delete_job):
            return
        for job_id in sorted(job_id for job_id in job_ids if job_id):
            delete_job(job_id)
    except Exception:
        _LOGGER.exception("Unable to clean jobs for deleted image asset asset_id=%s", asset.id)
        return


def _prune_deleted_image_asset_jobs(
    store: JobService,
    jobs: list[JobRecord],
    asset_store: AssetService,
) -> list[JobRecord]:
    delete_job = getattr(store, "delete_job", None)
    if not callable(delete_job):
        return jobs
    try:
        current_image_asset_ids = {
            asset.id
            for asset in iter_assets(asset_store, asset_type=AssetType.IMAGE.value, modules=IMAGE_GALLERY_MODULES)
            if _is_usable_image_asset(asset)
        }
    except Exception:
        _LOGGER.exception("Unable to load image assets while pruning stale jobs")
        return jobs

    retained: list[JobRecord] = []
    for job in jobs:
        if job.status == JobStatus.COMPLETED and _job_image_asset_ids(job) - current_image_asset_ids:
            try:
                delete_job(job.id)
            except Exception:
                _LOGGER.exception("Unable to delete stale image job job_id=%s", job.id)
                retained.append(job)
            continue
        retained.append(job)
    return retained


def _job_image_asset_ids(job: JobRecord) -> set[str]:
    asset_ids: set[str] = set()
    for ref in getattr(job, "output_refs", []) or []:
        if not isinstance(ref, dict):
            continue
        if str(ref.get("type") or "") != "image":
            continue
        asset_id = str(ref.get("asset_id") or "").strip()
        if asset_id:
            asset_ids.add(asset_id)
    return asset_ids


def _job_references_image_asset(job: JobRecord, asset_ids: set[str]) -> bool:
    return bool(_job_image_asset_ids(job) & asset_ids)


def _retry_request(source: JobRecord) -> CreateJobRequest:
    return CreateJobRequest(
        owner_id=source.owner_id,
        module=source.module,
        type=source.type,
        resource_class=source.resource_class,
        priority=source.priority,
        stages=[
            JobStage(id=stage.id, label=stage.label, status=JobStatus.QUEUED, resource_class=stage.resource_class)
            for stage in source.stages
        ],
        input_ref=source.input_ref,
        input_payload=source.input_payload,
        compat={**source.compat, "retry_of": source.id},
    )


def _is_image_job(job: JobRecord) -> bool:
    return job.type == "image.generate" or job.module in {"image", "image-generation"}


def _recent_image_jobs(store: Any, limit: int) -> list[Any]:
    return store.list_jobs(limit=limit, modules=IMAGE_GALLERY_MODULES)
