"""Pure GPU model residency planning for the shared job scheduler."""
from __future__ import annotations

import threading
from copy import deepcopy
from enum import Enum
from pathlib import Path
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from .executor import JobHandler
from .models import CreateJobRequest, JobRecord, JobStage, ResourceClass


class ModelResidencyStatus(str, Enum):
    UNLOADED = "unloaded"
    LOADING = "loading"
    LOADED = "loaded"
    UNLOADING = "unloading"
    ERROR = "error"


class ResidencyDecisionAction(str, Enum):
    CAN_RUN = "can_run"
    QUEUE = "queue"
    EVICT_FIRST = "evict_first"
    BLOCKED = "blocked"


class ModelResidencyRecord(BaseModel):
    model_id: str
    model_name: str = ""
    provider_id: str
    module: str
    resource_class: ResourceClass
    status: ModelResidencyStatus = ModelResidencyStatus.UNLOADED
    worker_id: str | None = None
    worker_endpoint: str | None = None
    estimated_vram_mb: int | None = Field(default=None, ge=0)
    compatibility_group: str | None = None
    last_used_at: str | None = None
    error: str | None = None


class GpuResidencyRequest(BaseModel):
    job_id: str
    model_id: str
    model_name: str = ""
    provider_id: str
    module: str
    resource_class: ResourceClass
    worker_id: str | None = None
    worker_endpoint: str | None = None
    estimated_vram_mb: int | None = Field(default=None, ge=0)
    compatibility_group: str | None = None


class GpuResidencyPolicy(BaseModel):
    total_vram_mb: int | None = Field(default=None, ge=0)
    allow_co_residency: bool = False
    compatible_model_pairs: list[tuple[str, str]] = Field(default_factory=list)
    allow_matching_compatibility_group: bool = False


class ResidencyDecision(BaseModel):
    action: ResidencyDecisionAction
    reason: str
    requested_model_id: str
    blocking_model_ids: list[str] = Field(default_factory=list)
    eviction_model_ids: list[str] = Field(default_factory=list)
    diagnostics: list[dict[str, str]] = Field(default_factory=list)


class ModelResidencyDiagnostics(BaseModel):
    status: str
    policy: GpuResidencyPolicy
    records: list[ModelResidencyRecord] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


ModelResidencyHook = Callable[[ModelResidencyRecord, JobRecord], dict[str, Any] | None]


_RESIDENCY: dict[str, dict[str, ModelResidencyRecord]] = {}
_RESIDENCY_LOCK = threading.RLock()


