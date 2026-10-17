"""Durable orchestration service for generalized agent runs."""
from __future__ import annotations

from .exception_logging import log_recovered_exception
from app.config.env import env_str as _env_str

from app.caching.bounded_cache import bounded_lru_cache
import os
import re
import threading
from typing import Any, Callable, ContextManager, TypedDict

from app.persistence.blob_store import default_blob_store
from app.persistence.contracts import BlobStore
from app.persistence.database import PostgresDatabase, default_database
from app.security.tenant_context import RequestTenant
from app.persistence.unit_of_work import unit_of_work

from .coding_quality_repository import PostgresCodingQualityRepository
from .evidence import (
    evaluate_evidence_set,
)
from .budget import AgentBudgetManager
from .contracts import (
    AgentArtifact,
    AgentEvent,
    AgentRunCommand,
    AgentRunSnapshot,
    AgentRunSpec,
    EvidencePolicy,
    TaskRevision,
    RunChangeSet,
    WorkspaceSpec,
)
from app.observability.agent_logging import configure_agent_debug_logging
from .pi_runtime import PiAgentRuntime
from .repository import PostgresAgentRunRepository
from .review_orchestration_core import consume_terminal_reviewer_in_repository
from .run_locks import RunLockRegistry
from .semantic_task_parser import (
    default_semantic_task_parser,
)
from .turn_plan import TurnPlan
from .workspace import WorkspaceAuthority
from app.prompts import prompt_template


ACCEPTANCE_RETRY_PROMPT_TEMPLATE = prompt_template(
    'agent_runtime.service_core.acceptance_retry_prompt', "2",
    (
        'Omnix acceptance did not pass ({joined}). Continue the same task; do not stop yet. '
        'Re-read the original user objective before making any repair: acceptance repair is not '
        'permission to change scope. Inspect the most recent failed or missing validation, '
        'correct the requested implementation or the relevant validation command as needed, and '
        'rerun the smallest task-relevant test/lint/typecheck until it exits successfully. For '
        'web UI work, the workspace command starts at the repository root, so use `npm --prefix '
        'web run build` or `npm --prefix web run test -- <focused-test>`; do '
        'not use Set-Location or shell directory changes. UI Playwright validation must select '
        'exactly one test by relative spec path and source line; do not run a whole spec, suite, '
        'or grep filter. Do not substitute an unrelated passing test, unrelated diff, or '
        'pre-existing workspace change for completion. This is automatic acceptance repair '
        'attempt {attempt}.'
    ),
)



_RETRYABLE_ACCEPTANCE_FAILURES = {
    "successful_test_command",
    "successful_typecheck_command",
    "successful_lint_command",
    "missing_diff_artifact",
    "missing_artifact:diff",
    "empty_diff_artifact",
    "modified_paths_not_task_relevant",
    "validation_not_task_relevant",
}

_CLARIFICATION_MARKER = re.compile(
    r"(?:^|\n)\s*(?:CLARIFICATION_REQUIRED|CLARIFY|NEED_CLARIFICATION)\s*[:\-]",
    re.I,
)
_CLARIFICATION_CUE = re.compile(
    r"(?:"
    r"\b(?:could|would|can)\s+you\s+(?:clarify|specify|tell|provide)\b|"
    r"\bplease\s+(?:clarify|specify|tell|provide)\b|"
    r"\b(?:i\s+)?need\s+(?:one\s+)?clarification\b|"
    r"\bno\s+specific\s+(?:implementation\s+)?request\b|"
    r"\bwhat\s+(?:behavior|change|visual\s+change|would\s+you\s+like)\b|"
    r"\bwhich\s+(?:one|option|behavior|change|file|target)\b"
    r")",
    re.I,
)


def _is_clarification_request(event: AgentEvent) -> bool:
    """Recognize a user-input request at the assistant turn boundary."""

    if event.event_type != "model.message" or event.payload.get("phase") != "message_end":
        return False
    if event.payload.get("clarification_suppressed") is True:
        return False
    if event.payload.get("requires_user_input") is True:
        return True
    text = str(event.payload.get("text") or "").strip()
    if not text:
        return False
    if _CLARIFICATION_MARKER.search(text):
        return True
    return bool(_CLARIFICATION_CUE.search(text)) and (
        "?" in text or bool(
            re.search(
                r"\b(?:no\s+specific\s+(?:implementation\s+)?request|need\s+(?:one\s+)?clarification)\b",
                text,
                re.I,
            )
        )
    )


