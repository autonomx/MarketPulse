"""Typed shared job/run contract for the web platform."""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, GetJsonSchemaHandler, PrivateAttr
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema


class JobStatus(str, Enum):
    QUEUED = "queued"
    LEASED = "leased"
    RUNNING = "running"
    WAITING = "waiting"
    RETRYING = "retrying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCEL_REQUESTED = "cancel_requested"
    PAUSED = "paused"
    CANCELED = "canceled"
    STALE = "stale"


class ResourceClass(str, Enum):
    CPU = "cpu"
    GPU_LLM = "gpu:llm"
    GPU_TTS = "gpu:tts"
    GPU_TTS_REALTIME = "gpu:tts:realtime"
    GPU_TTS_PREVIEW = "gpu:tts:preview"
    GPU_TTS_OFFLINE = "gpu:tts:offline"
    GPU_STT = "gpu:stt"
    GPU_IMAGE = "gpu:image"
    NETWORK = "network"
    RPG_CAMPAIGN_GENESIS = "rpg_campaign_genesis"
    RPG_WORLD_GENERATION = "rpg_world_generation"
    RPG_MAP_MATERIALIZATION = "rpg_map_materialization"

    @classmethod
    def __get_pydantic_json_schema__(
        cls,
        core_schema: CoreSchema,
        handler: GetJsonSchemaHandler,
    ) -> JsonSchemaValue:
        """Keep repository-only RPG worker queues out of public job creation docs."""

        schema = handler(core_schema)
        if isinstance(schema.get("enum"), list):
            hidden = {
                cls.RPG_WORLD_GENERATION.value,
                cls.RPG_MAP_MATERIALIZATION.value,
            }
            schema["enum"] = [member.value for member in cls if member.value not in hidden]
        return schema


TERMINAL_STATUSES = {
    JobStatus.COMPLETED,
    JobStatus.FAILED,
    JobStatus.CANCELED,
    JobStatus.STALE,
}


class JobProgress(BaseModel):
    current: int | float = 0
    total: int = 1
    message: str | None = None


class JobError(BaseModel):
    code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class RetryState(BaseModel):
    attempts: int = 0
    max_attempts: int = 0
    policy: str = "none"


class JobStage(BaseModel):
    id: str
    label: str
    status: JobStatus = JobStatus.QUEUED
    resource_class: ResourceClass
    progress: JobProgress = Field(default_factory=JobProgress)
    checkpoint_ref: dict[str, Any] | None = None
    output_refs: list[dict[str, Any]] = Field(default_factory=list)
    error: JobError | None = None
    started_at: str | None = None
    completed_at: str | None = None
    retry: RetryState = Field(default_factory=RetryState)


class JobLease(BaseModel):
    worker_id: str
    token: str
    claimed_at: str
    expires_at: str


class CancelState(BaseModel):
    requested: bool = False
    requested_at: str | None = None
    acknowledged_at: str | None = None
    reason: str | None = None


class CreateJobRequest(BaseModel):
    owner_id: str | None = None
    module: str
    type: str
    resource_class: ResourceClass
    priority: int = 0
    stages: list[JobStage] = Field(default_factory=list)
    input_ref: dict[str, Any] | None = None
    input_payload: dict[str, Any] | None = None
    compat: dict[str, Any] = Field(default_factory=dict)


class ClaimJobRequest(BaseModel):
    worker_id: str
    resource_classes: list[ResourceClass] = Field(default_factory=list)
    job_types: list[str] = Field(default_factory=list)
    lease_seconds: int = Field(default=30, ge=1, le=3600)
    cpu_limit: int = Field(default=2, ge=1, le=64)


class ClaimJobResponse(BaseModel):
    ok: bool
    job: "JobRecord | None" = None
    reason: str | None = None


class CompleteJobRequest(BaseModel):
    worker_id: str | None = Field(default=None, min_length=1)
    lease_token: str | None = Field(default=None, min_length=1)
    output_refs: list[dict[str, Any]] = Field(default_factory=list)
    logs: list[dict[str, Any]] = Field(default_factory=list)


class FailJobRequest(BaseModel):
    worker_id: str | None = Field(default=None, min_length=1)
    lease_token: str | None = Field(default=None, min_length=1)
    code: str = "job_failed"
    message: str
    retryable: bool = False
    _retry_delay_seconds: int = PrivateAttr(default=0)
    details: dict[str, Any] = Field(default_factory=dict)


class ReleaseJobRequest(BaseModel):
    worker_id: str = Field(min_length=1)
    lease_token: str = Field(min_length=1)
    reason: str = ""


class CancelJobRequest(BaseModel):
    reason: str | None = None


class JobRecord(BaseModel):
    _attempt_count: int = PrivateAttr(default=0)

    id: str
    owner_id: str | None = None
    module: str
    type: str
    status: JobStatus
    resource_class: ResourceClass
    priority: int = 0
    stages: list[JobStage] = Field(default_factory=list)
    progress: JobProgress = Field(default_factory=JobProgress)
    logs: list[dict[str, Any]] = Field(default_factory=list)
    input_ref: dict[str, Any] | None = None
    input_payload: dict[str, Any] | None = None
    output_refs: list[dict[str, Any]] = Field(default_factory=list)
    error: JobError | None = None
    lease: JobLease | None = None
    created_at: str
    updated_at: str
    started_at: str | None = None
    completed_at: str | None = None
    cancel: CancelState = Field(default_factory=CancelState)
    compat: dict[str, Any] = Field(default_factory=dict)
    # The submitting request's id (WP-10.2); workers bind it to their log lines.
    correlation_id: str | None = None


class JobListResponse(BaseModel):
    """A page of jobs, newest first (WP-5.5)."""

    jobs: list[JobRecord]
    next_cursor: str | None = None
    has_more: bool = False


class JobEventRecord(BaseModel):
    id: int
    job_id: str
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str
