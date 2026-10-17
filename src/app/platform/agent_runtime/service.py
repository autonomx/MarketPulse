"""Quality-aware orchestration facade over the stable generalized Agent service core.

The Phase 1-19 durable orchestration remains in service_core. This layer keeps
TaskRevision contracts, exact workspace identity, and bounded repair convergence.
Pi owns ordinary planning, validation, and self-review inside its coding loop;
Omnix remains the only completion authority for deterministic final acceptance.
"""
from __future__ import annotations

from app.config.env import env_str as _env_str

from app.caching.bounded_cache import bounded_lru_cache
import hashlib
import json
import os
from pathlib import Path
import tempfile


from app.capabilities import browser_capability_ids
from .profiles import profile_produces_diff
from .coding_quality import (
    CODING_INDEPENDENT_REVIEW_PHASE_ENABLED,
    compile_task_engineering_contract,
    missing_final_validations,
    review_payload_from_text,
)
from .coding_quality_repository import PostgresCodingQualityRepository
from .contracts import (
    AgentEvent,
    AgentRunCommand,
    AgentRunSnapshot,
    AgentRunSpec,
    ReviewResult,
    ReviewSnapshot,
    RunChangeSet,
    SelfReviewResult,
    TaskRevision,
)
from .evidence import EvidenceCompilationError
from .model_fidelity import resolve_run_model_fidelity
from .repository import PostgresAgentRunRepository
from .quality_recovery import reconcile_orphaned_quality_reviews
from .resource_grants import PostgresResourceGrantRepository
from .service_core import (
    AgentRunService as _CoreAgentRunService,
    _acceptance_retry_count as _acceptance_retry_count,
)
from .task_revision_quality import (
    hydrate_task_revisions,
    persist_task_revision_contract,
)
from app.prompts import prompt_template
from typing import Any


REQUEST_IMPLEMENTATION_CONTINUATION_TEMPLATE = prompt_template(
    'agent_runtime.service.request_implementation_continuation', "1",
    (
        'Omnix does not yet have a reviewable implementation candidate for quality attempt '
        '{attempt}. Authoritative objective: {effective_objective}\n'
        'Candidate gate failures: {failures}\n'
        'Do not self-review or declare completion. Re-read the authoritative objective, inspect '
        'the actual target surface and current diff, and carry out the requested implementation '
        'now. Make only task-scoped changes. If this is a user-visible UI task, locate the exact '
        'control/surface and verify the requested visible outcome with governed browser '
        'evidence. Then inspect the complete diff and run the required final-state validation '
        'before settling.'
    ),
)

REQUEST_VALIDATION_REPAIR_TEMPLATE = prompt_template(
    'agent_runtime.service.request_validation_repair', "1",
    (
        'The exact candidate failed required validation. Treat this as implementation evidence, '
        'not a reason to rerun the same candidate indefinitely. Diagnose and repair the cause. '
        'The repair must produce a new WorkspaceState before Omnix will authorize fresh '
        'validation.\n'
        'Validation failures JSON: {failure_rows}'
    ),
)



_TERMINAL = {"completed", "failed", "cancelled"}
_BLOCKED_SETTLE = {
    "waiting_for_approval",
    "waiting_for_input",
    "waiting_for_children",
    "pause_requested",
    "paused",
    "cancel_requested",
    "cancelled",
}

_QUALITY_DEFAULT_MAX_STEPS = {
    "standard": 350,
    "strict": 500,
    "critical": 750,
}

_BROWSER_VALIDATION_CAPABILITIES = frozenset(browser_capability_ids())


