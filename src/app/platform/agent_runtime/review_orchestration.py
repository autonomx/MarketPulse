"""Policy facade for durable independent-review orchestration.

The stable orchestration machinery lives in ``review_orchestration_core``. This
facade adds candidate-derived test-execution proof before reviewer launch while
preserving the existing import surface used by service, recovery, and tests.
"""
from __future__ import annotations

from typing import Any

from .event_queries import all_events
from . import review_orchestration_core as _core
from .candidate_test_validation import (
    candidate_test_validation_specs,
    missing_candidate_test_execution,
    reconcile_candidate_test_validation_results,
)
from .contracts import AgentEvent
from .repository import PostgresAgentRunRepository
from .run_repository_queries import PostgresAgentRunQueries


# Explicit aliases preserve the existing import surface without mutating the
# implementation module at call time.
review_snapshot_id_from_child = _core.review_snapshot_id_from_child
consume_terminal_reviewer_in_repository = _core.consume_terminal_reviewer_in_repository
finalize_reviewer_child_in_repository = _core.finalize_reviewer_child_in_repository


def __getattr__(name: str):
    """Delegate untouched implementation details for backward compatibility."""

    return getattr(_core, name)


def _redirect_missing_candidate_tests_before_review(
    service: Any,
    parent_run_id: str,
    snapshot_id: str,
) -> bool:
    """Complete run-owned test evidence before spending independent review.

    A TaskRevision validation plan is compiled before implementation and cannot
    name regression tests created during implementation or repair. The immutable
    ReviewSnapshot can. Any executable test in its authoritative subject must
    therefore have successful execution evidence bound to the same workspace
    state before a reviewer is launched.

    Legacy direct runner invocations (for example ``npx playwright test``) are
    reconciled into durable ValidationResult rows when their successful tool event
    occurred after the final potentially mutating tool completion. If evidence is
    still missing, the parent returns to ``validating`` on the *same* quality
    attempt via the existing bounded validation-retry path rather than consuming
    a semantic repair attempt.
    """

    action: tuple | None = None
    redirected = False
    with service._run_lock(parent_run_id):
        with service.unit_of_work(service.database) as work:
            repository = service.repository_factory(work.connection, service.context)
            locked = PostgresAgentRunQueries(work.connection, service.context).lock_run(parent_run_id).fetchone()
            if locked is None:
                work.rollback()
                return False

            parent = repository.get_run(parent_run_id)
            if (
                parent is None
                or parent.status != "waiting_for_children"
                or parent.desired_state != "running"
                or not service._quality_enabled(parent.spec)
            ):
                work.rollback()
                return False

            quality = service.quality_repository_factory(work.connection, service.context)
            snapshot = quality.get_review_snapshot(parent_run_id, snapshot_id)
            revision = service._current_revision(repository, parent_run_id)
            stage = quality.get_stage(parent_run_id) or {}
            if snapshot is None or revision is None:
                work.rollback()
                return False
            if (
                str(stage.get("stage") or "") != "reviewing"
                or snapshot.task_revision_id != revision.revision_id
                or snapshot.workspace_state_id != stage.get("workspace_state_id")
            ):
                work.rollback()
                return False

            validations = quality.list_validation_results(
                parent_run_id,
                task_revision_id=revision.revision_id,
            )
            events = all_events(repository, parent_run_id)

            reconciled = _reconcile_raw_test_runs(snapshot, validations, parent_run_id, revision, events, quality, repository)

            missing_paths = missing_candidate_test_execution(
                snapshot.subject_paths,
                validations,
                workspace_state_id=snapshot.workspace_state_id,
                events=events,
                workspace_root=snapshot.workspace_root,
            )
            if not missing_paths:
                if reconciled:
                    work.commit()
                else:
                    work.rollback()
                return False

            missing_specs = candidate_test_validation_specs(missing_paths)
            if not missing_specs:
                if reconciled:
                    work.commit()
                else:
                    work.rollback()
                return False

            attempt = max(1, int(stage.get("attempt") or 1))
            latest = repository.get_run(parent_run_id) or parent
            if latest.status == "waiting_for_children":
                latest = repository.update_state(
                    parent_run_id,
                    expected_revision=latest.revision,
                    status="running",
                    desired_state="running",
                    worker_id=service.worker_id,
                    last_error=None,
                )
            action = service._request_validation_execution(
                repository,
                latest,
                revision,
                attempt=attempt,
                workspace_state_id=snapshot.workspace_state_id,
                missing=missing_specs,
            )
            redirected = True
            work.commit()

    if action is not None:
        service._execute_quality_action(action)
    return redirected


def _reconcile_raw_test_runs(snapshot, validations, parent_run_id, revision, events, quality, repository):
    """Record successful direct test runs after the last mutation as validation results for the snapshot state."""
    reconciled = reconcile_candidate_test_validation_results(
        snapshot.subject_paths,
        validations,
        run_id=parent_run_id,
        task_revision_id=revision.revision_id,
        workspace_state_id=snapshot.workspace_state_id,
        events=events,
        workspace_root=snapshot.workspace_root,
        covers_requirement_ids=[item.id for item in revision.requirements if item.required],
    )
    for validation in reconciled:
        quality.add_validation_result(validation)
        repository.append_event(
            AgentEvent(
                run_id=parent_run_id,
                event_type="quality.validation_recorded",
                payload={
                    "result_id": validation.result_id,
                    "validation_id": validation.validation_id,
                    "kind": validation.kind,
                    "task_revision_id": validation.task_revision_id,
                    "workspace_state_id": validation.workspace_state_id,
                    "command": validation.command,
                    "exit_code": validation.exit_code,
                    "success": validation.success,
                    "outcome": validation.outcome,
                    "output_digest": validation.output_digest,
                    "covers_requirement_ids": list(validation.covers_requirement_ids),
                    "metadata": dict(validation.metadata),
                    "source": "candidate_test_raw_reconciliation",
                },
            )
        )
    if reconciled:
        validations.extend(reconciled)
    return reconciled


def launch_reviewer_children(
    service: Any,
    parent_run_id: str,
    snapshot_id: str,
    count: int,
) -> None:
    """Launch reviewers only after candidate-derived test proof is complete."""

    if _redirect_missing_candidate_tests_before_review(service, parent_run_id, snapshot_id):
        return
    _core.launch_reviewer_children(service, parent_run_id, snapshot_id, count)


def reconcile_review_progress_in_repository(
    service: Any,
    repository: PostgresAgentRunRepository,
    parent_run_id: str,
):
    """Delegate reconciliation with the service's constructed collaborators."""

    return _core.reconcile_review_progress_in_repository(
        service,
        repository,
        parent_run_id,
    )
