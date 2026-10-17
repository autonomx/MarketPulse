"""Runtime-neutral contracts for Omnix workflows and open-ended agents."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Protocol
import secrets
import uuid

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator

from app.runtime.ports import Port

AgentRunStatus = Literal[
    "queued",
    "starting",
    "running",
    "pause_requested",
    "paused",
    "waiting_for_approval",
    "waiting_for_input",
    "waiting_for_children",
    "resume_requested",
    "cancel_requested",
    "cancelled",
    "completed",
    "failed",
]
AgentDesiredState = Literal["running", "paused", "cancelled"]
AgentCommandType = Literal["steer", "pause", "resume", "cancel", "approve", "reject"]
AgentApprovalPolicy = Literal[
    "allow_automatic",
    "ask_sensitive",
    "always_ask",
    "disabled",
]
AgentEventType = Literal[
    "run.created",
    "run.started",
    "run.settled",
    "run.status",
    "run.completed",
    "run.failed",
    "run.recovery_requested",
    "run.recovery_failed",
    "run.stall_suspected",
    # The run went unsandboxed under OMNIX_AGENT_ALLOW_UNSANDBOXED (WP-4.7).
    "run.unsandboxed",
    "model.message",
    "tool.requested",
    "tool.started",
    "tool.output",
    "tool.completed",
    "approval.requested",
    "approval.resolved",
    "artifact.created",
    "steering.received",
    "acceptance.started",
    "acceptance.completed",
    "acceptance.retry_requested",
    "worker.heartbeat",
    "task.revised",
    "evidence.receipt",
    "run.superseded",
    "quality.stage",
    "quality.self_review_completed",
    "quality.self_review_protocol_retry_requested",
    "quality.self_review_protocol_exhausted",
    "quality.validation_recorded",
    "quality.validation_requested",
    "quality.validation_retry_requested",
    "quality.validation_retry_exhausted",
    "quality.validation_repair_requested",
    "quality.review_started",
    "quality.review_attempt_started",
    "quality.review_attempt_completed",
    "quality.review_retry_requested",
    "quality.review_runtime_exhausted",
    "quality.review_completed",
    "quality.implementation_continuation_requested",
    "quality.implementation_candidate_exhausted",
    "quality.repair_requested",
    "planning.conformance_evaluated",
]
AgentApprovalState = Literal["pending", "approved", "rejected", "expired"]
ArtifactKind = Literal["diff", "test_result", "log", "report", "file", "other"]
EvidenceRequirementLevel = Literal["none", "optional", "required"]
ExternalEvidenceAccess = Literal["allowed", "forbidden"]
EvidenceFreshness = Literal["timeless", "current", "as_of_date"]
EvidenceTrust = Literal["authoritative", "primary", "reputable", "general"]
EvidenceFallbackPolicy = Literal["fail_closed", "allow_fallback"]
EvidenceAttribution = Literal["none", "when_used", "required"]
RetrievalStrategy = Literal["lookup", "bounded", "adaptive"]
EvidenceClassifier = Literal["deterministic", "semantic", "conservative"]
EvidenceEvaluationStatus = Literal[
    "satisfied",
    "missing",
    "unavailable",
    "stale",
    "wrong_subject",
    "insufficient_trust",
    "rejected",
]
RequestMode = Literal["chat", "quick_research", "deep_research", "agent", "auto"]
RequestModeSource = Literal[
    "explicit_command",
    "turn_setting",
    "persistent_setting",
    "classifier",
    "default",
]
RequirementSource = Literal["user", "repository", "policy", "derived"]
ValidationKind = Literal["test", "typecheck", "lint", "build", "diff_review", "browser", "custom"]
ValidationOutcome = Literal[
    "passed",
    "substantive_failure",
    "infrastructure_failure",
    "protocol_failure",
    "blocked",
]
ReviewFindingAttribution = Literal[
    "run_owned",
    "run_owned_dependency",
    "baseline_context",
    "unattributed",
]
QualityPolicy = Literal["off", "standard", "strict", "critical"]
QualityStage = Literal[
    "inspect",
    "planning",
    "implementing",
    "self_review",
    "validating",
    "reviewing",
    "repairing",
    "acceptance",
]
ReviewVerdict = Literal["approve", "changes_required", "blocked"]
RequirementReviewStatus = Literal["satisfied", "partial", "missing", "not_applicable"]
ReviewAttemptStatus = Literal[
    "running",
    "completed",
    "runtime_failed",
    "protocol_failed",
    "cancelled",
]
ReviewAttemptFailureClass = Literal[
    "reviewer_local_budget_exhausted",
    "parent_global_budget_exhausted",
    "provider_rate_limited",
    "provider_transport_failure",
    "provider_unavailable",
    "review_protocol_invalid",
    "runtime_failure",
    "cancelled",
]
ResourceGrantState = Literal["active", "released", "exhausted"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ModelRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_id: str
    model_id: str
    reasoning_effort: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)


class SubjectRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: str
    canonical_id: str
    display_name: str | None = None
    qualifiers: dict[str, Any] = Field(default_factory=dict)


class EvidenceCoverage(BaseModel):
    """Identity of the fact/entity coverage an evidence item proves.

    Requirement ids remain tracing/persistence identifiers. Coverage identity is
    evaluated independently so two obligations may share a source class without
    becoming interchangeable.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str = Field(min_length=1, max_length=80)
    subject: SubjectRef | None = None
    coverage_key: str | None = Field(default=None, min_length=1, max_length=320)

    @model_validator(mode="after")
    def validate_identity(self) -> "EvidenceCoverage":
        if self.subject is None and not str(self.coverage_key or "").strip():
            raise ValueError("evidence coverage requires subject or coverage_key")
        return self