def _quality_sized_run_spec(spec: AgentRunSpec) -> AgentRunSpec:
    """Give default coding runs enough global authority to converge through repair.

    The generic 200-step default is too small for a normal strict repair cycle.
    Only implicit defaults are raised; any caller-supplied RunLimits remain
    authoritative.
    """

    if (
        not profile_produces_diff(spec.profile)
        or "diff" not in spec.expected_artifacts
        or spec.quality_policy == "off"
        or "limits" in spec.model_fields_set
    ):
        return spec
    max_steps = _QUALITY_DEFAULT_MAX_STEPS.get(spec.quality_policy, 500)
    max_tool_calls = max(spec.limits.max_tool_calls, int(max_steps * 2.5))
    limits = spec.limits.model_copy(
        update={
            "max_steps": max_steps,
            "max_tool_calls": max_tool_calls,
        }
    )
    return spec.model_copy(update={"limits": limits})


def _is_structured_self_review_message(event: AgentEvent) -> bool:
    """Recognize the terminal payload of the mandatory self-review turn.

    Pi emits ``run.settled`` for the initial implementation turn, but some
    RPC sessions only emit ``message_end`` after a quality-stage resume. The
    self-review prompt requires one JSON object and no tools, so this is a
    safe, deterministic fallback signal for advancing that stage.
    """

    if event.event_type != "model.message" or event.payload.get("phase") not in {"message_end", "turn_end"}:
        return False
    text = str(event.payload.get("text") or "").strip()
    return bool(review_payload_from_text(text))


def _is_terminal_self_review_message(event: AgentEvent) -> bool:
    """Return true for a visible terminal assistant response.

    This is intentionally broader than the structured-verdict predicate so
    observability can still recognize malformed self-review output. Malformed
    output does not itself settle a self-review turn; Omnix waits for the Pi
    settle boundary (or the stalled-run supervisor) before retrying the
    transport protocol. That prevents a trailing ``run.settled`` from consuming
    a second retry after a retry prompt has already been dispatched.
    """

    return (
        event.event_type == "model.message"
        and event.payload.get("phase") in {"message_end", "turn_end"}
        and bool(str(event.payload.get("text") or "").strip())
    )


def _terminal_message_settles_quality_stage(event: AgentEvent, stage: str) -> bool:
    """Treat only a structured verdict in the explicit self-review stage as a boundary.

    Normal Pi sessions emit ``run.settled`` after terminal assistant text. If
    malformed prose were allowed to settle the self-review stage immediately,
    the retry prompt could be sent before that trailing settle event and the
    same turn could burn two protocol retries. A structured verdict may advance
    early only after Omnix has durably entered ``self_review``. Structured JSON
    emitted by implementation or repair turns is never self-review evidence;
    those stages advance only at the Pi settle boundary.
    """

    return stage == "self_review" and _is_structured_self_review_message(event)


def _self_review_payload_is_protocol_valid(text: str, revision: TaskRevision) -> bool:
    """Validate the transport/schema contract separately from review quality.

    A missing or malformed payload is a protocol failure, not evidence that the
    implementation itself failed review. Keep that distinction explicit so
    protocol retries do not consume the bounded repair attempts.
    """

    payload = review_payload_from_text(text)
    if not payload:
        return False
    for field in ("requirements", "findings", "missing_tests", "residual_risks"):
        if not isinstance(payload.get(field), list):
            return False

    required_ids = {item.id for item in revision.requirements if item.required}
    observed_ids: set[str] = set()
    for item in payload["requirements"]:
        if not isinstance(item, dict):
            return False
        requirement_id = str(item.get("requirement_id") or "").strip()
        status = str(item.get("status") or "").strip()
        if (
            not requirement_id
            or requirement_id in observed_ids
            or status not in {"satisfied", "partial", "missing", "not_applicable"}
        ):
            return False
        observed_ids.add(requirement_id)

    for finding in payload["findings"]:
        if not isinstance(finding, dict) or not str(finding.get("problem") or "").strip():
            return False
        severity = str(finding.get("severity") or "medium").strip()
        if severity not in {"blocker", "high", "medium", "low"}:
            return False

    if any(not isinstance(item, str) for item in payload["missing_tests"]):
        return False
    if any(not isinstance(item, str) for item in payload["residual_risks"]):
        return False
    return required_ids.issubset(observed_ids)


