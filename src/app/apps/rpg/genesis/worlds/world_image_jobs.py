"""RPG-owned job submission for world image generation."""
from __future__ import annotations

from typing import Any

from app.jobs import CreateJobRequest, ResourceClass, default_job_store
from app.jobs.models import JobStage


def create_world_image_job(
    owner_id: str,
    payload: dict[str, Any],
    *,
    job_store: Any | None = None,
) -> Any:
    """Submit an RPG image request through the neutral jobs contract."""
    store = job_store if job_store is not None else default_job_store()
    return store.create_job(
        CreateJobRequest(
            owner_id=owner_id,
            module="image-generation",
            type="image.generate",
            resource_class=ResourceClass.GPU_IMAGE,
            stages=[
                JobStage(
                    id="generate-image",
                    label="Generate image",
                    resource_class=ResourceClass.GPU_IMAGE,
                ),
                JobStage(
                    id="store-asset",
                    label="Store image asset",
                    resource_class=ResourceClass.CPU,
                ),
            ],
            input_payload=payload,
        )
    )
