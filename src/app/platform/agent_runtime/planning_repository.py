"""PostgreSQL persistence for durable coding planning authority."""
from __future__ import annotations

import json
from typing import Any

from app.persistence.tenant import TenantContext

from .planning_contracts import (
    ImpactCandidate,
    ImplementationPlanRevision,
    InspectionEvidence,
    PlanningDecision,
)


def _json(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


class PostgresPlanningRepository:
    def __init__(self, connection: Any, context: TenantContext) -> None:
        self.connection = connection
        self.context = context

    def lock_state(self, run_id: str) -> None:
        """Row-lock the run's planning state for the rest of the transaction."""
        self.connection.execute(
            """
            SELECT run_id
              FROM omnix_agent_planning_state
             WHERE workspace_id = %s AND run_id = %s
             FOR UPDATE
            """,
            (self.context.workspace_id, run_id),
        ).fetchone()

    def completed_semantic_review_rejections(self, run_id: str, task_revision_id: str) -> int:
        """Count completed blocking reviews in the current consensus cycle.

        A successful approved plan ends a cycle. Structural rejections and reviewer
        transport failures do not consume semantic consensus rounds.
        """
        row = self.connection.execute(
            """
            WITH last_approved AS (
                SELECT COALESCE(MAX(sequence), 0) AS sequence
                  FROM omnix_agent_plan_revisions
                 WHERE workspace_id = %s AND run_id = %s AND task_revision_id = %s
                   AND status = 'approved'
            )
            SELECT COUNT(*)
              FROM omnix_agent_plan_revisions, last_approved
             WHERE workspace_id = %s AND run_id = %s AND task_revision_id = %s
               AND omnix_agent_plan_revisions.sequence > last_approved.sequence
               AND status = 'rejected'
               AND payload ? 'semantic_review'
               AND payload -> 'semantic_review' IS NOT NULL
               AND payload -> 'semantic_review' ->> 'status' = 'completed'
            """,
            (
                self.context.workspace_id,
                run_id,
                task_revision_id,
                self.context.workspace_id,
                run_id,
                task_revision_id,
            ),
        ).fetchone()
        return int(row[0] or 0)

    def get_state(self, run_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT mode, task_revision_id, status, latest_plan_revision_id,
                   active_plan_revision_id, planning_baseline_id,
                   baseline_provenance, updated_at
              FROM omnix_agent_planning_state
             WHERE workspace_id = %s AND run_id = %s
            """,
            (self.context.workspace_id, run_id),
        ).fetchone()
        if row is None:
            return None
        return {
            "mode": str(row[0]),
            "task_revision_id": str(row[1]) if row[1] else None,
            "status": str(row[2]),
            "latest_plan_revision_id": str(row[3]) if row[3] else None,
            "active_plan_revision_id": str(row[4]) if row[4] else None,
            "planning_baseline_id": str(row[5]) if row[5] else None,
            "baseline_provenance": dict(row[6] or {}),
            "updated_at": row[7],
        }

    def set_state(
        self,
        run_id: str,
        *,
        mode: str,
        task_revision_id: str | None,
        status: str,
        latest_plan_revision_id: str | None,
        active_plan_revision_id: str | None,
        planning_baseline_id: str | None,
        baseline_provenance: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        row = self.connection.execute(
            """
            INSERT INTO omnix_agent_planning_state (
                workspace_id, run_id, mode, task_revision_id, status,
                latest_plan_revision_id, active_plan_revision_id,
                planning_baseline_id, baseline_provenance, updated_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,CURRENT_TIMESTAMP)
            ON CONFLICT (workspace_id, run_id) DO UPDATE
               SET mode = EXCLUDED.mode,
                   task_revision_id = EXCLUDED.task_revision_id,
                   status = CASE
                       WHEN omnix_agent_planning_state.task_revision_id = EXCLUDED.task_revision_id
                            AND omnix_agent_planning_state.status = 'blocked'
                            AND EXCLUDED.status <> 'approved'
                       THEN 'blocked'
                       ELSE EXCLUDED.status
                   END,
                   latest_plan_revision_id = EXCLUDED.latest_plan_revision_id,
                   active_plan_revision_id = EXCLUDED.active_plan_revision_id,
                   planning_baseline_id = EXCLUDED.planning_baseline_id,
                   baseline_provenance = EXCLUDED.baseline_provenance,
                   updated_at = CURRENT_TIMESTAMP
            RETURNING mode, task_revision_id, status, latest_plan_revision_id,
                      active_plan_revision_id, planning_baseline_id,
                      baseline_provenance, updated_at
            """,
            (
                self.context.workspace_id, run_id, mode, task_revision_id, status,
                latest_plan_revision_id, active_plan_revision_id,
                planning_baseline_id, _json(baseline_provenance or {}),
            ),
        ).fetchone()
        return {
            "mode": str(row[0]),
            "task_revision_id": str(row[1]) if row[1] else None,
            "status": str(row[2]),
            "latest_plan_revision_id": str(row[3]) if row[3] else None,
            "active_plan_revision_id": str(row[4]) if row[4] else None,
            "planning_baseline_id": str(row[5]) if row[5] else None,
            "baseline_provenance": dict(row[6] or {}),
            "updated_at": row[7],
        }

    def add_inspection_evidence(self, item: InspectionEvidence) -> InspectionEvidence:
        self.connection.execute(
            """
            INSERT INTO omnix_agent_inspection_evidence (
                workspace_id, run_id, task_revision_id, evidence_id, kind, path,
                completeness, result_digest, payload, created_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
            ON CONFLICT (workspace_id, run_id, evidence_id) DO UPDATE
               SET completeness = EXCLUDED.completeness,
                   result_digest = EXCLUDED.result_digest,
                   payload = EXCLUDED.payload
            """,
            (
                self.context.workspace_id, item.run_id, item.task_revision_id,
                item.evidence_id, item.kind, item.path, item.completeness,
                item.result_digest, _json(item), item.created_at,
            ),
        )
        return item

    def list_inspection_evidence(
        self, run_id: str, *, task_revision_id: str
    ) -> list[InspectionEvidence]:
        rows = self.connection.execute(
            """
            SELECT payload
              FROM omnix_agent_inspection_evidence
             WHERE workspace_id = %s AND run_id = %s AND task_revision_id = %s
             ORDER BY created_at, evidence_id
            """,
            (self.context.workspace_id, run_id, task_revision_id),
        ).fetchall()
        return [InspectionEvidence.model_validate(dict(row[0] or {})) for row in rows]

    def add_impact_candidate(self, item: ImpactCandidate) -> ImpactCandidate:
        self.connection.execute(
            """
            INSERT INTO omnix_agent_impact_candidates (
                workspace_id, run_id, task_revision_id, candidate_id, path,
                relation, impact_likelihood, payload, created_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
            ON CONFLICT (workspace_id, run_id, candidate_id) DO UPDATE
               SET impact_likelihood = EXCLUDED.impact_likelihood,
                   payload = EXCLUDED.payload
            """,
            (
                self.context.workspace_id, item.run_id, item.task_revision_id,
                item.candidate_id, item.path, item.relation,
                item.impact_likelihood, _json(item), item.created_at,
            ),
        )
        return item

    def list_impact_candidates(
        self, run_id: str, *, task_revision_id: str
    ) -> list[ImpactCandidate]:
        rows = self.connection.execute(
            """
            SELECT payload
              FROM omnix_agent_impact_candidates
             WHERE workspace_id = %s AND run_id = %s AND task_revision_id = %s
             ORDER BY created_at, candidate_id
            """,
            (self.context.workspace_id, run_id, task_revision_id),
        ).fetchall()
        return [ImpactCandidate.model_validate(dict(row[0] or {})) for row in rows]

    def next_plan_sequence(self, run_id: str, task_revision_id: str) -> int:
        row = self.connection.execute(
            """
            SELECT COALESCE(MAX(sequence), 0) + 1
              FROM omnix_agent_plan_revisions
             WHERE workspace_id = %s AND run_id = %s AND task_revision_id = %s
            """,
            (self.context.workspace_id, run_id, task_revision_id),
        ).fetchone()
        return int(row[0] or 1)

    def add_plan(self, plan: ImplementationPlanRevision) -> ImplementationPlanRevision:
        self.connection.execute(
            """
            INSERT INTO omnix_agent_plan_revisions (
                workspace_id, run_id, task_revision_id, plan_revision_id,
                sequence, previous_plan_revision_id, source, status, mode,
                planning_baseline_id, inspection_evidence_digest, payload, created_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
            """,
            (
                self.context.workspace_id, plan.run_id, plan.task_revision_id,
                plan.plan_revision_id, plan.sequence, plan.previous_plan_revision_id,
                plan.source, plan.status, plan.mode,
                plan.authority.planning_baseline_id,
                plan.authority.inspection_evidence_digest,
                _json(plan), plan.created_at,
            ),
        )
        if (
            plan.status != "approved"
            and plan.semantic_review is not None
            and plan.semantic_review.status == "unavailable"
        ):
            self.mark_state_blocked(
                plan.run_id,
                task_revision_id=plan.task_revision_id,
            )
        return plan

    def get_plan(self, run_id: str, plan_revision_id: str) -> ImplementationPlanRevision | None:
        row = self.connection.execute(
            """
            SELECT payload
              FROM omnix_agent_plan_revisions
             WHERE workspace_id = %s AND run_id = %s AND plan_revision_id = %s
            """,
            (self.context.workspace_id, run_id, plan_revision_id),
        ).fetchone()
        return ImplementationPlanRevision.model_validate(dict(row[0] or {})) if row else None

    def latest_plan(
        self, run_id: str, *, task_revision_id: str
    ) -> ImplementationPlanRevision | None:
        row = self.connection.execute(
            """
            SELECT plan_revision_id
              FROM omnix_agent_plan_revisions
             WHERE workspace_id = %s AND run_id = %s AND task_revision_id = %s
             ORDER BY sequence DESC, created_at DESC
             LIMIT 1
            """,
            (self.context.workspace_id, run_id, task_revision_id),
        ).fetchone()
        return self.get_plan(run_id, str(row[0])) if row else None

    def latest_approved_plan(
        self, run_id: str, *, task_revision_id: str
    ) -> ImplementationPlanRevision | None:
        row = self.connection.execute(
            """
            SELECT plan_revision_id
              FROM omnix_agent_plan_revisions
             WHERE workspace_id = %s AND run_id = %s AND task_revision_id = %s
               AND status = 'approved'
             ORDER BY sequence DESC, created_at DESC
             LIMIT 1
            """,
            (self.context.workspace_id, run_id, task_revision_id),
        ).fetchone()
        return self.get_plan(run_id, str(row[0])) if row else None

    def mark_state_blocked(self, run_id: str, *, task_revision_id: str) -> None:
        self.connection.execute(
            """
            UPDATE omnix_agent_planning_state
               SET status = 'blocked', updated_at = CURRENT_TIMESTAMP
             WHERE workspace_id = %s AND run_id = %s AND task_revision_id = %s
            """,
            (self.context.workspace_id, run_id, task_revision_id),
        )

    def mark_state_stale(self, run_id: str) -> None:
        self.connection.execute(
            """
            UPDATE omnix_agent_planning_state
               SET status = 'stale', updated_at = CURRENT_TIMESTAMP
             WHERE workspace_id = %s AND run_id = %s
            """,
            (self.context.workspace_id, run_id),
        )

    def add_decision(self, decision: PlanningDecision) -> PlanningDecision:
        self.connection.execute(
            """
            INSERT INTO omnix_agent_planning_decisions (
                workspace_id, run_id, decision_id, task_revision_id,
                plan_revision_id, mode, tool_name, effect, target,
                allowed, would_block, reasons, created_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
            ON CONFLICT (workspace_id, run_id, decision_id) DO NOTHING
            """,
            (
                self.context.workspace_id, decision.run_id, decision.decision_id,
                decision.task_revision_id, decision.plan_revision_id,
                decision.mode, decision.tool_name, decision.effect,
                decision.target, decision.allowed, decision.would_block,
                _json(decision.reasons), decision.created_at,
            ),
        )
        return decision