class EvidenceSourceOption(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_class: str
    trust_floor: EvidenceTrust = "general"
    provider_hint: str | None = None
    preference: int = Field(default=100, ge=0, le=10_000)


class EvidenceRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    source_class: str
    subject: SubjectRef | None = None
    coverage: EvidenceCoverage | None = None
    purpose: str = Field(default="fact", min_length=1, max_length=80)
    freshness: EvidenceFreshness = "timeless"
    trust_floor: EvidenceTrust = "general"
    acceptable_sources: list[EvidenceSourceOption] = Field(default_factory=list)
    minimum_matches: int = Field(default=1, ge=1, le=100)
    fallback_policy: EvidenceFallbackPolicy = "fail_closed"
    as_of_date: datetime | None = None
    max_age_seconds: int | None = Field(default=None, ge=1)


class RetrievalPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy: RetrievalStrategy = "adaptive"
    max_queries: int = Field(default=4, ge=1, le=100)
    max_sources: int = Field(default=10, ge=1, le=200)
    max_extracts: int = Field(default=4, ge=0, le=50)
    max_wall_time_seconds: int = Field(default=60, ge=1, le=3600)


class EvidencePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    requirement: EvidenceRequirementLevel = "none"
    external_access: ExternalEvidenceAccess = "allowed"
    requirements: list[EvidenceRequirement] = Field(default_factory=list)
    user_visible_attribution: EvidenceAttribution = "when_used"
    retrieval: RetrievalPolicy = Field(default_factory=RetrievalPolicy)


class EvidenceDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy: EvidencePolicy = Field(default_factory=EvidencePolicy)
    confidence: float = Field(default=1.0, ge=0, le=1)
    reason: str = "model_knowledge_sufficient"
    classifier: EvidenceClassifier = "deterministic"


class RequestModeCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: RequestMode
    source: RequestModeSource
    priority: int


class RequestModeSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: RequestMode
    source: RequestModeSource
    priority: int
    suppressed: list[RequestModeCandidate] = Field(default_factory=list)


class TaskRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=4000)
    source: RequirementSource = "user"
    required: bool = True
    validation_ids: list[str] = Field(default_factory=list)


class TaskConstraint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=4000)
    source: RequirementSource = "derived"


class ValidationSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=160)
    kind: ValidationKind
    description: str = Field(min_length=1, max_length=2000)
    covers: list[str] = Field(default_factory=list)
    required: bool = True
    command_hint: str | None = Field(default=None, max_length=1000)