class _DiffFileStat(TypedDict):
    path: str
    additions: int
    deletions: int


class _LeaseBoundRunRepository:
    """Bind worker state changes in a job to the run's current lease owner."""

    def __init__(self, repository, *, worker_id: str, lease_token: str) -> None:
        self._repository = repository
        self._worker_id = worker_id
        self._lease_token = lease_token

    def update_state(self, run_id: str, **kwargs):
        kwargs["worker_id"] = self._worker_id
        kwargs["lease_token"] = self._lease_token
        return self._repository.update_state(run_id, **kwargs)

    def __getattr__(self, name: str):
        return getattr(self._repository, name)


def _acceptance_retry_limit() -> int:
    raw = str(_env_str("OMNIX_AGENT_ACCEPTANCE_RETRY_LIMIT", "2") or "2").strip()
    try:
        return max(0, min(int(raw), 5))
    except ValueError:
        return 2


def _acceptance_failures_retryable(failures: list[str]) -> bool:
    if not failures:
        return False
    return all(
        failure in _RETRYABLE_ACCEPTANCE_FAILURES
        or failure.startswith("required_command:")
        for failure in failures
    )


def _progress_idle_timeout_seconds() -> int:
    # A live worker heartbeat is not agent progress. Keep the recovery window
    # short enough that a Pi turn which ended without a terminal event cannot
    # leave the run looking active for several minutes.
    raw = str(_env_str("OMNIX_AGENT_PROGRESS_IDLE_TIMEOUT_SECONDS", "120") or "120").strip()
    try:
        return max(60, min(int(raw), 86_400))
    except ValueError:
        return 120


def _stalled_recovery_limit() -> int:
    raw = str(_env_str("OMNIX_AGENT_STALLED_RECOVERY_LIMIT", "2") or "2").strip()
    try:
        return max(0, min(int(raw), 5))
    except ValueError:
        return 2


def _acceptance_retry_count(
    events: list[AgentEvent],
    task_revision_id: str | None,
) -> int:
    return sum(
        event.event_type == "acceptance.retry_requested"
        and event.payload.get("task_revision_id") == task_revision_id
        for event in events
    )


def _acceptance_retry_prompt(failures: list[str], *, attempt: int) -> str:
    joined = ", ".join(failures)
    return (
        ACCEPTANCE_RETRY_PROMPT_TEMPLATE.format(joined=joined, attempt=attempt)
    )


def _diff_file_stats(diff: str, modified_paths: list[str]) -> list[_DiffFileStat]:
    stats: dict[str, _DiffFileStat] = {
        path: {"path": path, "additions": 0, "deletions": 0}
        for path in modified_paths
    }
    current_path = ""
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            current_path = ""
            continue
        if line.startswith("--- ") or line.startswith("+++ "):
            candidate = line[4:].strip()
            if candidate != "/dev/null":
                if candidate.startswith(("a/", "b/")):
                    candidate = candidate[2:]
                current_path = candidate
                stats.setdefault(
                    current_path,
                    {"path": current_path, "additions": 0, "deletions": 0},
                )
            continue
        if not current_path:
            continue
        if line.startswith("+"):
            stats[current_path]["additions"] += 1
        elif line.startswith("-"):
            stats[current_path]["deletions"] += 1
    ordered_paths = [*modified_paths, *(path for path in stats if path not in modified_paths)]
    return [stats[path] for path in ordered_paths]


