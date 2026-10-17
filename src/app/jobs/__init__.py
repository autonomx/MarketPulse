"""Shared Omnix job/run kernel primitives.

Feature execution is registered through FeatureModule.job_handlers. Importing
this package performs no feature installation or class mutation.
"""

from .models import (
    CancelJobRequest,
    ClaimJobRequest,
    ClaimJobResponse,
    CompleteJobRequest,
    CreateJobRequest,
    FailJobRequest,
    JobListResponse,
    JobRecord,
    JobStatus,
    ReleaseJobRequest,
    ResourceClass,
)
from .executor import LocalJobExecutor
from .handlers import (
    AnyJobInput,
    Backoff,
    JobExecutionContext,
    JobHandlerRegistry,
    JobHandlerSpec,
    JobObserver,
)
from .provider_control import (
    create_worker_model_control_hooks,
    evict_worker_model,
    load_worker_model,
)
from .residency import (
    ModelResidencyDiagnostics,
    GpuResidencyPolicy,
    GpuResidencyRequest,
    InMemoryModelResidencyStore,
    ModelResidencyRecord,
    ModelResidencyStatus,
    ResidencyDecision,
    ResidencyDecisionAction,
    create_model_evict_job_request,
    create_model_load_job_request,
    create_model_residency_handlers,
    default_model_residency_store,
    get_model_residency_diagnostics,
    plan_model_residency,
)
from .store import default_job_store

__all__ = [
    "AnyJobInput",
    "Backoff",
    "CancelJobRequest",
    "ClaimJobRequest",
    "ClaimJobResponse",
    "CompleteJobRequest",
    "CreateJobRequest",
    "FailJobRequest",
    "GpuResidencyPolicy",
    "GpuResidencyRequest",
    "InMemoryModelResidencyStore",
    "JobExecutionContext",
    "JobHandlerRegistry",
    "JobHandlerSpec",
    "JobObserver",
    "JobListResponse",
    "JobRecord",
    "JobStatus",
    "ReleaseJobRequest",
    "LocalJobExecutor",
    "ModelResidencyDiagnostics",
    "ModelResidencyRecord",
    "ModelResidencyStatus",
    "ResourceClass",
    "ResidencyDecision",
    "ResidencyDecisionAction",
    "create_model_evict_job_request",
    "create_model_load_job_request",
    "create_model_residency_handlers",
    "create_worker_model_control_hooks",
    "default_model_residency_store",
    "default_job_store",
    "evict_worker_model",
    "get_model_residency_diagnostics",
    "load_worker_model",
    "plan_model_residency",
]
