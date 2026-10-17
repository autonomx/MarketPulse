"""Durable parent/child resource grants with actual-spend reclamation.

A child ``RunLimits`` value is a local circuit breaker, not permanent spend.
Parent/global accounting therefore reserves the full grant only while a direct
child is active. Once that child is terminal, only actual usage of the child's
entire subtree remains charged to the parent and the unused grant is reclaimed.
Wall time is deadline-like and is never additively summed across concurrent
children.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from typing import Any, cast

from app.persistence.tenant import TenantContext

from .contracts import AgentRunSnapshot, ResourceGrant, RunLimits, utc_now

_TERMINAL = {"completed", "failed", "cancelled"}


class ResourceGrantError(ValueError):
    pass


def _grant_id(parent_run_id: str, child_run_id: str) -> str:
    return hashlib.sha256(
        f"resource-grant:{parent_run_id}:{child_run_id}".encode("utf-8")
    ).hexdigest()


class PostgresResourceGrantRepository:
    def __init__(self, connection: Any, context: TenantContext) -> None:
        self.connection = connection
        self.context = context

    def add_grant(
        self,
        *,
        parent_run_id: str,
        child_run_id: str,
        limits: RunLimits,
    ) -> ResourceGrant:
        grant = ResourceGrant(
            grant_id=_grant_id(parent_run_id, child_run_id),
            parent_run_id=parent_run_id,
            child_run_id=child_run_id,
            limits=limits,
            state="active",
        )
        self.connection.execute(
            """
            INSERT INTO omnix_agent_resource_grants (
                workspace_id, parent_run_id, child_run_id, grant_id,
                max_steps, max_tool_calls, max_tokens, max_cost,
                max_wall_time_seconds, state, created_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'active',%s)
            ON CONFLICT (workspace_id, child_run_id) DO NOTHING
            """,
            (
                self.context.workspace_id,
                parent_run_id,
                child_run_id,
                grant.grant_id,
                limits.max_steps,
                limits.max_tool_calls,
                limits.max_tokens,
                limits.max_cost,
                limits.max_wall_time_seconds,
                grant.created_at,
            ),
        )
        persisted = self.get_for_child(child_run_id)
        if persisted is None:
            raise ResourceGrantError("resource_grant_persistence_failed")
        # Child identity is unique. Retrying the exact same grant is idempotent,
        # but a different parent or limit set for that same child would silently
        # change authority if ON CONFLICT were treated as success. Fail closed so
        # concurrent/replayed child starts must converge on identical authority.
        if (
            persisted.parent_run_id != parent_run_id
            or persisted.grant_id != grant.grant_id
            or persisted.limits != limits
        ):
            raise ResourceGrantError("resource_grant_identity_conflict")
        return persisted

    def get_for_child(self, child_run_id: str) -> ResourceGrant | None:
        row = self.connection.execute(
            """
            SELECT parent_run_id, grant_id, max_steps, max_tool_calls,
                   max_tokens, max_cost, max_wall_time_seconds, state,
                   created_at, released_at
              FROM omnix_agent_resource_grants
             WHERE workspace_id = %s AND child_run_id = %s
            """,
            (self.context.workspace_id, child_run_id),
        ).fetchone()
        if row is None:
            return None
        return ResourceGrant(
            grant_id=str(row[1]),
            parent_run_id=str(row[0]),
            child_run_id=child_run_id,
            limits=RunLimits(
                max_steps=int(row[2]),
                max_tool_calls=int(row[3]),
                max_tokens=int(row[4]) if row[4] is not None else None,
                max_cost=float(row[5]) if row[5] is not None else None,
                max_wall_time_seconds=int(row[6]),
            ),
            state=cast(Any, str(row[7])),
            created_at=row[8],
            released_at=row[9],
        )

    def list_direct(self, parent_run_id: str) -> list[ResourceGrant]:
        rows = self.connection.execute(
            """
            SELECT child_run_id
              FROM omnix_agent_resource_grants
             WHERE workspace_id = %s AND parent_run_id = %s
             ORDER BY created_at, child_run_id
            """,
            (self.context.workspace_id, parent_run_id),
        ).fetchall()
        return [
            grant
            for row in rows
            if (grant := self.get_for_child(str(row[0]))) is not None
        ]

    def _run_status(self, run_id: str) -> str | None:
        row = self.connection.execute(
            """
            SELECT status FROM omnix_agent_runs
             WHERE workspace_id = %s AND run_id = %s
            """,
            (self.context.workspace_id, run_id),
        ).fetchone()
        return str(row[0]) if row else None

    def subtree_usage(self, root_run_id: str) -> dict[str, int | float]:
        row = self.connection.execute(
            """
            WITH RECURSIVE descendants(run_id) AS (
                SELECT %s::text
                UNION ALL
                SELECT child.run_id
                  FROM omnix_agent_runs AS child
                  JOIN descendants AS parent
                    ON child.parent_run_id = parent.run_id
                 WHERE child.workspace_id = %s
            )
            SELECT COALESCE(SUM(usage.steps), 0),
                   COALESCE(SUM(usage.tool_calls), 0),
                   COALESCE(SUM(usage.output_tokens), 0),
                   COALESCE(SUM(usage.cost), 0.0)
              FROM descendants
              LEFT JOIN omnix_agent_run_usage AS usage
                ON usage.workspace_id = %s
               AND usage.run_id = descendants.run_id
            """,
            (root_run_id, self.context.workspace_id, self.context.workspace_id),
        ).fetchone()
        return {
            "steps": int(row[0] or 0),
            "tool_calls": int(row[1] or 0),
            "output_tokens": int(row[2] or 0),
            "cost": float(row[3] or 0.0),
        }

    def reconcile_terminal_grants(self, parent_run_id: str) -> None:
        for grant in self.list_direct(parent_run_id):
            status = self._run_status(grant.child_run_id)
            if status not in _TERMINAL or grant.state != "active":
                continue
            usage = self.subtree_usage(grant.child_run_id)
            exhausted = (
                int(usage["steps"]) >= grant.limits.max_steps
                or int(usage["tool_calls"]) >= grant.limits.max_tool_calls
                or (
                    grant.limits.max_tokens is not None
                    and int(usage["output_tokens"]) >= grant.limits.max_tokens
                )
                or (
                    grant.limits.max_cost is not None
                    and float(usage["cost"]) >= grant.limits.max_cost
                )
            )
            self.connection.execute(
                """
                UPDATE omnix_agent_resource_grants
                   SET state = %s, released_at = CURRENT_TIMESTAMP
                 WHERE workspace_id = %s AND parent_run_id = %s
                   AND child_run_id = %s AND state = 'active'
                """,
                (
                    "exhausted" if exhausted else "released",
                    self.context.workspace_id,
                    parent_run_id,
                    grant.child_run_id,
                ),
            )

    def reserved_child_resources(self, parent_run_id: str) -> dict[str, int | float]:
        """Return direct-child authority currently charged to ``parent_run_id``.

        Active direct children reserve their whole grant. Terminal children charge
        only actual subtree spend. This deliberately does not add nested grants:
        nested work is already contained inside its direct ancestor's grant.
        """

        self.reconcile_terminal_grants(parent_run_id)
        totals: dict[str, int | float] = {
            "steps": 0,
            "tool_calls": 0,
            "output_tokens": 0,
            "cost": 0.0,
        }
        for grant in self.list_direct(parent_run_id):
            status = self._run_status(grant.child_run_id)
            if status not in _TERMINAL and grant.state == "active":
                totals["steps"] += grant.limits.max_steps
                totals["tool_calls"] += grant.limits.max_tool_calls
                totals["output_tokens"] += grant.limits.max_tokens or 0
                totals["cost"] += grant.limits.max_cost or 0.0
                continue
            usage = self.subtree_usage(grant.child_run_id)
            totals["steps"] += int(usage["steps"])
            totals["tool_calls"] += int(usage["tool_calls"])
            totals["output_tokens"] += int(usage["output_tokens"])
            totals["cost"] += float(usage["cost"])
        return totals

    def available_capacity(
        self,
        parent: AgentRunSnapshot,
        *,
        parent_usage: dict[str, Any],
        protected_fraction: float = 0.0,
    ) -> dict[str, int | float | None]:
        child = self.reserved_child_resources(parent.run_id)
        fraction = max(0.0, min(float(protected_fraction), 0.5))
        limits = parent.spec.limits

        def remaining_int(maximum: int, used: int, child_used: int) -> int:
            protected = int(maximum * fraction)
            return max(0, maximum - used - child_used - protected)

        return {
            "max_steps": remaining_int(
                limits.max_steps,
                int(parent_usage.get("steps", 0)),
                int(child["steps"]),
            ),
            "max_tool_calls": remaining_int(
                limits.max_tool_calls,
                int(parent_usage.get("tool_calls", 0)),
                int(child["tool_calls"]),
            ),
            "max_tokens": (
                remaining_int(
                    limits.max_tokens,
                    int(parent_usage.get("output_tokens", 0)),
                    int(child["output_tokens"]),
                )
                if limits.max_tokens is not None
                else None
            ),
            "max_cost": (
                max(
                    0.0,
                    float(limits.max_cost)
                    - float(parent_usage.get("cost", 0.0))
                    - float(child["cost"])
                    - float(limits.max_cost) * fraction,
                )
                if limits.max_cost is not None
                else None
            ),
            "max_wall_time_seconds": self.remaining_wall_time(parent),
        }

    @staticmethod
    def remaining_wall_time(parent: AgentRunSnapshot) -> int:
        anchor = parent.started_at or parent.created_at
        elapsed = max(0.0, (datetime.now(timezone.utc) - anchor).total_seconds())
        return max(0, int(parent.spec.limits.max_wall_time_seconds - elapsed))

    def assert_can_grant(
        self,
        parent: AgentRunSnapshot,
        child_limits: RunLimits,
        *,
        parent_usage: dict[str, Any],
        protected_fraction: float = 0.0,
    ) -> None:
        available = self.available_capacity(
            parent,
            parent_usage=parent_usage,
            protected_fraction=protected_fraction,
        )
        checks = (
            ("max_steps", child_limits.max_steps),
            ("max_tool_calls", child_limits.max_tool_calls),
        )
        for name, requested in checks:
            if requested > int(available[name] or 0):
                raise ResourceGrantError(
                    f"parent_global_budget_exhausted:{name}:requested={requested}:available={available[name]}"
                )
        if child_limits.max_tokens is not None and available["max_tokens"] is not None:
            if child_limits.max_tokens > int(available["max_tokens"] or 0):
                raise ResourceGrantError("parent_global_budget_exhausted:max_tokens")
        if child_limits.max_cost is not None and available["max_cost"] is not None:
            if child_limits.max_cost > float(available["max_cost"] or 0.0):
                raise ResourceGrantError("parent_global_budget_exhausted:max_cost")
        # Wall time is an absolute/deadline-style bound. Concurrent child wall
        # times are not summed; each child simply cannot outlive the parent.
        if child_limits.max_wall_time_seconds > int(available["max_wall_time_seconds"] or 0):
            raise ResourceGrantError("parent_global_budget_exhausted:max_wall_time_seconds")

    def own_effective_limits(
        self,
        parent: AgentRunSnapshot,
        *,
        quality_reserve: dict[str, int | float] | None = None,
    ) -> dict[str, int | float | None]:
        """Return maxima available to the parent's *own* meter.

        Historical terminal children contribute actual spend. Active direct
        children reserve their grant. The caller may additionally protect future
        quality work without confusing that protection with child spend.
        """

        child = self.reserved_child_resources(parent.run_id)
        reserve = quality_reserve or {"steps": 0, "tools": 0, "tokens": 0, "cost": 0.0}
        limits = parent.spec.limits
        return {
            "max_steps": max(0, limits.max_steps - int(child["steps"]) - int(reserve.get("steps", 0))),
            "max_tool_calls": max(
                0,
                limits.max_tool_calls - int(child["tool_calls"]) - int(reserve.get("tools", 0)),
            ),
            "max_tokens": (
                max(
                    0,
                    limits.max_tokens - int(child["output_tokens"]) - int(reserve.get("tokens", 0)),
                )
                if limits.max_tokens is not None
                else None
            ),
            "max_cost": (
                max(
                    0.0,
                    limits.max_cost - float(child["cost"]) - float(reserve.get("cost", 0.0)),
                )
                if limits.max_cost is not None
                else None
            ),
        }