class AgentRunService:
    context = RequestTenant()
    def __init__(
        self,
        database: PostgresDatabase | None = None,
        *,
        context=None,
        job_store=None,
        pi_path: str | None = None,
        worker_id: str | None = None,
        blob_store: BlobStore | None = None,
        unit_of_work_fn: Callable[..., Any] | None = None,
        repository_factory: Callable[..., Any] | None = None,
        quality_repository_factory: Callable[..., Any] | None = None,
        workspace_authority_factory: Callable[..., Any] | None = None,
        semantic_task_parser: Callable[..., Any] | None = None,
        terminal_reviewer_consumer: Callable[..., Any] | None = None,
    ) -> None:
        configure_agent_debug_logging()
        self.database = database or default_database()
        self.context = context
        self.job_store = job_store
        self.unit_of_work = unit_of_work_fn or unit_of_work
        self._lease_tokens: dict[str, str] = {}
        self._lease_token_lock = threading.Lock()
        self.repository_factory = repository_factory or (
            lambda connection, tenant: PostgresAgentRunRepository(
                connection,
                tenant,
                lease_token_provider=self._lease_token_for,
            )
        )
        self.quality_repository_factory = (
            quality_repository_factory or PostgresCodingQualityRepository
        )
        self.workspace_authority_factory: Any = workspace_authority_factory or WorkspaceAuthority
        self.semantic_task_parser = semantic_task_parser or default_semantic_task_parser
        self.terminal_reviewer_consumer = (
            terminal_reviewer_consumer or consume_terminal_reviewer_in_repository
        )
        self.worker_id = worker_id or f"agent-worker:{os.getpid()}"
        self.blob_store = blob_store or default_blob_store()
        self.runtime = PiAgentRuntime(
            pi_path=pi_path or _env_str("OMNIX_PI_PATH", "pi"),
            event_sink=self._persist_runtime_event,
            run_token_issuer=self._issue_run_token,
        )
        self.budgets = AgentBudgetManager(self.database, context=self.context)
        self._run_locks = RunLockRegistry()
        self._supervisor_lock = threading.Lock()
        self._supervisor_started = False
        self._supervisor_stop = threading.Event()
        self._supervisor_thread: threading.Thread | None = None

    def _issue_run_token(self, spec: AgentRunSpec) -> str:
        """Token the Pi process uses for the broker and model gateway (WP-4.6)."""
        from app.security.run_tokens import capabilities_digest, issue_run_token

        return issue_run_token(
            run_id=spec.run_id,
            workspace_id=self.context.workspace_id,
            owner=self.worker_id,
            caps_digest=capabilities_digest(spec.capabilities, spec.external_capabilities),
        )

    def _run_lock(self, run_id: str) -> ContextManager[None]:
        return self._run_locks.hold(run_id)

    def _remember_lease(self, lease) -> None:
        with self._lease_token_lock:
            self._lease_tokens[lease.run_id] = lease.lease_token

    def _lease_token_for(self, run_id: str) -> str | None:
        with self._lease_token_lock:
            return self._lease_tokens.get(run_id)

    def _runtime_owns_run(self, run_id: str) -> bool:
        status = getattr(getattr(self, "runtime", None), "get_status", None)
        if not callable(status):
            return False
        try:
            return status(run_id) is not None
        except Exception as exc:
            log_recovered_exception("local runtime ownership lookup", exc, level="DEBUG")
            return False

    def start_supervisor(self) -> None:
        from . import run_supervision

        return run_supervision.start_supervisor(self)

    def stop_supervisor(self) -> None:
        from . import run_supervision

        return run_supervision.stop_supervisor(self)

    def _close_terminal_runtime(self, run_id: str) -> None:
        from . import run_lifecycle

        return run_lifecycle._close_terminal_runtime(self, run_id)

    def start(self, spec: AgentRunSpec) -> AgentRunSnapshot:
        return self.start_with_context(spec)

    def start_with_context(
        self,
        spec: AgentRunSpec,
        *,
        reference_context: str = "",
        reference_images: list[dict[str, str]] | None = None,
    ) -> AgentRunSnapshot:
        from . import run_lifecycle

        return run_lifecycle.start_with_context(self, spec, reference_context=reference_context, reference_images=reference_images)

    def _prepare_start_spec(self, spec: AgentRunSpec) -> AgentRunSpec:
        """Apply process-specific policy before a run becomes durable."""
        return spec

    def submit_start(
        self,
        spec: AgentRunSpec,
        *,
        job_store,
        reference_session_id: str | None = None,
        reference_message_id: str | None = None,
        reference_message_ids: list[str] | None = None,
        task_graph_run_id: str | None = None,
        task_graph_node_ids: list[str] | None = None,
    ) -> AgentRunSnapshot:
        from . import run_lifecycle

        return run_lifecycle.submit_start(self, spec, job_store=job_store, reference_session_id=reference_session_id, reference_message_id=reference_message_id, reference_message_ids=reference_message_ids, task_graph_run_id=task_graph_run_id, task_graph_node_ids=task_graph_node_ids)

    def prepare_workspace_job(
        self,
        run_id: str,
        *,
        job_store,
        reference_session_id: str | None = None,
        reference_message_id: str | None = None,
        reference_message_ids: list[str] | None = None,
        task_graph_run_id: str | None = None,
        task_graph_node_ids: list[str] | None = None,
    ) -> AgentRunSnapshot:
        from . import run_lifecycle

        return run_lifecycle.prepare_workspace_job(self, run_id, job_store=job_store, reference_session_id=reference_session_id, reference_message_id=reference_message_id, reference_message_ids=reference_message_ids, task_graph_run_id=task_graph_run_id, task_graph_node_ids=task_graph_node_ids)

    def start_prepared_run(
        self,
        run_id: str,
        *,
        reference_context: str = "",
        reference_images: list[dict[str, str]] | None = None,
    ) -> AgentRunSnapshot:
        from . import run_lifecycle

        return run_lifecycle.start_prepared_run(self, run_id, reference_context=reference_context, reference_images=reference_images)

    def start_child(self, parent_run_id: str, request) -> AgentRunSnapshot:
        from . import run_lifecycle

        return run_lifecycle.start_child(self, parent_run_id, request)

    def submit_child_start(self, parent_run_id: str, request, *, job_store) -> AgentRunSnapshot:
        from . import run_lifecycle

        return run_lifecycle.submit_child_start(self, parent_run_id, request, job_store=job_store)

    def _enqueue_promote_job(self, run_id: str, *, trigger_id: str) -> bool:
        from . import run_lifecycle

        return run_lifecycle._enqueue_promote_job(self, run_id, trigger_id=trigger_id)

    def process_promote_job(self, run_id: str) -> AgentRunSnapshot:
        from . import run_lifecycle

        return run_lifecycle.process_promote_job(self, run_id)

    def _create_child_start(self, parent_run_id: str, request):
        from . import run_lifecycle

        return run_lifecycle._create_child_start(self, parent_run_id, request)

    def _reserve_child_start(self, repository, parent, child_spec: AgentRunSpec) -> None:
        from . import run_lifecycle

        return run_lifecycle._reserve_child_start(self, repository, parent, child_spec)

    def _record_child_grant(self, repository, parent, child_spec: AgentRunSpec) -> None:
        return None

    def _persist_starting_run(
        self,
        repository: PostgresAgentRunRepository,
        issued: AgentRunSpec,
    ) -> AgentRunSnapshot:
        from . import run_lifecycle

        return run_lifecycle._persist_starting_run(self, repository, issued)

    def _launch_runtime(
        self,
        issued: AgentRunSpec,
        snapshot: AgentRunSnapshot,
        *,
        reference_context: str = "",
        reference_images: list[dict[str, str]] | None = None,
    ) -> AgentRunSnapshot:
        from . import run_lifecycle

        return run_lifecycle._launch_runtime(self, issued, snapshot, reference_context=reference_context, reference_images=reference_images)

    def get(self, run_id: str) -> AgentRunSnapshot | None:
        with self.unit_of_work(self.database) as work:
            repository = self.repository_factory(work.connection, self.context)
            snapshot = repository.get_run(run_id)
            work.rollback()
            return snapshot

    def command(self, command: AgentRunCommand) -> AgentRunSnapshot:
        return self.command_with_context(command)

    def command_with_context(
        self,
        command: AgentRunCommand,
        *,
        reference_context: str = "",
        reference_images: list[dict[str, str]] | None = None,
        turn_plan: TurnPlan | None = None,
    ) -> AgentRunSnapshot:
        from . import run_steering

        return run_steering.command_with_context(self, command, reference_context=reference_context, reference_images=reference_images, turn_plan=turn_plan)

    @staticmethod
    def _validate_run_spec_authority(spec: AgentRunSpec) -> None:
        from . import run_steering

        return run_steering._validate_run_spec_authority(spec)

    def _validate_evidence_authority(self, spec: AgentRunSpec) -> None:
        from . import run_steering

        return run_steering._validate_evidence_authority(self, spec)

    def _compile_steering(
        self,
        current: AgentRunSnapshot,
        command: AgentRunCommand,
        *,
        reference_context: str = "",
        turn_plan: TurnPlan | None = None,
    ) -> dict[str, Any]:
        from . import run_steering

        return run_steering._compile_steering(self, current, command, reference_context=reference_context, turn_plan=turn_plan)

    def _start_superseding_revision(
        self,
        current: AgentRunSnapshot,
        command: AgentRunCommand,
        revision: TaskRevision,
        replacement_spec: AgentRunSpec,
        *,
        reference_context: str = "",
        reference_images: list[dict[str, str]] | None = None,
    ) -> AgentRunSnapshot:
        from . import run_steering

        return run_steering._start_superseding_revision(self, current, command, revision, replacement_spec, reference_context=reference_context, reference_images=reference_images)

    def _submit_superseding_revision(
        self,
        current: AgentRunSnapshot,
        command: AgentRunCommand,
        revision: TaskRevision,
        replacement_spec: AgentRunSpec,
    ) -> AgentRunSnapshot:
        from . import run_steering

        return run_steering._submit_superseding_revision(self, current, command, revision, replacement_spec)

    def _mark_command_failed(self, command: AgentRunCommand, error: Exception) -> None:
        from . import run_steering

        return run_steering._mark_command_failed(self, command, error)

    def _apply_claimed_command(
        self,
        stored: AgentRunCommand,
        *,
        reference_context: str = "",
        reference_images: list[dict[str, str]] | None = None,
    ) -> AgentRunSnapshot:
        from . import run_steering

        return run_steering._apply_claimed_command(self, stored, reference_context=reference_context, reference_images=reference_images)

    def _cancel_descendants(self, run_id: str) -> None:
        from . import run_steering

        return run_steering._cancel_descendants(self, run_id)

    def events(self, run_id: str, *, after_sequence: int = 0) -> list[AgentEvent]:
        with self.unit_of_work(self.database) as work:
            repository = self.repository_factory(work.connection, self.context)
            rows = repository.list_events(run_id, after_sequence=after_sequence)
            work.rollback()
            return rows

    def approvals(self, run_id: str, *, state: str | None = None):
        with self.unit_of_work(self.database) as work:
            repository = self.repository_factory(work.connection, self.context)
            rows = repository.list_approvals(run_id, state=state)
            work.rollback()
            return rows

    def artifacts(self, run_id: str) -> list[AgentArtifact]:
        with self.unit_of_work(self.database) as work:
            repository = self.repository_factory(work.connection, self.context)
            rows = repository.list_artifacts(run_id)
            work.rollback()
            return rows

    def task_revisions(self, run_id: str) -> list[TaskRevision]:
        with self.unit_of_work(self.database) as work:
            repository = self.repository_factory(work.connection, self.context)
            rows = repository.list_task_revisions(run_id)
            work.rollback()
            return rows

    def evidence_receipts(self, run_id: str):
        with self.unit_of_work(self.database) as work:
            repository = self.repository_factory(work.connection, self.context)
            rows = repository.list_evidence_receipts(run_id)
            work.rollback()
            return rows

    def evidence_set(self, run_id: str):
        with self.unit_of_work(self.database) as work:
            repository = self.repository_factory(work.connection, self.context)
            snapshot = repository.get_run(run_id)
            if snapshot is None:
                work.rollback()
                raise KeyError(run_id)
            revision = repository.latest_task_revision(run_id)
            receipts = repository.list_evidence_receipts(run_id)
            work.rollback()
        policy = revision.evidence_decision.policy if revision is not None else snapshot.spec.evidence_policy
        receipts = self._receipts_for_revision(receipts, revision)
        return evaluate_evidence_set(run_id, policy, receipts)

    def _maybe_finalize_parent_in_repository(
        self,
        repository: PostgresAgentRunRepository,
        child_run_id: str,
    ) -> None:
        from . import run_acceptance

        return run_acceptance._maybe_finalize_parent_in_repository(self, repository, child_run_id)

    @staticmethod
    def _children_terminal_state(repository: PostgresAgentRunRepository, run_id: str) -> tuple[bool, bool]:
        from . import run_acceptance

        return run_acceptance._children_terminal_state(repository, run_id)

    def _finalize_acceptance(
        self,
        repository: PostgresAgentRunRepository,
        current: AgentRunSnapshot,
    ) -> None:
        from . import run_acceptance

        return run_acceptance._finalize_acceptance(self, repository, current)

    def _persist_runtime_event(self, event: AgentEvent) -> None:
        from . import run_acceptance

        return run_acceptance._persist_runtime_event(self, event)

    @staticmethod
    def _events_for_revision(
        events: list[AgentEvent],
        task_revision: TaskRevision | None,
    ) -> list[AgentEvent]:
        from . import run_acceptance

        return run_acceptance._events_for_revision(events, task_revision)

    @staticmethod
    def _artifacts_for_revision(
        artifacts: list[AgentArtifact],
        task_revision: TaskRevision | None,
    ) -> list[AgentArtifact]:
        from . import run_acceptance

        return run_acceptance._artifacts_for_revision(artifacts, task_revision)

    @staticmethod
    def _receipts_for_revision(receipts, task_revision: TaskRevision | None):
        from . import run_acceptance

        return run_acceptance._receipts_for_revision(receipts, task_revision)

    def _capture_workspace_baseline(
        self,
        repository: PostgresAgentRunRepository,
        spec: AgentRunSpec,
    ) -> None:
        from . import run_workspace

        return run_workspace._capture_workspace_baseline(self, repository, spec)

    def _quarantine_isolated_workspace_contamination(
        self,
        repository: PostgresAgentRunRepository,
        spec: AgentRunSpec,
        *,
        authority: WorkspaceAuthority | None = None,
    ) -> list[dict[str, str]]:
        from . import run_workspace

        return run_workspace._quarantine_isolated_workspace_contamination(self, repository, spec, authority=authority)

    def _capture_diff(
        self,
        repository: PostgresAgentRunRepository,
        spec: AgentRunSpec,
        *,
        task_revision_id: str | None = None,
        workspace_state_id: str | None = None,
    ) -> RunChangeSet | None:
        from . import run_workspace

        return run_workspace._capture_diff(self, repository, spec, task_revision_id=task_revision_id, workspace_state_id=workspace_state_id)

    def recover_orphaned_runs(self) -> list[str]:
        from . import run_supervision

        return run_supervision.recover_orphaned_runs(self)

    def _fail_recovery(self, run_id: str, exc: Exception) -> None:
        from . import run_supervision

        return run_supervision._fail_recovery(self, run_id, exc)

    def _supervisor_loop(self) -> None:
        from . import run_supervision

        return run_supervision._supervisor_loop(self)

    def _supervise_once(self) -> None:
        from . import run_supervision

        return run_supervision._supervise_once(self)

    def _deliver_pending_commands(self, run_id: str) -> None:
        from . import run_supervision

        return run_supervision._deliver_pending_commands(self, run_id)

    def _supervise_stalled_run(self, run_id: str) -> None:
        from . import run_supervision

        return run_supervision._supervise_stalled_run(self, run_id)

    def heartbeat(self, run_id: str, *, ttl_seconds: int = 60) -> None:
        from . import run_supervision

        return run_supervision.heartbeat(self, run_id, ttl_seconds=ttl_seconds)

    @staticmethod
    def _github_origin_repository(repository: str) -> str:
        from . import run_workspace

        return run_workspace._github_origin_repository(repository)

    @staticmethod
    def _resolve_repository_commit(repository: str, ref: str) -> str:
        from . import run_workspace

        return run_workspace._resolve_repository_commit(repository, ref)

    @classmethod
    def _bind_repository_evidence_policy(
        cls,
        policy: EvidencePolicy,
        *,
        workspace: WorkspaceSpec,
        repository_name: str,
    ) -> EvidencePolicy:
        from . import run_workspace

        return run_workspace._bind_repository_evidence_policy(cls, policy, workspace=workspace, repository_name=repository_name)

    @classmethod
    def _bind_github_repository_authority(
        cls,
        spec: AgentRunSpec,
    ) -> AgentRunSpec:
        from . import run_workspace

        return run_workspace._bind_github_repository_authority(cls, spec)

    def _promote_accepted_workspace(
        self,
        repository: PostgresAgentRunRepository,
        current: AgentRunSnapshot,
        *,
        task_revision_id: str | None,
        workspace_state_id: str | None,
    ) -> dict[str, Any] | None:
        from . import run_workspace

        return run_workspace._promote_accepted_workspace(self, repository, current, task_revision_id=task_revision_id, workspace_state_id=workspace_state_id)

    @staticmethod
    def workspace_preview_launcher(spec: AgentRunSpec):
        from . import run_workspace

        return run_workspace.workspace_preview_launcher(spec)

    def _prepare_workspace(self, spec: AgentRunSpec) -> AgentRunSpec:
        from . import run_workspace

        return run_workspace._prepare_workspace(self, spec)


@bounded_lru_cache(max_entries=1, ttl_seconds=3600.0)
def default_agent_run_service() -> AgentRunService:
    return AgentRunService()