class TaskRevision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    revision_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    run_id: str
    sequence: int = Field(ge=1)
    previous_revision_id: str | None = None
    source_command_id: str | None = None
    user_instruction: str
    effective_objective: str
    effective_success_criteria: list[SuccessCriterion] = Field(default_factory=list)
    requirements: list[TaskRequirement] = Field(default_factory=list)
    constraints: list[TaskConstraint] = Field(default_factory=list)
    validation_plan: list[ValidationSpec] = Field(default_factory=list)
    evidence_decision: EvidenceDecision = Field(default_factory=EvidenceDecision)
    required_local_capabilities: list[str] = Field(default_factory=list)
    required_external_capabilities: list[str] = Field(default_factory=list)
    expected_artifacts: list[ArtifactKind] = Field(default_factory=list)
    acceptance_checks: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class WorkspaceState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state_id: str
    run_id: str
    task_revision_id: str | None = None
    base_commit_sha: str
    tracked_diff_sha256: str
    untracked_file_manifest_sha256: str
    modified_paths: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class RunChangeSet(BaseModel):
    """Canonical run-owned change subject bound to one exact candidate state.

    WorkspaceState intentionally contains the entire exact checkout, including
    dirties that predated the run. RunChangeSet is the smaller authoritative
    subject Omnix attributes to the run and presents to validation/review.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    change_set_id: str
    run_id: str
    task_revision_id: str | None = None
    baseline_id: str
    baseline_head_sha: str
    candidate_workspace_state_id: str
    run_owned_paths: list[str] = Field(default_factory=list)
    baseline_context_paths: list[str] = Field(default_factory=list)
    tracked_patch_sha256: str
    patch_checksum: str
    patch_storage_ref: str
    untracked_manifest: dict[str, dict[str, Any]] = Field(default_factory=dict)
    deletions: list[str] = Field(default_factory=list)
    renames: list[dict[str, str]] = Field(default_factory=list)
    mode_changes: list[str] = Field(default_factory=list)
    baseline_conflicts: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class ValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    result_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    run_id: str
    validation_id: str
    kind: ValidationKind
    task_revision_id: str | None = None
    workspace_state_id: str
    command: str
    exit_code: int | None = None
    success: bool
    outcome: ValidationOutcome = "passed"
    output_digest: str
    covers_requirement_ids: list[str] = Field(default_factory=list)
    started_at: datetime | None = None
    finished_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReviewRequirementResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    requirement_id: str
    status: RequirementReviewStatus
    evidence: str = ""


class ReviewFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    severity: Literal["blocker", "high", "medium", "low"] = "medium"
    category: str = "correctness"
    file: str | None = None
    location: str | None = None
    problem: str
    recommended_fix: str | None = None
    # The reviewer supplies path claims only. Attribution/blocking are rewritten
    # by Omnix against the authoritative RunChangeSet before persistence.
    subject_paths: list[str] = Field(default_factory=list)
    context_paths: list[str] = Field(default_factory=list)
    attribution: ReviewFindingAttribution = "unattributed"
    blocking: bool = False


class SelfReviewResult(BaseModel):
    """Structured implementer self-review bound to one exact final state."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    self_review_result_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    run_id: str
    task_revision_id: str | None = None
    workspace_state_id: str
    verdict: ReviewVerdict
    requirements: list[ReviewRequirementResult] = Field(default_factory=list)
    findings: list[ReviewFinding] = Field(default_factory=list)
    missing_tests: list[str] = Field(default_factory=list)
    residual_risks: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class ReviewSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    snapshot_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    run_id: str
    task_revision_id: str | None = None
    workspace_state_id: str
    base_commit_sha: str
    patch_checksum: str
    patch_storage_ref: str | None = None
    run_change_set_id: str | None = None
    workspace_root: str
    subject_paths: list[str] = Field(default_factory=list)
    context_paths: list[str] = Field(default_factory=list)
    relevant_files: list[str] = Field(default_factory=list)
    validation_result_ids: list[str] = Field(default_factory=list)
    repository_guidance_digest: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class ReviewAttempt(BaseModel):
    """One reviewer execution attempt against an immutable review snapshot.

    This is execution/protocol evidence, not a substantive code-quality verdict.
    Runtime or protocol failures therefore cannot be mistaken for ReviewResult.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    review_attempt_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    run_id: str
    reviewer_run_id: str
    review_snapshot_id: str
    task_revision_id: str | None = None
    workspace_state_id: str
    reviewer_slot: int = Field(ge=0)
    runtime_attempt: int = Field(ge=1)
    protocol_version: str
    model_provider_id: str
    model_id: str
    reasoning_effort: str | None = None
    status: ReviewAttemptStatus = "running"
    failure_class: ReviewAttemptFailureClass | None = None
    failure_reason: str | None = None
    retryable: bool = False
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)


class ReviewResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    review_result_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    run_id: str
    reviewer_run_id: str
    review_snapshot_id: str
    task_revision_id: str | None = None
    workspace_state_id: str
    verdict: ReviewVerdict
    requirements: list[ReviewRequirementResult] = Field(default_factory=list)
    findings: list[ReviewFinding] = Field(default_factory=list)
    missing_tests: list[str] = Field(default_factory=list)
    residual_risks: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class EvidenceReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    receipt_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    run_id: str
    task_revision_id: str | None = None
    capability_id: str
    source_class: str
    subject: SubjectRef | None = None
    coverage: list[EvidenceCoverage] = Field(default_factory=list)
    request_digest: str
    provider: str | None = None
    origin: str | None = None
    source_manifest_id: str | None = None
    source_count: int = Field(default=0, ge=0)
    executed_at: datetime = Field(default_factory=utc_now)
    observed_at: datetime = Field(default_factory=utc_now)
    freshest_source_at: datetime | None = None
    trust_level: EvidenceTrust = "general"
    result_digest: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceRequirementEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    requirement_id: str
    status: EvidenceEvaluationStatus
    matching_receipt_ids: list[str] = Field(default_factory=list)
    rejected_receipt_ids: list[str] = Field(default_factory=list)
    reason: str | None = None


class EvidenceSet(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    evaluated_at: datetime = Field(default_factory=utc_now)
    requirements: list[EvidenceRequirementEvaluation] = Field(default_factory=list)
    missing_requirements: list[str] = Field(default_factory=list)
    stale_receipts: list[str] = Field(default_factory=list)
    wrong_subject_receipts: list[str] = Field(default_factory=list)
    insufficient_trust_receipts: list[str] = Field(default_factory=list)
    source_manifest_ids: list[str] = Field(default_factory=list)
    attribution_refs: list[str] = Field(default_factory=list)
    passed: bool = True


class ResourceScope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    capability: str
    resource_type: str
    resource_id: str
    constraints: dict[str, Any] = Field(default_factory=dict)


class SuccessCriterion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    description: str
    required: bool = True


class AcceptancePlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    required_commands: list[list[str]] = Field(default_factory=list)
    allowed_modified_paths: list[str] = Field(default_factory=list)
    forbidden_modified_paths: list[str] = Field(default_factory=list)
    required_artifacts: list[ArtifactKind] = Field(default_factory=list)
    require_diff: bool = False
    checks: list[str] = Field(default_factory=list)


class WorkspaceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    root: str
    repository: str | None = None
    base_ref: str = "main"
    worktree: str | None = None
    isolation_policy: str = "supervised_worktree"
    allowed_paths: list[str] = Field(default_factory=lambda: ["**"])
    forbidden_paths: list[str] = Field(default_factory=list)


class ExecutionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    command_policy: str = "safe-development"
    network_policy: str = "broker-only"
    environment_policy: str = "minimal"
    allowed_environment_keys: list[str] = Field(default_factory=list)


class RunLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_steps: int = Field(default=200, ge=1, le=10_000)
    max_wall_time_seconds: int = Field(default=3600, ge=1)
    max_tokens: int | None = Field(default=None, ge=1)
    max_cost: float | None = Field(default=None, ge=0)
    max_tool_calls: int = Field(default=500, ge=1, le=100_000)


class ResourceGrant(BaseModel):
    """Durable parent-to-child resource authority.

    Grant ceilings are local circuit breakers. Parent/global accounting charges
    actual terminal child spend and reserves only the unused capacity of active
    direct-child grants, so unused child capacity is reclaimable.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    grant_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    parent_run_id: str
    child_run_id: str
    limits: RunLimits
    state: ResourceGrantState = "active"
    created_at: datetime = Field(default_factory=utc_now)
    released_at: datetime | None = None


class AgentRunSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    session_id: str | None = None
    parent_run_id: str | None = None
    task: str
    objective: str = ""
    success_criteria: list[SuccessCriterion] = Field(default_factory=list)
    runtime: str = "pi"
    profile: str = "coding"
    model: ModelRef
    capabilities: list[str] = Field(default_factory=list)
    resource_scopes: list[ResourceScope] = Field(default_factory=list)
    external_capabilities: list[str] = Field(default_factory=list)
    request_mode: RequestModeSelection | None = None
    evidence_policy: EvidencePolicy = Field(default_factory=EvidencePolicy)
    supersedes_run_id: str | None = None
    workspace: WorkspaceSpec | None = None
    execution: ExecutionPolicy = Field(default_factory=ExecutionPolicy)
    limits: RunLimits = Field(default_factory=RunLimits)
    approval_policy: AgentApprovalPolicy = "ask_sensitive"
    quality_policy: QualityPolicy = "strict"
    quality_reserve_fraction: float = Field(default=0.25, ge=0.0, le=0.5)
    context_sources: list[str] = Field(default_factory=list)
    artifact_policy: str = "metadata_in_postgres_blobs_external"
    expected_artifacts: list[ArtifactKind] = Field(default_factory=list)
    persistence_policy: str = "postgresql"
    acceptance_plan: AcceptancePlan | None = None


class AgentEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    run_id: str
    sequence: int | None = None
    event_type: AgentEventType
    payload: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str | None = None
    causation_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class AgentArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    run_id: str
    kind: ArtifactKind
    name: str
    storage_ref: str | None = None
    checksum: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class AgentApproval(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    approval_id: str = Field(default_factory=lambda: secrets.token_urlsafe(24))
    run_id: str
    capability_id: str
    state: AgentApprovalState = "pending"
    request_payload: dict[str, Any] = Field(default_factory=dict)
    resolution_payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    resolved_at: datetime | None = None
    # PA-1.4: the capability's definition when the approval was issued. Kept
    # out of the API schema; rows stored before the binding read back as None
    # and cannot authorize.
    _definition_hash: str | None = PrivateAttr(default=None)

    def model_post_init(self, __context: Any) -> None:
        from app.capabilities.registry import capability_definition_hash

        self._definition_hash = capability_definition_hash(self.capability_id)

    @property
    def capability_definition_hash(self) -> str | None:
        return self._definition_hash

    def with_definition_hash(self, value: str | None) -> "AgentApproval":
        """This approval as stored: bound to ``value`` (None for rows issued before binding)."""
        bound = self.model_copy()
        bound._definition_hash = value
        return bound


class AgentRunCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    command_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    run_id: str
    command_type: AgentCommandType
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str = Field(default_factory=lambda: uuid.uuid4().hex)
    created_at: datetime = Field(default_factory=utc_now)


class AgentRunUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    input_tokens_reported: bool = False
    output_tokens_reported: bool = False


class AgentRunSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    spec: AgentRunSpec
    status: AgentRunStatus = "queued"
    desired_state: AgentDesiredState = "running"
    revision: int = 1
    worker_id: str | None = None
    superseded_by_run_id: str | None = None
    quality_stage: QualityStage | None = None
    quality_attempt: int = Field(default=0, ge=0)
    workspace_state_id: str | None = None
    usage: AgentRunUsage = Field(default_factory=AgentRunUsage)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class WorkerLease(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    worker_id: str
    lease_token: str
    lease_expires_at: datetime
    heartbeat_at: datetime
    revision: int


class SecurityInstruments(Protocol):
    """Resolves an exact US equity ticker to its canonical instrument id, or None."""

    def equity_instrument_id(self, ticker: str) -> str | None: ...


# Trading contributes this; without it evidence subjects use the ticker form.
SECURITY_INSTRUMENTS: Port[SecurityInstruments] = Port(
    "agent_runtime.security_instruments", SecurityInstruments, "at_most_one",
)