def _self_review_protocol_retry_limit() -> int:
    raw = str(_env_str("OMNIX_AGENT_SELF_REVIEW_PROTOCOL_RETRIES", "2") or "2").strip()
    try:
        value = int(raw)
    except ValueError:
        value = 2
    return max(0, min(value, 5))


def _default_review_root(spec: AgentRunSpec) -> str:
    """Choose a review root that leaves room for repository-relative paths.

    Windows Git worktrees can reject otherwise valid repository paths when the
    temporary root already consumes most of the MAX_PATH budget. Review
    snapshots only need to be outside the active checkout, so a short sibling
    directory is a safer default on Windows. Keep the longer temp-root default
    for other platforms and retain the explicit environment override at the
    call site.
    """

    if os.name == "nt" and spec.workspace is not None:
        repository = spec.workspace.repository or spec.workspace.root
        if repository:
            try:
                return str(Path(repository).expanduser().resolve().parent / ".omnix-agent-review")
            except (OSError, RuntimeError):
                pass
    return str(Path(tempfile.gettempdir()) / "omnix-agent-review-snapshots")


def _self_review_protocol_retry_count(
    repository: PostgresAgentRunRepository,
    *,
    run_id: str,
    attempt: int,
    task_revision_id: str,
    workspace_state_id: str,
    page_size: int = 5000,
) -> int:
    """Count protocol retries for one exact quality attempt and workspace state."""

    after_sequence = 0
    count = 0
    while True:
        batch = repository.list_events(
            run_id,
            after_sequence=after_sequence,
            limit=page_size,
        )
        if not batch:
            break
        for item in batch:
            if item.event_type != "quality.self_review_protocol_retry_requested":
                continue
            payload = item.payload
            if (
                int(payload.get("quality_attempt") or 0) == attempt
                and str(payload.get("task_revision_id") or "") == task_revision_id
                and str(payload.get("workspace_state_id") or "") == workspace_state_id
            ):
                count += 1
        if len(batch) < page_size:
            break
        sequence = batch[-1].sequence
        if sequence is None or int(sequence) <= after_sequence:
            break
        after_sequence = int(sequence)
    return count


def _validation_retry_limit() -> int:
    raw = str(_env_str("OMNIX_AGENT_VALIDATION_RETRIES", "2") or "2").strip()
    try:
        value = int(raw)
    except ValueError:
        value = 2
    return max(0, min(value, 5))


def _quality_events(repository: PostgresAgentRunRepository, run_id: str) -> list[AgentEvent]:
    events: list[AgentEvent] = []
    after_sequence = 0
    page_size = 1000
    while True:
        page = repository.list_events(run_id, after_sequence=after_sequence, limit=page_size)
        if not page:
            break
        events.extend(page)
        next_sequence = max(int(item.sequence or after_sequence) for item in page)
        if next_sequence <= after_sequence:
            break
        after_sequence = next_sequence
        if len(page) < page_size:
            break
    return events


def _validation_event_count(
    repository: PostgresAgentRunRepository,
    *,
    run_id: str,
    event_type: str,
    task_revision_id: str,
    workspace_state_id: str,
    fingerprint: str | None = None,
) -> int:
    count = 0
    for event in _quality_events(repository, run_id):
        if event.event_type != event_type:
            continue
        payload = event.payload
        if str(payload.get("task_revision_id") or "") != task_revision_id:
            continue
        if str(payload.get("workspace_state_id") or "") != workspace_state_id:
            continue
        if fingerprint is not None and str(payload.get("fingerprint") or "") != fingerprint:
            continue
        count += 1
    return count


def _validation_failure_fingerprint(rows) -> str:
    material = [
        {
            "validation_id": item.validation_id,
            "outcome": item.outcome,
            "output_digest": item.output_digest,
        }
        for item in sorted(rows, key=lambda row: (row.validation_id, row.result_id))
    ]
    return hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:24]


def _implementation_candidate_retry_limit() -> int:
    raw = str(_env_str("OMNIX_AGENT_IMPLEMENTATION_SETTLE_RETRIES", "2") or "2").strip()
    try:
        value = int(raw)
    except ValueError:
        value = 2
    return max(0, min(value, 5))


def _implementation_candidate_retry_count(
    repository: PostgresAgentRunRepository,
    *,
    run_id: str,
    attempt: int,
    task_revision_id: str,
    page_size: int = 5000,
) -> int:
    """Count same-attempt continuations where Pi settled without a reviewable implementation."""
    after_sequence = 0
    count = 0
    while True:
        batch = repository.list_events(run_id, after_sequence=after_sequence, limit=page_size)
        if not batch:
            break
        for item in batch:
            if item.event_type != "quality.implementation_continuation_requested":
                continue
            payload = item.payload
            if (
                int(payload.get("quality_attempt") or 0) == attempt
                and str(payload.get("task_revision_id") or "") == task_revision_id
            ):
                count += 1
        if len(batch) < page_size:
            break
        sequence = batch[-1].sequence
        if sequence is None or int(sequence) <= after_sequence:
            break
        after_sequence = int(sequence)
    return count


def _implementation_candidate_failures(
    diff_artifact,
    validations=(),
    *,
    allow_browser_noop: bool = False,
) -> list[str]:
    """Return deterministic reasons why the current state is not ready for review."""
    if diff_artifact is None:
        return ["missing_run_owned_diff"]
    metadata = diff_artifact.metadata if isinstance(getattr(diff_artifact, "metadata", None), dict) else {}
    failures: list[str] = []
    conflicts = metadata.get("baseline_conflicts")
    if isinstance(conflicts, list) and conflicts:
        failures.append("run_owned_diff_conflicts_with_workspace_baseline")
    modified_paths = metadata.get("modified_paths")
    paths = modified_paths if isinstance(modified_paths, list) else []
    try:
        byte_size = int(metadata.get("byte_size") or 0)
    except (TypeError, ValueError):
        byte_size = 0
    if not (paths and byte_size > 0):
        browser_proof = allow_browser_noop and any(
            getattr(item, "validation_id", None) == "browser-validation"
            and bool(getattr(item, "success", False))
            for item in validations
        )
        if not browser_proof:
            failures.append("empty_run_owned_diff_without_already_satisfied_proof")
    return failures


def _pre_review_gate(
    revision: TaskRevision,
    validations,
    *,
    workspace_state_id: str,
    diff_artifact,
) -> tuple[str, list]:
    """Order proof gates so self-review cannot begin before implementation truth."""
    missing = missing_final_validations(revision, validations, workspace_state_id=workspace_state_id)
    if missing:
        return "validating", list(missing)
    current_validations = [
        item for item in validations
        if getattr(item, "workspace_state_id", None) == workspace_state_id
    ]
    allow_browser_noop = any(
        item.id == "browser-validation" and item.required
        for item in revision.validation_plan
    )
    candidate_failures = _implementation_candidate_failures(
        diff_artifact,
        current_validations,
        allow_browser_noop=allow_browser_noop,
    )
    if candidate_failures:
        return "implementing", candidate_failures
    return "ready", []


def _self_review_response_text(
    events: list[AgentEvent],
    *,
    attempt: int,
    task_revision_id: str,
) -> str:
    """Select only the response emitted after this self-review request."""

    marker = -1
    for index, item in enumerate(events):
        if item.event_type != "quality.stage":
            continue
        if str(item.payload.get("stage") or "") != "self_review":
            continue
        if int(item.payload.get("attempt") or 0) != attempt:
            continue
        if str(item.payload.get("task_revision_id") or "") != task_revision_id:
            continue
        marker = index
    if marker < 0:
        return ""
    return next(
        (
            str(item.payload.get("text") or "").strip()
            for item in reversed(events[marker + 1 :])
            if item.event_type == "model.message"
            and item.payload.get("phase") in {"message_end", "turn_end"}
            and str(item.payload.get("text") or "").strip()
        ),
        "",
    )


def _self_review_response_from_repository(
    repository: PostgresAgentRunRepository,
    *,
    run_id: str,
    attempt: int,
    task_revision_id: str,
    workspace_state_id: str,
    page_size: int = 5000,
) -> str:
    """Read only the response emitted inside the exact self-review stage window.

    Every quality-stage marker closes the previous window. Only the most recent
    marker that exactly matches revision, attempt, and workspace state can make
    subsequent terminal assistant text eligible as self-review evidence. This
    prevents a later retry/state/phase from being attributed to stale review
    identity.
    """

    after_sequence = 0
    response = ""
    marker_seen = False
    while True:
        batch = repository.list_events(
            run_id,
            after_sequence=after_sequence,
            limit=page_size,
        )
        if not batch:
            break
        for item in batch:
            if item.event_type == "quality.stage":
                marker_seen = (
                    str(item.payload.get("stage") or "") == "self_review"
                    and int(item.payload.get("attempt") or 0) == attempt
                    and str(item.payload.get("task_revision_id") or "") == task_revision_id
                    and str(item.payload.get("workspace_state_id") or "") == workspace_state_id
                )
                response = ""
                continue
            if (
                marker_seen
                and item.event_type == "model.message"
                and item.payload.get("phase") in {"message_end", "turn_end"}
            ):
                text = str(item.payload.get("text") or "").strip()
                if text:
                    response = text
        sequence = batch[-1].sequence
        if len(batch) < page_size or sequence is None or int(sequence) <= after_sequence:
            break
        after_sequence = int(sequence)
    return response if marker_seen else ""


class AgentRunService(_CoreAgentRunService):
    """Durable generalized Agent service with coding completion acceptance."""

    @staticmethod
    def _quality_enabled(spec: AgentRunSpec) -> bool:
        from . import quality_state_machine

        return quality_state_machine._quality_enabled(spec)

    @staticmethod
    def _validate_run_spec_authority(spec: AgentRunSpec) -> None:
        """Reject UI quality runs that cannot execute their required browser proof."""

        _CoreAgentRunService._validate_run_spec_authority(spec)
        if not AgentRunService._quality_enabled(spec):
            return

        _requirements, _constraints, validation_plan = compile_task_engineering_contract(
            spec.objective or spec.task,
            spec.success_criteria,
            profile=spec.profile,
            mutating=True,
        )
        browser_required = any(
            item.id == "browser-validation" and item.required
            for item in validation_plan
        )
        if not browser_required:
            return

        missing = sorted(
            _BROWSER_VALIDATION_CAPABILITIES.difference(set(spec.external_capabilities))
        )
        if missing:
            raise EvidenceCompilationError(
                "browser_validation_authority_unavailable",
                "UI quality validation requires the complete governed browser capability set; "
                f"missing: {', '.join(missing)}",
            )

    def _supervise_once(self) -> None:
        # Review reconciliation is idempotent and now also drives same-snapshot
        # reviewer runtime/protocol retries, not only orphan recovery.
        reconcile_orphaned_quality_reviews(self)
        super()._supervise_once()

    def _prepare_start_spec(self, spec: AgentRunSpec) -> AgentRunSpec:
        # Resolve provider/model/reasoning before the durable RunSpec is written,
        # so observability and recovery see the exact configuration Pi receives.
        return resolve_run_model_fidelity(_quality_sized_run_spec(spec))

    def _reserve_child_start(self, repository, parent, child_spec: AgentRunSpec) -> None:
        parent_usage = repository.get_usage(parent.run_id)
        grants = PostgresResourceGrantRepository(repository.connection, self.context)
        protected_fraction = (
            parent.spec.quality_reserve_fraction
            if self._quality_enabled(parent.spec) and CODING_INDEPENDENT_REVIEW_PHASE_ENABLED
            else 0.0
        )
        grants.assert_can_grant(
            parent,
            child_spec.limits,
            parent_usage=parent_usage,
            protected_fraction=protected_fraction,
        )

    def _record_child_grant(self, repository, parent, child_spec: AgentRunSpec) -> None:
        grants = PostgresResourceGrantRepository(repository.connection, self.context)
        grants.add_grant(
            parent_run_id=parent.run_id,
            child_run_id=child_spec.run_id,
            limits=child_spec.limits,
        )

    def _persist_starting_run(
        self,
        repository: PostgresAgentRunRepository,
        issued: AgentRunSpec,
    ) -> AgentRunSnapshot:
        snapshot = super()._persist_starting_run(repository, issued)
        revision = repository.latest_task_revision(issued.run_id)
        if revision is not None:
            mutating = "diff" in revision.expected_artifacts
            requirements, constraints, validation_plan = compile_task_engineering_contract(
                revision.effective_objective,
                revision.effective_success_criteria,
                profile=issued.profile,
                mutating=mutating,
            )
            revision = revision.model_copy(
                update={
                    "requirements": requirements,
                    "constraints": constraints,
                    "validation_plan": validation_plan,
                }
            )
            persist_task_revision_contract(repository.connection, self.context, revision)
            if self._quality_enabled(issued):
                quality = self.quality_repository_factory(repository.connection, self.context)
                quality.set_stage(
                    issued.run_id,
                    stage="implementing",
                    attempt=1,
                    task_revision_id=revision.revision_id,
                )
                repository.append_event(
                    AgentEvent(
                        run_id=issued.run_id,
                        event_type="quality.stage",
                        payload={
                            "stage": "implementing",
                            "attempt": 1,
                            "task_revision_id": revision.revision_id,
                        },
                    )
                )
        return snapshot

    def get(self, run_id: str) -> AgentRunSnapshot | None:
        snapshot = super().get(run_id)
        if snapshot is None:
            return None
        try:
            with self.unit_of_work(self.database) as work:
                quality = self.quality_repository_factory(work.connection, self.context)
                stage = quality.get_stage(run_id)
                work.rollback()
        except Exception:
            return snapshot
        if stage is None:
            return snapshot
        return snapshot.model_copy(
            update={
                "quality_stage": stage.get("stage"),
                "quality_attempt": int(stage.get("attempt") or 0),
                "workspace_state_id": stage.get("workspace_state_id"),
            }
        )

    def task_revisions(self, run_id: str) -> list[TaskRevision]:
        with self.unit_of_work(self.database) as work:
            repository = self.repository_factory(work.connection, self.context)
            rows = hydrate_task_revisions(
                work.connection,
                self.context,
                repository.list_task_revisions(run_id),
            )
            work.rollback()
        return rows

    def quality_state(self, run_id: str) -> dict[str, Any] | None:
        with self.unit_of_work(self.database) as work:
            if self.repository_factory(work.connection, self.context).get_run(run_id) is None:
                work.rollback()
                raise KeyError(run_id)
            row = self.quality_repository_factory(work.connection, self.context).get_stage(run_id)
            work.rollback()
        return row

    def validation_results(self, run_id: str):
        with self.unit_of_work(self.database) as work:
            rows = self.quality_repository_factory(work.connection, self.context).list_validation_results(run_id)
            work.rollback()
        return rows

    def self_review_results(self, run_id: str):
        with self.unit_of_work(self.database) as work:
            rows = self.quality_repository_factory(work.connection, self.context).list_self_review_results(run_id)
            work.rollback()
        return rows

    def review_results(self, run_id: str):
        with self.unit_of_work(self.database) as work:
            rows = self.quality_repository_factory(work.connection, self.context).list_review_results(run_id)
            work.rollback()
        return rows

    def review_attempts(self, run_id: str):
        with self.unit_of_work(self.database) as work:
            rows = self.quality_repository_factory(work.connection, self.context).list_review_attempts(run_id)
            work.rollback()
        return rows

    def run_change_set(self, run_id: str) -> tuple[RunChangeSet, str]:
        from . import quality_acceptance

        return quality_acceptance.run_change_set(self, run_id)

    def command_with_context(
        self,
        command: AgentRunCommand,
        *,
        reference_context: str = "",
        reference_images: list[dict[str, str]] | None = None,
        turn_plan=None,
    ) -> AgentRunSnapshot:
        from . import quality_acceptance

        return quality_acceptance.command_with_context(self, command, reference_context=reference_context, reference_images=reference_images, turn_plan=turn_plan)

    def _current_revision(
        self,
        repository: PostgresAgentRunRepository,
        run_id: str,
    ) -> TaskRevision | None:
        from . import quality_acceptance

        return quality_acceptance._current_revision(self, repository, run_id)

    def _set_quality_stage(
        self,
        repository: PostgresAgentRunRepository,
        *,
        run_id: str,
        stage: str,
        attempt: int,
        task_revision_id: str | None,
        workspace_state_id: str | None = None,
        reason: str | None = None,
    ) -> None:
        from . import quality_state_machine

        return quality_state_machine._set_quality_stage(self, repository, run_id=run_id, stage=stage, attempt=attempt, task_revision_id=task_revision_id, workspace_state_id=workspace_state_id, reason=reason)

    def _record_workspace_tool_result(self, event: AgentEvent) -> None:
        from . import quality_acceptance

        return quality_acceptance._record_workspace_tool_result(self, event)

    def _reconcile_change_set_validation(
        self,
        repository: PostgresAgentRunRepository,
        current: AgentRunSnapshot,
        revision: TaskRevision,
        quality: PostgresCodingQualityRepository,
        *,
        workspace_state_id: str,
    ) -> None:
        from . import quality_acceptance

        return quality_acceptance._reconcile_change_set_validation(self, repository, current, revision, quality, workspace_state_id=workspace_state_id)

    def _persist_runtime_event(self, event: AgentEvent) -> None:
        from . import quality_acceptance

        return quality_acceptance._persist_runtime_event(self, event)

    def _queue_quality_resume(
        self,
        repository: PostgresAgentRunRepository,
        *,
        run_id: str,
        prompt: str,
        idempotency_key: str,
        quality_stage: str,
        quality_attempt: int,
        task_revision_id: str | None,
        workspace_state_id: str | None,
    ) -> tuple | None:
        from . import quality_state_machine

        return quality_state_machine._queue_quality_resume(self, repository, run_id=run_id, prompt=prompt, idempotency_key=idempotency_key, quality_stage=quality_stage, quality_attempt=quality_attempt, task_revision_id=task_revision_id, workspace_state_id=workspace_state_id)

    def _request_implementation_continuation(
        self,
        repository: PostgresAgentRunRepository,
        current: AgentRunSnapshot,
        revision: TaskRevision,
        *,
        attempt: int,
        workspace_state_id: str,
        failures: list[str],
        prior_stage: str,
    ) -> tuple | None:
        from . import quality_state_machine

        return quality_state_machine._request_implementation_continuation(self, repository, current, revision, attempt=attempt, workspace_state_id=workspace_state_id, failures=failures, prior_stage=prior_stage)

    def _request_self_review_protocol_retry(
        self,
        repository: PostgresAgentRunRepository,
        current: AgentRunSnapshot,
        revision: TaskRevision,
        quality: PostgresCodingQualityRepository,
        *,
        attempt: int,
        workspace_state_id: str,
        response_text: str,
    ) -> tuple | None:
        from . import quality_state_machine

        return quality_state_machine._request_self_review_protocol_retry(self, repository, current, revision, quality, attempt=attempt, workspace_state_id=workspace_state_id, response_text=response_text)

    def _request_validation_execution(
        self,
        repository: PostgresAgentRunRepository,
        current: AgentRunSnapshot,
        revision: TaskRevision,
        *,
        attempt: int,
        workspace_state_id: str,
        missing,
    ) -> tuple | None:
        from . import quality_state_machine

        return quality_state_machine._request_validation_execution(self, repository, current, revision, attempt=attempt, workspace_state_id=workspace_state_id, missing=missing)

    def _request_validation_retry(
        self,
        repository: PostgresAgentRunRepository,
        current: AgentRunSnapshot,
        revision: TaskRevision,
        *,
        attempt: int,
        workspace_state_id: str,
        failures,
    ) -> tuple | None:
        from . import quality_state_machine

        return quality_state_machine._request_validation_retry(self, repository, current, revision, attempt=attempt, workspace_state_id=workspace_state_id, failures=failures)

    def _request_validation_repair(
        self,
        repository: PostgresAgentRunRepository,
        current: AgentRunSnapshot,
        revision: TaskRevision,
        *,
        attempt: int,
        workspace_state_id: str,
        failures,
    ) -> tuple | None:
        from . import quality_state_machine

        return quality_state_machine._request_validation_repair(self, repository, current, revision, attempt=attempt, workspace_state_id=workspace_state_id, failures=failures)

    def _advance_quality_on_settle(
        self,
        repository: PostgresAgentRunRepository,
        current: AgentRunSnapshot,
    ) -> tuple | None:
        from . import quality_state_machine

        return quality_state_machine._advance_quality_on_settle(self, repository, current)

    def _execute_quality_action(self, action: tuple | None) -> None:
        from . import quality_state_machine

        return quality_state_machine._execute_quality_action(self, action)

    def _dispatch_pending_quality_commands(self, run_id: str, *, include_parent: bool = False) -> None:
        from . import quality_state_machine

        return quality_state_machine._dispatch_pending_quality_commands(self, run_id, include_parent=include_parent)

    def _launch_reviewer_children(self, parent_run_id: str, snapshot_id: str, count: int) -> None:
        from . import quality_state_machine

        return quality_state_machine._launch_reviewer_children(self, parent_run_id, snapshot_id, count)

    @staticmethod
    def _review_snapshot_id_from_child(child: AgentRunSnapshot) -> str | None:
        from . import quality_state_machine

        return quality_state_machine._review_snapshot_id_from_child(child)

    def _review_result_from_child(
        self,
        repository: PostgresAgentRunRepository,
        child: AgentRunSnapshot,
        snapshot: ReviewSnapshot,
    ) -> ReviewResult | None:
        from . import quality_state_machine

        return quality_state_machine._review_result_from_child(self, repository, child, snapshot)

    def _maybe_finalize_parent_in_repository(
        self,
        repository: PostgresAgentRunRepository,
        child_run_id: str,
    ) -> None:
        from . import quality_acceptance

        return quality_acceptance._maybe_finalize_parent_in_repository(self, repository, child_run_id)

    def _request_quality_workspace_refresh(
        self,
        repository: PostgresAgentRunRepository,
        current: AgentRunSnapshot,
        revision: TaskRevision,
        *,
        current_workspace_state_id: str,
        prior_workspace_state_id: str,
    ) -> tuple | None:
        from . import quality_state_machine

        return quality_state_machine._request_quality_workspace_refresh(self, repository, current, revision, current_workspace_state_id=current_workspace_state_id, prior_workspace_state_id=prior_workspace_state_id)

    def _request_quality_repair(
        self,
        repository: PostgresAgentRunRepository,
        current: AgentRunSnapshot,
        revision: TaskRevision,
        review: ReviewResult | SelfReviewResult | None,
        *,
        failures: list[str],
    ) -> tuple | None:
        from . import quality_state_machine

        return quality_state_machine._request_quality_repair(self, repository, current, revision, review, failures=failures)

    def _quality_fail(
        self,
        repository: PostgresAgentRunRepository,
        current: AgentRunSnapshot,
        reason: str,
    ) -> None:
        from . import quality_state_machine

        return quality_state_machine._quality_fail(self, repository, current, reason)

    def _finalize_acceptance(
        self,
        repository: PostgresAgentRunRepository,
        current: AgentRunSnapshot,
    ) -> None:
        from . import quality_acceptance

        return quality_acceptance._finalize_acceptance(self, repository, current)


@bounded_lru_cache(max_entries=1, ttl_seconds=3600.0)
def default_agent_run_service() -> AgentRunService:
    from app.jobs.store import default_job_store

    try:
        jobs = default_job_store()
    except RuntimeError:
        jobs = None
    return AgentRunService(job_store=jobs)