class InMemoryModelResidencyStore:
    """Provider-free deterministic residency state keyed by test namespace."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else Path(":memory:residency")
        self._key = str(self.db_path)

    def upsert_record(self, record: ModelResidencyRecord) -> ModelResidencyRecord:
        with _RESIDENCY_LOCK:
            _RESIDENCY.setdefault(self._key, {})[record.model_id] = deepcopy(record)
        return deepcopy(record)

    def delete_record(self, model_id: str) -> bool:
        with _RESIDENCY_LOCK:
            return _RESIDENCY.setdefault(self._key, {}).pop(model_id, None) is not None

    def list_records(self) -> list[ModelResidencyRecord]:
        with _RESIDENCY_LOCK:
            values = list(_RESIDENCY.setdefault(self._key, {}).values())
            values.sort(key=lambda item: (item.worker_id or "", item.resource_class.value, item.model_id))
            return deepcopy(values)

    def diagnostics(self, policy: GpuResidencyPolicy | None = None) -> ModelResidencyDiagnostics:
        return get_model_residency_diagnostics(self.list_records(), policy)


def default_model_residency_store() -> InMemoryModelResidencyStore:
    return InMemoryModelResidencyStore()


def get_model_residency_diagnostics(
    residency: list[ModelResidencyRecord] | None = None,
    policy: GpuResidencyPolicy | None = None,
) -> ModelResidencyDiagnostics:
    records = residency or []
    active = [
        record
        for record in records
        if record.status in {ModelResidencyStatus.LOADING, ModelResidencyStatus.LOADED, ModelResidencyStatus.UNLOADING}
    ]
    errors = [record for record in records if record.status == ModelResidencyStatus.ERROR]
    warnings = []
    if any(record.estimated_vram_mb is None for record in active):
        warnings.append("unknown_vram_records_require_exclusive_gpu")
    if errors:
        warnings.append("model_residency_errors_present")
    return ModelResidencyDiagnostics(
        status="degraded" if errors else "active" if active else "idle",
        policy=policy or GpuResidencyPolicy(),
        records=records,
        warnings=warnings,
    )


def create_model_load_job_request(request: GpuResidencyRequest, *, priority: int = 0) -> CreateJobRequest:
    return CreateJobRequest(
        module="models",
        type="model.load",
        resource_class=request.resource_class,
        priority=priority,
        stages=[JobStage(id="load-model", label=f"Load {request.model_name or request.model_id}", resource_class=request.resource_class)],
        input_payload={
            "model_id": request.model_id,
            "model_name": request.model_name,
            "provider_id": request.provider_id,
            "module": request.module,
            "worker_id": request.worker_id,
            "worker_endpoint": request.worker_endpoint,
            "estimated_vram_mb": request.estimated_vram_mb,
            "compatibility_group": request.compatibility_group,
        },
        compat={"residency_transition": "load"},
    )


def create_model_evict_job_request(record: ModelResidencyRecord, *, priority: int = 0) -> CreateJobRequest:
    return CreateJobRequest(
        module="models",
        type="model.evict",
        resource_class=record.resource_class,
        priority=priority,
        stages=[JobStage(id="evict-model", label=f"Evict {record.model_name or record.model_id}", resource_class=record.resource_class)],
        input_payload={
            "model_id": record.model_id,
            "model_name": record.model_name,
            "provider_id": record.provider_id,
            "module": record.module,
            "worker_id": record.worker_id,
            "worker_endpoint": record.worker_endpoint,
            "estimated_vram_mb": record.estimated_vram_mb,
            "compatibility_group": record.compatibility_group,
        },
        compat={"residency_transition": "evict"},
    )


def create_model_residency_handlers(
    store: Any,
    *,
    load_model: ModelResidencyHook | None = None,
    evict_model: ModelResidencyHook | None = None,
) -> dict[str, JobHandler]:
    return {
        "model.load": lambda job: _handle_model_load_job(job, store, load_model=load_model),
        "model.evict": lambda job: _handle_model_evict_job(job, store, evict_model=evict_model),
    }


def _handle_model_load_job(
    job: JobRecord,
    store: Any,
    *,
    load_model: ModelResidencyHook | None,
) -> dict[str, Any]:
    record = _record_from_job_payload(job, status=ModelResidencyStatus.LOADING)
    store.upsert_record(record)
    hook_result: dict[str, Any] = {}
    try:
        if load_model is not None:
            hook_result = load_model(record, job) or {}
    except Exception as exc:
        store.upsert_record(record.model_copy(update={"status": ModelResidencyStatus.ERROR, "error": str(exc)}))
        raise
    loaded = record.model_copy(update={"status": ModelResidencyStatus.LOADED, "error": None})
    store.upsert_record(loaded)
    return {
        "logs": [{
            "level": "info",
            "message": "model residency marked loaded",
            "model_id": loaded.model_id,
            "worker_id": loaded.worker_id or "",
        }] + _safe_hook_items(hook_result.get("logs")),
        "output_refs": [{
            "kind": "model_residency",
            "action": "loaded",
            "model_id": loaded.model_id,
            "worker_id": loaded.worker_id,
        }] + _safe_hook_items(hook_result.get("output_refs")),
    }


def _handle_model_evict_job(
    job: JobRecord,
    store: Any,
    *,
    evict_model: ModelResidencyHook | None,
) -> dict[str, Any]:
    record = _record_from_job_payload(job, status=ModelResidencyStatus.UNLOADING)
    store.upsert_record(record)
    hook_result: dict[str, Any] = {}
    try:
        if evict_model is not None:
            hook_result = evict_model(record, job) or {}
    except Exception as exc:
        store.upsert_record(record.model_copy(update={"status": ModelResidencyStatus.ERROR, "error": str(exc)}))
        raise
    removed = store.delete_record(record.model_id)
    return {
        "logs": [{
            "level": "info",
            "message": "model residency marked unloaded",
            "model_id": record.model_id,
            "removed": removed,
        }] + _safe_hook_items(hook_result.get("logs")),
        "output_refs": [{
            "kind": "model_residency",
            "action": "evicted",
            "model_id": record.model_id,
            "removed": removed,
        }] + _safe_hook_items(hook_result.get("output_refs")),
    }


def gpu_residency_request_from_job(job: JobRecord) -> GpuResidencyRequest | None:
    if not _is_gpu_resource(job.resource_class):
        return None
    payload = job.input_payload or {}
    model_id = _safe_str(payload.get("model_id")).strip()
    if not model_id:
        return None
    return GpuResidencyRequest(
        job_id=job.id,
        model_id=model_id,
        model_name=_safe_str(payload.get("model_name")).strip() or model_id,
        provider_id=_safe_str(payload.get("provider_id")).strip(),
        module=_safe_str(payload.get("module")).strip() or job.module,
        resource_class=job.resource_class,
        worker_id=_safe_str(payload.get("worker_id")).strip() or None,
        worker_endpoint=_safe_str(payload.get("worker_endpoint")).strip() or None,
        estimated_vram_mb=_safe_optional_int(payload.get("estimated_vram_mb")),
        compatibility_group=_safe_str(payload.get("compatibility_group")).strip() or None,
    )


def _record_from_job_payload(job: JobRecord, *, status: ModelResidencyStatus) -> ModelResidencyRecord:
    payload = job.input_payload or {}
    model_id = _safe_str(payload.get("model_id")).strip()
    if not model_id:
        raise ValueError("model residency job missing input_payload.model_id")
    return ModelResidencyRecord(
        model_id=model_id,
        model_name=_safe_str(payload.get("model_name")).strip() or model_id,
        provider_id=_safe_str(payload.get("provider_id")).strip(),
        module=_safe_str(payload.get("module")).strip() or job.module,
        resource_class=job.resource_class,
        status=status,
        worker_id=_safe_str(payload.get("worker_id")).strip() or (job.lease.worker_id if job.lease else None),
        worker_endpoint=_safe_str(payload.get("worker_endpoint")).strip() or None,
        estimated_vram_mb=_safe_optional_int(payload.get("estimated_vram_mb")),
        compatibility_group=_safe_str(payload.get("compatibility_group")).strip() or None,
    )


def plan_model_residency(
    request: GpuResidencyRequest,
    residency: list[ModelResidencyRecord],
    policy: GpuResidencyPolicy | None = None,
) -> ResidencyDecision:
    policy = policy or GpuResidencyPolicy()
    if not _is_gpu_resource(request.resource_class):
        return ResidencyDecision(action=ResidencyDecisionAction.CAN_RUN, reason="non_gpu_job", requested_model_id=request.model_id)
    errored = [record for record in residency if record.model_id == request.model_id and record.status == ModelResidencyStatus.ERROR]
    if errored:
        return ResidencyDecision(
            action=ResidencyDecisionAction.BLOCKED,
            reason="requested_model_in_error",
            requested_model_id=request.model_id,
            blocking_model_ids=sorted({record.model_id for record in errored}),
            diagnostics=[{
                "kind": "model_residency_error",
                "model_id": record.model_id,
                "message": record.error or "model residency is in error state",
            } for record in errored],
        )
    active = [record for record in residency if record.status in {ModelResidencyStatus.LOADING, ModelResidencyStatus.LOADED, ModelResidencyStatus.UNLOADING}]
    if not active:
        return ResidencyDecision(action=ResidencyDecisionAction.CAN_RUN, reason="gpu_idle", requested_model_id=request.model_id)
    if any(record.model_id == request.model_id and record.status == ModelResidencyStatus.LOADED for record in active):
        return ResidencyDecision(action=ResidencyDecisionAction.CAN_RUN, reason="requested_model_already_loaded", requested_model_id=request.model_id)
    transitioning = [record for record in active if record.status in {ModelResidencyStatus.LOADING, ModelResidencyStatus.UNLOADING}]
    if transitioning:
        return ResidencyDecision(
            action=ResidencyDecisionAction.QUEUE,
            reason="model_transition_in_progress",
            requested_model_id=request.model_id,
            blocking_model_ids=sorted({record.model_id for record in transitioning}),
        )
    loaded = [record for record in active if record.status == ModelResidencyStatus.LOADED]
    if not policy.allow_co_residency:
        ids = sorted({record.model_id for record in loaded})
        return ResidencyDecision(action=ResidencyDecisionAction.EVICT_FIRST, reason="conservative_single_gpu_policy", requested_model_id=request.model_id, blocking_model_ids=ids, eviction_model_ids=ids)
    incompatible = [record for record in loaded if not _compatible(record, request, policy)]
    if incompatible:
        ids = sorted({record.model_id for record in incompatible})
        return ResidencyDecision(action=ResidencyDecisionAction.EVICT_FIRST, reason="incompatible_loaded_model", requested_model_id=request.model_id, blocking_model_ids=ids, eviction_model_ids=ids)
    if request.estimated_vram_mb is None or any(record.estimated_vram_mb is None for record in loaded):
        ids = sorted({record.model_id for record in loaded})
        return ResidencyDecision(action=ResidencyDecisionAction.EVICT_FIRST, reason="unknown_vram_requires_exclusive_gpu", requested_model_id=request.model_id, blocking_model_ids=ids, eviction_model_ids=ids)
    if policy.total_vram_mb is not None:
        used = sum(record.estimated_vram_mb or 0 for record in loaded)
        if used + request.estimated_vram_mb > policy.total_vram_mb:
            ids = sorted({record.model_id for record in loaded})
            return ResidencyDecision(action=ResidencyDecisionAction.EVICT_FIRST, reason="insufficient_vram", requested_model_id=request.model_id, blocking_model_ids=ids, eviction_model_ids=ids)
    return ResidencyDecision(action=ResidencyDecisionAction.CAN_RUN, reason="compatible_vram_available", requested_model_id=request.model_id)


def _is_gpu_resource(resource_class: ResourceClass) -> bool:
    return resource_class.value.startswith("gpu:")


def _compatible(record: ModelResidencyRecord, request: GpuResidencyRequest, policy: GpuResidencyPolicy) -> bool:
    if record.model_id == request.model_id:
        return True
    pair = tuple(sorted((record.model_id, request.model_id)))
    if pair in {tuple(sorted(item)) for item in policy.compatible_model_pairs}:
        return True
    if policy.allow_matching_compatibility_group and record.compatibility_group and request.compatibility_group:
        return record.compatibility_group == request.compatibility_group
    return False


def _safe_str(value: Any) -> str:
    return "" if value is None else value if isinstance(value, str) else str(value)


def _safe_optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_hook_items(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []
